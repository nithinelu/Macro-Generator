"""Runs every generated macro in headless Chrome/Edge against a mock xapi with a virtual clock.

Skipped automatically when no Chromium-based browser is installed.
"""
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_generator.profile import generate
from macro_generator.templates import BY_ID

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser():
    for p in BROWSERS:
        if Path(p).exists():
            return p
    return shutil.which("chrome") or shutil.which("msedge") or shutil.which("google-chrome")


def build_macros() -> dict[str, str]:
    def gen(tid, **overrides):
        t = BY_ID[tid]
        js, errors = generate(t, t.default_macro_name, t.defaults() | overrides)
        assert not errors, (tid, errors)
        return js

    rc = BY_ID["room_control"].defaults()
    rc_status = dict(
        lights=[dict(rc["lights"][0], statusUrl="http://192.168.1.50/api/lights/1/status")],
        climate=[dict(rc["climate"][0], statusUrl="http://192.168.1.50/api/climate/1/status")],
    )
    return {
        "room_release": gen("room_release", releaseDelaySeconds=60, promptDurationSeconds=30),
        "room_control": gen("room_control"),
        "room_control_status": gen("room_control", **rc_status),
        "visitor_kiosk": gen("visitor_kiosk"),
        "quick_dial": gen("quick_dial"),
        "quick_dial_special": gen("quick_dial", entries=[{"label": 'R&D "Lab" <1>', "number": "lab@example.com"}]),
        "report_issue": gen("report_issue"),
    }


def run_harness(browser: str) -> list[dict]:
    template = (Path(__file__).parent / "js_harness.html.tpl").read_text(encoding="utf-8")
    page = template.replace("/*__MACROS__*/", json.dumps(build_macros()).replace("</", "<\\/"))
    with tempfile.TemporaryDirectory() as tmp:
        page_path = Path(tmp) / "harness.html"
        page_path.write_text(page, encoding="utf-8")
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", f"--user-data-dir={Path(tmp) / 'profile'}",
             "--virtual-time-budget=120000", "--dump-dom", page_path.as_uri()],
            capture_output=True, text=True, timeout=180, encoding="utf-8",
        )
    match = re.search(r'<pre id="out">(.*?)</pre>', proc.stdout, re.S)
    if not match or match.group(1) == "PENDING":
        raise RuntimeError(f"Harness did not finish.\nstdout tail: {proc.stdout[-500:]}\nstderr tail: {proc.stderr[-500:]}")
    return json.loads(html.unescape(match.group(1)))


@unittest.skipUnless(find_browser(), "no Chrome/Edge found")
class GeneratedMacroBehaviour(unittest.TestCase):
    def test_scenarios(self):
        results = run_harness(find_browser())
        self.assertGreater(len(results), 20)
        failures = [f"{r['name']}: {r['detail']}" for r in results if not r["ok"]]
        self.assertEqual(failures, [], "\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
