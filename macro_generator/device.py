"""Push a macro to a device over its local HTTP xAPI (POST /putxml)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests
import urllib3


@dataclass
class PushResult:
    host: str
    ok: bool
    steps: list[str] = field(default_factory=list)
    error: str = ""


def _command_xml(path: list[str], params: dict[str, str] | None = None, body: str | None = None) -> bytes:
    root = ET.Element("Command")
    node = root
    for part in path:
        node = ET.SubElement(node, part)
    for key, value in (params or {}).items():
        ET.SubElement(node, key).text = value
    if body is not None:
        ET.SubElement(node, "body").text = body
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _config_xml(path: list[str], value: str) -> bytes:
    root = ET.Element("Configuration")
    node = root
    for part in path:
        node = ET.SubElement(node, part)
    node.text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def find_error(response_xml: str) -> str:
    """Return the device's error text from a putxml reply, or '' when it succeeded."""
    try:
        root = ET.fromstring(response_xml)
    except ET.ParseError:
        return f"Unreadable reply from device: {response_xml[:200]!r}"
    for el in root.iter():
        if el.tag in ("Error", "Reason") and (el.text or "").strip():
            return el.text.strip()
        if el.attrib.get("status", "").lower() == "error":
            reason = el.find("Reason")
            return (reason.text or "").strip() if reason is not None else f"{el.tag} reported an error"
    return ""



def push_bundle(
    host: str,
    username: str,
    password: str,
    macros: list[tuple[str, str, bool]],
    panels: list[tuple[str, str]] | None = None,
    restart_runtime: bool = True,
    transpile: bool = True,
    verify_tls: bool = False,
    timeout: float = 20,
    session: requests.Session | None = None,
) -> PushResult:
    """Install macros (name, code, activate) and UI panels (panel_id, xml) on one device.

    Order matters: every macro is saved first (so './lib' imports resolve), then the panels,
    then macros are activated (libraries are saved but not activated), then the runtime restarts once.
    """
    result = PushResult(host=host, ok=False)
    http = session or requests.Session()
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def post(payload: bytes, label: str) -> bool:
        try:
            reply = http.post(
                f"https://{host}/putxml",
                data=payload,
                headers={"Content-Type": "text/xml"},
                auth=(username, password),
                verify=verify_tls,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            result.error = f"{label}: {exc}"
            return False
        if reply.status_code == 401:
            result.error = f"{label}: login rejected (check username / password; the user needs the Admin role)."
            return False
        if reply.status_code != 200:
            result.error = f"{label}: HTTP {reply.status_code}"
            return False
        err = find_error(reply.text)
        if err:
            result.error = f"{label}: {err}"
            return False
        result.steps.append(label)
        return True

    if macros and not post(_config_xml(["Macros", "Mode"], "On"), "Macros mode enabled"):
        return result
    for name, code, _activate in macros:
        save = _command_xml(
            ["Macros", "Macro", "Save"],
            {"Name": name, "Overwrite": "True", "Transpile": "True" if transpile else "False"},
            code,
        )
        if not post(save, f"Macro '{name}' saved"):
            return result
    for panel_id, xml in panels or []:
        save = _command_xml(["UserInterface", "Extensions", "Panel", "Save"], {"PanelId": panel_id}, xml)
        if not post(save, f"UI panel '{panel_id}' saved"):
            return result
    for name, _code, activate in macros:
        if activate and not post(_command_xml(["Macros", "Macro", "Activate"], {"Name": name}), f"Macro '{name}' activated"):
            return result
    if macros and restart_runtime and not post(_command_xml(["Macros", "Runtime", "Restart"]), "Macro runtime restarted"):
        return result

    result.ok = True
    return result


def push_macro(
    host: str,
    username: str,
    password: str,
    name: str,
    code: str,
    activate: bool = True,
    restart_runtime: bool = True,
    transpile: bool = True,
    verify_tls: bool = False,
    timeout: float = 20,
    session: requests.Session | None = None,
) -> PushResult:
    return push_bundle(
        host, username, password, [(name, code, activate)], None,
        restart_runtime=restart_runtime, transpile=transpile, verify_tls=verify_tls, timeout=timeout, session=session,
    )
