/* ============================================================
   TrackMap - animated canvas race map.

   Renders the circuit and every car as a numbered roundel.  Cars are
   advanced by dead-reckoning (using their reported speed) between the
   ~11 Hz server snapshots and gently corrected toward the true position,
   so motion looks smooth at 60 fps.
   ============================================================ */
'use strict';

class TrackMap {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.path = null;          // normalised [[x,y],...] closed loop
    this.pit = null;
    this.trackLenM = 5000;
    this.cars = {};            // idx -> {target, display, ...}
    this.selectedIdx = null;
    this.scaledPath = null;
    this.scaledPit = null;
    this._last = performance.now();

    window.addEventListener('resize', () => this.resize());
    this.resize();
    requestAnimationFrame((t) => this._tick(t));
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const r = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = r.width;
    this.h = r.height;
    this._rescale();
  }

  setTrack(path, pit, lengthKm) {
    this.path = (path && path.length > 2) ? path : null;
    this.pit = (pit && pit.length > 1) ? pit : null;
    if (lengthKm) this.trackLenM = lengthKm * 1000;
    this._rescale();
  }

  _rescale() {
    // Uniform fit: scale the path's own bounding box into the canvas with
    // padding, preserving aspect ratio. Works for any track outline.
    const pad = 26;
    const all = [];
    if (this.path) all.push(...this.path);
    if (this.pit) all.push(...this.pit);
    if (!all.length || !this.w) {
      this.scaledPath = this.scaledPit = null;
      return;
    }
    let minx = 1e9, miny = 1e9, maxx = -1e9, maxy = -1e9;
    for (const [x, y] of all) {
      if (x < minx) minx = x; if (x > maxx) maxx = x;
      if (y < miny) miny = y; if (y > maxy) maxy = y;
    }
    const cw = Math.max(1e-6, maxx - minx);
    const ch = Math.max(1e-6, maxy - miny);
    const s = Math.min((this.w - 2 * pad) / cw, (this.h - 2 * pad) / ch);
    const ox = (this.w - cw * s) / 2;
    const oy = (this.h - ch * s) / 2;
    const map = (pts) => pts.map(([x, y]) =>
      [ox + (x - minx) * s, oy + (y - miny) * s]);
    this.scaledPath = this.path ? map(this.path) : null;
    this.scaledPit = this.pit ? map(this.pit) : null;
  }

  setSelected(idx) { this.selectedIdx = idx; }

  /* Receive a fresh snapshot of cars. */
  update(cars) {
    const seen = new Set();
    for (const c of cars) {
      seen.add(c.car_idx);
      let s = this.cars[c.car_idx];
      if (!s) {
        s = { display: c.lap_dist_pct, target: c.lap_dist_pct };
        this.cars[c.car_idx] = s;
      }
      s.target = c.lap_dist_pct;
      s.number = c.number;
      s.color = c.class_color || '#888';
      s.position = c.position;
      s.off = c.off_track;
      s.pit = c.on_pit_road;
      s.inWorld = c.in_world;
      s.speed = c.speed_ms || 0;
      s.finished = c.finished;
    }
    for (const idx of Object.keys(this.cars)) {
      if (!seen.has(parseInt(idx, 10))) delete this.cars[idx];
    }
  }

  _tick(now) {
    const dt = Math.min(0.1, (now - this._last) / 1000);
    this._last = now;
    // Advance each car smoothly toward its true position.
    for (const s of Object.values(this.cars)) {
      const predicted = s.pit ? 0 : (s.speed / this.trackLenM) * dt;
      let d = s.target - s.display;
      d = ((d % 1) + 1.5) % 1 - 0.5;        // signed shortest delta
      s.display += predicted + d * 0.10;
      s.display = ((s.display % 1) + 1) % 1;
    }
    this._render();
    requestAnimationFrame((t) => this._tick(t));
  }

  /* Position + heading at a given lap-distance fraction. */
  _pointAt(pct) {
    if (this.scaledPath) {
      const n = this.scaledPath.length;
      const f = ((pct % 1) + 1) % 1 * n;
      const i = Math.floor(f) % n;
      const j = (i + 1) % n;
      const t = f - Math.floor(f);
      const a = this.scaledPath[i], b = this.scaledPath[j];
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    // Circle fallback.
    const cx = this.w / 2, cy = this.h / 2;
    const r = Math.min(this.w, this.h) / 2 - 34;
    const ang = -Math.PI / 2 + pct * Math.PI * 2;
    return [cx + r * Math.cos(ang), cy + r * Math.sin(ang)];
  }

  _render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);

    // ---- track ----
    if (this.scaledPath) {
      this._strokePath(this.scaledPath, 13, '#26262f');
      this._strokePath(this.scaledPath, 9, '#34343f');
      if (this.scaledPit) this._strokePath(this.scaledPit, 4, '#2b2b38', false);
    } else {
      const cx = this.w / 2, cy = this.h / 2;
      const r = Math.min(this.w, this.h) / 2 - 34;
      ctx.lineWidth = 11;
      ctx.strokeStyle = '#34343f';
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      ctx.lineWidth = 3;
      ctx.strokeStyle = '#2b2b38';
      ctx.beginPath(); ctx.arc(cx, cy, r - 24, 0, Math.PI * 2); ctx.stroke();
    }

    // ---- start / finish marker ----
    const sf = this._pointAt(0);
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(sf[0], sf[1], 3.4, 0, Math.PI * 2); ctx.fill();

    // ---- off-track yellow zones ----
    for (const s of Object.values(this.cars)) {
      if (s.off && s.inWorld && !s.pit) this._yellowZone(s.display);
    }

    // ---- cars ----
    const list = Object.entries(this.cars)
      .filter(([, s]) => s.inWorld)
      .sort(([a], [b]) => (parseInt(a) === this.selectedIdx ? 1 : 0)
                        - (parseInt(b) === this.selectedIdx ? 1 : 0));
    for (const [idx, s] of list) {
      this._drawCar(parseInt(idx, 10), s);
    }
  }

  _strokePath(pts, width, color, closed = true) {
    const ctx = this.ctx;
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    if (closed) ctx.closePath();
    ctx.stroke();
  }

  _yellowZone(pct) {
    const ctx = this.ctx;
    ctx.strokeStyle = 'rgba(245,197,24,0.55)';
    ctx.lineWidth = 13;
    ctx.lineCap = 'round';
    ctx.beginPath();
    const steps = 8;
    for (let i = 0; i <= steps; i++) {
      const p = this._pointAt(pct - 0.045 + (0.05 * i / steps));
      i === 0 ? ctx.moveTo(p[0], p[1]) : ctx.lineTo(p[0], p[1]);
    }
    ctx.stroke();
  }

  _drawCar(idx, s) {
    const ctx = this.ctx;
    const [x, y] = this._pointAt(s.display);
    const sel = idx === this.selectedIdx;
    const leader = s.position === 1;
    const R = sel ? 11 : 9.2;

    // outer ring
    if (sel || leader || s.off) {
      ctx.beginPath();
      ctx.arc(x, y, R + 3.2, 0, Math.PI * 2);
      ctx.lineWidth = 2.6;
      ctx.strokeStyle = sel ? '#e63946' : (s.off ? '#f5c518' : '#d4af37');
      ctx.stroke();
    }
    // body
    ctx.beginPath();
    ctx.arc(x, y, R, 0, Math.PI * 2);
    ctx.fillStyle = s.pit ? '#5f5f70' : s.color;
    ctx.fill();
    ctx.lineWidth = 1.4;
    ctx.strokeStyle = 'rgba(0,0,0,0.6)';
    ctx.stroke();
    // number
    ctx.fillStyle = this._textColor(s.pit ? '#5f5f70' : s.color);
    ctx.font = `700 ${R - 0.5}px ui-monospace, Menlo, monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(s.number).slice(0, 3), x, y + 0.5);
  }

  _textColor(hex) {
    const c = hex.replace('#', '');
    if (c.length < 6) return '#fff';
    const r = parseInt(c.substr(0, 2), 16);
    const g = parseInt(c.substr(2, 2), 16);
    const b = parseInt(c.substr(4, 2), 16);
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? '#000' : '#fff';
  }
}

window.TrackMap = TrackMap;
