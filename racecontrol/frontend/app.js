/* ============================================================
   iCASControl - dashboard front-end logic.
   Connects to the server over a WebSocket and renders the live
   race state: timing, track map, race log and race-control tools.
   ============================================================ */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

// ---- application state ----------------------------------------------------
const State = {
  ws: null,
  snapshot: null,
  events: [],
  selectedCar: null,        // car_idx
  selectedEvent: null,      // event id (an incident)
  replayEvent: null,        // any clicked log line - the Replay buttons use it
  filters: new Set(['incident', 'offtrack', 'pit', 'flag',
                     'penalty', 'message', 'info']),
  showTeam: false,
  showInterval: false,
  showSpeed: false,
  running: true,
};

const map = new TrackMap($('#trackmap'));

// ---- formatting helpers ---------------------------------------------------
function fmtClock(sec, withHours = true) {
  if (sec === null || sec === undefined || sec < 0 || !isFinite(sec))
    return '—';
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  const pad = (n) => String(n).padStart(2, '0');
  if (withHours || h > 0) return `${pad(h)}:${pad(m)}:${pad(s)}`;
  return `${pad(m)}:${pad(s)}`;
}

function fmtLap(sec) {
  if (!sec || sec <= 0) return '—';
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(3);
  return m > 0 ? `${m}:${s.padStart(6, '0')}` : s;
}

function fmtGap(car) {
  if (car.position === 1) return State.showInterval ? 'INT' : 'LEADER';
  if (State.showInterval) {
    return car.interval > 0 ? `+${car.interval.toFixed(2)}` : '—';
  }
  if (car.laps_down > 0) return `+${car.laps_down}L`;
  return car.gap > 0 ? `+${car.gap.toFixed(2)}` : '—';
}

function toast(text) {
  let el = $('#toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'toast';
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 2600);
}

// ---- websocket ------------------------------------------------------------
// ---- who am I (multi-steward) ----------------------------------------------
function store(kind, key, val) {
  try {
    const st = kind === 'local' ? localStorage : sessionStorage;
    if (val === undefined) return st.getItem(key) || '';
    st.setItem(key, val);
  } catch (e) { /* private window */ }
  return '';
}
const Me = { name: store('local', 'icas_name'), pw: store('session', 'icas_pw'),
  place: '', token: '', loginOpen: false };

function connect() {
  const q = new URLSearchParams({ name: Me.name, pw: Me.pw });
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws?${q}`;
  const ws = new WebSocket(url);
  State.ws = ws;

  ws.onopen = () => setBadge('#ws-badge', 'connected', 'good');
  ws.onclose = () => {
    if (State.ws !== ws) return;          // replaced by a new login
    setBadge('#ws-badge', Me.loginOpen ? 'login needed' : 'reconnecting…', 'bad');
    if (!Me.loginOpen) setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    handleMessage(msg);
  };
}

function send(obj) {
  if (State.ws && State.ws.readyState === WebSocket.OPEN) {
    State.ws.send(JSON.stringify(obj));
  }
}

function handleMessage(msg) {
  switch (msg.type) {
    case 'init':
      $('#app-version').textContent = 'v' + (msg.version || '');
      State.running = msg.running;
      updateRunToggle();
      break;
    case 'track':
      map.setTrack(msg.path, msg.pit, msg.length_km);
      break;
    case 'events':
      State.events = msg.events || [];
      announceAuto();
      $('#log-count').textContent = msg.total || State.events.length;
      renderLog();
      renderCarLog();
      updateIncidentActions();
      break;
    case 'snapshot':
      State.snapshot = msg;
      State.running = msg.running;
      State.chat = msg.chat || null;
      State.stream = msg.stream || null;
      State.steward = msg.steward || null;
      updateChatToggle();
      updateStreamToggle();
      updateStewardToggle();
      renderNet(msg.net);
      render();
      break;
    case 'ack':
      if (msg.text) toast(msg.text);
      break;
    case 'login':                         // refused: name / password
      openLogin(msg.error || 'password needed');
      break;
    case 'hello':
      Me.name = msg.name; Me.place = msg.place; Me.token = msg.token;
      Me.following = !!msg.following;
      $('#stw-me').textContent = msg.name + (Me.following ? ' → main iCAS'
        : msg.place === 'local' ? ' (host)' : '');
      $('#stw-toggle').classList.toggle('following', Me.following);
      $('#stw-pw').classList.toggle('hidden', msg.place !== 'local' || Me.following);
      $('#stw-net').classList.toggle('hidden', msg.place !== 'local');
      $('#save-link').href = '/api/save' + (msg.place === 'local' ? ''
        : '?t=' + encodeURIComponent(msg.token));
      if (msg.place !== 'local' && !store('local', 'icas_name')) openLogin('');
      break;
    case 'stewards':
      onStewards(msg);
      break;
  }
}

function setBadge(sel, text, cls) {
  const el = $(sel);
  el.textContent = text;
  el.className = 'badge ' + (cls || '');
}

// ---- master render --------------------------------------------------------
function render() {
  const s = State.snapshot;
  if (!s) return;
  renderHeader(s);
  renderTiming(s);
  renderSelected(s);
  renderCamera(s);
  map.update(s.cars);
  map.setSelected(State.selectedCar);
}

function renderHeader(s) {
  const ss = s.session;
  $('#session-time').textContent = fmtClock(ss.time);
  $('#time-remain').textContent =
    ss.time_remain >= 0 ? fmtClock(ss.time_remain) : '—';
  $('#leader-lap').textContent = ss.laps_total > 0
    ? `${ss.leader_lap}/${ss.laps_total}` : ss.leader_lap;
  $('#total-inc').textContent = ss.total_incidents;
  // iRacing sends other drivers' incident points live only to a session
  // ADMIN; a spectator gets them at the finish.
  const spectator = s.source === 'iracing' && !ss.official_points;
  const incBox = $('#total-inc-box');
  incBox.classList.toggle('spectator', spectator);
  incBox.title = spectator
    ? 'No live incident points from iRacing yet - they come live only when '
      + 'this PC is ADMIN of the session. Spins / contacts are detected from '
      + 'car motion meanwhile.'
    : '';
  $('#replay-banner').classList.toggle('hidden', !ss.in_replay);
  $('#car-count').textContent = ss.car_count;
  $('#session-type').textContent = ss.type;
  $('#session-state').textContent = ss.state;
  $('#track-name').textContent =
    ss.track + (ss.config ? ' – ' + ss.config : '');

  // flag box - show the most significant active flag
  const flags = ss.flags || [];
  // (local_yellow = a waved yellow at a corner, NOT a full course caution)
  const order = ['red', 'checkered', 'white', 'caution', 'one_to_green',
    'yellow', 'green', 'local_yellow'];
  let shown = order.find((f) => flags.includes(f)) || 'none';
  const fb = $('#flagbox');
  fb.className = 'flagbox ' + (['caution', 'one_to_green', 'local_yellow'].includes(shown)
    ? 'yellow' : shown);
  const label = { red: 'RED FLAG', checkered: 'CHECKERED', white: 'WHITE',
    caution: 'CAUTION', one_to_green: 'ONE TO GREEN', yellow: 'YELLOW',
    green: 'GREEN', local_yellow: 'LOCAL YELLOW', none: 'NO FLAG' };
  const local = shown === 'green' && flags.includes('local_yellow')
    ? '<small> · local yellow</small>' : '';
  fb.innerHTML = `<span>${label[shown]}${local}</span>`;
  fb.title = 'iRacing SessionFlags 0x' + ((ss.flags_raw || 0) >>> 0).toString(16)
    + ' = ' + (flags.join(', ') || '-');

  // source badge
  const srcBadge = $('#source-badge');
  if (s.source === 'iracing') {
    srcBadge.textContent = 'iRacing LIVE';
    srcBadge.className = 'badge live';
  } else {
    srcBadge.textContent = 'Simulator';
    srcBadge.className = 'badge';
  }
  updateRunToggle();
}

function updateChatToggle() {
  const c = State.chat;
  const t = $('#chat-toggle');
  if (!c) return;
  t.textContent = c.enabled ? 'iRACING CHAT ON' : 'iRACING CHAT OFF';
  t.className = 'chat-toggle ' + (c.enabled ? 'on' : 'off')
    + (c.available ? '' : ' na');
  t.title = c.available ? '' : 'Only with iRacing running - in this mode the '
    + 'commands are only written to the log';
  const m = $('#chat-method');
  if (document.activeElement !== m) m.value = c.method || 'post';
}

function updateStreamToggle() {
  const s = State.stream;
  if (!s) return;
  const t = $('#stream-toggle');
  t.textContent = s.enabled ? 'STREAM BANNER ON' : 'STREAM BANNER OFF';
  t.className = 'chat-toggle stream ' + (s.enabled ? 'on' : 'off');
  t.title = 'Race Control overlay: ' + (s.banner_url || '-');
}

// ---- Auto Steward (step 3c) ------------------------------------------------
const AUTO_ALERT = ['auto_steward', 'auto_caution_ask', 'auto_caution'];
function announceAuto() {
  let max = State.lastAutoId || 0;
  const fresh = State.events.filter((e) => e.source === 'auto'
    && AUTO_ALERT.includes(e.kind) && e.id > max);
  State.events.forEach((e) => { if (e.id > max) max = e.id; });
  if (State.lastAutoId !== undefined && fresh.length)
    toast((fresh[fresh.length - 1].car_number
      ? '#' + fresh[fresh.length - 1].car_number + ' ' : '')
      + fresh[fresh.length - 1].text);
  State.lastAutoId = max;
}

function updateStewardToggle() {
  const s = State.steward;
  if (!s) return;
  const t = $('#steward-toggle');
  t.textContent = s.enabled ? 'AUTO STEWARD ON' : 'AUTO STEWARD OFF';
  t.className = 'chat-toggle steward ' + (s.enabled ? 'on' : 'off');
  t.title = `Incidents: drive through at ${s.dt_first}x, then every ${s.dt_every}x`
    + ` | caution: ${s.fcy_enabled ? `${s.fcy_incs}x / ${s.fcy_window_s}s (${s.fcy_mode}`
        + `${s.fcy_laps > 0 ? `, ${s.fcy_laps} laps` : ''})` : 'off'}`
    + (s.fcy_short ? ` | short caution: pits +${s.fcy_pit_open_s}s, ${s.fcy_short_laps} lap(s)${s.fcy_lineup_adj ? ` (${s.fcy_lineup_adj > 0 ? '+' : ''}${s.fcy_lineup_adj} when lined up)` : ''}, wave ${s.fcy_wave}` : '')
    + ` | sequencer: ${s.seq_enabled ? (s.sequence || []).length + ' msg' : 'off'}`
    + (s.cb_enabled ? ` | blue flag rule: ${s.cb_gap_s}s, every ${s.cb_every_s}s`
        + (s.cb_dt ? `, DT after a lap (max ${s.cb_dt_max_s}s)` : '')
      : ` | blue flag: warn ${s.bf_warn_s || 'off'}s, DT ${s.bf_dt_s || 'off'}s`)
    + ` | fast repair: ${s.fr_used || 'off'} | meatball: ${s.meatball_enabled ? 'on' : 'off'}`
    + ` | PRO behind AM: ${s.pro_enabled ? `${s.pro_gap_s}s, once` : 'off'}`;
}

const STEWARD_FIELDS = ['dt_first', 'dt_every', 'fcy_enabled', 'fcy_incs',
  'fcy_window_s', 'fcy_mode', 'fcy_cooldown_s', 'fcy_laps', 'seq_enabled',
  'blue_enabled', 'blue_gap_s', 'blue_repeat_s',
  'fcy_end_protect_min', 'bf_warn_s', 'bf_warn_text', 'bf_dt_s', 'bf_dt_text',
  'fr_used', 'fr_text', 'meatball_enabled', 'meatball_text',
  'pro_enabled', 'pro_gap_s', 'pro_from_lap', 'pro_text',
  'cb_enabled', 'cb_gap_s', 'cb_every_s', 'cb_from_lap', 'cb_text', 'cb_dt',
  'cb_dt_max_s', 'cb_dt_text', 'cb_grace_s',
  'fcy_short', 'fcy_pit_open_s', 'fcy_short_laps', 'fcy_wave', 'fcy_lineup_adj'];
function openStewardRules() {
  const s = State.steward || {};
  STEWARD_FIELDS.forEach((k) => {
    const el = $('#s-' + k);
    if (el.type === 'checkbox') el.checked = !!s[k];
    else el.value = s[k] ?? '';
  });
  $('#seq-rows').innerHTML = '';
  (s.sequence || []).forEach(addSeqRow);
  syncBlueSections();
  $('#sdlg').classList.remove('hidden');
}
const SEQ_SESSIONS = [['quali', 'Qualifying'], ['race', 'Race'],
  ['practice', 'Practice'], ['warmup', 'Warmup'], ['any', 'Any session']];
function fmtSeqTime(sec) {
  sec = Math.max(0, Math.round(+sec || 0));
  const p = (n) => String(n).padStart(2, '0');
  return `${p(Math.floor(sec / 3600))}:${p(Math.floor(sec / 60) % 60)}:${p(sec % 60)}`;
}
function addSeqRow(st) {
  st = st || { session: 'quali', at: 0, text: '' };
  const row = document.createElement('div');
  row.className = 'seq-row';
  row.innerHTML = `<select class="seq-s">${SEQ_SESSIONS.map(([v, l]) =>
    `<option value="${v}">${l}</option>`).join('')}</select>`
    + '<input class="seq-t" type="text" maxlength="8" placeholder="00:15:00">'
    + '<input class="seq-x" type="text" maxlength="200" placeholder="message">'
    + '<button type="button" class="seq-del" title="Remove">&times;</button>';
  row.querySelector('.seq-s').value = st.session;
  row.querySelector('.seq-t').value = fmtSeqTime(st.at);
  row.querySelector('.seq-x').value = st.text;
  row.querySelector('.seq-del').addEventListener('click', () => row.remove());
  $('#seq-rows').appendChild(row);
}
// the blue flag rule (multiclass + lapping) replaces these two while ON
function syncBlueSections() {
  const on = $('#s-cb_enabled').checked;
  ['#fs-bf', '#fs-blue'].forEach((id) => { $(id).disabled = on; });
}
function saveStewardRules(ok) {
  $('#sdlg').classList.add('hidden');
  if (!ok) return;
  const msg = { action: 'set_steward' };
  STEWARD_FIELDS.forEach((k) => {
    const el = $('#s-' + k);
    msg[k] = el.type === 'checkbox' ? el.checked
      : (el.type === 'number' ? parseFloat(el.value) : el.value);
  });
  msg.sequence = [...document.querySelectorAll('#seq-rows .seq-row')].map((r) => ({
    session: r.querySelector('.seq-s').value,
    at: r.querySelector('.seq-t').value.trim(),
    text: r.querySelector('.seq-x').value.trim(),
  })).filter((st) => st.at && st.text);
  send(msg);
}

async function askStreamUrl() {
  const r = await askReason({
    title: 'Stream banner address',
    label: 'Race Control overlay (5019) on the stream PC, e.g. http://192.168.86.138:5019',
    value: (State.stream && State.stream.banner_url) || 'http://localhost:5019',
    required: true,
  });
  if (r) send({ action: 'set_stream', banner_url: r.text });
  return r;
}

function updateRunToggle() {
  const t = $('#run-toggle');
  t.textContent = State.running ? 'RUN' : 'STOP';
  t.className = 'run-toggle ' + (State.running ? 'run' : 'stop');
}

// ---- timing table ---------------------------------------------------------
// Sector columns S1..Sn before "Last" - as many as the track has.
function syncSectorHead(n) {
  if (State.sectorCount === n) return;
  State.sectorCount = n;
  document.querySelectorAll('th.c-sec').forEach((th) => th.remove());
  const last = document.querySelector('th.c-last');
  for (let i = 1; i <= n; i++) {
    const th = document.createElement('th');
    th.className = 'c-sec';
    th.textContent = `S${i}`;
    last.parentNode.insertBefore(th, last);
  }
}

function sectorCells(c, n) {
  let out = '';
  for (let i = 0; i < n; i++) {
    const v = (c.sectors || [])[i];
    const cls = v && v[1] === 'overall' ? ' class="best-overall"'
      : v && v[1] === 'personal' ? ' class="best-class"' : '';
    out += `<td class="c-sec"><span${cls}>${v ? v[0].toFixed(2) : ''}</span></td>`;
  }
  return out;
}

function renderTiming(s) {
  const body = $('#timing-body');
  const nSec = (s.session && s.session.sector_count) || 0;
  syncSectorHead(nSec);
  const rows = s.cars.map((c) => {
    const sel = c.car_idx === State.selectedCar ? ' selected' : '';
    const pit = c.on_pit_road ? ' pit' : '';
    const fin = c.finished ? ' finished' : '';
    let flagCls = '';
    if (c.finished) flagCls = 'checkered';
    else if (c.off_track) flagCls = 'off';

    const name = State.showTeam ? c.team : c.driver;
    const lastCls = (c.best_lap > 0 && c.last_lap === c.best_lap)
      ? ' class="lap-fast"' : '';
    let bestCls = '';
    if (c.best_overall) bestCls = ' class="best-overall"';
    else if (c.best_in_class) bestCls = ' class="best-class"';

    const incCls = c.unresolved > 0 ? 'inc-unresolved' : 'inc-ok';
    const gapCls = (!State.showInterval && c.laps_down > 0)
      ? ' class="lapped"' : '';
    const dsq = c.dsq ? ' dsq' : '';

    return `<tr class="row${sel}${pit}${fin}" data-idx="${c.car_idx}">
      <td class="c-flag"><div class="flag-cell ${flagCls}"></div></td>
      <td class="c-pos${dsq}">${c.dsq ? 'DSQ' : c.position}</td>
      <td class="c-num">${c.number}</td>
      <td class="c-name" title="${esc(c.driver)} – ${esc(c.team)}">${
        c.brand_logo ? `<img class="brand-logo" src="${c.brand_logo}" alt="">`
                     : ''}${esc(name)}</td>
      <td class="c-cls"><span class="cls-pill"
          style="background:${c.class_color}">${esc(c.class_short)}</span></td>
      <td class="c-cpos">${c.class_position || '—'}</td>
      <td class="c-lap">${c.laps_completed}</td>
      ${sectorCells(c, nSec)}<td class="c-last"><span${lastCls}>${fmtLap(c.last_lap)}</span></td>
      <td class="c-best"><span${bestCls}>${fmtLap(c.best_lap)}</span></td>
      <td class="c-gap"><span${gapCls}>${fmtGap(c)}</span></td>
      <td class="c-inc" title="${c.unresolved > 0
        ? c.unresolved + ' open in the log (no decision yet)' : ''}"><span class="${incCls}">${
        c.incidents}x${c.unresolved > 0 ? ` <small>(${c.unresolved})</small>` : ''}</span></td>
      <td class="c-pit">${c.pit_stops}</td>
      <td class="c-car" title="${esc(c.car_model || '')}">${esc(c.car_model || '')}</td>
      <td class="c-spd ${State.showSpeed ? '' : 'off'}">${
        Math.round((c.speed_ms || 0) * 3.6)}</td>
    </tr>`;
  });
  body.innerHTML = rows.join('');
}

// ---- selected car panel ---------------------------------------------------
function renderSelected(s) {
  const car = s.cars.find((c) => c.car_idx === State.selectedCar);
  // weather always available
  const w = s.weather || {};
  $('#w-air').textContent = (w.air_temp ?? 0).toFixed(1) + ' °C';
  $('#w-track').textContent = (w.track_temp ?? 0).toFixed(1) + ' °C';
  $('#w-hum').textContent = Math.round((w.humidity ?? 0) * 100) + ' %';
  $('#w-wind').textContent = (w.wind_ms ?? 0).toFixed(1) + ' m/s';
  $('#w-sky').textContent = w.skies || '—';
  $('#w-wet').textContent = w.track_wetness || '—';

  if (!car) {
    $('#sel-empty').classList.remove('hidden');
    $('#sel-detail').classList.add('hidden');
    return;
  }
  $('#sel-empty').classList.add('hidden');
  $('#sel-detail').classList.remove('hidden');
  $('#sel-num').textContent = car.number;
  $('#sel-driver').textContent = car.driver;
  $('#sel-team').textContent = car.team;
  const cls = $('#sel-class');
  cls.textContent = car.class_short;
  cls.style.background = car.class_color;
  cls.style.color = '#000';
  const brandImg = $('#sel-brand');
  if (car.brand_logo) {
    brandImg.src = car.brand_logo;
    brandImg.title = car.brand || '';
    brandImg.classList.remove('hidden');
  } else {
    brandImg.classList.add('hidden');
  }
  $('#sel-pos').textContent = car.dsq ? 'DSQ' : car.position;
  $('#sel-lap').textContent = car.laps_completed;
  $('#sel-last').textContent = fmtLap(car.last_lap);
  $('#sel-best').textContent = fmtLap(car.best_lap);
  $('#sel-gap').textContent = car.position === 1 ? 'Leader' : fmtGap(car);
  $('#sel-pits').textContent = car.pit_stops;
  $('#sel-inc').textContent = car.incidents + 'x'
    + (car.unresolved ? ` (${car.unresolved}!)` : '');
  $('#sel-niw').textContent = car.niw;
  $('#sel-pen').textContent = car.time_penalty
    ? car.time_penalty.toFixed(0) + 's' : '—';
}

function renderCarLog() {
  const wrap = $('#car-log');
  if (!wrap) return;
  if (State.selectedCar === null) { wrap.innerHTML = ''; return; }
  const evs = State.events
    .filter((e) => e.car_idx === State.selectedCar)
    .slice(-14).reverse();
  wrap.innerHTML = evs.length
    ? evs.map((e) => logRowHtml(e, true)).join('')
    : '<div class="sel-empty" style="padding:10px 0">No events.</div>';
}

// ---- race log -------------------------------------------------------------
function renderFilters() {
  const cats = [
    ['incident', 'Incidents'], ['offtrack', 'Off-track'], ['pit', 'Pit'],
    ['flag', 'Flags'], ['penalty', 'Penalties'], ['message', 'Messages'],
    ['info', 'Info'],
  ];
  $('#log-filters').innerHTML = cats.map(([c, label]) =>
    `<button class="filter-btn ${c} ${State.filters.has(c) ? 'on' : ''}"
       data-filter="${c}">${label}</button>`).join('');
}

function logRowHtml(e, compact) {
  const sel = (e.id === State.selectedEvent || e.id === State.replayEvent)
    ? ' selected' : '';
  const resolved = e.resolved ? ' resolved' : '';
  let badge = '';
  if (e.is_incident && e.source === 'detected' && !e.resolved)
    badge += '<span class="log-badge det" title="Detected from car motion - no iRacing points (yet)">DET</span>';
  if (e.is_incident && e.inc_points > 0)
    badge += `<span class="log-badge pts">${e.inc_points}x</span>`;
  if (e.resolved) badge += `<span class="log-badge done">${
    esc(e.resolution || 'DONE')}</span>`;
  else if (e.investigating) badge += '<span class="log-badge inv">INV</span>';
  else if (e.noted) badge += '<span class="log-badge noted">NOTED</span>';
  if (e.claimed_by && !e.resolved)
    badge += `<span class="log-badge claim" title="${esc(e.claimed_by)} is looking at it">&#128064; ${esc(e.claimed_by)}</span>`;
  const who = e.decided_by || e.by;
  if (who) badge += `<span class="log-by" title="${e.decided_by ? 'decided' : 'done'} by ${esc(who)}">&middot; ${esc(who)}</span>`;
  if (e.kind === 'auto_caution_ask' && !(State.deployed || new Set()).has(e.id)
      && (State.snapshot?.session?.time ?? 0) - e.sim_time < 120)
    badge += `<button class="log-act" data-deploy="${e.id}" title="Sends !yellow">DEPLOY CAUTION</button>`;
  const auto = e.source === 'auto' ? ` auto ${esc(e.kind)}` : '';
  const num = e.car_number ? `#${e.car_number} ` : '';
  return `<div class="log-row ${e.category}${sel}${resolved}${auto}"
       data-event="${e.id}" data-car="${e.car_idx}">
    <span class="log-time">${fmtClock(e.sim_time, false)}</span>
    <span class="log-cat ${e.category}"></span>
    ${compact ? '' : `<span class="log-lap">L${e.lap}</span>`}
    <span class="log-text">${esc(num + e.text)}</span>
    ${badge}
  </div>`;
}

function renderLog() {
  const list = $('#log-list');
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  const visible = State.events.filter((e) => State.filters.has(e.category));
  list.innerHTML = visible.map((e) => logRowHtml(e, false)).join('');
  if (atBottom) list.scrollTop = list.scrollHeight;
}

// ---- right-click menu, reason dialog, car commands ------------------------
const PENALTY_REASONS = ['Track Limits', 'Forcing another driver off the track',
                         'Causing a collision'];
const CAR_DIALOG = {
  drive_through: { title: 'Drive through penalty',
                   label: 'Reason for drive through:', presets: PENALTY_REASONS },
  stop_go: { title: 'Stop and go penalty', label: 'Reason for stop and go:',
             presets: PENALTY_REASONS },
  time_penalty: { title: 'Time penalty', label: 'Reason for the time penalty:',
                  presets: PENALTY_REASONS, seconds: true },
  dsq: { title: 'Disqualification', label: 'Reason for the DQ:',
         presets: PENALTY_REASONS.concat(['Unsportsmanlike conduct']) },
  notify: { title: 'Notify driver', label: 'Message to the driver:',
            required: true,
            presets: ['Watch your track limits', 'Blue flag - let the faster car through',
                      'Rejoin the track safely'] },
  remove: { title: 'Remove from the session',
            label: 'Reason (the driver is removed for good):',
            presets: ['Unsportsmanlike conduct'] },
};
const RES_DIALOG = {
  'DRIVE THROUGH': CAR_DIALOG.drive_through,
  'STOP/GO': CAR_DIALOG.stop_go,
  'TIME PENALTY': CAR_DIALOG.time_penalty,
  DSQ: CAR_DIALOG.dsq,
  WARNING: { title: 'Warning', label: 'Reason for the warning:',
             presets: PENALTY_REASONS },
};

let dlgResolve = null;
function askReason(opts) {
  $('#dlg-title').textContent = opts.title;
  $('#dlg-label').textContent = opts.label;
  $('#dlg-text').value = opts.value || '';
  $('#dlg-secs-row').classList.toggle('hidden', !opts.seconds);
  $('#dlg-secs').value = '5';
  const pre = $('#dlg-presets');
  pre.innerHTML = '';
  (opts.presets || []).forEach((p) => {
    const b = document.createElement('button');
    b.textContent = p;
    b.addEventListener('click', () => { $('#dlg-text').value = p; closeDialog(true); });
    pre.appendChild(b);
  });
  State.dlgRequired = !!opts.required;
  $('#dlg').classList.remove('hidden');
  setTimeout(() => $('#dlg-text').focus(), 0);
  return new Promise((res) => { dlgResolve = res; });
}

function closeDialog(ok) {
  const text = $('#dlg-text').value.trim();
  if (ok && State.dlgRequired && !text) { $('#dlg-text').focus(); return; }
  $('#dlg').classList.add('hidden');
  const r = dlgResolve; dlgResolve = null;
  if (r) r(ok ? { text, seconds: parseFloat($('#dlg-secs').value) || 0 } : null);
}

function openCtxMenu(idx, x, y) {
  const car = State.snapshot?.cars.find((c) => c.car_idx === idx);
  if (!car) return;
  State.ctxCar = idx;
  const m = $('#ctx-menu');
  $('#ctx-title').textContent = `#${car.number}  ${car.driver}`;
  m.classList.remove('hidden', 'flip');
  const w = m.offsetWidth, h = m.offsetHeight;
  const left = Math.min(x, window.innerWidth - w - 4);
  m.style.left = left + 'px';
  m.style.top = Math.max(4, Math.min(y, window.innerHeight - h - 4)) + 'px';
  if (left + w + 190 > window.innerWidth) m.classList.add('flip');
}

function closeCtxMenu() { $('#ctx-menu').classList.add('hidden'); }

/* One steward action on a car: the dialog (reason / message) where the
   command needs one, then to the server (which types it into iRacing). */
async function carCommand(idx, cmd) {
  if (idx === null || idx === undefined) { toast('Select a car first'); return; }
  let message = '', seconds = 0;
  const dlg = CAR_DIALOG[cmd];
  if (dlg) {
    const r = await askReason(dlg);
    if (!r) return;
    message = r.text; seconds = r.seconds;
    if (cmd === 'remove' && !confirm('Remove this driver from the session '
        + 'for good?')) return;
  }
  send({ action: 'car', car_idx: idx, command: cmd, message, seconds });
}

// ---- selection ------------------------------------------------------------
// Second press on the same row within 400 ms = double click.
const lastPress = { where: '', key: null, t: 0 };
function isDoublePress(where, key) {
  const now = performance.now();
  const dbl = lastPress.where === where && lastPress.key === key
    && now - lastPress.t < 400;
  lastPress.where = where; lastPress.key = key; lastPress.t = dbl ? 0 : now;
  return dbl;
}

function selectCar(idx) {
  State.selectedCar = (State.selectedCar === idx) ? null : idx;
  if (State.snapshot) render();
  renderCarLog();
  renderLog();
}

function selectEvent(id) {
  const ev = State.events.find((e) => e.id === id);
  State.replayEvent = ev ? id : null;
  if (ev && ev.is_incident && !ev.resolved) {
    State.selectedEvent = (State.selectedEvent === id) ? null : id;
  } else {
    State.selectedEvent = null;
  }
  updateIncidentActions();
  renderLog();
  renderCarLog();
}

function replay(id, preroll) {
  if (id === null || id === undefined) { toast('Click a log line first'); return; }
  send({ action: 'replay', id, preroll });
}

function updateIncidentActions() {
  const head = $('#ia-head');
  const canReplay = State.events.some((e) => e.id === State.replayEvent);
  $('#ia-replay').disabled = !canReplay;
  $('#ia-replay-long').disabled = !canReplay;
  $('#ia-replay').title = canReplay
    ? 'Replay from 8 s before (double-click a log line does the same)'
    : 'Select a line in the race log first (incident or any event)';
  $('#ia-replay-long').title = canReplay
    ? 'Replay from 20 s before' : 'Select a line in the race log first';
  const ev = State.events.find((e) => e.id === State.selectedEvent);
  const buttons = $$('#incident-actions .ia-grid button');
  if (!ev) {
    $('#ia-claim').disabled = true;
    $('#ia-claim').textContent = 'I take it';
    head.textContent = 'No incident selected';
    head.classList.remove('active');
    buttons.forEach((b) => (b.disabled = true));
    return;
  }
  head.textContent = `Incident #${ev.id} – car #${ev.car_number} – L${ev.lap}`;
  head.classList.add('active');
  buttons.forEach((b) => (b.disabled = false));
  const claim = $('#ia-claim');
  const mine = ev.claimed_by && ev.claimed_by === Me.name;
  claim.disabled = !!ev.resolved || (!!ev.claimed_by && !mine);
  claim.classList.toggle('mine', !!mine);
  claim.textContent = mine ? 'Release' : ev.claimed_by
    ? '\u{1F440} ' + ev.claimed_by : 'I take it';
}

// ---- camera (switches the iRacing of the main iCAS PC) ---------------------
let camSig = '';
function renderCamera(s) {
  const cam = s.camera;
  const box = $('#cam-groups');
  if ($('#tab-camera').classList.contains('hidden')) return;
  if (!cam || !(cam.groups || []).length) {
    if (camSig !== 'none') {
      box.innerHTML = '<span class="dim">no camera groups (iRacing not connected?)</span>';
      camSig = 'none';
    }
    $('#cam-car').textContent = '—';
    return;
  }
  const sig = cam.groups.map((g) => g.num + g.name).join('|');
  if (sig !== camSig) {           // rebuilt only when the list changes
    box.innerHTML = cam.groups.map((g) =>
      `<button data-group="${g.num}">${esc(g.name)}</button>`).join('');
    camSig = sig;
  }
  $$('#cam-groups button').forEach((b) =>
    b.classList.toggle('on', +b.dataset.group === cam.group));
  const car = (s.cars || []).find((c) => c.car_idx === cam.car_idx);
  $('#cam-car').textContent = car ? `#${car.number} ${car.driver}`
    : (cam.car_number ? '#' + cam.car_number : '—');
}

// ---- stewards: who is online, steward chat ---------------------------------
const Stw = { lastId: null, unread: 0, data: null };
function onStewards(msg) {
  Stw.data = msg;
  const online = msg.online || [];
  $('#stw-count').textContent = online.length;
  $('#stw-online').innerHTML = online.map((o) =>
    `<span class="${o.name === Me.name ? 'me' : ''}">${esc(o.name)}<small>${
      o.place === 'local' ? 'host' : o.place === 'lan' ? 'LAN' : 'online'}</small></span>`).join('');
  const chat = msg.chat || [];
  const open = !$('#stw-panel').classList.contains('hidden');
  if (Stw.lastId !== null) {
    const fresh = chat.filter((l) => l.id > Stw.lastId && l.name !== Me.name);
    if (fresh.length && !open) {
      Stw.unread += fresh.length;
      const l = fresh[fresh.length - 1];
      toast(`💬 ${l.name}: ${l.text}`);
    }
  }
  Stw.lastId = chat.length ? chat[chat.length - 1].id : (Stw.lastId ?? 0);
  updateUnread();
  const list = $('#stw-chat');
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.innerHTML = chat.map((l) => `<div class="stw-line${l.name === Me.name ? ' me' : ''}">
    <time>${new Date(l.t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time><b>${esc(l.name)}</b>${esc(l.text)}</div>`).join('')
    || '<span class="dim">No messages yet.</span>';
  if (atBottom || !open) list.scrollTop = list.scrollHeight;
  $('#stw-pw-state').textContent = msg.password
    ? 'password set' : 'no password - LAN only';
}
// ---- internet access (tunnel) + follow a main iCAS ---------------------------
const TUN_TEXT = { off: 'off', starting: 'starting…', on: 'ON', error: 'error',
  downloading: 'downloading cloudflared…' };
function renderNet(net) {
  if (!net) return;
  const t = net.tunnel || {}, f = net.follow || {};
  const ts = $('#tun-state');
  ts.textContent = t.state === 'error' ? (t.error || 'error')
    : t.state === 'off' && !net.password ? 'off - set an access password first'
    : (TUN_TEXT[t.state] || t.state || 'off') + (t.named && t.state === 'on' ? ' (fixed address)' : '');
  ts.className = t.state === 'on' ? 'good' : t.state === 'error' ? 'bad'
    : t.state === 'starting' || t.state === 'downloading' ? 'warn' : 'dim';
  ts.title = t.error || '';
  const running = t.state === 'on' || t.state === 'starting';
  $('#tun-btn').textContent = running ? 'Stop' : 'Start';
  $('#tun-btn').disabled = t.state === 'downloading' || f.state === 'connected';
  $('#tun-dl').classList.toggle('hidden', !!t.binary || t.state === 'downloading');
  const following = f.state === 'connected' || f.state === 'connecting';
  $('#tun-line').classList.toggle('hidden', following);
  $('#tun-row').classList.toggle('hidden', !t.url || !running || following);
  $('#tun-url').textContent = t.url || '';
  const fs = $('#fol-state');
  const since = f.since ? ` · ${Math.floor(f.since / 60)} min` : '';
  fs.textContent = f.state === 'connected'
    ? `as ${f.as || f.name} → ${f.url}${since}` + (f.warning ? ' ⚠ ' + f.warning : '')
    : f.state === 'connecting' ? `connecting to ${f.url}… ${f.info || ''}`
    : f.state === 'refused' || f.state === 'error' ? (f.info || f.state)
    : 'off - own data';
  fs.className = f.state === 'connected' ? (f.warning ? 'warn' : 'good')
    : f.state === 'connecting' ? 'warn'
    : f.state === 'refused' || f.state === 'error' ? 'bad' : 'dim';
  fs.title = fs.textContent;
  $('#fol-btn').textContent = f.state === 'off' || f.state === 'refused'
    || f.state === 'error' ? 'Follow main iCAS…' : 'Stop following';
  Me.net = net;
}
function openFollow() {
  const f = (Me.net && Me.net.follow) || {};
  $('#fol-url').value = f.url || '';
  $('#fol-name').value = f.name || (Me.name !== 'Host' ? Me.name : '');
  $('#fol-pw').value = '';
  $('#follow-dlg').classList.remove('hidden');
  setTimeout(() => $(f.url ? '#fol-pw' : '#fol-url').focus(), 30);
}
function doFollow() {
  const url = $('#fol-url').value.trim(), name = $('#fol-name').value.trim();
  if (!url) { $('#fol-url').focus(); return; }
  if (!name) { $('#fol-name').focus(); return; }
  send({ action: 'follow', op: 'start', url, name, password: $('#fol-pw').value });
  $('#follow-dlg').classList.add('hidden');
}

function updateUnread() {
  const el = $('#stw-unread');
  el.textContent = Stw.unread;
  el.classList.toggle('hidden', !Stw.unread);
}
function openLogin(error) {
  Me.loginOpen = true;
  $('#login-name').value = Me.name && Me.name !== 'Steward' ? Me.name : '';
  $('#login-pw').value = '';
  $('#login-err').textContent = error ? 'Main iCAS: ' + error : '';
  $('#login').classList.remove('hidden');
  setTimeout(() => $(Me.name ? '#login-pw' : '#login-name').focus(), 30);
}
function doLogin() {
  const name = $('#login-name').value.trim();
  if (!name) { $('#login-name').focus(); return; }
  Me.name = name; store('local', 'icas_name', name);
  const pw = $('#login-pw').value;
  if (pw || Me.place !== 'local') { Me.pw = pw; store('session', 'icas_pw', pw); }
  Me.loginOpen = false;
  $('#login').classList.add('hidden');
  const old = State.ws;
  State.ws = null;
  if (old) old.close();
  connect();
}

// ---- misc -----------------------------------------------------------------
function esc(t) {
  return String(t).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ---- event wiring ---------------------------------------------------------
function wire() {
  // stewards panel / chat / login
  $('#stw-toggle').addEventListener('click', () => {
    const p = $('#stw-panel');
    p.classList.toggle('hidden');
    if (!p.classList.contains('hidden')) {
      Stw.unread = 0; updateUnread();
      const list = $('#stw-chat'); list.scrollTop = list.scrollHeight;
      $('#stw-text').focus();
    }
  });
  $('#stw-x').addEventListener('click', () => $('#stw-panel').classList.add('hidden'));
  $('#stw-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = $('#stw-text').value.trim();
    if (!text) return;
    send({ action: 'steward_chat', text });
    $('#stw-text').value = '';
  });
  $('#stw-name').addEventListener('click', () => openLogin(''));
  $('#stw-pw').addEventListener('click', () => {
    const pw = prompt('Access password for the other stewards (at least 6 '
      + 'characters). Empty = remove it (then only this PC and the home network '
      + 'can connect).');
    if (pw === null) return;
    send({ action: 'set_password', password: pw });
  });
  $('#login-ok').addEventListener('click', doLogin);
  $('#tun-btn').addEventListener('click', () => {
    const t = (Me.net && Me.net.tunnel) || {};
    if (t.state === 'on' || t.state === 'starting') {
      if (confirm('Switch internet access off? The other stewards lose the connection.'))
        send({ action: 'tunnel', op: 'stop' });
    } else send({ action: 'tunnel', op: 'start' });
  });
  $('#tun-dl').addEventListener('click', () => send({ action: 'tunnel', op: 'download' }));
  $('#tun-copy').addEventListener('click', () => {
    const url = $('#tun-url').textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject())
      .then(() => toast('Link copied - send it to the stewards'),
        () => toast(url));
  });
  $('#fol-btn').addEventListener('click', () => {
    const f = (Me.net && Me.net.follow) || {};
    if (f.state === 'connected' || f.state === 'connecting') {
      send({ action: 'follow', op: 'stop' });
    } else openFollow();
  });
  $('#fol-x').addEventListener('click', () => $('#follow-dlg').classList.add('hidden'));
  $('#fol-ok').addEventListener('click', doFollow);
  $$('#follow-dlg input').forEach((i) => i.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doFollow();
  }));
  $$('#login input').forEach((i) => i.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doLogin();
  }));
  $('#ia-claim').addEventListener('click', () => {
    const ev = State.events.find((x) => x.id === State.selectedEvent);
    if (!ev) return;
    send({ action: 'claim', id: ev.id, on: ev.claimed_by !== Me.name });
  });
  // camera tab
  $('#cam-groups').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-group]');
    if (b) send({ action: 'camera', op: 'group', group: +b.dataset.group });
  });
  $('#cam-prev').addEventListener('click', () => send({ action: 'camera', op: 'prev' }));
  $('#cam-next').addEventListener('click', () => send({ action: 'camera', op: 'next' }));
  $('#cam-sel').addEventListener('click', () => {
    if (State.selectedCar === null) { toast('Select a car in the timing list first'); return; }
    send({ action: 'camera', op: 'car', car_idx: State.selectedCar });
  });

  // chat to iRacing (step 2)
  $('#chat-toggle').addEventListener('click', () => {
    const on = !(State.chat && State.chat.enabled);
    if (on && !confirm('Send steward commands (penalties, yellow, pit '
        + 'close ...) into the iRacing chat?\n\nThis PC\'s iRacing account '
        + 'must be ADMIN of the session.')) return;
    send({ action: 'set_chat', enabled: on, method: $('#chat-method').value });
  });
  $('#chat-method').addEventListener('change', () => {
    send({ action: 'set_chat', enabled: !!(State.chat && State.chat.enabled),
           method: $('#chat-method').value });
  });
  $('#chat-test').addEventListener('click', () =>
    send({ action: 'chat_test' }));

  // decisions to the stream banner (step 3)
  $('#stream-toggle').addEventListener('click', () => {
    const on = !(State.stream && State.stream.enabled);
    send({ action: 'set_stream', enabled: on });
  });
  $('#stream-url').addEventListener('click', askStreamUrl);

  // Auto Steward (step 3c)
  $('#steward-toggle').addEventListener('click', () =>
    send({ action: 'set_steward', enabled: !(State.steward && State.steward.enabled) }));
  $('#steward-rules').addEventListener('click', openStewardRules);
  $('#s-cb_enabled').addEventListener('change', syncBlueSections);
  $('#sdlg-ok').addEventListener('click', () => saveStewardRules(true));
  $('#seq-add').addEventListener('click', () => addSeqRow());
  $('#sdlg-cancel').addEventListener('click', () => saveStewardRules(false));
  $('#sdlg-x').addEventListener('click', () => saveStewardRules(false));
  $('#stream-test').addEventListener('click', () =>
    send({ action: 'stream_test' }));

  // RUN / STOP
  $('#run-toggle').addEventListener('click', () => {
    State.running = !State.running;
    updateRunToggle();
    send({ action: 'set_running', running: State.running });
  });

  // timing rows - on PRESS, not click: the table is rebuilt ~11x a second,
  // and a click whose press and release land on different (rebuilt) rows
  // never fires (2026-10-27: "need several clicks, often nothing").  The
  // double press is detected here for the same reason.
  $('#timing-body').addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const tr = e.target.closest('tr');
    if (!tr) return;
    const idx = parseInt(tr.dataset.idx, 10);
    if (isDoublePress('timing', idx)) {
      State.selectedCar = idx;
      if (State.snapshot) render();
      renderCarLog(); renderLog();
      const car = State.snapshot?.cars.find((c) => c.car_idx === idx);
      if (car) send({ action: 'command', command: 'cam_car',
        params: { number: car.number } });
      return;
    }
    selectCar(idx);
  });

  // timing toggles
  $('#tg-name').addEventListener('click', (e) => {
    State.showTeam = !State.showTeam;
    e.target.textContent = State.showTeam ? 'TEAM' : 'DRVR';
    if (State.snapshot) renderTiming(State.snapshot);
  });
  $('#tg-gap').addEventListener('click', (e) => {
    State.showInterval = !State.showInterval;
    e.target.textContent = State.showInterval ? 'INT' : 'GAP';
    $('#th-gap').textContent = State.showInterval ? 'Int' : 'Gap';
    if (State.snapshot) renderTiming(State.snapshot);
  });
  $('#tg-speed').addEventListener('click', (e) => {
    State.showSpeed = !State.showSpeed;
    e.target.classList.toggle('off', !State.showSpeed);
    $('#th-spd').classList.toggle('off', !State.showSpeed);
    if (State.snapshot) renderTiming(State.snapshot);
  });

  // tabs
  $$('.tab-btn').forEach((b) => b.addEventListener('click', () => {
    $$('.tab-btn').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    $('#tab-car').classList.toggle('hidden', b.dataset.tab !== 'car');
    $('#tab-weather').classList.toggle('hidden', b.dataset.tab !== 'weather');
    $('#tab-camera').classList.toggle('hidden', b.dataset.tab !== 'camera');
    if (State.snapshot) renderCamera(State.snapshot);
  }));

  // log filters
  $('#log-filters').addEventListener('click', (e) => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    const f = btn.dataset.filter;
    State.filters.has(f) ? State.filters.delete(f) : State.filters.add(f);
    btn.classList.toggle('on');
    renderLog();
  });

  // log rows (rebuilt on every new line - same press handling)
  $('#log-list').addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const dep = e.target.closest('[data-deploy]');
    if (dep) {
      e.stopPropagation();
      const id = parseInt(dep.dataset.deploy, 10);
      if (!confirm('Deploy a FULL COURSE CAUTION now (!yellow)?')) return;
      (State.deployed = State.deployed || new Set()).add(id);
      send({ action: 'command', command: 'pace_deploy', auto: true });
      renderLog();
      return;
    }
    const row = e.target.closest('.log-row');
    if (!row) return;
    const id = parseInt(row.dataset.event, 10);
    const car = parseInt(row.dataset.car, 10);
    if (car >= 0) { State.selectedCar = car; if (State.snapshot) render(); }
    selectEvent(id);
    if (isDoublePress('log', id)) replay(id, 8);
  });
  $('#ia-replay').addEventListener('click', () => replay(State.replayEvent, 8));
  $('#ia-replay-long').addEventListener('click', () => replay(State.replayEvent, 20));
  const goLive = () => send({ action: 'command', command: 'replay_live' });
  $('#ia-live').addEventListener('click', goLive);
  $('#replay-banner').addEventListener('click', goLive);
  $('#car-log').addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const row = e.target.closest('.log-row');
    if (row) selectEvent(parseInt(row.dataset.event, 10));
  });

  // race-control command buttons
  $$('.command-grid .cmd').forEach((b) => b.addEventListener('click', () => {
    const cmd = b.dataset.cmd;
    if (cmd === 'red_flag' && !confirm('Post a RED FLAG?')) return;
    const ask = { pace_deploy: 'Full course yellow (!yellow)?',
                  pit_close: 'Close the pit entry (!pitclose)?',
                  clear_all: 'Clear ALL penalties of the field (!clearall)?' };
    if (ask[cmd] && State.chat && State.chat.enabled && !confirm(ask[cmd]))
      return;
    send({ action: 'command', command: cmd });
  }));

  // caution length (!pacelaps): exact number or one lap more / less
  const paceLaps = (n) => send({ action: 'command', command: 'pace_laps', n });
  $('#pace-laps-set').addEventListener('click', () => {
    const n = parseInt($('#pace-laps-n').value, 10);
    if (n >= 1 && n <= 20) paceLaps(String(n));
  });
  $$('.pace-laps [data-laps]').forEach((b) =>
    b.addEventListener('click', () => paceLaps(b.dataset.laps)));

  // incident resolution buttons
  $$('#incident-actions .ia-grid button').forEach((b) =>
    b.addEventListener('click', async () => {
      if (State.selectedEvent === null) return;
      const evId = State.selectedEvent;
      const res = b.dataset.res;
      let seconds = 0;
      let message = '';
      const dlg = RES_DIALOG[res];
      if (dlg) {                          // reason (+ seconds) = the confirmation
        const r = await askReason(dlg);
        if (!r) return;
        message = r.text; seconds = r.seconds;
      }
      send({ action: 'resolve', id: evId,
             resolution: res, message, seconds });
      // optimistic: drop selection if final
      if (!['NOTED', 'UNDER INVESTIGATION'].includes(res)) {
        State.selectedEvent = null;
        updateIncidentActions();
      }
    }));

  // car action buttons
  $$('.sel-actions button').forEach((b) =>
    b.addEventListener('click', () => {
      if (State.selectedCar === null) { toast('Select a car first'); return; }
      const cmd = b.dataset.carCmd;
      carCommand(State.selectedCar, cmd);
    }));

  // right-click menu on a timing row.  The row is taken on the right PRESS
  // (rows are rebuilt ~11x a second, the contextmenu event can land on a
  // row that is already gone).
  let ctxPress = { idx: null, t: 0 };
  $('#timing-body').addEventListener('pointerdown', (e) => {
    if (e.button !== 2) return;
    const tr = e.target.closest('tr');
    if (!tr) return;
    ctxPress = { idx: parseInt(tr.dataset.idx, 10), t: performance.now() };
    State.selectedCar = ctxPress.idx;
    if (State.snapshot) render();
    renderCarLog(); renderLog();
  });
  document.addEventListener('contextmenu', (e) => {
    if (ctxPress.idx === null || performance.now() - ctxPress.t > 1500) return;
    e.preventDefault();
    openCtxMenu(ctxPress.idx, e.clientX, e.clientY);
    ctxPress = { idx: null, t: 0 };
  });
  document.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('#ctx-menu')) closeCtxMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeCtxMenu();
  });
  $$('#ctx-menu button').forEach((b) => b.addEventListener('click', () => {
    const idx = State.ctxCar;
    closeCtxMenu();
    if (b.dataset.ctx === 'info') {
      State.selectedCar = idx;
      $$('.tab-btn').forEach((x) =>
        x.classList.toggle('active', x.dataset.tab === 'car'));
      $('#tab-car').classList.remove('hidden');
      $('#tab-weather').classList.add('hidden');
      $('#tab-camera').classList.add('hidden');
      if (State.snapshot) render();
      return;
    }
    carCommand(idx, b.dataset.ctx);
  }));

  // dialog buttons
  $('#dlg-ok').addEventListener('click', () => closeDialog(true));
  $('#dlg-cancel').addEventListener('click', () => closeDialog(false));
  $('#dlg-cancel-x').addEventListener('click', () => closeDialog(false));
  $('#dlg-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') closeDialog(true);
    if (e.key === 'Escape') closeDialog(false);
  });

  // map dot colours (kept per browser)
  const mapColor = $('#map-color');
  let savedMode = 'car';
  try { savedMode = localStorage.getItem('icas.mapColor') || 'car'; } catch (e) {}
  mapColor.value = savedMode;
  map.setColorMode(mapColor.value);
  mapColor.addEventListener('change', () => {
    map.setColorMode(mapColor.value);
    try { localStorage.setItem('icas.mapColor', mapColor.value); } catch (e) {}
    render();
  });

  // RC message
  const sendMsg = () => {
    const input = $('#rc-input');
    const text = input.value.trim();
    if (!text) return;
    send({ action: 'rc_message', target: $('#rc-target').value,
           text, car_idx: State.selectedCar ?? -1 });
    input.value = '';
  };
  $('#rc-send').addEventListener('click', sendMsg);
  $('#rc-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendMsg();
  });
}

// ---- boot -----------------------------------------------------------------
function boot() {
  renderFilters();
  updateIncidentActions();
  wire();
  connect();
  setInterval(() => {
    $('#real-time').textContent = new Date().toLocaleTimeString();
  }, 1000);
  $('#real-time').textContent = new Date().toLocaleTimeString();
}

boot();
