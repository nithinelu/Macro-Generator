# Macro Generator

*by Nithin Eluvathingal - MIT Licensed*

Builds Cisco RoomOS macros from templates: pick a template, fill in the form, check the live
preview, then save the `.js`, copy it, or push it straight to one or more devices.

    py main.py                 # GUI
    py main.py list            # templates
    py main.py init room_control mysite.json     # starter profile
    py main.py render mysite.json -o Room_Control.js
    py main.py push mysite.json --host 10.0.0.21 --host 10.0.0.22 --user admin

Needs Python 3.10+ and `requests` (`pip install -r requirements.txt`). The GUI uses Tkinter.

## Templates

| id | What it does |
|---|---|
| `room_release` | Declines a booking when nobody turns up (grace period -> presence check -> on-screen prompt -> decline) |
| `room_control` | Touch panel: light switches, blind Up/Stop/Down, temperature sliders -> REST calls to the room controller |
| `visitor_kiosk` | Sets a Desk / Board Pro up as a reception kiosk (kiosk URL, standby, wake-on-motion, `sip:` links) |
| `quick_dial` | Panel of one-tap speed-dial buttons |
| `report_issue` | "Report an issue" button -> text box -> JSON POST to a webhook (Slack / Teams / ServiceNow...) |

Each template lists its own "Before you deploy" requirements in the GUI.

## Cisco sample library (62 samples)

The **Cisco samples** tab bundles every macro from
[CiscoDevNet/roomdevices-macros-samples](https://github.com/CiscoDevNet/roomdevices-macros-samples)
(MIT licence - `macro_generator/samples_data/LICENSE`, commit in `samples_data/SOURCES.md`).
Pick one and you get:

* **Settings** - the install-time options from the sample's `manifest.json` as a real form (a setting
  is a top-level `const/let/var` in the macro; the form rewrites its value, exactly like Cisco's installer)
* **Files** - the macros and UI panels, each with a tick box. Libraries are saved but never activated,
  examples and alternative panels are off by default, and conflicts (two panels with one id, more than 10
  active macros, a missing `./library` import) are reported before you deploy
* **Editable code** - every file can be edited in the Preview tab ("Reset file" restores upstream)
* **README** tab, "Open on GitHub", **Export files...** (for manual upload) and **Deploy to devices**
  (macros first, then UI panels, then activation, one runtime restart)

Command line: `main.py samples [words]`, `main.py init <sample-id> p.json`, `main.py render p.json -o folder`,
`main.py push p.json --host IP`.

**Not bundled:** Philips Hue and Settings Pro need Cisco's `ui` / `ui-builder` libraries and the Report Issue
sample is entirely hosted in other Cisco repos (`cisco-ce/guido`, `cisco-ce/roomos-samples`). Those repos
declare no licence, so they are not redistributed. The tool downloads them on demand (the **Download**
button, or `--download` on the CLI) from a URL pinned to the reviewed commit, over HTTPS from
`raw.githubusercontent.com` only, size-capped and checked against a SHA-256; they are cached under
`%LOCALAPPDATA%\Macro-Generator\cache`.

These are third-party samples: read them before putting them on a customer device. The tool flags samples
that store passwords / tokens in the macro source, and the Report Issue sample sends credentials in a URL.

Images, zip packages and PDFs from the upstream repo are not copied.

**Refreshing the library:**

    git clone --depth 1 https://github.com/CiscoDevNet/roomdevices-macros-samples.git <dir>
    py tools/import_samples.py <dir>

The importer only reads text, honours each `manifest.json`, and rebuilds `samples_data/`. To move the pinned
commit of a remote dependency, review the new version and update `REMOTE_PINS` in `tools/import_samples.py`.

## Profiles

"Save profile" stores the template, macro name and every field as JSON, so a customer's setup can be
reloaded and regenerated later (or batch-rendered with `main.py render`).

## Pushing to devices

Uses the device's local HTTP xAPI (`POST https://<ip>/putxml`) with a local user that has the
**Admin** role: enables Macros mode, saves the macro (overwriting one with the same name),
activates it and restarts the macro runtime. Devices ship with self-signed certificates, so TLS
verification is off unless you tick the box. The password is never written to disk.
Hosts and username are remembered in `settings.json`.

## Adding a template

Add a `Template(...)` in `macro_generator/templates.py` and append it to `TEMPLATES`. The JS body
uses `/*__CONFIG__*/` where the generated `const config = {...};` goes; fields declared on the
template become the form. Check every xAPI path against https://roomos.cisco.com/xapi first, then
add scenarios to `tests/js_harness.html.tpl`.

## Tests

    py -I -m unittest discover -s tests

* `test_generator.py` - rendering, validation, profiles, putxml request building
* `test_macros_js.py` - runs each generated macro in headless Chrome/Edge against a mock `xapi`
  with a virtual clock (booking starts, button taps, HTTP replies...) and checks the calls it makes
  (skipped if no Chromium browser is installed)
* `test_gui.py`, `test_gui_samples.py` - drive the Tk GUI without a mainloop (both modes)
* `test_samples.py` - sample catalog integrity, settings rewriting, panel preparation, bundle rules,
  remote-file handling, profiles, CLI, and the multi-macro push order
* `test_samples_js.py` - parses every bundled sample macro with Chrome's module parser (nothing is run)
  and checks that setting values with quotes, newlines, unicode etc. are escaped correctly

These prove the macros do what the templates intend against the documented API. They do not replace
a test on a real device.

## License

Released under the **MIT License** - © 2026 Nithin Eluvathingal. See the [LICENSE](LICENSE) file for the
full text. The author and license are also shown in the app (header, footer and Help menu) and by `main.py --version`.

The bundled Cisco sample macros keep their own MIT licence from Cisco (`macro_generator/samples_data/LICENSE`).
