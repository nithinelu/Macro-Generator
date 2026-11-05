"""Drives the Cisco-samples side of the GUI without a mainloop. Skipped when Tk can't open a display."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_gui import tk_available


def pump(app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


@unittest.skipUnless(tk_available(), "no display for Tk")
class SampleGui(unittest.TestCase):
    def setUp(self):
        from macro_generator import gui
        self.gui = gui
        settings = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        settings.close()
        patcher = mock.patch.object(gui, "SETTINGS_FILE", Path(settings.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = gui.App()
        self.addCleanup(self.app.destroy)
        pump(self.app, 0.1)

    def test_starts_in_template_mode_with_sample_widgets_hidden(self):
        self.assertEqual(self.app.mode, "template")
        self.assertFalse(self.app.file_row.winfo_ismapped())
        self.assertEqual(self.app.tabs.tab(self.app.readme_tab, "state"), "hidden")
        self.assertEqual(self.app.save_btn.cget("text"), "Save .js...")
        self.assertGreater(len(self.app.shown_samples), 60)

    def test_clicking_the_samples_tab_switches_mode_and_back(self):
        self.app.source_tabs.select(1)
        pump(self.app)
        self.assertEqual(self.app.mode, "sample")
        self.assertIsNotNone(self.app.session)
        self.assertEqual(self.app.save_btn.cget("text"), "Export files...")
        self.assertNotEqual(self.app.tabs.tab(self.app.readme_tab, "state"), "hidden")
        self.app.source_tabs.select(0)
        pump(self.app)
        self.assertEqual(self.app.mode, "template")
        self.assertEqual(self.app.save_btn.cget("text"), "Save .js...")
        self.assertTrue(self.app.name_frame.winfo_ismapped())

    def test_every_sample_opens_without_error(self):
        for s in list(self.app.shown_samples):
            with self.subTest(s.id):
                self.app.select_sample(s.id)
                self.app.update()
                self.assertEqual(self.app.session.sample.id, s.id)
                self.assertTrue(self.app.status.cget("text"))
                self.assertTrue(self.app.readme.get("1.0", "end").strip())

    def test_search_and_tag_filter(self):
        self.app.search_var.set("zoom")
        pump(self.app, 0.1)
        self.assertTrue(self.app.shown_samples)
        self.assertTrue(all("zoom" in (s.title + s.description + " ".join(s.tags)).lower() for s in self.app.shown_samples))
        self.app.search_var.set("")
        self.app.tag_var.set("calling")
        self.app._refilter()
        self.assertTrue(all("calling" in s.tags for s in self.app.shown_samples))
        self.assertIn("of", self.app.count_label.cget("text"))

    def test_changing_a_setting_updates_the_macro_and_the_editor(self):
        self.app.select_sample("energy-prices")
        self.app._load_editor("macro:energyprices")
        zone_var = self.app.param_vars["zone"]
        zone_var.set("Zone 3 - Central Norway")
        pump(self.app, 0.1)
        self.assertIn("const zone = 'NO3';", self.app.session.get("macro:energyprices"))
        self.assertIn("const zone = 'NO3';", self.app.preview.get("1.0", "end"))
        self.app.param_vars["hasPlotPanel"].set(False)
        self.assertIn("const hasPlotPanel = false;", self.app.session.get("macro:energyprices"))

    def test_invalid_setting_is_flagged_and_blocks_export_until_fixed(self):
        self.app.select_sample("daily-reboot")
        self.app.param_vars["hourBoot"].set("abc")
        self.app.update()
        self.assertIn("enter a number", self.app.param_notes["hourBoot"].cget("text"))
        self.assertIn("Fix before", self.app.status.cget("text"))
        with mock.patch.object(self.gui.messagebox, "showwarning") as warn:
            self.assertFalse(self.app._require_ready())
            warn.assert_called_once()
        self.app.param_vars["hourBoot"].set("5")
        self.assertTrue(self.app._require_ready())

    def test_secret_settings_are_masked(self):
        self.app.select_sample("pin-code-lock")
        entries = [w for w in self.app.form.winfo_children() if w.winfo_class() == "TEntry"]
        self.assertTrue(entries and all(e.cget("show") for e in entries))

    def test_hand_editing_marks_the_file_and_flows_into_the_job(self):
        self.app.select_sample("block-outgoing-calls")
        key = self.app.current_key
        self.app.preview.insert("end", "\n// my change\n")
        self.app._on_edit()
        self.assertEqual(self.app.edited_label.cget("text"), "edited")
        macros, panels = self.app.build_job()
        self.assertIn("// my change", macros[0][1])
        with mock.patch.object(self.gui.messagebox, "askyesno", return_value=True):
            self.app.reset_file()
        self.assertEqual(self.app.edited_label.cget("text"), "")
        self.assertNotIn("my change", self.app.build_job()[0][0][1])

    def test_file_checkboxes_drive_the_bundle(self):
        self.app.select_sample("quiet-mode")
        self.assertEqual({n for n, _c, _a in self.app.build_job()[0]}, {"schedule", "quietmode"})
        self.app.file_vars["macro:schedule"].set(False)
        self.app.update()
        self.assertEqual({n for n, _c, _a in self.app.build_job()[0]}, {"quietmode"})
        self.assertIn("'./schedule'", self.app.status.cget("text"))             # dependency warning surfaced

    def test_job_activation_follows_checkbox_and_never_activates_libraries(self):
        self.app.select_sample("quiet-mode")
        macros, panels = self.app.build_job()
        self.assertEqual({n: a for n, _c, a in macros}, {"schedule": False, "quietmode": True})
        self.assertEqual([p for p, _x in panels], ["quietmode"])
        self.app.activate_var.set(False)
        self.assertTrue(all(not a for _n, _c, a in self.app.build_job()[0]))

    def test_remote_sample_is_blocked_until_downloaded_then_works(self):
        self.app.select_sample("report-issue")
        self.assertIn("Fix before", self.app.status.cget("text"))
        self.assertTrue(hasattr(self.app, "download_btn"))

        def fake(ref):
            return ("<Extensions><Panel><PanelId>report_issue</PanelId></Panel></Extensions>" if ref.url.endswith(".xml")
                    else "import xapi from 'xapi';\nconst serviceNowInstance = '';\n")

        with mock.patch.object(self.gui, "download_remote", fake):
            self.app.download_remote_files()
            pump(self.app, 0.5)
        self.assertEqual(self.app.session.missing_remote(), [])
        self.assertIn("Ready", self.app.status.cget("text"))
        self.assertEqual(self.app.param_vars["serviceNowInstance"].get(), "")
        self.app.param_vars["serviceNowInstance"].set("dev1")
        self.assertIn("const serviceNowInstance = 'dev1';", self.app.session.get("macro:report-issue"))

    def test_download_failure_is_reported_and_retryable(self):
        from macro_generator.samples import RemoteError
        self.app.select_sample("settings-pro")
        with mock.patch.object(self.gui, "download_remote", side_effect=RemoteError("offline")), \
                mock.patch.object(self.gui.messagebox, "showerror") as err:
            self.app.download_remote_files()
            pump(self.app, 0.5)
            err.assert_called_once()
        self.assertEqual(str(self.app.download_btn.cget("text")), "Download")
        self.assertTrue(self.app.session.missing_remote())

    def test_profile_load_keeps_settings_despite_the_queued_tab_event(self):
        self.app.select_sample("energy-prices")
        self.app.param_vars["zone"].set("Zone 5 - West Norway")
        profile = self.app.session.to_profile()
        self.app.select_template(0)                                         # back to template mode
        pump(self.app, 0.1)
        self.assertEqual(self.app.mode, "template")

        self.assertEqual(self.app.select_sample("energy-prices", profile), [])
        pump(self.app, 0.4)                                                  # let <<NotebookTabChanged>> arrive
        self.assertEqual(self.app.mode, "sample")
        self.assertEqual(self.app.session.params["zone"], "NO5")
        self.assertIn("const zone = 'NO5';", self.app.session.get("macro:energyprices"))

    def test_profile_files_round_trip_through_the_dialogs(self):
        self.app.select_sample("quiet-mode")
        self.app.file_vars["macro:schedule"].set(False)
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "p.json")
            with mock.patch.object(self.gui.filedialog, "asksaveasfilename", return_value=path):
                self.app.save_profile()
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(data["template"], "sample:quiet-mode")
            self.app.select_template(1)
            with mock.patch.object(self.gui.filedialog, "askopenfilename", return_value=path):
                self.app.load_profile()
            pump(self.app, 0.3)
        self.assertEqual(self.app.mode, "sample")
        self.assertFalse(self.app.session.selected["macro:schedule"])

    def test_generated_template_profiles_still_load(self):
        from macro_generator.profile import save_profile
        from macro_generator.templates import TEMPLATES
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "t.json")
            save_profile(path, TEMPLATES[3], "My_Dial", TEMPLATES[3].defaults())
            self.app.select_sample("timer")
            with mock.patch.object(self.gui.filedialog, "askopenfilename", return_value=path):
                self.app.load_profile()
            pump(self.app, 0.3)
        self.assertEqual((self.app.mode, self.app.template.id, self.app.macro_name.get()), ("template", "quick_dial", "My_Dial"))

    def test_bad_profile_shows_an_error_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            path.write_text('{"template": "sample:no-such-sample"}', encoding="utf-8")
            with mock.patch.object(self.gui.filedialog, "askopenfilename", return_value=str(path)), \
                    mock.patch.object(self.gui.messagebox, "showerror") as err:
                self.app.load_profile()
                err.assert_called_once()

    def test_export_writes_every_selected_file(self):
        self.app.select_sample("quiet-mode")
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(self.gui.filedialog, "askdirectory", return_value=d):
                self.app.export_files()
            out = Path(d) / "quiet-mode"
            self.assertEqual({p.name for p in out.iterdir()}, {"schedule.js", "quietmode.js", "quietmode.xml", "README.md"})
            self.assertIn("quiet", (out / "quietmode.js").read_text(encoding="utf-8").lower())

    def test_push_sends_the_whole_bundle(self):
        self.app.select_sample("quiet-mode")
        self.app.hosts_text.insert("1.0", "10.0.0.1\n10.0.0.2")
        self.app.pass_var.set("pw")
        from macro_generator.device import PushResult
        calls = []

        def fake_push(host, user, pw, macros, panels, **kw):
            calls.append((host, [m[0] for m in macros], [p[0] for p in panels], kw))
            return PushResult(host, True, ["a", "b"])

        with mock.patch.object(self.gui, "push_bundle", fake_push), \
                mock.patch.object(self.gui.messagebox, "askyesno", return_value=True):
            self.app.push()
            pump(self.app, 0.6)
        self.assertEqual([c[0] for c in calls], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(calls[0][1], ["schedule", "quietmode"])
        self.assertEqual(calls[0][2], ["quietmode"])
        self.assertIn("OK", self.app.log.get("1.0", "end"))
        self.assertEqual(str(self.app.push_btn.cget("state")), "normal")


if __name__ == "__main__":
    unittest.main()
