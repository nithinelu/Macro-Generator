"""Shared helpers: drive the virtual-device page in a real-time headless Chrome (see cdp.py)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdp import Chrome
from macro_generator.simulator import build_page
from test_macros_js import find_browser

READY = """(async () => {
  await Promise.race([window.__sim.ready, new Promise((r) => setTimeout(r, 15000))]);
  if (!window.__sim.isReady()) {
    throw new Error('runtime never became ready. console: ' + JSON.stringify(window.__sim.lines().map((l) => l.text)) +
                    ' loaded: ' + JSON.stringify(window.__sim.loaded()));
  }
})()"""


class Sim:
    """One browser; `open()` loads a page and waits for the macros to finish loading."""

    def __enter__(self):
        self.chrome = Chrome(find_browser()).__enter__()
        self._tmp = tempfile.TemporaryDirectory()
        self._count = 0
        return self

    def __exit__(self, *exc):
        self.chrome.__exit__(*exc)
        self._tmp.cleanup()

    def open(self, page: str):
        self._count += 1
        path = Path(self._tmp.name) / f"sim{self._count}.html"
        path.write_text(page, encoding="utf-8")
        self.chrome.navigate(path.as_uri())
        self.chrome.evaluate(READY)

    def run(self, js: str, timeout: float = 60):
        """Run an async function body in the page (window.__sim available) and return its value."""
        return self.chrome.evaluate(f"(async () => {{ {js} }})()", timeout=timeout)

    def screenshot(self, path: str):
        self.chrome.screenshot(path)


def page_for(title, macros, panels=None, state=None):
    return build_page(title, macros, panels, state)
