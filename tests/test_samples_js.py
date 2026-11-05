"""Parses every bundled sample macro with Chrome's own module parser, and checks setting-value escaping.

Skipped automatically when no Chromium-based browser is installed.
"""
import html
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from macro_generator import samples
from macro_generator.samples import SampleSession, apply_param, load_catalog
from test_macros_js import find_browser

TRICKY = [
    "plain", "it's", 'say "hi"', "back\\slash", "line1\nline2", "tab\there", "\r\n", "unicode é 中 \U0001f600",
    "</script>", "${notATemplate}", "`tick`", "  sep", "", "'; alert(1); //",
]


def collect():
    macros = {}
    for sample in load_catalog():
        s = SampleSession(sample, use_cache=False)
        for m in sample.macros:
            if m.key in s.text:
                macros[f"{sample.id}/{m.name}"] = s.text[m.key]
        # every example/optional macro too (selected or not): parse is what matters
    escapes = [{"code": apply_param("const v = 'x';", "v", value), "expected": value} for value in TRICKY]
    return macros, escapes


def run(browser, macros, escapes):
    template = (Path(__file__).parent / "js_parse_harness.html.tpl").read_text(encoding="utf-8")
    def embed(data):   # "</script>" inside the data must not end the <script> block
        return json.dumps(data).replace("</", "<\\/")

    page = template.replace("/*__MACROS__*/", embed(macros)).replace("/*__ESCAPES__*/", embed(escapes))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "h.html"
        path.write_text(page, encoding="utf-8")
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", f"--user-data-dir={Path(tmp) / 'p'}",
             "--virtual-time-budget=60000", "--dump-dom", path.as_uri()],
            capture_output=True, text=True, timeout=180, encoding="utf-8")
    m = re.search(r'<pre id="out">(.*?)</pre>', proc.stdout, re.S)
    if not m or m.group(1) == "PENDING":
        raise RuntimeError(f"harness did not finish: {proc.stderr[-400:]}")
    return json.loads(html.unescape(m.group(1)))


@unittest.skipUnless(find_browser(), "no Chrome/Edge found")
class SampleJavaScript(unittest.TestCase):
    def test_every_bundled_macro_is_valid_javascript_and_settings_escape_correctly(self):
        macros, escapes = collect()
        self.assertGreater(len(macros), 80)
        result = run(find_browser(), macros, escapes)

        bad = {k: v for k, v in result["parse"].items() if v != "ok"}
        self.assertEqual(bad, {}, "macros that do not parse")
        self.assertEqual(result["escapes"], ["ok"] * len(TRICKY), dict(zip(TRICKY, result["escapes"])))


if __name__ == "__main__":
    unittest.main()
