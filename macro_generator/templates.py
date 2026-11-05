"""The built-in macro templates. Every xAPI path/parameter used here was checked against roomos.cisco.com/xapi."""
from __future__ import annotations

from .core import CONFIG_MARKER, Column, Field, Template

PANEL_ICONS = ["Lightbulb", "Sliders", "Home", "Power", "Info", "Tv", "Hvac", "Handset", "Help"]

_LOG_AND_CONFIG_HELPERS = """
function log(...args) {
  if (config.enableLogging) console.log('[__TAG__]', ...args);
}

async function setConfig(path, value) {
  try {
    await path.reduce((node, key) => node[key], xapi.Config).set(value);
    log(`${path.join(' ')} = ${value}`);
  } catch (e) {
    log(`Could not set ${path.join(' ')}: ${e.message}`);
  }
}
"""

_XML_ESCAPE_HELPER = """
function esc(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
"""


def _helpers(tag: str, xml: bool = False) -> str:
    return _LOG_AND_CONFIG_HELPERS.replace("__TAG__", tag) + (_XML_ESCAPE_HELPER if xml else "")


# --------------------------------------------------------------------------- Room release

_ROOM_RELEASE = (
    "import xapi from 'xapi';\n\n"
    + CONFIG_MARKER
    + "\n"
    + _helpers("RoomRelease")
    + """
const PROMPT_ID = 'room_release_prompt';
const graceTimers = new Map();   // booking id -> grace-period timer
let activePrompt = null;         // { key, meetingId, timer }

async function isRoomOccupied() {
  const calls = await xapi.Status.SystemUnit.State.NumberOfActiveCalls.get().catch(() => 0);
  if (Number(calls) > 0) return true;

  if (config.usePeopleCount) {
    const count = await xapi.Status.RoomAnalytics.PeopleCount.Current.get().catch(() => -1);
    if (Number(count) >= config.minPeopleCount) return true;
  }
  const presence = await xapi.Status.RoomAnalytics.PeoplePresence.get().catch(() => 'Unknown');
  return presence === 'Yes';
}

async function resolveMeetingId(id, meetingId) {
  if (meetingId) return meetingId;
  try {
    const res = await xapi.Command.Bookings.Get({ Id: id });
    const booking = res.Booking || res;
    return booking.MeetingId || id;
  } catch (e) {
    log(`Could not look up MeetingId for booking ${id}: ${e.message}`);
    return id;
  }
}

function clearPrompt() {
  xapi.Command.UserInterface.Message.Prompt.Clear({ FeedbackId: PROMPT_ID }).catch(() => {});
  if (activePrompt) clearTimeout(activePrompt.timer);
  activePrompt = null;
}

async function releaseBooking(meetingId) {
  const params = { MeetingId: meetingId, Type: 'Decline' };
  if (config.declineComment) params.Comment = config.declineComment;
  try {
    await xapi.Command.Bookings.Respond(params);
    log(`Released meeting ${meetingId} - nobody checked in.`);
    xapi.Command.UserInterface.Message.Alert.Display({
      Title: 'Room released',
      Text: 'No one checked in, so this booking was released.',
      Duration: 10,
    }).catch(() => {});
  } catch (e) {
    log(`Could not release meeting ${meetingId}: ${e.message}`);
  }
}

async function showCheckInPrompt(key, meetingId) {
  await xapi.Command.UserInterface.Message.Prompt.Display({
    Title: 'Is this room in use?',
    Text: `Tap below within ${config.promptDurationSeconds} seconds or this booking will be released.`,
    FeedbackId: PROMPT_ID,
    'Option.1': 'Yes, we are using this room',
  });
  const timer = setTimeout(async () => {
    clearPrompt();
    await releaseBooking(meetingId);
  }, config.promptDurationSeconds * 1000);
  activePrompt = { key, meetingId, timer };
}

function scheduleCheck(key, meetingId) {
  cancel(key);
  const timer = setTimeout(async () => {
    graceTimers.delete(key);
    if (await isRoomOccupied()) {
      log(`Booking ${key}: room is occupied, nothing to do.`);
      return;
    }
    log(`Booking ${key}: room looks empty, asking for check-in.`);
    showCheckInPrompt(key, meetingId).catch((e) => log(`Prompt failed: ${e.message}`));
  }, config.releaseDelaySeconds * 1000);
  graceTimers.set(key, timer);
}

function cancel(key) {
  const timer = graceTimers.get(key);
  if (timer) {
    clearTimeout(timer);
    graceTimers.delete(key);
  }
  if (activePrompt && activePrompt.key === key) clearPrompt();
}

xapi.Event.Bookings.Start.on(async (event) => {
  const key = event.Id || event.MeetingId;
  log(`Booking started: ${key}`);
  scheduleCheck(key, await resolveMeetingId(event.Id, event.MeetingId));
});

xapi.Event.Bookings.End.on((event) => {
  cancel(event.Id || event.MeetingId);
});

xapi.Event.UserInterface.Message.Prompt.Response.on((event) => {
  if (event.FeedbackId !== PROMPT_ID || !activePrompt) return;
  log(`Booking ${activePrompt.key}: check-in confirmed.`);
  clearPrompt();
});

async function start() {
  await setConfig(['RoomAnalytics', 'PeoplePresenceDetector'], 'On');
  if (config.usePeopleCount) await setConfig(['RoomAnalytics', 'PeopleCountOutOfCall'], 'On');
  if (config.allowDeclineAllMeetings) await setConfig(['Bookings', 'DeclinableBookingType'], 'All');

  const currentId = await xapi.Status.Bookings.Current.Id.get().catch(() => '');
  if (currentId) {
    log(`Meeting already in progress at start-up: ${currentId}`);
    scheduleCheck(currentId, await resolveMeetingId(currentId, ''));
  }
  log('Room Release macro running.');
}

start();
"""
)

room_release = Template(
    id="room_release",
    name="Room Release (ghost bookings)",
    description=(
        "Declines a booking when nobody turns up. After the grace period it checks calls + presence "
        "sensors, asks 'Is this room in use?' on screen, and declines the meeting if nobody taps."
    ),
    default_macro_name="Room_Release",
    prerequisites=[
        "Device must be registered to Webex (or on-prem linked to Webex Edge for Devices) with a calendar integration - Bookings.Respond does not work on ad-hoc/unsynced bookings.",
        "Macro needs an Admin role to change RoomAnalytics / Bookings settings (the Macro runtime normally has it).",
        "Bookings DeclinableBookingType defaults to AdhocOnly; the macro switches it to All when the option below is ticked.",
    ],
    fields=[
        Field("releaseDelaySeconds", "Grace period (seconds)", "int", 300, "Time after the meeting starts before the room is checked.", min=30, max=3600),
        Field("promptDurationSeconds", "Prompt duration (seconds)", "int", 60, "How long the 'Is this room in use?' prompt stays up.", min=10, max=600),
        Field("usePeopleCount", "Also use camera people count", "bool", True, "In addition to the presence sensor (ultrasound/head detection)."),
        Field("minPeopleCount", "People needed to count as occupied", "int", 1, min=1, max=50),
        Field("allowDeclineAllMeetings", "Allow declining ALL meeting types", "bool", True, "Sets Bookings DeclinableBookingType to All (default is AdhocOnly)."),
        Field("declineComment", "Decline comment (optional)", "str", "Released automatically - no check-in.", "Sent with the decline response."),
        Field("enableLogging", "Log to macro console", "bool", True),
    ],
    body=_ROOM_RELEASE,
)


# --------------------------------------------------------------------------- Room control panel

_ROOM_CONTROL = (
    "import xapi from 'xapi';\n\n"
    + CONFIG_MARKER
    + "\n"
    + _helpers("RoomControl", xml=True)
    + """
const PANEL_ID = 'panel_room_control';
let suppressUntil = 0;   // programmatic SetValue also raises widget events - ignore them

async function callHttp(method, url, bodyObj) {
  if (!url) return null;
  const params = { Url: url, ResultBody: 'PlainText' };
  const headers = [...config.headers];
  let payload = '';
  if (method === 'Post') {
    payload = bodyObj !== undefined ? JSON.stringify(bodyObj) : config.postBody;
    if (payload) headers.push('Content-Type: application/json');
  }
  if (headers.length) params.Header = headers;
  if (config.allowInsecureHttps) params.AllowInsecureHTTPS = 'True';

  try {
    const result = method === 'Get'
      ? await xapi.Command.HttpClient.Get(params)
      : await xapi.Command.HttpClient.Post(params, payload);
    const status = Number(result.StatusCode);
    log(`${method} ${url} -> ${status}`);
    if (status >= 200 && status < 300) return result;
    throw new Error(`HTTP ${status}`);
  } catch (e) {
    log(`${method} ${url} failed: ${e.message}`);
    xapi.Command.UserInterface.Message.Alert.Display({
      Title: 'Room control',
      Text: 'The room controller did not respond.',
      Duration: 5,
    }).catch(() => {});
    return null;
  }
}

// Sliders are fixed at 0-255 by RoomOS, so map them onto the zone's temperature range.
const toTemp = (zone, raw) => Math.round(zone.minTemp + (Number(raw) / 255) * (zone.maxTemp - zone.minTemp));
const toRaw = (zone, temp) => Math.round(((temp - zone.minTemp) / (zone.maxTemp - zone.minTemp)) * 255);

function buildPanelXml() {
  const rows = [];

  config.lights.forEach((light, i) => rows.push(`
      <Row>
        <Name>${esc(light.label)}</Name>
        <Widget><WidgetId>label_light_${i}</WidgetId><Name>${esc(light.label)}</Name><Type>Text</Type><Options>size=3;fontSize=normal;align=left</Options></Widget>
        <Widget><WidgetId>light_${i}</WidgetId><Type>ToggleButton</Type><Options>size=1</Options></Widget>
      </Row>`));

  config.blinds.forEach((blind, i) => rows.push(`
      <Row>
        <Name>${esc(blind.label)}</Name>
        <Widget><WidgetId>label_blind_${i}</WidgetId><Name>${esc(blind.label)}</Name><Type>Text</Type><Options>size=4;fontSize=normal;align=left</Options></Widget>
      </Row>
      <Row>
        <Name>${esc(blind.label)} controls</Name>
        <Widget><WidgetId>blind_${i}_up</WidgetId><Name>Up</Name><Type>Button</Type><Options>size=1</Options></Widget>
        <Widget><WidgetId>blind_${i}_stop</WidgetId><Name>Stop</Name><Type>Button</Type><Options>size=2</Options></Widget>
        <Widget><WidgetId>blind_${i}_down</WidgetId><Name>Down</Name><Type>Button</Type><Options>size=1</Options></Widget>
      </Row>`));

  config.climate.forEach((zone, i) => rows.push(`
      <Row>
        <Name>${esc(zone.label)}</Name>
        <Widget><WidgetId>label_climate_${i}</WidgetId><Name>${esc(zone.label)}</Name><Type>Text</Type><Options>size=3;fontSize=normal;align=left</Options></Widget>
        <Widget><WidgetId>climate_${i}_value</WidgetId><Name>${config.defaultTemp}</Name><Type>Text</Type><Options>size=1;fontSize=normal;align=center</Options></Widget>
      </Row>
      <Row>
        <Name>${esc(zone.label)} slider</Name>
        <Widget><WidgetId>climate_${i}</WidgetId><Type>Slider</Type><Options>size=4</Options></Widget>
      </Row>`));

  return `<Extensions>
    <Version>1.11</Version>
    <Panel>
      <Order>1</Order>
      <PanelId>${PANEL_ID}</PanelId>
      <Origin>local</Origin>
      <Location>HomeScreen</Location>
      <Icon>${esc(config.panelIcon)}</Icon>
      <Name>${esc(config.panelName)}</Name>
      <ActivityType>Custom</ActivityType>
      <Page>
        <Name>${esc(config.panelName)}</Name>${rows.join('')}
        <Options>hideRowNames=1</Options>
      </Page>
    </Panel>
  </Extensions>`;
}

async function setWidget(widgetId, value) {
  await xapi.Command.UserInterface.Extensions.Widget.SetValue({ WidgetId: widgetId, Value: String(value) }).catch(() => {});
}

async function syncInitialState() {
  suppressUntil = Date.now() + 3000;

  for (const [i, light] of config.lights.entries()) {
    if (!light.statusUrl) continue;
    const result = await callHttp('Get', light.statusUrl);
    if (!result) continue;
    try {
      await setWidget(`light_${i}`, JSON.parse(result.Body).on ? 'on' : 'off');
    } catch (e) {
      log(`Light ${i}: status reply was not {"on": true|false}`);
    }
  }

  for (const [i, zone] of config.climate.entries()) {
    let temp = config.defaultTemp;
    if (zone.statusUrl) {
      const result = await callHttp('Get', zone.statusUrl);
      try {
        if (result) temp = Number(JSON.parse(result.Body).temp);
      } catch (e) {
        log(`Climate ${i}: status reply was not {"temp": <number>}`);
      }
    }
    temp = Math.min(zone.maxTemp, Math.max(zone.minTemp, temp));
    await setWidget(`climate_${i}`, toRaw(zone, temp));
    await setWidget(`climate_${i}_value`, `${temp}${config.tempUnit}`);
  }

  suppressUntil = Date.now() + 1500;
}

xapi.Event.UserInterface.Extensions.Widget.Action.on(async ({ WidgetId, Type, Value }) => {
  if (Date.now() < suppressUntil) return;
  let m;

  if ((m = WidgetId.match(/^light_(\\d+)$/)) && Type === 'changed') {
    const light = config.lights[Number(m[1])];
    if (light) await callHttp('Post', Value === 'on' ? light.onUrl : light.offUrl);
    return;
  }

  if ((m = WidgetId.match(/^blind_(\\d+)_(up|stop|down)$/)) && Type === 'clicked') {
    const blind = config.blinds[Number(m[1])];
    if (blind) await callHttp('Post', blind[`${m[2]}Url`]);
    return;
  }

  if ((m = WidgetId.match(/^climate_(\\d+)$/))) {
    const zone = config.climate[Number(m[1])];
    if (!zone) return;
    const temp = toTemp(zone, Value);
    if (Type === 'changed') await setWidget(`climate_${m[1]}_value`, `${temp}${config.tempUnit}`);
    if (Type === 'released') await callHttp('Post', zone.setUrl, { temp });
  }
});

async function start() {
  await setConfig(['HttpClient', 'Mode'], 'On');
  if (config.allowInsecureHttps) await setConfig(['HttpClient', 'AllowInsecureHTTPS'], 'True');
  await xapi.Command.UserInterface.Extensions.Panel.Save({ PanelId: PANEL_ID }, buildPanelXml());
  log('Panel deployed.');
  await syncInitialState();
}

start().catch((e) => log(`Start-up failed: ${e.message}`));
"""
)


def _room_control_validate(v):
    errors = []
    if not (v["lights"] or v["blinds"] or v["climate"]):
        errors.append("Add at least one light, blind or climate row.")
    for i, row in enumerate(v["lights"], 1):
        if not (row["onUrl"] and row["offUrl"]):
            errors.append(f"Lights row {i}: both On URL and Off URL are required.")
    for i, row in enumerate(v["blinds"], 1):
        if not (row["upUrl"] and row["stopUrl"] and row["downUrl"]):
            errors.append(f"Blinds row {i}: Up, Stop and Down URLs are required.")
    for i, row in enumerate(v["climate"], 1):
        if not row["setUrl"]:
            errors.append(f"Climate row {i}: Set URL is required.")
        try:
            lo, hi = int(row["minTemp"]), int(row["maxTemp"])
            if lo >= hi:
                errors.append(f"Climate row {i}: Min must be lower than Max.")
        except ValueError:
            errors.append(f"Climate row {i}: Min and Max must be whole numbers.")
    return errors


def _room_control_post(values):
    """Climate min/max are typed as text in the table; the macro needs numbers."""
    for row in values["climate"]:
        row["minTemp"] = int(row["minTemp"])
        row["maxTemp"] = int(row["maxTemp"])
    return values


room_control = Template(
    id="room_control",
    name="Room Control Panel (lights / blinds / climate)",
    description=(
        "Adds a touch-panel to the device with light switches, blind Up/Stop/Down buttons and "
        "temperature sliders. Each control calls a REST endpoint on the room's controller."
    ),
    default_macro_name="Room_Control",
    prerequisites=[
        "The controller / gateway must expose HTTP(S) endpoints reachable from the device network.",
        "Macro needs Admin role (HttpClient is Admin-only) - the macro runtime normally has it.",
        "Optional status URLs must return JSON: lights {\"on\": true}, climate {\"temp\": 24}.",
    ],
    fields=[
        Field("panelName", "Panel name", "str", "Room Control", required=True),
        Field("panelIcon", "Panel icon", "choice", "Lightbulb", choices=PANEL_ICONS),
        Field("headers", "HTTP headers (one per line)", "lines", [], "e.g.  Authorization: Bearer abc123"),
        Field("postBody", "Default POST body", "str", "{}", "Sent with On/Off/Up/Stop/Down calls. Leave empty to send no body."),
        Field("allowInsecureHttps", "Accept self-signed HTTPS certificates", "bool", True),
        Field("tempUnit", "Temperature unit label", "str", "°C"),
        Field("defaultTemp", "Default temperature shown", "int", 24, min=5, max=40),
        Field(
            "lights", "Lights", "table",
            columns=[
                Column("label", "Label", "Main lights"),
                Column("onUrl", "On URL", "http://192.168.1.50/api/lights/1/on", 300),
                Column("offUrl", "Off URL", "http://192.168.1.50/api/lights/1/off", 300),
                Column("statusUrl", "Status URL (optional)", "", 260),
            ],
        ),
        Field(
            "blinds", "Blinds", "table",
            columns=[
                Column("label", "Label", "Main blinds"),
                Column("upUrl", "Up URL", "http://192.168.1.50/api/blinds/1/up", 280),
                Column("stopUrl", "Stop URL", "http://192.168.1.50/api/blinds/1/stop", 280),
                Column("downUrl", "Down URL", "http://192.168.1.50/api/blinds/1/down", 280),
            ],
        ),
        Field(
            "climate", "Climate zones", "table",
            columns=[
                Column("label", "Label", "AC temperature"),
                Column("setUrl", "Set URL (POST {\"temp\": n})", "http://192.168.1.50/api/climate/1/set", 320),
                Column("minTemp", "Min", "18", 60),
                Column("maxTemp", "Max", "30", 60),
                Column("statusUrl", "Status URL (optional)", "", 260),
            ],
        ),
        Field("enableLogging", "Log to macro console", "bool", True),
    ],
    body=_ROOM_CONTROL,
    extra_validate=_room_control_validate,
    post_render=_room_control_post,
)


# --------------------------------------------------------------------------- Visitor kiosk

_VISITOR_KIOSK = (
    "import xapi from 'xapi';\n\n"
    + CONFIG_MARKER
    + "\n"
    + _helpers("VisitorKiosk")
    + """
async function start() {
  await setConfig(['UserInterface', 'Kiosk', 'URL'], config.kioskUrl);
  await setConfig(['UserInterface', 'Kiosk', 'Mode'], 'On');

  await setConfig(['Standby', 'Control'], 'On');
  await setConfig(['Standby', 'Delay'], config.standbyDelayMinutes);
  await setConfig(['Standby', 'WakeupOnMotionDetection'], config.wakeOnMotion ? 'On' : 'Off');

  await setConfig(['WebEngine', 'Features', 'SipUrlHandler'], config.enableSipUrlHandler ? 'On' : 'Off');
  await setConfig(['UserInterface', 'Features', 'HideAll'], config.hideAllButtons ? 'True' : 'False');

  log('Kiosk configuration applied. Branding / wallpaper is set from Control Hub, not from a macro.');
}

start();
"""
)

visitor_kiosk = Template(
    id="visitor_kiosk",
    name="Visitor Registration Kiosk (Desk / Board Pro)",
    description=(
        "Turns a Desk-series or Board Pro device into a reception kiosk that shows your visitor "
        "registration web app, wakes when someone walks up, and allows sip: links from the page."
    ),
    default_macro_name="Visitor_Kiosk",
    prerequisites=[
        "Kiosk mode is only supported on Desk series and Board Pro devices.",
        "Host the kiosk web page over HTTPS where the device can reach it.",
        "The page can start a call with a button whose label is sip:<address> (WebEngine Features SipUrlHandler).",
        "Wallpaper / branding: upload from Control Hub - there is no macro API for it.",
    ],
    fields=[
        Field("kioskUrl", "Kiosk page URL", "str", "https://your-domain.example.com/visitor-kiosk.html", required=True),
        Field("standbyDelayMinutes", "Standby after (minutes)", "int", 3, "Device sleeps after this long with no activity.", min=1, max=480),
        Field("wakeOnMotion", "Wake when someone approaches", "bool", True),
        Field("enableSipUrlHandler", "Allow sip: buttons in the web page", "bool", True),
        Field("hideAllButtons", "Hide all default UI buttons", "bool", True),
        Field("enableLogging", "Log to macro console", "bool", True),
    ],
    body=_VISITOR_KIOSK,
)


# --------------------------------------------------------------------------- Quick dial

_QUICK_DIAL = (
    "import xapi from 'xapi';\n\n"
    + CONFIG_MARKER
    + "\n"
    + _helpers("QuickDial", xml=True)
    + """
const PANEL_ID = 'panel_quick_dial';

function buildPanelXml() {
  const rows = config.entries.map((entry, i) => `
      <Row>
        <Name>${esc(entry.label)}</Name>
        <Widget><WidgetId>dial_${i}</WidgetId><Name>${esc(entry.label)}</Name><Type>Button</Type><Options>size=4</Options></Widget>
      </Row>`);

  return `<Extensions>
    <Version>1.11</Version>
    <Panel>
      <Order>2</Order>
      <PanelId>${PANEL_ID}</PanelId>
      <Origin>local</Origin>
      <Location>${esc(config.location)}</Location>
      <Icon>${esc(config.panelIcon)}</Icon>
      <Name>${esc(config.panelName)}</Name>
      <ActivityType>Custom</ActivityType>
      <Page>
        <Name>${esc(config.panelName)}</Name>${rows.join('')}
        <Options>hideRowNames=1</Options>
      </Page>
    </Panel>
  </Extensions>`;
}

xapi.Event.UserInterface.Extensions.Widget.Action.on(async ({ WidgetId, Type }) => {
  const m = WidgetId.match(/^dial_(\\d+)$/);
  if (!m || Type !== 'clicked') return;
  const entry = config.entries[Number(m[1])];
  if (!entry) return;
  log(`Dialling ${entry.label} (${entry.number})`);
  try {
    await xapi.Command.Dial({ Number: entry.number });
  } catch (e) {
    log(`Dial failed: ${e.message}`);
    xapi.Command.UserInterface.Message.Alert.Display({
      Title: 'Could not place call',
      Text: `${entry.label}: ${e.message}`,
      Duration: 6,
    }).catch(() => {});
  }
});

xapi.Command.UserInterface.Extensions.Panel.Save({ PanelId: PANEL_ID }, buildPanelXml())
  .then(() => log('Quick dial panel deployed.'))
  .catch((e) => log(`Could not deploy panel: ${e.message}`));
"""
)


def _quick_dial_validate(v):
    errors = []
    for i, row in enumerate(v["entries"], 1):
        if not row["number"]:
            errors.append(f"Speed dial row {i}: Number / URI is required.")
        if len(row["label"]) > 30:
            errors.append(f"Speed dial row {i}: keep the label under 30 characters so it fits the button.")
    return errors


quick_dial = Template(
    id="quick_dial",
    name="Quick Dial Panel",
    description="A touch-panel of one-tap speed-dial buttons (helpdesk, reception, a standing bridge...).",
    default_macro_name="Quick_Dial",
    prerequisites=["Numbers can be SIP URIs, Webex addresses or E.164 numbers - whatever the device can dial."],
    fields=[
        Field("panelName", "Panel name", "str", "Quick Dial", required=True),
        Field("panelIcon", "Panel icon", "choice", "Handset", choices=PANEL_ICONS),
        Field("location", "Where the button appears", "choice", "HomeScreen",
              choices=["HomeScreen", "HomeScreenAndCallControls", "CallControls", "ControlPanel"]),
        Field(
            "entries", "Speed dials", "table", required=True,
            columns=[Column("label", "Button label", "IT Helpdesk"), Column("number", "Number / URI", "helpdesk@example.com")],
        ),
        Field("enableLogging", "Log to macro console", "bool", True),
    ],
    body=_QUICK_DIAL,
    extra_validate=_quick_dial_validate,
)


# --------------------------------------------------------------------------- Report an issue

_REPORT_ISSUE = (
    "import xapi from 'xapi';\n\n"
    + CONFIG_MARKER
    + "\n"
    + _helpers("ReportIssue", xml=True)
    + """
const PANEL_ID = 'panel_report_issue';
const FEEDBACK_ID = 'report_issue_text';

function buildPanelXml() {
  return `<Extensions>
    <Version>1.11</Version>
    <Panel>
      <Order>9</Order>
      <PanelId>${PANEL_ID}</PanelId>
      <Origin>local</Origin>
      <Location>${esc(config.location)}</Location>
      <Icon>Help</Icon>
      <Name>${esc(config.buttonLabel)}</Name>
      <ActivityType>Custom</ActivityType>
    </Panel>
  </Extensions>`;
}

async function deviceInfo() {
  const name = await xapi.Status.UserInterface.ContactInfo.Name.get().catch(() => 'Unknown device');
  const ip = await xapi.Status.Network[1].IPv4.Address.get().catch(() => '');
  return { name, ip };
}

async function sendReport(text) {
  const { name, ip } = await deviceInfo();
  const payload = {
    text: `[${name}] ${text}`,
    device: name,
    ip,
    issue: text,
    time: new Date().toISOString(),
  };
  const params = {
    Url: config.webhookUrl,
    Header: [...config.headers, 'Content-Type: application/json'],
    ResultBody: 'PlainText',
  };
  if (config.allowInsecureHttps) params.AllowInsecureHTTPS = 'True';

  try {
    const result = await xapi.Command.HttpClient.Post(params, JSON.stringify(payload));
    const status = Number(result.StatusCode);
    if (status < 200 || status >= 300) throw new Error(`HTTP ${status}`);
    log(`Report sent (${status}).`);
    xapi.Command.UserInterface.Message.Alert.Display({
      Title: 'Thank you', Text: 'Your report has been sent to support.', Duration: 6,
    }).catch(() => {});
  } catch (e) {
    log(`Report failed: ${e.message}`);
    xapi.Command.UserInterface.Message.Alert.Display({
      Title: 'Report not sent', Text: 'Could not reach the support system. Please call the helpdesk.', Duration: 8,
    }).catch(() => {});
  }
}

xapi.Event.UserInterface.Extensions.Panel.Clicked.on(async ({ PanelId }) => {
  if (PanelId !== PANEL_ID) return;
  await xapi.Command.UserInterface.Message.TextInput.Display({
    Title: config.buttonLabel,
    Text: 'Briefly describe the problem',
    FeedbackId: FEEDBACK_ID,
    InputType: 'SingleLine',
    SubmitText: 'Send',
  });
});

xapi.Event.UserInterface.Message.TextInput.Response.on(async ({ FeedbackId, Text }) => {
  if (FeedbackId !== FEEDBACK_ID || !Text.trim()) return;
  await sendReport(Text.trim());
});

async function start() {
  await setConfig(['HttpClient', 'Mode'], 'On');
  if (config.allowInsecureHttps) await setConfig(['HttpClient', 'AllowInsecureHTTPS'], 'True');
  await xapi.Command.UserInterface.Extensions.Panel.Save({ PanelId: PANEL_ID }, buildPanelXml());
  log('Report-an-issue button deployed.');
}

start().catch((e) => log(`Start-up failed: ${e.message}`));
"""
)

report_issue = Template(
    id="report_issue",
    name="Report an Issue button",
    description=(
        "Adds a 'Report an issue' button. The user types a short description and the macro POSTs it "
        "(with device name + IP) to your webhook - Slack / Teams / ServiceNow / any HTTP endpoint."
    ),
    default_macro_name="Report_Issue",
    prerequisites=[
        "The webhook must accept a JSON POST. Payload: {text, device, ip, issue, time} - 'text' works for Slack-style hooks.",
        "Macro needs Admin role (HttpClient is Admin-only).",
    ],
    fields=[
        Field("webhookUrl", "Webhook URL", "str", "https://hooks.example.com/services/XXXX", required=True),
        Field("headers", "HTTP headers (one per line)", "lines", [], "e.g.  Authorization: Bearer abc123"),
        Field("allowInsecureHttps", "Accept self-signed HTTPS certificates", "bool", False),
        Field("buttonLabel", "Button label", "str", "Report an issue", required=True),
        Field("location", "Where the button appears", "choice", "HomeScreenAndCallControls",
              choices=["HomeScreen", "HomeScreenAndCallControls", "CallControls", "ControlPanel"]),
        Field("enableLogging", "Log to macro console", "bool", True),
    ],
    body=_REPORT_ISSUE,
)


TEMPLATES: list[Template] = [room_release, room_control, visitor_kiosk, quick_dial, report_issue]
BY_ID = {t.id: t for t in TEMPLATES}
