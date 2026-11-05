// Virtual RoomOS runtime. Runs inside a sandboxed, network-blocked iframe next to the macros.
// It fakes the `xapi` module and a virtual clock, and reports everything to the page around it.
const RealDate = Date;
const realSetTimeout = setTimeout.bind(window);
const post = (message) => parent.postMessage(message, '*');
// Yield to other tasks WITHOUT timers: browsers throttle timers in a background / hidden frame to ~1 per second,
// which would make every virtual-time step take seconds. MessageChannel tasks are not throttled.
const tick = () => new Promise((resolve) => {
  const channel = new MessageChannel();
  channel.port1.onmessage = () => { channel.port1.close(); resolve(); };
  channel.port2.postMessage(0);
});
const flush = async () => { for (let i = 0; i < 6; i++) await tick(); };

/* ---------------------------------------------------------------- virtual clock */
let now = RealDate.now();
let timers = [];
let nextTimerId = 1;
const MAX_TIMER_RUNS = 20000;

window.setTimeout = (fn, ms = 0, ...args) => {
  const id = nextTimerId++;
  timers.push({ id, at: now + Math.max(0, Number(ms) || 0), fn, args, every: 0 });
  return id;
};
window.setInterval = (fn, ms = 0, ...args) => {
  const every = Math.max(1, Number(ms) || 1);
  const id = nextTimerId++;
  timers.push({ id, at: now + every, fn, args, every });
  return id;
};
window.clearTimeout = window.clearInterval = (id) => { timers = timers.filter((t) => t.id !== id); };
window.Date = class extends RealDate {
  constructor(...a) { a.length ? super(...a) : super(now); }
  static now() { return now; }
};

async function advance(ms) {
  const target = now + ms;
  let runs = 0;
  for (;;) {
    await flush();
    const due = timers.filter((t) => t.at <= target).sort((a, b) => a.at - b.at || a.id - b.id)[0];
    if (!due) break;
    if (++runs > MAX_TIMER_RUNS) {
      post({ type: 'console', level: 'error', text: `Simulator stopped after ${MAX_TIMER_RUNS} timer runs in one step (a timer with a very short interval?).` });
      break;
    }
    now = Math.max(now, due.at);
    if (due.every) due.at += due.every; else timers = timers.filter((t) => t !== due);
    try { await due.fn(...due.args); } catch (e) { reportError(e); }
  }
  now = target;
  await flush();
  post({ type: 'time', now });
}

/* ---------------------------------------------------------------- console + errors */
const show = (v) => {
  if (typeof v === 'string') return v;
  if (v instanceof Error) return `${v.name}: ${v.message}`;
  try { return JSON.stringify(v); } catch { return String(v); }
};
const send = (level) => (...args) => post({ type: 'console', level, text: args.map(show).join(' '), t: now });
window.console = { log: send('log'), info: send('log'), debug: send('debug'), warn: send('warn'), error: send('error'), trace: send('debug'), assert() {}, table: send('log') };
function reportError(e) { post({ type: 'console', level: 'error', text: show(e && e.stack ? `${e.name}: ${e.message}` : e), t: now }); }
window.addEventListener('error', (ev) => { reportError(ev.error || ev.message); ev.preventDefault(); });
window.addEventListener('unhandledrejection', (ev) => { reportError(ev.reason); ev.preventDefault(); });

/* ---------------------------------------------------------------- state (status + config) */
const state = new Map();        // 'Status.Audio.Volume' -> value
const lowerIndex = new Map();   // lower-case key -> canonical key
const listeners = [];           // { kind, path: [lower-case segments], cb, once }
let httpRules = [];

const norm = (segments) => segments.map((s) => String(s).toLowerCase());

function setState(key, value, by) {
  const canonical = lowerIndex.get(key.toLowerCase()) || key;
  lowerIndex.set(key.toLowerCase(), canonical);
  const changed = JSON.stringify(state.get(canonical)) !== JSON.stringify(value);
  state.set(canonical, value);
  post({ type: 'state', key: canonical, value, by });
  if (changed) {
    const [kind, ...rest] = canonical.split('.');
    dispatch(kind, rest, value);
  }
}

function getState(kind, segments) {
  const key = [kind, ...segments].join('.');
  const exact = lowerIndex.get(key.toLowerCase());
  if (exact !== undefined) return state.get(exact);
  const prefix = key.toLowerCase() + '.';
  const tree = {};
  let found = false;
  for (const [canonical, value] of state) {
    if (!canonical.toLowerCase().startsWith(prefix)) continue;
    found = true;
    const parts = canonical.slice(prefix.length).split('.');
    let node = tree;
    parts.slice(0, -1).forEach((p) => { node = node[p] = node[p] || {}; });
    node[parts[parts.length - 1]] = value;
  }
  if (found) return tree;
  post({ type: 'miss', key });
  return '';
}

/* ---------------------------------------------------------------- events */
const nest = (rest, payload) => rest.reduceRight((inner, name) => ({ [name]: inner }), payload);

function dispatch(kind, segments, payload) {
  const want = norm(segments);
  const kindLower = kind.toLowerCase();
  for (const l of [...listeners]) {
    if (l.kind !== kindLower || l.path.length > want.length) continue;
    if (!l.path.every((p, i) => p === want[i])) continue;
    const arg = l.path.length === want.length ? payload : nest(segments.slice(l.path.length), payload);
    if (l.once) listeners.splice(listeners.indexOf(l), 1);
    Promise.resolve().then(() => l.cb(arg)).catch(reportError);
  }
}

function subscribe(kind, segments, cb, once = false) {
  const entry = { kind: kind.toLowerCase(), path: norm(segments), cb, once };
  listeners.push(entry);
  const off = () => { const i = listeners.indexOf(entry); if (i >= 0) listeners.splice(i, 1); };
  return off;
}

function emit(path, payload) {
  const [kind, ...rest] = path.split('.');
  post({ type: 'call', kind: 'event', path, params: payload, t: now });
  dispatch(kind, rest, payload);
}

/* ---------------------------------------------------------------- commands */
function httpReply(method, params, body) {
  const url = String(params.Url || '');
  const rule = httpRules.find((r) => r.match === '*' || (r.match && url.includes(r.match))) || { status: 200, body: '{}' };
  post({ type: 'http', method, url, headers: params.Header, body, rule: rule.match, status: rule.status, reply: rule.body, t: now });
  if (String(rule.status).toLowerCase() === 'error') return Promise.reject(new Error('Network error (simulated)'));
  return Promise.resolve({ StatusCode: String(rule.status), Body: String(rule.body ?? ''), Headers: [] });
}

function runCommand(segments, params = {}, body) {
  const path = segments.join('.');
  const lower = path.toLowerCase();
  post({ type: 'call', kind: 'command', path, params, body, t: now });

  if (lower.startsWith('httpclient.')) return httpReply(segments[1].toUpperCase(), params || {}, body);

  switch (lower) {
    case 'standby.activate': setState('Status.Standby.State', 'Standby', 'macro'); break;
    case 'standby.deactivate': setState('Status.Standby.State', 'Off', 'macro'); break;
    case 'audio.volume.set': setState('Status.Audio.Volume', Number(params.Level), 'macro'); break;
    case 'audio.volume.mute': setState('Status.Audio.VolumeMute', 'On', 'macro'); break;
    case 'audio.volume.unmute': setState('Status.Audio.VolumeMute', 'Off', 'macro'); break;
    case 'audio.microphones.mute': setState('Status.Audio.Microphones.Mute', 'On', 'macro'); break;
    case 'audio.microphones.unmute': setState('Status.Audio.Microphones.Mute', 'Off', 'macro'); break;
    case 'dial':
      setState('Status.SystemUnit.State.NumberOfActiveCalls', 1, 'macro');
      emit('Event.CallSuccessful', { CallId: 1, CallType: 'Video', Protocol: 'sip', RemoteURI: String(params.Number || '') });
      break;
    case 'call.disconnect':
      setState('Status.SystemUnit.State.NumberOfActiveCalls', 0, 'macro');
      emit('Event.CallDisconnect', { CallId: 1, Reason: 'Local' });
      break;
    case 'bookings.list': return Promise.resolve({ Booking: [] });
    case 'bookings.get': return Promise.resolve({ Booking: { Id: params.Id || 'B1', MeetingId: params.MeetingId || 'M1', Title: 'Simulated meeting' } });
    default: break;
  }
  return Promise.resolve({});
}

/* ---------------------------------------------------------------- the xapi object */
const pathOf = (p) => (Array.isArray(p) ? p : String(p).split(/[ ./]+/)).filter(Boolean);
const ROOTS = { command: 'Command', event: 'Event', status: 'Status', config: 'Config', configuration: 'Config' };

function node(root, segments) {
  return new Proxy(function () {}, {
    get(_t, prop) {
      if (typeof prop === 'symbol' || prop === 'then') return undefined;
      return node(root, [...segments, String(prop)]);
    },
    apply(_t, _this, args) {
      const last = segments[segments.length - 1];
      const base = segments.slice(0, -1);
      if (root === 'Command') return runCommand(segments, args[0], args[1]);
      if (last === 'on' || last === 'once') return subscribe(root, base, args[0], last === 'once');
      if (root === 'Status' && last === 'get') return Promise.resolve(getState('Status', base));
      if (root === 'Config' && last === 'get') return Promise.resolve(getState('Config', base));
      if (root === 'Config' && last === 'set') { setState(['Config', ...base].join('.'), args[0], 'macro'); return Promise.resolve(); }
      throw new TypeError(`xapi.${root}.${segments.join('.')} is not a function`);
    },
  });
}

const feedbackParts = (expression) => {
  const parts = pathOf(expression);
  const root = ROOTS[String(parts[0]).toLowerCase()];
  return root ? [root, parts.slice(1)] : ['Event', parts];
};

const xapi = {
  Command: node('Command', []), Event: node('Event', []), Status: node('Status', []), Config: node('Config', []),
  command: (path, params, body) => runCommand(pathOf(path), params, body),
  event: { on: (path, cb) => subscribe('Event', pathOf(path), cb), once: (path, cb) => subscribe('Event', pathOf(path), cb, true) },
  status: {
    get: (path) => Promise.resolve(getState('Status', pathOf(path))),
    on: (path, cb) => subscribe('Status', pathOf(path), cb),
    once: (path, cb) => subscribe('Status', pathOf(path), cb, true),
  },
  config: {
    get: (path) => Promise.resolve(getState('Config', pathOf(path))),
    set: (path, value) => { setState(['Config', ...pathOf(path)].join('.'), value, 'macro'); return Promise.resolve(); },
    on: (path, cb) => subscribe('Config', pathOf(path), cb),
  },
  feedback: {
    on: (expression, cb) => { const [root, segs] = feedbackParts(expression); return subscribe(root, segs, cb); },
    once: (expression, cb) => { const [root, segs] = feedbackParts(expression); return subscribe(root, segs, cb, true); },
  },
};
xapi.default = xapi;
globalThis.__xapi = xapi;

/* ---------------------------------------------------------------- macro loader (native ES modules from blob URLs) */
const SPECIFIER = /(\b(?:from|import)\s*)(['"])([^'"\n]+)\2/g;

async function loadBundle(macros) {
  const byName = new Map(macros.map((m) => [m.name, m]));
  const blob = (code) => URL.createObjectURL(new Blob([code], { type: 'text/javascript' }));
  const xapiUrl = blob('export default globalThis.__xapi;');
  const urls = new Map();

  const urlFor = (name, chain = []) => {
    if (urls.has(name)) return urls.get(name);
    const macro = byName.get(name);
    if (!macro || chain.includes(name)) {
      const why = macro ? `circular import of './${name}'` : `macro './${name}' is not part of this bundle`;
      const url = blob(`throw new Error(${JSON.stringify(why)});`);
      urls.set(name, url);
      return url;
    }
    const code = macro.code.replace(SPECIFIER, (full, lead, quote, spec) => {
      if (spec === 'xapi') return `${lead}${quote}${xapiUrl}${quote}`;
      if (spec.startsWith('./')) return `${lead}${quote}${urlFor(spec.slice(2).replace(/\.js$/, ''), [...chain, name])}${quote}`;
      return full;
    });
    const url = blob(code);
    urls.set(name, url);
    return url;
  };

  for (const macro of macros.filter((m) => m.activate)) {
    try {
      await Promise.race([
        import(urlFor(macro.name)),
        new Promise((resolve) => realSetTimeout(resolve, 1500)),   // top-level await that waits for an event
      ]);
      post({ type: 'loaded', name: macro.name, ok: true });
    } catch (e) {
      post({ type: 'loaded', name: macro.name, ok: false, error: show(e) });
    }
  }
}

/* ---------------------------------------------------------------- messages from the page */
window.addEventListener('message', async (ev) => {
  const m = ev.data || {};
  switch (m.type) {
    case 'init':
      for (const [key, value] of Object.entries(m.state || {})) { lowerIndex.set(key.toLowerCase(), key); state.set(key, value); }
      httpRules = m.httpRules || [];
      await loadBundle(m.macros || []);
      await flush();
      post({ type: 'ready', now });
      break;
    case 'advance': await advance(m.ms); break;
    case 'emit': emit(m.path, m.payload); await flush(); break;
    case 'setState': setState(m.key, m.value, 'user'); await flush(); break;
    case 'httpRules': httpRules = m.rules || []; break;
    default: break;
  }
});
post({ type: 'booted' });
