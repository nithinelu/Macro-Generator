"""Builds the self-contained "virtual device" page: a simulated RoomOS touch screen that runs macros.

The page is plain HTML. Macros run in a sandboxed iframe whose content-security policy blocks every
network request, against a fake `xapi` and a virtual clock (see simulator/runtime.js).
"""
from __future__ import annotations

import json
import re
import tempfile
import webbrowser
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "simulator"

# What a typical Room Kit Pro reports. Macros read these with xapi.Status...get() / xapi.Config...get().
DEFAULT_STATE: dict[str, object] = {
    "Status.SystemUnit.ProductId": "Cisco Webex Room Kit Pro",
    "Status.SystemUnit.ProductType": "Cisco Codec",
    "Status.SystemUnit.Software.Version": "RoomOS 11.27.1.4",
    "Status.SystemUnit.Software.DisplayName": "RoomOS 11.27.1.4 a1b2c3d4e5f",
    "Status.SystemUnit.Software.ReleaseDate": "2026-01-15",
    "Status.SystemUnit.Hardware.Module.SerialNumber": "FOC00000001",
    "Status.SystemUnit.State.NumberOfActiveCalls": 0,
    "Status.SystemUnit.State.NumberOfInProgressCalls": 0,
    "Status.SystemUnit.Uptime": 86400,
    "Status.UserInterface.ContactInfo.Name": "Boardroom 1",
    "Status.Network.1.IPv4.Address": "10.0.0.50",
    "Status.Network.1.CDP.DeviceId": "",
    "Status.RoomAnalytics.PeoplePresence": "No",
    "Status.RoomAnalytics.PeopleCount.Current": 0,
    "Status.RoomAnalytics.AmbientNoise.Level.A": 30,
    "Status.RoomAnalytics.AmbientTemperature": 22,
    "Status.RoomAnalytics.RelativeHumidity": 45,
    "Status.Audio.Volume": 50,
    "Status.Audio.VolumeMute": "Off",
    "Status.Audio.Microphones.Mute": "Off",
    "Status.Standby.State": "Off",
    "Status.Video.Input.MainVideoMute": "Off",
    "Status.Video.Selfview.Mode": "Off",
    "Status.Conference.Presentation.Mode": "Off",
    "Status.Bookings.Current.Id": "",
    "Status.Peripherals.ConnectedDevice": [],
    "Config.Audio.DefaultVolume": 50,
    "Config.UserInterface.Language": "English",
    "Config.UserInterface.Features.HideAll": "False",
    "Config.Standby.Control": "On",
    "Config.Standby.Delay": 10,
    "Config.Time.Zone": "UTC",
}

# Events worth firing by hand. Paths are the xAPI event paths; payloads are examples to edit.
PRESETS: list[dict] = [
    {"label": "Meeting starts (Bookings.Start)", "path": "Event.Bookings.Start", "payload": {"Id": "booking-1", "MeetingId": "meeting-1"}},
    {"label": "Meeting ends (Bookings.End)", "path": "Event.Bookings.End", "payload": {"Id": "booking-1", "MeetingId": "meeting-1"}},
    {"label": "Call connected (CallSuccessful)", "path": "Event.CallSuccessful", "payload": {"CallId": 1, "CallType": "Video", "Protocol": "sip", "RemoteURI": "alex@example.com"}},
    {"label": "Call ended (CallDisconnect)", "path": "Event.CallDisconnect", "payload": {"CallId": 1, "Reason": "RemoteDisconnect"}},
    {"label": "Incoming call", "path": "Event.IncomingCallIndication", "payload": {"CallId": 2, "DisplayName": "Alex", "RemoteURI": "alex@example.com"}},
    {"label": "Panel tapped", "path": "Event.UserInterface.Extensions.Panel.Clicked", "payload": {"PanelId": "panel_id"}},
    {"label": "Widget used", "path": "Event.UserInterface.Extensions.Widget.Action", "payload": {"WidgetId": "widget_id", "Type": "clicked", "Value": ""}},
    {"label": "Prompt answered", "path": "Event.UserInterface.Message.Prompt.Response", "payload": {"FeedbackId": "feedback", "OptionId": 1}},
    {"label": "Text typed (TextInput)", "path": "Event.UserInterface.Message.TextInput.Response", "payload": {"FeedbackId": "feedback", "Text": "hello"}},
]

# What the fake network answers: first matching rule wins.
DEFAULT_HTTP_RULES: list[dict] = [
    {"match": "*", "status": 200, "body": "{}"},
]


def _embed(data) -> str:
    """JSON that is safe inside an HTML <script> block (ASCII-only, no literal '</')."""
    return json.dumps(data).replace("</", "<\\/")


def build_page(
    title: str,
    macros: list[tuple[str, str, bool]],
    panels: list[tuple[str, str]] | None = None,
    state: dict[str, object] | None = None,
) -> str:
    """macros: (name, code, activate); panels: (panel_id, xml) that are already installed on the device."""
    bundle = {
        "title": title,
        "macros": [{"name": n, "code": c, "activate": a} for n, c, a in macros],
        "panels": [{"panel_id": pid, "xml": xml} for pid, xml in (panels or [])],
        "state": {**DEFAULT_STATE, **(state or {})},
        "presets": PRESETS,
        "httpRules": DEFAULT_HTTP_RULES,
    }
    page = (ASSETS / "simulator.html").read_text(encoding="utf-8")
    runtime = (ASSETS / "runtime.js").read_text(encoding="utf-8")
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = {"__TITLE__": safe_title, "/*__BUNDLE__*/": _embed(bundle), "/*__RUNTIME__*/": _embed(runtime)}
    # one pass, so text inside a macro can never be mistaken for a placeholder
    return re.sub(r"__TITLE__|/\*__BUNDLE__\*/|/\*__RUNTIME__\*/", lambda m: parts[m.group(0)], page)


def write_page(path: str | Path, title: str, macros, panels=None, state=None) -> Path:
    path = Path(path)
    path.write_text(build_page(title, macros, panels, state), encoding="utf-8", newline="\n")
    return path


def open_in_browser(title: str, macros, panels=None, state=None) -> Path:
    """Write the page to a temp file and open it in the default browser."""
    directory = Path(tempfile.mkdtemp(prefix="macro-generator-sim-"))
    path = write_page(directory / "virtual-device.html", title, macros, panels, state)
    webbrowser.open(path.as_uri())
    return path
