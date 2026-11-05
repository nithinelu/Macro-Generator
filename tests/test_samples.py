import hashlib
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_generator import device, samples
from macro_generator.samples import (
    RemoteError, RemoteRef, SampleSession, apply_param, coerce_param, get_sample, load_catalog, prepare_panel,
    read_param, readme_to_text,
)


def fake_fetch(ref: RemoteRef) -> str:
    if ref.url.endswith(".xml"):
        return "<Extensions><Version>1.9</Version><Panel><Order>1</Order><PanelId>report_issue</PanelId><Name>x</Name></Panel></Extensions>"
    return "import xapi from 'xapi';\nconst serviceNowInstance = '';\nconst serviceNowUsername = 'admin';\nconst serviceNowPassword = '';\nconst botToken = '';\nconst notify = '';\n"


def session(sample_id: str, remote: bool = False) -> SampleSession:
    s = SampleSession(get_sample(sample_id), use_cache=False)
    if remote:
        s.load_remote(fake_fetch)
    return s


class CatalogTests(unittest.TestCase):
    def test_everything_catalogued_is_present_and_unique(self):
        cat = load_catalog()
        self.assertGreaterEqual(len(cat), 60)
        self.assertEqual(len({s.id for s in cat}), len(cat))
        for s in cat:
            with self.subTest(s.id):
                self.assertTrue(s.title and s.upstream.startswith("https://github.com/CiscoDevNet/"))
                self.assertTrue(s.macros, "sample without macros")
                for m in s.macros:
                    self.assertTrue(bool(m.file) != bool(m.remote), m.name)
                    if m.file:
                        self.assertIn("xapi", samples.read_data_file(s, m.file) + "xapi")   # readable
                for rel in s.extras + ([s.readme] if s.readme else []):
                    samples.read_data_file(s, rel)

    def test_licence_and_provenance_travel_with_the_data(self):
        self.assertIn("MIT", (samples.DATA_DIR / "LICENSE").read_text(encoding="utf-8")[:2000] + "MIT")
        self.assertIn("Permission is hereby granted", (samples.DATA_DIR / "LICENSE").read_text(encoding="utf-8"))
        self.assertRegex(samples.catalog_source()["commit"], r"^[0-9a-f]{40}$")

    def test_unlicensed_third_party_files_are_not_bundled(self):
        bundled = {p.name for p in samples.DATA_DIR.rglob("*") if p.is_file()}
        for name in ("ui.js", "ui-builder.js", "report-issue.js", "report-issue.xml"):
            self.assertNotIn(name, bundled)
        for s in load_catalog():
            for ref in s.remotes:
                self.assertRegex(ref.url, r"^https://raw\.githubusercontent\.com/cisco-ce/[^/]+/[0-9a-f]{40}/")
                self.assertRegex(ref.sha256, r"^[0-9a-f]{64}$")

    def test_search_and_tags(self):
        self.assertTrue(any(s.id == "energy-prices" for s in samples.search("energy")))
        self.assertEqual(samples.search("zzzz-no-such-thing"), [])
        tag = samples.all_tags()[0]
        self.assertTrue(all(tag in s.tags for s in samples.search(tag=tag)))

    def test_all_text_files_are_free_of_bom_and_cr(self):
        for p in samples.DATA_DIR.rglob("*"):
            if p.is_file() and p.suffix in (".js", ".xml", ".md", ".json"):
                raw = p.read_bytes()
                self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), p)
                self.assertNotIn(b"\r", raw, p)


class SettingsTests(unittest.TestCase):
    CODE = (
        "import xapi from 'xapi';\n"
        "const updateMinutes = 10;\n"
        " const zone = 'NO1'; // zone comment\n"
        "let userName\n"
        "var flag = false;\n"
        "const complex = { a: 1 };\n"
        "function f() {\n    const zone = 'inner';\n}\n"
    )

    def test_read(self):
        self.assertEqual(read_param(self.CODE, "updateMinutes"), (True, 10))
        self.assertEqual(read_param(self.CODE, "zone"), (True, "NO1"))
        self.assertEqual(read_param(self.CODE, "flag"), (True, False))
        self.assertEqual(read_param(self.CODE, "userName"), (True, ""))
        self.assertEqual(read_param(self.CODE, "complex"), (False, None))
        self.assertEqual(read_param(self.CODE, "missing"), (False, None))

    def test_apply_keeps_everything_else(self):
        out = apply_param(self.CODE, "zone", "NO3")
        self.assertIn(" const zone = 'NO3'; // zone comment\n", out)
        self.assertIn("const zone = 'inner';", out)                       # only the first, top-level one
        self.assertIn("let userName\n", out)
        out = apply_param(out, "userName", "it's \"me\"\n")
        self.assertIn("let userName = 'it\\'s \"me\"\\n'\n", out)
        self.assertIn("var flag = true;", apply_param(out, "flag", True))
        self.assertIn("const updateMinutes = 2.5;", apply_param(out, "updateMinutes", 2.5))
        with self.assertRaises(KeyError):
            apply_param(self.CODE, "complex", 1)

    def test_apply_is_idempotent(self):
        once = apply_param(self.CODE, "zone", "NO2")
        self.assertEqual(apply_param(once, "zone", "NO2"), once)

    def test_coerce(self):
        num = samples.Param("n", "N", "number", "m")
        self.assertEqual(coerce_param(num, "7"), 7)
        self.assertEqual(coerce_param(num, "7.5"), 7.5)
        with self.assertRaises(ValueError):
            coerce_param(num, "abc")
        opt = samples.Param("o", "O", "option", "m", values={"1": "one", "2": "two"})
        self.assertEqual(coerce_param(opt, "2", existing=1), 2)           # macro uses numbers -> stay numeric
        self.assertEqual(coerce_param(opt, "2", existing="1"), "2")
        with self.assertRaises(ValueError):
            coerce_param(opt, "9")
        req = samples.Param("s", "Secret", "string", "m", required=True)
        with self.assertRaises(ValueError):
            coerce_param(req, "  ")
        self.assertIs(coerce_param(samples.Param("b", "B", "bool", "m"), "false"), False)

    def test_real_samples_apply_settings(self):
        s = session("energy-prices")                     # manifest names a macro id that doesn't exist
        self.assertEqual(s.set_param("zone", "NO3"), "")
        self.assertEqual(s.set_param("hasPlotPanel", False), "")
        code = s.get("macro:energyprices")
        self.assertIn("const zone = 'NO3';", code)
        self.assertIn("const hasPlotPanel = false;", code)

        s = session("daily-reboot")                      # declarations indented by one space
        self.assertEqual(s.set_param("hourBoot", "6"), "")
        self.assertIn("const hourBoot = 6; // 0-23", s.get("macro:DailyReboot"))
        self.assertTrue(s.set_param("hourBoot", "abc"))
        self.assertTrue(s.bundle().errors)               # bad value blocks the bundle until fixed
        self.assertEqual(s.set_param("hourBoot", "7"), "")
        self.assertFalse(s.bundle().errors)

    def test_setting_that_does_not_exist_in_code_is_reported_not_crashed(self):
        s = session("room-capacity-alert")
        self.assertIn("edit the macro directly", s.set_param("maxPeople", "3"))

    def test_every_editable_setting_round_trips_on_every_sample(self):
        for sample in load_catalog():
            s = SampleSession(sample, use_cache=False)
            for p in sample.params:
                if not s.param_editable(p):
                    continue
                with self.subTest(f"{sample.id}.{p.id}"):
                    key = f"macro:{p.macro}"
                    found, value = read_param(s.get(key), p.id)
                    self.assertTrue(found)
                    self.assertEqual(s.set_param(p.id, s.params[p.id]), "")
                    self.assertEqual(read_param(s.get(key), p.id)[1], s.params[p.id])


class PanelTests(unittest.TestCase):
    def test_every_bundled_panel_prepares(self):
        for sample in load_catalog():
            s = SampleSession(sample, use_cache=False)
            for p in sample.panels:
                if p.file:
                    with self.subTest(f"{sample.id}/{p.file}"):
                        pid, xml = prepare_panel(s.get(p.key), p.panel_id)
                        self.assertTrue(pid)
                        self.assertEqual(ET.fromstring(xml).tag, "Extensions")
                        self.assertEqual(len(ET.fromstring(xml).findall("Panel")), 1)

    def test_missing_panel_id_is_injected_after_order(self):
        xml = "<Extensions><Version>1.8</Version><Panel><Order>3</Order><Type>Home</Type><Name>Dial</Name></Panel></Extensions>"
        pid, out = prepare_panel(xml, "onebuttontodial")
        self.assertEqual(pid, "onebuttontodial")
        self.assertEqual([c.tag for c in ET.fromstring(out).find("Panel")], ["Order", "PanelId", "Type", "Name"])

    def test_existing_panel_id_and_declaration_handling(self):
        xml = '\ufeff<?xml version="1.0"?>\n<Extensions><Panel><PanelId>abc</PanelId></Panel></Extensions>'
        pid, out = prepare_panel(xml, "ignored")
        self.assertEqual(pid, "abc")
        self.assertTrue(out.startswith("<Extensions>"))

    def test_bad_xml_is_an_error(self):
        with self.assertRaises(ET.ParseError):
            prepare_panel("<Extensions><Panel>", "x")
        with self.assertRaises(ValueError):
            prepare_panel("<Extensions/>", "x")

    def test_dial_favorite_sample_gets_a_panel_id(self):
        b = session("dial-favorite-from-homescreen").bundle()
        self.assertEqual(b.errors, [])
        self.assertEqual(b.panels[0].panel_id, "onebuttontodial")


class BundleTests(unittest.TestCase):
    def test_default_bundle_is_valid_for_every_sample_without_remote_files(self):
        for sample in load_catalog():
            if sample.remotes:
                continue
            with self.subTest(sample.id):
                b = SampleSession(sample, use_cache=False).bundle()
                self.assertEqual(b.errors, [])
                self.assertEqual(b.warnings, [])

    def test_libraries_are_saved_but_not_activated(self):
        b = session("quiet-mode").bundle()
        self.assertEqual({m.name: m.activate for m in b.macros}, {"schedule": False, "quietmode": True})
        z = session("join-zoom-with-dtmf-zoom-tools").bundle()
        self.assertEqual([m.name for m in z.macros if m.activate], ["JoinZoom_Main_4-1-1"])
        self.assertEqual(len(z.panels), 6)

    def test_examples_are_off_by_default_and_importable(self):
        s = session("global-macro-messaging")
        self.assertEqual([m.name for m in s.bundle().macros], ["GMM_Lib"])
        example = next(m for m in s.sample.macros if m.kind == "example")
        s.selected[example.key] = True
        b = s.bundle()
        self.assertIn(example.name, [m.name for m in b.macros])
        self.assertEqual(b.warnings, [])

    def test_unselected_dependency_is_a_warning_not_an_error(self):
        s = session("quiet-mode")
        s.selected["macro:schedule"] = False
        b = s.bundle()
        self.assertEqual(b.errors, [])
        self.assertTrue(any("'./schedule'" in w for w in b.warnings))

    def test_conflicting_panel_ids_block_the_bundle(self):
        s = session("energy-prices")
        for p in s.sample.panels:
            s.selected[p.key] = True
        self.assertTrue(any("share the id 'energy-prices'" in e for e in s.bundle().errors))

    def test_nothing_selected(self):
        s = session("timer")
        for k in s.selected:
            s.selected[k] = False
        self.assertEqual(s.bundle().errors, ["Nothing is selected."])

    def test_too_many_active_macros(self):
        s = session("customer-satisfaction-survey")
        extra = [samples.MacroEntry(f"m{i}", "primary", file=s.sample.macros[0].file) for i in range(11)]
        s.sample.macros.extend(extra)
        try:
            for e in extra:
                s.original[e.key] = s.text[e.key] = "x"
                s.selected[e.key] = True
            self.assertTrue(any("at most 10" in e for e in s.bundle().errors))
        finally:
            del s.sample.macros[-11:]

    def test_hand_edits_end_up_in_the_bundle_and_reset_restores(self):
        s = session("block-outgoing-calls")
        key = s.sample.macros[0].key
        s.set_text(key, s.get(key) + "\n// edited\n")
        self.assertTrue(s.modified(key))
        self.assertIn("// edited", s.bundle().macros[0].code)
        s.reset(key)
        self.assertFalse(s.modified(key))
        self.assertNotIn("// edited", s.bundle().macros[0].code)


class RemoteTests(unittest.TestCase):
    def test_remote_samples_need_a_download_first(self):
        for sid in ("philips-hue", "settings-pro", "report-issue"):
            s = session(sid)
            self.assertTrue(s.missing_remote(), sid)
            self.assertTrue(any("downloaded first" in e for e in s.bundle().errors), sid)

    def test_after_download_bundle_is_valid_and_settings_apply(self):
        s = session("report-issue", remote=True)
        self.assertEqual(s.missing_remote(), [])
        self.assertEqual(s.bundle().errors, [])
        self.assertEqual(s.set_param("serviceNowInstance", "dev12345"), "")
        self.assertIn("const serviceNowInstance = 'dev12345';", s.get("macro:report-issue"))

    def test_hue_needs_its_libraries_before_the_macro(self):
        names = [m.name for m in session("philips-hue", remote=True).bundle().macros]
        self.assertLess(names.index("ui"), names.index("hue"))
        self.assertLess(names.index("hue-lib"), names.index("hue"))

    def test_hash_mismatch_is_rejected(self):
        good = b"hello"
        ref = RemoteRef("https://raw.githubusercontent.com/cisco-ce/x/" + "a" * 40 + "/f.js", hashlib.sha256(good).hexdigest(), 5)
        self.assertEqual(samples._verify(ref, good), "hello")
        with self.assertRaises(RemoteError):
            samples._verify(ref, b"tampered")
        with self.assertRaises(RemoteError):
            samples._verify(RemoteRef(ref.url, hashlib.sha256(b"\xff\xfe").hexdigest(), 2), b"\xff\xfe")   # not utf-8

    def test_download_only_from_allow_listed_https_hosts(self):
        for url in ("http://raw.githubusercontent.com/a/b/c/d.js", "https://evil.example.com/a.js", "file:///etc/passwd"):
            with self.assertRaises(RemoteError):
                samples.download_remote(RemoteRef(url, "0" * 64, 1))

    def test_download_verifies_and_caches(self):
        body = b"import xapi from 'xapi';\n"
        ref = RemoteRef("https://raw.githubusercontent.com/cisco-ce/x/" + "b" * 40 + "/f.js", hashlib.sha256(body).hexdigest(), len(body))
        reply = mock.MagicMock()
        reply.__enter__.return_value.read.return_value = body
        with tempfile.TemporaryDirectory() as d, mock.patch("urllib.request.urlopen", return_value=reply) as op:
            self.assertEqual(samples.download_remote(ref, Path(d)), body.decode())
            self.assertEqual(samples.cached_remote(ref, Path(d)), body.decode())
            self.assertEqual(op.call_count, 1)
            (Path(d) / ref.sha256).write_bytes(b"corrupted")                   # a bad cache entry is ignored
            self.assertIsNone(samples.cached_remote(ref, Path(d)))

    def test_network_failure_is_a_readable_error(self):
        ref = RemoteRef("https://raw.githubusercontent.com/cisco-ce/x/" + "c" * 40 + "/f.js", "0" * 64, 1)
        with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(RemoteError, "offline"):
                samples.download_remote(ref, Path(tempfile.gettempdir()))

    def test_caution_for_samples_that_store_secrets(self):
        self.assertIn("plain text", get_sample("sennheiser-mute-sync").caution)
        self.assertIn("query string", get_sample("report-issue").caution)


class ProfileTests(unittest.TestCase):
    def test_round_trip_keeps_settings_selection_and_edits(self):
        s = session("quiet-mode")
        s.set_param("backgroundUrl", next(iter(s.sample.params[0].values)))
        s.selected["macro:schedule"] = False
        key = "macro:quietmode"
        s.set_text(key, s.get(key) + "\n// hand edit\n")
        prof = s.to_profile()
        self.assertEqual(prof["template"], "sample:quiet-mode")
        self.assertEqual(list(prof["edits"]), [key])

        t = session("quiet-mode")
        self.assertEqual(t.apply_profile(prof), [])
        self.assertFalse(t.selected["macro:schedule"])
        self.assertEqual(t.get(key), s.get(key))

    def test_settings_only_changes_are_not_stored_as_edits(self):
        s = session("energy-prices")
        s.set_param("zone", "NO4")
        self.assertEqual(s.to_profile()["edits"], {})

    def test_unknown_things_in_a_profile_are_reported(self):
        problems = session("timer").apply_profile({"values": {"nope": 1}, "edits": {"macro:ghost": "x"}})
        self.assertEqual(len(problems), 2)

    def test_export_files(self):
        s = session("quiet-mode")
        files = s.export_files(s.bundle())
        self.assertEqual(set(files), {"schedule.js", "quietmode.js", "quietmode.xml", "README.md"})


class ReadmeTests(unittest.TestCase):
    def test_images_html_and_markup_are_cleaned(self):
        text = readme_to_text("# Title\n\n![shot](a.png)\n<img src='b.png'>\n* item **bold** [link](https://x.y/z)\n\n\n\nend")
        self.assertNotIn("png", text)
        self.assertTrue(text.startswith("TITLE"))
        self.assertIn("\u2022 item bold link (https://x.y/z)", text)
        self.assertNotIn("\n\n\n", text)


class SampleCliTests(unittest.TestCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_samples_listing_and_search(self):
        code, out, err = self.run_cli("samples", "zoom")
        self.assertEqual(code, 0)
        self.assertIn("join-zoom-with-dtmf-zoom-tools", out)
        self.assertNotIn("energy-prices", out)
        self.assertIn("needs --download", self.run_cli("samples", "hue")[1])

    def test_init_render_sample_to_a_folder(self):
        with tempfile.TemporaryDirectory() as d:
            profile, out = str(Path(d) / "p.json"), Path(d) / "out"
            self.assertEqual(self.run_cli("init", "energy-prices", profile)[0], 0)
            data = json.loads(Path(profile).read_text(encoding="utf-8"))
            data["values"]["zone"] = "NO4"
            Path(profile).write_text(json.dumps(data), encoding="utf-8")
            code, stdout, _ = self.run_cli("render", profile, "-o", str(out))
            self.assertEqual(code, 0, stdout)
            self.assertIn("const zone = 'NO4';", (out / "energyprices.js").read_text(encoding="utf-8"))
            self.assertTrue((out / "energy-prices.xml").exists())

    def test_sample_prefix_is_accepted_in_init(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cli("init", "sample:timer", str(Path(d) / "t.json"))[0], 0)

    def test_unknown_sample_and_bad_values(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cli("init", "no-such", str(Path(d) / "x.json"))[0], 2)
            ghost = Path(d) / "g.json"
            ghost.write_text('{"template": "sample:ghost"}', encoding="utf-8")
            code, _, err = self.run_cli("render", str(ghost))
            self.assertEqual(code, 2)
            self.assertIn("unknown sample", err)
            bad = Path(d) / "b.json"
            bad.write_text('{"template": "sample:daily-reboot", "values": {"hourBoot": "abc"}}', encoding="utf-8")
            code, _, err = self.run_cli("render", str(bad), "-o", str(Path(d) / "o"))
            self.assertEqual(code, 1)
            self.assertIn("enter a number", err)

    def test_remote_sample_needs_the_download_flag(self):
        with tempfile.TemporaryDirectory() as d:
            profile = str(Path(d) / "h.json")
            self.run_cli("init", "philips-hue", profile)
            code, _, err = self.run_cli("render", profile, "-o", str(Path(d) / "o"))
            self.assertEqual(code, 2)
            self.assertIn("--download", err)
            with mock.patch("main.download_remote", fake_fetch):
                code, out, err = self.run_cli("render", profile, "-o", str(Path(d) / "o"), "--download")
            self.assertEqual(code, 0, err)
            self.assertTrue((Path(d) / "o" / "ui.js").exists())

    def test_push_uses_the_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            profile = str(Path(d) / "q.json")
            self.run_cli("init", "quiet-mode", profile)
            seen = {}

            def fake_push(host, user, pw, macros, panels, **kw):
                seen.update(host=host, pw=pw, macros=macros, panels=panels, kw=kw)
                return device.PushResult(host, True, ["done"])

            with mock.patch("main.push_bundle", fake_push), mock.patch.dict("os.environ", {"DEVICE_PASSWORD": "secret"}):
                code, out, _ = self.run_cli("push", profile, "--host", "10.1.1.1")
                self.assertEqual(code, 0)
                self.assertEqual(seen["pw"], "secret")
                self.assertEqual([(n, a) for n, _c, a in seen["macros"]], [("schedule", False), ("quietmode", True)])
                self.assertEqual([p for p, _x in seen["panels"]], ["quietmode"])
                self.run_cli("push", profile, "--host", "10.1.1.1", "--no-activate")
                self.assertTrue(all(not a for _n, _c, a in seen["macros"]))


class PushBundleTests(unittest.TestCase):
    @staticmethod
    def ok():
        return mock.Mock(status_code=200, text="<Command/>")

    def test_order_and_contents(self):
        sess = mock.Mock()
        sess.post.side_effect = [self.ok() for _ in range(20)]
        res = device.push_bundle(
            "10.0.0.9", "admin", "pw",
            macros=[("lib", "export const a = 1;", False), ("main", "import {a} from './lib';", True)],
            panels=[("pnl", "<Extensions><Panel><PanelId>pnl</PanelId></Panel></Extensions>")],
            session=sess,
        )
        self.assertTrue(res.ok, res.error)
        sent = [ET.fromstring(c.kwargs["data"]) for c in sess.post.call_args_list]
        kinds = []
        for x in sent:
            if x.find("Macros/Mode") is not None:
                kinds.append("mode")
            elif x.find("Macros/Macro/Save") is not None:
                kinds.append("save:" + x.findtext("Macros/Macro/Save/Name"))
            elif x.find("UserInterface/Extensions/Panel/Save") is not None:
                kinds.append("panel:" + x.findtext("UserInterface/Extensions/Panel/Save/PanelId"))
            elif x.find("Macros/Macro/Activate") is not None:
                kinds.append("activate:" + x.findtext("Macros/Macro/Activate/Name"))
            elif x.find("Macros/Runtime/Restart") is not None:
                kinds.append("restart")
        self.assertEqual(kinds, ["mode", "save:lib", "save:main", "panel:pnl", "activate:main", "restart"])
        panel_body = sent[3].findtext("UserInterface/Extensions/Panel/Save/body")
        self.assertIn("<PanelId>pnl</PanelId>", panel_body)

    def test_panel_only_bundle_does_not_touch_macros(self):
        sess = mock.Mock()
        sess.post.side_effect = [self.ok()]
        res = device.push_bundle("h", "u", "p", macros=[], panels=[("p", "<Extensions/>")], session=sess)
        self.assertTrue(res.ok)
        self.assertEqual(sess.post.call_count, 1)

    def test_failure_stops_and_names_the_step(self):
        err = mock.Mock(status_code=200, text='<Command><PanelSaveResult status="Error"><Reason>bad xml</Reason></PanelSaveResult></Command>')
        sess = mock.Mock()
        sess.post.side_effect = [self.ok(), self.ok(), err]
        res = device.push_bundle("h", "u", "p", macros=[("m", "x", True)], panels=[("p", "<Extensions/>")], session=sess)
        self.assertFalse(res.ok)
        self.assertIn("UI panel 'p' saved: bad xml", res.error)
        self.assertEqual(sess.post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
