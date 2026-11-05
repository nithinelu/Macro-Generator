<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>macro harness</title></head>
<body><pre id="out">PENDING</pre>
<script>
const MACROS = /*__MACROS__*/;
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const realSetTimeout = window.setTimeout.bind(window);
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => realSetTimeout(r, 0)); };

function makeEnv(opts = {}) {
  let now = 1000000;
  let timers = [];
  let nextId = 1;
  const RealDate = Date;
  const env = { calls: [], handlers: {}, errors: [], logs: [] };
  const status = Object.assign({
    'Status.RoomAnalytics.PeoplePresence': 'No',
    'Status.RoomAnalytics.PeopleCount.Current': 0,
    'Status.SystemUnit.State.NumberOfActiveCalls': 0,
    'Status.Bookings.Current.Id': '',
    'Status.UserInterface.ContactInfo.Name': 'Boardroom 1',
    'Status.Network.1.IPv4.Address': '10.1.1.9',
  }, opts.status || {});
  const results = Object.assign({
    'Command.Bookings.Get': { Booking: { Id: 'B1', MeetingId: 'M-from-get' } },
    'Command.HttpClient.Post': { StatusCode: '200', Body: '' },
    'Command.HttpClient.Get': { StatusCode: '200', Body: '{}' },
  }, opts.results || {});

  const node = (path) => new Proxy(function () {}, {
    get(_t, prop) { return prop === 'then' ? undefined : node([...path, String(prop)]); },
    apply(_t, _this, args) {
      const last = path[path.length - 1];
      const owner = path.slice(0, -1).join('.');
      if (last === 'on') { (env.handlers[owner] = env.handlers[owner] || []).push(args[0]); return () => {}; }
      if (last === 'get') {
        if (!(owner in status)) return Promise.reject(new Error('no status ' + owner));
        return Promise.resolve(status[owner]);
      }
      env.calls.push({ path: path.join('.'), args });
      return Promise.resolve(results[path.join('.')] || {});
    },
  });

  env.xapi = node([]);
  env.setTimeout = (fn, ms) => { const id = nextId++; timers.push({ id, at: now + (ms || 0), fn }); return id; };
  env.clearTimeout = (id) => { timers = timers.filter((t) => t.id !== id); };
  env.Date = Object.assign(function (...a) { return a.length ? new RealDate(...a) : new RealDate(now); }, { now: () => now });
  env.console = { log: (...a) => env.logs.push(a.join(' ')), error: (...a) => env.logs.push('ERR ' + a.join(' ')) };
  env.advance = async (ms) => {
    const target = now + ms;
    for (;;) {
      await flush();
      const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      timers = timers.filter((t) => t !== due);
      now = Math.max(now, due.at);
      try { await due.fn(); } catch (e) { env.errors.push(String(e)); }
    }
    now = target;
    await flush();
  };
  env.emit = async (path, payload) => {
    for (const h of env.handlers[path] || []) { try { await h(payload); } catch (e) { env.errors.push(String(e)); } }
    await flush();
  };
  env.called = (path) => env.calls.filter((c) => c.path === path);
  env.run = async (id) => {
    const code = MACROS[id].replace(/^\s*import xapi from 'xapi';/m, '');
    const fn = new AsyncFunction('xapi', 'setTimeout', 'clearTimeout', 'Date', 'console', code);
    await fn(env.xapi, env.setTimeout, env.clearTimeout, env.Date, env.console);
    await flush();
    return env;
  };
  return env;
}

const results = [];
async function scenario(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail: detail || '' });
  } catch (e) {
    results.push({ name, ok: false, detail: String(e && e.message || e) });
  }
}
function expect(cond, msg) { if (!cond) throw new Error(msg); }
const eq = (a, b, msg) => expect(JSON.stringify(a) === JSON.stringify(b), `${msg}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
function wellFormed(xml, what) {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  expect(!doc.querySelector('parsererror'), what + ' XML is not well-formed: ' + (doc.querySelector('parsererror') || {}).textContent);
  return doc;
}
const ROOM_PROMPT = 'Command.UserInterface.Message.Prompt.Display';
const RESPOND = 'Command.Bookings.Respond';

(async () => {
  // ---- every macro loads without throwing and without logged errors
  for (const id of Object.keys(MACROS)) {
    await scenario('loads: ' + id, async () => {
      const env = await makeEnv().run(id);
      await env.advance(5000);
      eq(env.errors, [], 'timer errors');
      expect(!env.logs.some((l) => /failed|Could not/i.test(l)), 'macro logged a failure: ' + env.logs.join(' | '));
    });
  }

  // ---- room release
  await scenario('room_release: declines after grace + prompt timeout', async () => {
    const env = await makeEnv().run('room_release');
    await env.emit('Event.Bookings.Start', { Id: 'B1', MeetingId: 'M1' });
    await env.advance(59000);
    eq(env.called(ROOM_PROMPT).length, 0, 'prompt before grace period ends');
    await env.advance(2000);
    eq(env.called(ROOM_PROMPT).length, 1, 'prompt after grace period');
    await env.advance(28000);
    eq(env.called(RESPOND).length, 0, 'decline before prompt times out');
    await env.advance(2000);
    const r = env.called(RESPOND);
    eq(r.length, 1, 'decline count');
    eq(r[0].args[0].MeetingId, 'M1', 'MeetingId');
    eq(r[0].args[0].Type, 'Decline', 'Type');
    eq(env.errors, [], 'errors');
  });
  await scenario('room_release: tap on prompt keeps the booking', async () => {
    const env = await makeEnv().run('room_release');
    await env.emit('Event.Bookings.Start', { Id: 'B1', MeetingId: 'M1' });
    await env.advance(61000);
    await env.emit('Event.UserInterface.Message.Prompt.Response', { FeedbackId: 'room_release_prompt', OptionId: 1 });
    await env.advance(120000);
    eq(env.called(RESPOND).length, 0, 'decline after check-in');
  });
  await scenario('room_release: occupied room is left alone', async () => {
    const env = await makeEnv({ status: { 'Status.RoomAnalytics.PeoplePresence': 'Yes' } }).run('room_release');
    await env.emit('Event.Bookings.Start', { Id: 'B1', MeetingId: 'M1' });
    await env.advance(200000);
    eq(env.called(ROOM_PROMPT).length, 0, 'prompt shown to occupied room');
  });
  await scenario('room_release: active call counts as occupied', async () => {
    const env = await makeEnv({ status: { 'Status.SystemUnit.State.NumberOfActiveCalls': 1 } }).run('room_release');
    await env.emit('Event.Bookings.Start', { Id: 'B1', MeetingId: 'M1' });
    await env.advance(200000);
    eq(env.called(ROOM_PROMPT).length, 0, 'prompt during a call');
  });
  await scenario('room_release: meeting ending cancels the check', async () => {
    const env = await makeEnv().run('room_release');
    await env.emit('Event.Bookings.Start', { Id: 'B1', MeetingId: 'M1' });
    await env.emit('Event.Bookings.End', { Id: 'B1' });
    await env.advance(200000);
    eq(env.called(ROOM_PROMPT).length, 0, 'prompt after meeting end');
  });
  await scenario('room_release: looks up MeetingId when the event has none', async () => {
    const env = await makeEnv().run('room_release');
    await env.emit('Event.Bookings.Start', { Id: 'B1' });
    await env.advance(100000);
    eq(env.called(RESPOND)[0].args[0].MeetingId, 'M-from-get', 'MeetingId from Bookings.Get');
  });
  await scenario('room_release: picks up a meeting already running at start-up', async () => {
    const env = await makeEnv({ status: { 'Status.Bookings.Current.Id': 'B9' } }).run('room_release');
    await env.advance(100000);
    eq(env.called(RESPOND).length, 1, 'decline of in-progress meeting');
  });
  await scenario('room_release: required settings are applied', async () => {
    const env = await makeEnv().run('room_release');
    eq(env.called('Config.RoomAnalytics.PeoplePresenceDetector.set')[0].args, ['On'], 'PeoplePresenceDetector');
    eq(env.called('Config.Bookings.DeclinableBookingType.set')[0].args, ['All'], 'DeclinableBookingType');
  });

  // ---- room control
  const PANEL_SAVE = 'Command.UserInterface.Extensions.Panel.Save';
  const POST = 'Command.HttpClient.Post';
  await scenario('room_control: panel XML is well-formed and has every widget', async () => {
    const env = await makeEnv().run('room_control');
    const save = env.called(PANEL_SAVE)[0];
    expect(save, 'Panel.Save not called');
    eq(save.args[0], { PanelId: 'panel_room_control' }, 'PanelId');
    const doc = wellFormed(save.args[1], 'room control');
    const ids = [...doc.querySelectorAll('WidgetId')].map((n) => n.textContent);
    for (const w of ['light_0', 'blind_0_up', 'blind_0_stop', 'blind_0_down', 'climate_0', 'climate_0_value']) expect(ids.includes(w), 'missing widget ' + w);
    expect(!/range=/.test(save.args[1]), 'slider range option should not exist');
  });
  await scenario('room_control: light toggle posts On/Off URL with body + auth header', async () => {
    const env = await makeEnv().run('room_control');
    await env.advance(5000);
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'light_0', Type: 'changed', Value: 'on' });
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'light_0', Type: 'changed', Value: 'off' });
    const p = env.called(POST);
    eq(p.length, 2, 'post count');
    eq(p[0].args[0].Url, 'http://192.168.1.50/api/lights/1/on', 'on url');
    eq(p[1].args[0].Url, 'http://192.168.1.50/api/lights/1/off', 'off url');
    expect(p[0].args[0].Header.includes('Content-Type: application/json'), 'content-type header');
    eq(p[0].args[0].ResultBody, 'PlainText', 'ResultBody');
    eq(p[0].args[1], '{}', 'POST body is the second argument');
    eq(p[0].args[0].AllowInsecureHTTPS, 'True', 'AllowInsecureHTTPS param');
  });
  await scenario('room_control: widget events during start-up sync are ignored', async () => {
    const env = await makeEnv().run('room_control');
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'light_0', Type: 'changed', Value: 'on' });
    eq(env.called(POST).length, 0, 'post during suppress window');
  });
  await scenario('room_control: blind buttons only fire on clicked', async () => {
    const env = await makeEnv().run('room_control');
    await env.advance(5000);
    for (const [w, t] of [['blind_0_up', 'pressed'], ['blind_0_up', 'released'], ['blind_0_up', 'clicked'], ['blind_0_down', 'clicked'], ['blind_0_stop', 'clicked']])
      await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: w, Type: t, Value: '' });
    eq(env.called(POST).map((c) => c.args[0].Url.split('/').pop()), ['up', 'down', 'stop'], 'urls hit');
  });
  await scenario('room_control: slider maps 0-255 onto temperature range, sends on release only', async () => {
    const env = await makeEnv().run('room_control');
    await env.advance(5000);
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'climate_0', Type: 'changed', Value: '255' });
    eq(env.called(POST).length, 0, 'post while dragging');
    eq(env.called('Command.UserInterface.Extensions.Widget.SetValue').pop().args[0], { WidgetId: 'climate_0_value', Value: '30°C' }, 'label at max');
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'climate_0', Type: 'released', Value: '128' });
    const p = env.called(POST);
    eq(p.length, 1, 'post on release');
    eq(p[0].args[1], '{"temp":24}', 'body');
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'climate_0', Type: 'released', Value: '0' });
    eq(env.called(POST)[1].args[1], '{"temp":18}', 'min temperature');
  });
  await scenario('room_control: initial state is read from status URLs', async () => {
    const env = await makeEnv({ results: { 'Command.HttpClient.Get': { StatusCode: '200', Body: '{"on": true, "temp": 27}' } } }).run('room_control_status');
    await env.advance(5000);
    expect(env.called('Command.HttpClient.Get').length >= 2, 'Get not called for status URLs');
    eq(env.called('Command.HttpClient.Get')[0].args[0].ResultBody, 'PlainText', 'ResultBody on Get');
    const sets = env.called('Command.UserInterface.Extensions.Widget.SetValue').map((c) => c.args[0]);
    expect(sets.some((s) => s.WidgetId === 'light_0' && s.Value === 'on'), 'light not set to on: ' + JSON.stringify(sets));
    expect(sets.some((s) => s.WidgetId === 'climate_0_value' && s.Value === '27°C'), 'temp label not 27: ' + JSON.stringify(sets));
    eq(env.called(POST).length, 0, 'sync must not trigger controller calls');
  });
  await scenario('room_control: controller failure shows an alert', async () => {
    const env = await makeEnv({ results: { 'Command.HttpClient.Post': { StatusCode: '500', Body: '' } } }).run('room_control');
    await env.advance(5000);
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'light_0', Type: 'changed', Value: 'on' });
    eq(env.called('Command.UserInterface.Message.Alert.Display').length, 1, 'alert count');
  });

  // ---- quick dial
  await scenario('quick_dial: panel XML ok and button dials', async () => {
    const env = await makeEnv().run('quick_dial');
    wellFormed(env.called(PANEL_SAVE)[0].args[1], 'quick dial');
    await env.emit('Event.UserInterface.Extensions.Widget.Action', { WidgetId: 'dial_0', Type: 'clicked', Value: '' });
    eq(env.called('Command.Dial')[0].args[0], { Number: 'helpdesk@example.com' }, 'Dial args');
  });
  await scenario('quick_dial: labels with & and quotes stay well-formed', async () => {
    const env = await makeEnv().run('quick_dial_special');
    const doc = wellFormed(env.called(PANEL_SAVE)[0].args[1], 'quick dial special');
    expect([...doc.querySelectorAll('Widget > Name')].some((n) => n.textContent === 'R&D "Lab" <1>'), 'label not preserved');
  });

  // ---- report an issue
  await scenario('report_issue: button -> text box -> webhook', async () => {
    const env = await makeEnv().run('report_issue');
    wellFormed(env.called(PANEL_SAVE)[0].args[1], 'report issue');
    await env.emit('Event.UserInterface.Extensions.Panel.Clicked', { PanelId: 'panel_report_issue' });
    eq(env.called('Command.UserInterface.Message.TextInput.Display')[0].args[0].FeedbackId, 'report_issue_text', 'FeedbackId');
    await env.emit('Event.UserInterface.Message.TextInput.Response', { FeedbackId: 'report_issue_text', Text: 'Projector dead' });
    const p = env.called(POST);
    eq(p.length, 1, 'webhook post count');
    eq(p[0].args[0].Url, 'https://hooks.example.com/services/XXXX', 'webhook url');
    const body = JSON.parse(p[0].args[1]);
    eq([body.issue, body.device, body.ip, body.text], ['Projector dead', 'Boardroom 1', '10.1.1.9', '[Boardroom 1] Projector dead'], 'payload');
  });
  await scenario('report_issue: other panels and empty text are ignored', async () => {
    const env = await makeEnv().run('report_issue');
    await env.emit('Event.UserInterface.Extensions.Panel.Clicked', { PanelId: 'something_else' });
    await env.emit('Event.UserInterface.Message.TextInput.Response', { FeedbackId: 'report_issue_text', Text: '   ' });
    eq(env.called('Command.UserInterface.Message.TextInput.Display').length, 0, 'text box for other panel');
    eq(env.called(POST).length, 0, 'webhook post for empty text');
  });

  // ---- visitor kiosk
  await scenario('visitor_kiosk: applies kiosk, standby and sip settings', async () => {
    const env = await makeEnv().run('visitor_kiosk');
    const set = (p) => (env.called('Config.' + p + '.set')[0] || {}).args;
    eq(set('UserInterface.Kiosk.Mode'), ['On'], 'Kiosk Mode');
    eq(set('UserInterface.Kiosk.URL'), ['https://your-domain.example.com/visitor-kiosk.html'], 'Kiosk URL');
    eq(set('Standby.Delay'), [3], 'Standby Delay');
    eq(set('Standby.WakeupOnMotionDetection'), ['On'], 'WakeupOnMotionDetection');
    eq(set('WebEngine.Features.SipUrlHandler'), ['On'], 'SipUrlHandler');
    eq(set('UserInterface.Features.HideAll'), ['True'], 'HideAll');
  });

  document.getElementById('out').textContent = JSON.stringify(results);
})();
</script></body></html>
