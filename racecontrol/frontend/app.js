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
function connect() {
  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
  const ws = new WebSocket(url);
  State.ws = ws;

  ws.onopen = () => setBadge('#ws-badge', 'connected', 'good');
  ws.onclose = () => {
    setBadge('#ws-badge', 'reconnecting…', 'bad');
    setTimeout(connect, 1500);
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
      $('#log-count').textContent = msg.total || State.events.length;
      renderLog();
      renderCarLog();
      break;
    case 'snapshot':
      State.snapshot = msg;
      State.running = msg.running;
      render();
      break;
    case 'ack':
      if (msg.text) toast(msg.text);
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
  $('#car-count').textContent = ss.car_count;
  $('#session-type').textContent = ss.type;
  $('#session-state').textContent = ss.state;
  $('#track-name').textContent =
    ss.track + (ss.config ? ' – ' + ss.config : '');

  // flag box - show the most significant active flag
  const flags = ss.flags || [];
  const order = ['red', 'checkered', 'white', 'caution', 'yellow', 'green'];
  let shown = order.find((f) => flags.includes(f)) || 'none';
  const fb = $('#flagbox');
  fb.className = 'flagbox ' + (shown === 'caution' ? 'yellow' : shown);
  const label = { red: 'RED FLAG', checkered: 'CHECKERED', white: 'WHITE',
    caution: 'CAUTION', yellow: 'YELLOW', green: 'GREEN', none: 'NO FLAG' };
  fb.innerHTML = `<span>${label[shown]}</span>`;

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

function updateRunToggle() {
  const t = $('#run-toggle');
  t.textContent = State.running ? 'RUN' : 'STOP';
  t.className = 'run-toggle ' + (State.running ? 'run' : 'stop');
}

// ---- timing table ---------------------------------------------------------
function renderTiming(s) {
  const body = $('#timing-body');
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
      <td class="c-last"><span${lastCls}>${fmtLap(c.last_lap)}</span></td>
      <td class="c-best"><span${bestCls}>${fmtLap(c.best_lap)}</span></td>
      <td class="c-gap"><span${gapCls}>${fmtGap(c)}</span></td>
      <td class="c-inc"><span class="${incCls}">${c.incidents}x</span></td>
      <td class="c-pit">${c.pit_stops}</td>
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
  const sel = e.id === State.selectedEvent ? ' selected' : '';
  const resolved = e.resolved ? ' resolved' : '';
  let badge = '';
  if (e.resolved) badge = `<span class="log-badge done">${
    esc(e.resolution || 'DONE')}</span>`;
  else if (e.investigating) badge = '<span class="log-badge inv">INV</span>';
  else if (e.noted) badge = '<span class="log-badge noted">NOTED</span>';
  const num = e.car_number ? `#${e.car_number} ` : '';
  return `<div class="log-row ${e.category}${sel}${resolved}"
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

// ---- selection ------------------------------------------------------------
function selectCar(idx) {
  State.selectedCar = (State.selectedCar === idx) ? null : idx;
  if (State.snapshot) render();
  renderCarLog();
  renderLog();
}

function selectEvent(id) {
  const ev = State.events.find((e) => e.id === id);
  if (ev && ev.is_incident && !ev.resolved) {
    State.selectedEvent = (State.selectedEvent === id) ? null : id;
  } else {
    State.selectedEvent = null;
  }
  updateIncidentActions();
  renderLog();
  renderCarLog();
}

function updateIncidentActions() {
  const head = $('#ia-head');
  const ev = State.events.find((e) => e.id === State.selectedEvent);
  const buttons = $$('#incident-actions .ia-grid button');
  if (!ev) {
    head.textContent = 'No incident selected';
    head.classList.remove('active');
    buttons.forEach((b) => (b.disabled = true));
    return;
  }
  head.textContent = `Incident #${ev.id} – car #${ev.car_number} – L${ev.lap}`;
  head.classList.add('active');
  buttons.forEach((b) => (b.disabled = false));
}

// ---- misc -----------------------------------------------------------------
function esc(t) {
  return String(t).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

// ---- event wiring ---------------------------------------------------------
function wire() {
  // RUN / STOP
  $('#run-toggle').addEventListener('click', () => {
    State.running = !State.running;
    updateRunToggle();
    send({ action: 'set_running', running: State.running });
  });

  // timing rows
  $('#timing-body').addEventListener('click', (e) => {
    const tr = e.target.closest('tr');
    if (tr) selectCar(parseInt(tr.dataset.idx, 10));
  });
  $('#timing-body').addEventListener('dblclick', (e) => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    const idx = parseInt(tr.dataset.idx, 10);
    const car = State.snapshot?.cars.find((c) => c.car_idx === idx);
    if (car) send({ action: 'command', command: 'cam_car',
      params: { number: car.number } });
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

  // log rows
  $('#log-list').addEventListener('click', (e) => {
    const row = e.target.closest('.log-row');
    if (!row) return;
    const car = parseInt(row.dataset.car, 10);
    if (car >= 0) { State.selectedCar = car; if (State.snapshot) render(); }
    selectEvent(parseInt(row.dataset.event, 10));
  });
  $('#car-log').addEventListener('click', (e) => {
    const row = e.target.closest('.log-row');
    if (row) selectEvent(parseInt(row.dataset.event, 10));
  });

  // race-control command buttons
  $$('.command-grid .cmd').forEach((b) => b.addEventListener('click', () => {
    const cmd = b.dataset.cmd;
    if (cmd === 'red_flag' && !confirm('Post a RED FLAG?')) return;
    send({ action: 'command', command: cmd });
  }));

  // incident resolution buttons
  $$('#incident-actions .ia-grid button').forEach((b) =>
    b.addEventListener('click', () => {
      if (State.selectedEvent === null) return;
      const res = b.dataset.res;
      let seconds = 0;
      if (b.dataset.needsSeconds) {
        const v = prompt('Time penalty in seconds:', '5');
        if (v === null) return;
        seconds = parseFloat(v) || 0;
      }
      if (res === 'DSQ' && !confirm('Disqualify this car?')) return;
      const message = '';
      send({ action: 'resolve', id: State.selectedEvent,
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
      let message = '', seconds = 0;
      if (cmd === 'dsq' && !confirm('Disqualify this car?')) return;
      if (cmd === 'notify') {
        message = prompt('Message to driver:', '') || '';
        if (!message) return;
      }
      send({ action: 'car', car_idx: State.selectedCar,
             command: cmd, message, seconds });
    }));

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
