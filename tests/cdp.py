"""Tiny Chrome DevTools Protocol client (standard library only) for driving a real-time headless browser.

Used instead of `--dump-dom --virtual-time-budget`, because virtual time stalls while a module import is
pending, which is exactly how the virtual device loads macros.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


class WebSocket:
    def __init__(self, url: str):
        assert url.startswith("ws://")
        host_port, _, path = url[5:].partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        reply = b""
        while b"\r\n\r\n" not in reply:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            reply += chunk
        if b" 101 " not in reply.split(b"\r\n", 1)[0]:
            raise ConnectionError(reply.split(b"\r\n", 1)[0].decode(errors="replace"))
        self._buffer = reply.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self._buffer) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:n], self._buffer[n:]
        return data

    def send(self, text: str):
        payload = text.encode()
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self) -> str:
        message = b""
        while True:
            b1, b2 = self._read(2)
            opcode, length = b1 & 0x0F, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            data = self._read(length)
            if opcode == 0x9:                      # ping -> ignore (Chrome does not require pongs)
                continue
            if opcode == 0x8:
                raise ConnectionError("websocket closed by browser")
            message += data
            if b1 & 0x80:
                return message.decode()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Chrome:
    def __init__(self, browser: str, width: int = 1500, height: int = 950):
        self.browser, self.width, self.height = browser, width, height
        self._next = 0

    def __enter__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = subprocess.Popen(
            [self.browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             f"--remote-debugging-port={port}", f"--user-data-dir={Path(self.tmp.name) / 'profile'}",
             f"--window-size={self.width},{self.height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        targets = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2) as r:
                    targets = [t for t in json.load(r) if t.get("type") == "page"]
                if targets:
                    break
            except OSError:
                time.sleep(0.2)
        if not targets:
            self.__exit__()
            raise RuntimeError("Chrome did not start with a debuggable page")
        self.ws = WebSocket(targets[0]["webSocketDebuggerUrl"])
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride", width=self.width, height=self.height, deviceScaleFactor=1, mobile=False)
        return self

    def __exit__(self, *exc):
        try:
            self.ws.close()
        except AttributeError:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.tmp.cleanup()

    def call(self, method: str, **params):
        self._next += 1
        ident = self._next
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def navigate(self, url: str):
        self.call("Page.navigate", url=url)
        deadline = time.time() + 30
        while time.time() < deadline:                       # wait for document.readyState === 'complete'
            if self.evaluate("document.readyState") == "complete" and self.evaluate("location.href") != "about:blank":
                return
            time.sleep(0.05)
        raise TimeoutError(f"page did not load: {url}")

    def evaluate(self, expression: str, timeout: float = 60):
        """Evaluate JS (awaiting a returned promise) and return its JSON-able value."""
        self.ws.sock.settimeout(timeout)
        result = self.call("Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True, timeout=int(timeout * 1000))
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = (details.get("exception") or {}).get("description") or details.get("text")
            raise AssertionError(text)
        return result["result"].get("value")

    def screenshot(self, path: str):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        Path(path).write_bytes(base64.b64decode(data))
