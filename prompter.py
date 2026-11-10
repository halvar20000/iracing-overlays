"""Teleprompter for the stream notes (dashboard 5000 `/prompter`).

The notes in show_notes/*.md are cut into single INFOS (cards): one list item,
one table row, one paragraph or quote each; a short label line directly before
a list of short items (e.g. "**LMP2** (18 Teams)" + top 5) stays one card.
Headings are not cards, they label the cards below them. "Inhalt" and
"Quellen" are left out.

The position lives on the SERVER, so every open prompter page and the Stream
Deck keys share it: GET /prompter/next | prev | first | section_next |
section_prev | goto/<n> | file/<name> (Stream Deck "Website" action, in the
background). The page polls /prompter/state and fetches /prompter/data only
when the file or its content changed.
"""
import html
import re
import threading
from pathlib import Path

SKIP_SECTIONS = ("inhalt", "quellen", "sources", "contents")
LABEL_MAX = 60          # a paragraph this short before a list = its label
GROUP_ITEM_MAX = 70     # ... if every item of that list is this short


def _inline(text: str) -> str:
    """Markdown inline -> safe HTML (bold, italic, code, links as text)."""
    t = html.escape(text.strip(), quote=False)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t


def _plain(text: str) -> str:
    """Plain (unescaped) text: for headings and sizes."""
    return _strip(_inline(text))


def _strip(h: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", h))


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_cards(rows: list[list[str]]) -> list[str]:
    """One card per body row. With a header: 'Header: value' lines under the
    row's first cell; a header-less 2-column table = 'Key: value'."""
    if not rows:
        return []
    head, body = rows[0], rows[1:]
    named = any(h for h in head[1:])
    out = []
    for r in body:
        if not any(r):
            continue
        first = _inline(r[0]) if r else ""
        if named:
            lines = [f"<span class='k'>{_inline(h)}:</span> {_inline(v)}"
                     for h, v in zip(head[1:], r[1:]) if v]
            out.append((f"<div class='th'>{first}</div>" if first else "") + "<br>".join(lines))
        else:
            rest = " · ".join(_inline(v) for v in r[1:] if v)
            out.append(f"<div class='th'>{first}</div>{rest}" if rest else first)
    return out


def parse(md: str) -> list[dict]:
    """Cards: [{"section": "7. Die IEC › Teamwertung", "html": "..."}]."""
    cards: list[dict] = []
    h2 = h3 = ""
    skip = False
    lines = md.splitlines()
    i = 0
    pending_label = None        # short paragraph that may head the next list

    def section():
        return f"{h2} › {h3}" if h3 else h2

    def add(body):
        if body and not skip:
            cards.append({"section": section(), "html": body})

    def flush_label():
        nonlocal pending_label
        if pending_label is not None:
            add(pending_label)
            pending_label = None

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s or re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            i += 1
            continue
        m = re.match(r"(#{1,6})\s+(.*)", s)
        if m:
            flush_label()
            level, title = len(m.group(1)), _plain(m.group(2))
            if level == 1:
                h2, h3 = title, ""
                skip = False
            elif level == 2:
                h2, h3 = title, ""
                skip = any(w in title.lower() for w in SKIP_SECTIONS)
            else:
                h3 = title
            i += 1
            continue
        if s.startswith("|"):
            flush_label()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                c = _cells(lines[i])
                if not (any(c) and all(re.fullmatch(r":?-{2,}:?", x) for x in c if x)):
                    rows.append(c)
                i += 1
            for body in _table_cards(rows):
                add(body)
            continue
        if re.match(r"([-*+]|\d+[.)])\s+", s):
            items = []
            while i < len(lines):
                t = lines[i].strip()
                mm = re.match(r"([-*+]|\d+[.)])\s+(.*)", t)
                if mm:
                    num = mm.group(1) if mm.group(1)[0].isdigit() else ""
                    items.append([num, mm.group(2)])
                elif t and lines[i][:1].isspace() and items and not t.startswith(("|", "#")):
                    items[-1][1] += " " + t          # continuation line
                else:
                    break
                i += 1
            fmt = [(f"<span class='n'>{n}</span> " if n else "") + _inline(x) for n, x in items]
            if pending_label is not None and all(len(_plain(x)) <= GROUP_ITEM_MAX for _, x in items):
                add(f"<div class='th'>{pending_label}</div>" + "<br>".join(fmt))
                pending_label = None
            else:
                sub, pending_label = pending_label, None
                for f in fmt:
                    if sub:
                        cards.append({"section": f"{section()} › {_strip(sub).rstrip(':')}", "html": f}) \
                            if not skip else None
                    else:
                        add(f)
            continue
        # paragraph / quote: consecutive non-empty lines of the same kind
        quote = s.startswith(">")
        buf = []
        while i < len(lines):
            t = lines[i].strip()
            if not t or t.startswith(("|", "#")) or re.match(r"([-*+]|\d+[.)])\s+", t) \
                    or t.startswith(">") != quote or re.fullmatch(r"-{3,}", t):
                break
            buf.append(t.lstrip(">").strip() if quote else t)
            i += 1
        if not quote and len(buf) > 1 and all(b.startswith("**") for b in buf):
            flush_label()
            for b in buf:                              # "**Datum:** …" lines
                add(_inline(b))
            continue
        text = " ".join(buf)
        if not quote and len(_plain(text)) <= LABEL_MAX and not text.rstrip().endswith("."):
            flush_label()
            pending_label = _inline(text)
            continue
        body = f"<div class='q'>{_inline(text)}</div>" if quote else _inline(text)
        if pending_label is not None:
            body, pending_label = f"<div class='th'>{pending_label}</div>" + body, None
        add(body)
    flush_label()
    return cards


def note_files(notes_dir: Path) -> list[str]:
    if not notes_dir.is_dir():
        return []
    return sorted(p.name for p in notes_dir.iterdir()
                  if p.suffix.lower() in (".md", ".txt")
                  and not p.name.lower().startswith(("readme", "schedule-")))


def pick_for_track(files: list[str], track: str) -> str | None:
    """The notes file sharing the longest words with the track name."""
    words = {w for w in re.findall(r"[a-zäöüß]+", (track or "").lower()) if len(w) >= 4}
    best, score = None, 0
    for f in files:
        sc = sum(len(w) for w in words & set(re.findall(r"[a-zäöüß]+", f.lower())))
        if sc > score:
            best, score = f, sc
    return best


class Prompter:
    def __init__(self, notes_dir: Path, track_fn=None):
        self.notes_dir = Path(notes_dir)
        self.track_fn = track_fn or (lambda: "")
        self._lock = threading.Lock()
        self.file = None
        self.idx = 0
        self.seq = 0
        self._cards: list[dict] = []
        self._mtime = None
        self._loaded = None
        self._track = None       # track the file was picked for
        self._manual = False     # file chosen by hand: kept until the track changes

    # -- content -----------------------------------------------------------
    def _ensure(self):
        files = note_files(self.notes_dir)
        # follow the track (2026-11-10: it stayed on the file picked at start-up,
        # Hungaroring, while iRacing was at Spa); a file chosen by hand stays
        # until the track changes; a new matching file (after an update) is taken
        try:
            track = self.track_fn() or ""
        except Exception:
            track = ""
        if track and track != self._track:
            self._track, self._manual = track, False
        best = pick_for_track(files, track) if track and not self._manual else None
        if best and best != self.file:
            self.file, self.idx = best, 0
        elif self.file not in files:
            self.file = pick_for_track(files, track) or (files[0] if files else None)
            self.idx = 0
        if not self.file:
            self._cards, self._mtime, self._loaded = [], None, None
            return
        p = self.notes_dir / self.file
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = None
        if (self._loaded, self._mtime) != (self.file, mt):
            try:
                self._cards = parse(p.read_text(encoding="utf-8"))
            except OSError:
                self._cards = []
            self._loaded, self._mtime = self.file, mt
            self.seq += 1
        self.idx = max(0, min(self.idx, len(self._cards) - 1))

    def state(self) -> dict:
        with self._lock:
            self._ensure()
            return {"file": self.file, "idx": self.idx, "count": len(self._cards),
                    "ver": f"{self.file}:{self._mtime}", "seq": self.seq}

    def data(self) -> dict:
        with self._lock:
            self._ensure()
            return {"file": self.file, "files": note_files(self.notes_dir), "idx": self.idx,
                    "cards": self._cards, "ver": f"{self.file}:{self._mtime}"}

    # -- moving ------------------------------------------------------------
    def command(self, cmd: str, arg: str | None = None) -> dict:
        with self._lock:
            self._ensure()
            n = len(self._cards)
            if cmd == "next":
                self.idx = min(self.idx + 1, max(n - 1, 0))
            elif cmd == "prev":
                self.idx = max(self.idx - 1, 0)
            elif cmd == "first":
                self.idx = 0
            elif cmd == "goto":
                try:
                    self.idx = max(0, min(int(arg) - 1, max(n - 1, 0)))
                except (TypeError, ValueError):
                    pass
            elif cmd in ("section_next", "section_prev") and n:
                starts = [k for k in range(n) if k == 0 or self._cards[k]["section"] != self._cards[k - 1]["section"]]
                here = max(k for k in starts if k <= self.idx)
                if cmd == "section_next":
                    later = [k for k in starts if k > self.idx]
                    self.idx = later[0] if later else self.idx
                else:
                    # back to this section's start, or the previous one if already there
                    earlier = [k for k in starts if k < here]
                    self.idx = here if self.idx != here or not earlier else earlier[-1]
            elif cmd == "file" and arg in note_files(self.notes_dir):
                self._manual = True
                if arg != self.file:
                    self.file, self.idx = arg, 0
                    self._loaded = None
            self.seq += 1
        return self.state()


PAGE_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Teleprompter</title>
<style>
  :root { --bg:#0b0b10; --panel:#16161f; --line:#2a2a36; --text:#f2f2f6; --muted:#8d8d9c; --acc:#ff9d2e; }
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
    font-family: "Segoe UI", system-ui, sans-serif; }
  body { display:flex; flex-direction:column; zoom: var(--zoom, 1); }
  .bar { display:flex; align-items:center; gap:10px; padding:8px 14px; background:var(--panel);
    border-bottom:1px solid var(--line); font-size:14px; color:var(--muted); flex-wrap:wrap; }
  .bar select, .bar button { background:#22222e; color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:6px 10px; font-size:14px; cursor:pointer; }
  .bar button:hover { border-color:var(--acc); }
  .count { margin-left:auto; font-variant-numeric: tabular-nums; color:var(--text); font-weight:700; }
  .sec { color:var(--acc); font-weight:700; letter-spacing:.3px; font-size:18px; padding:14px 28px 0; }
  .stage { flex:1; overflow:hidden; display:flex; flex-direction:column; padding:6px 28px 18px; gap:14px; }
  .card { border-radius:10px; padding:16px 22px; line-height:1.35; }
  .prev { color:var(--muted); font-size:calc(var(--fs) * .55); max-height:22%; overflow:hidden; opacity:.6; }
  .cur { background:var(--panel); border-left:6px solid var(--acc); font-size:var(--fs); font-weight:500; }
  .next { color:#b9b9c6; font-size:calc(var(--fs) * .6); border-left:6px solid var(--line); }
  .next + .next { opacity:.6; }
  .nsec { display:block; font-size:13px; color:var(--acc); opacity:.8; margin-bottom:4px; letter-spacing:.3px; }
  .th { font-weight:800; margin-bottom:4px; }
  .k { color:var(--muted); }
  .n { color:var(--acc); font-weight:800; }
  .q { font-style:italic; }
  b { color:#fff; font-weight:800; }
  .end { color:var(--muted); font-size:18px; padding:20px 28px; }
  body.clean .bar { display:none; }
</style></head><body>
<div class="bar">
  <select id="file" title="Notizen"></select>
  <select id="secs" title="Kapitel"></select>
  <button id="bp" title="← / Bild hoch">◀ zurück</button>
  <button id="bn" title="→ / Leertaste / Bild runter">weiter ▶</button>
  <button id="bf" title="Pos1">⏮ Anfang</button>
  <button id="bsz" title="Schrift kleiner">A−</button><button id="bsZ" title="Schrift größer">A+</button>
  <span class="count" id="count"></span>
</div>
<div class="sec" id="sec"></div>
<div class="stage" id="stage"></div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('clean') === '1') document.body.classList.add('clean');
document.body.style.setProperty('--zoom', parseFloat(qs.get('zoom') || '1'));
let fs = parseFloat(qs.get('size') || localStorage.getItem('prompter_fs') || '40');
function setFs(v) { fs = Math.max(18, Math.min(90, v)); document.body.style.setProperty('--fs', fs + 'px');
  try { localStorage.setItem('prompter_fs', fs); } catch (e) {} }
setFs(fs);
let data = null, ver = null, idx = 0, busy = false;
const $ = id => document.getElementById(id);
const esc = t => String(t).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function cmd(c, a) {
  return fetch('/prompter/' + c + (a !== undefined ? '/' + encodeURIComponent(a) : ''), {method:'POST'})
    .then(r => r.json()).then(s => apply(s)).catch(() => {});
}
function render() {
  if (!data) return;
  const cards = data.cards, n = cards.length;
  $('count').textContent = n ? (idx + 1) + ' / ' + n : 'keine Infos';
  const c = cards[idx];
  $('sec').textContent = c ? c.section : '';
  let h = '';
  if (idx > 0) h += `<div class="card prev">${cards[idx-1].html}</div>`;
  if (c) h += `<div class="card cur">${c.html}</div>`;
  for (let k = idx + 1; k <= idx + 2 && k < n; k++) {
    const ns = cards[k].section !== cards[k-1].section ? `<span class="nsec">${esc(cards[k].section)}</span>` : '';
    h += `<div class="card next">${ns}${cards[k].html}</div>`;
  }
  if (c && idx === n - 1) h += '<div class="end">— Ende der Notizen —</div>';
  $('stage').innerHTML = h;
  const sel = $('secs'); let si = -1;
  [...sel.options].forEach((o, k) => { if (+o.value <= idx) si = k; });
  sel.selectedIndex = si;
}
function load() {
  return fetch('/prompter/data', {cache:'no-store'}).then(r => r.json()).then(d => {
    data = d; ver = d.ver; idx = d.idx;
    $('file').innerHTML = d.files.map(f => `<option${f === d.file ? ' selected' : ''}>${esc(f)}</option>`).join('');
    const opts = []; d.cards.forEach((c, k) => { if (!k || c.section !== d.cards[k-1].section) opts.push(`<option value="${k}">${esc(c.section)}</option>`); });
    $('secs').innerHTML = opts.join('');
    render();
  });
}
function apply(s) {
  if (!s) return;
  if (s.ver !== ver) { load(); return; }
  if (s.idx !== idx) { idx = s.idx; render(); }
}
function poll() {
  if (busy) return; busy = true;
  fetch('/prompter/state', {cache:'no-store'}).then(r => r.json()).then(apply).catch(() => {})
    .finally(() => { busy = false; });
}
$('bn').onclick = () => cmd('next');
$('bp').onclick = () => cmd('prev');
$('bf').onclick = () => cmd('first');
$('bsz').onclick = () => setFs(fs - 4);
$('bsZ').onclick = () => setFs(fs + 4);
$('file').onchange = e => cmd('file', e.target.value);
$('secs').onchange = e => cmd('goto', +e.target.value + 1);
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'SELECT') return;
  const k = e.key;
  if (k === 'ArrowRight' || k === 'ArrowDown' || k === 'PageDown' || k === ' ') { cmd('next'); e.preventDefault(); }
  else if (k === 'ArrowLeft' || k === 'ArrowUp' || k === 'PageUp') { cmd('prev'); e.preventDefault(); }
  else if (k === 'Home') cmd('first');
  else if (k === 'n' || k === 'N') cmd('section_next');
  else if (k === 'p' || k === 'P') cmd('section_prev');
  else if (k === '+') setFs(fs + 4); else if (k === '-') setFs(fs - 4);
});
load().then(() => setInterval(poll, 300));
</script></body></html>
"""


def register(app, notes_dir: Path, track_fn=None) -> Prompter:
    """Add the /prompter routes to a Flask app."""
    from flask import jsonify, Response, request
    p = Prompter(notes_dir, track_fn)

    @app.route("/prompter")
    def prompter_page():
        f = request.args.get("file")
        if f:
            p.command("file", f)
        return Response(PAGE_HTML, mimetype="text/html")

    @app.route("/prompter/state")
    def prompter_state():
        return jsonify(p.state())

    @app.route("/prompter/data")
    def prompter_data():
        return jsonify(p.data())

    @app.route("/prompter/<cmd>", methods=["GET", "POST"])
    @app.route("/prompter/<cmd>/<arg>", methods=["GET", "POST"])
    def prompter_cmd(cmd, arg=None):
        if cmd not in ("next", "prev", "first", "goto", "section_next", "section_prev", "file"):
            return jsonify({"ok": False, "error": "unknown command"}), 404
        s = p.command(cmd, arg)
        s["ok"] = True
        return jsonify(s)

    return p
