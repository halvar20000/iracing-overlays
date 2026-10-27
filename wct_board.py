"""Shared pieces of the WCT-look broadcast pages (2026-10-25, from Thomas's
WCT graphics "STARTING GRID" / "RESULTS - LAUF 5 - CAS GT3 WCT -
INDIANAPOLIS RC"):

  • BOARD_HTML  — a paged list page: blue tab + title bar, orange rule,
                  section pills (GESAMT / PRO / AM / class / race), rows of
                  pos · blue number box · name · car · value · points,
                  league logo at the bottom. Used by
                  5002 /wct (final result), 5010 /lastrace/wct (last CLS
                  round) and 5010 /teams/wct (team championship). The grid
                  (5001) has its own staggered page but the same look.
  • board_page()— the page for one data URL.
  • PageNav     — Stream Deck / keyboard page commands. ONE instance lives on
                  the grid server (5001 /nav, CORS open); every WCT page polls
                  it, so the same four Stream Deck keys page whichever of
                  them is on air.
  • EventInfo   — league + round from 5010 /api/roundinfo for the titles.

Data format a board endpoint returns:
  {connected, tab, title, slug, empty,
   sections: [{label, color, rows: [{pos, num, name, sub, value, pts,
                                     mark, fl}]}]}
mark = colour of a small bar before the name (PRO red / AM green / class),
fl = fastest lap (purple dot).
"""
import json
import threading
import time
import urllib.request

PRO_COLOR, AM_COLOR = "#e5484d", "#30c45a"     # the tower's Pro/Am bars
UNRATED_COLOR = "#000000"                        # WCT driver without PRO / AM in CLS


class EventInfo:
    """League + round for the titles ("LAUF 5 - CAS GT3 WCT - …") from the
    championship server (5010 /api/roundinfo: it detects the league from the
    grid and knows a replayed earlier round). 5010 not running = no title
    parts. `last` = the last COMPLETED round (for the result after it)."""
    URL = "http://127.0.0.1:5010/api/roundinfo"

    def __init__(self):
        self.data = {}

    def run(self):
        while True:
            try:
                with urllib.request.urlopen(self.URL, timeout=8) as r:
                    d = json.loads(r.read().decode("utf-8"))
                info = (d.get("info") or {}) if d.get("ok") else {}
                league = info.get("league") or {}
                self.data = {"league": league.get("name") or "", "slug": league.get("slug") or "",
                             "round": (info.get("nextRound") or {}).get("number"),
                             "last": (info.get("lastRound") or {}).get("number")} if league else {}
            except Exception:
                self.data = {}
            time.sleep(30)


class PageNav:
    """Page commands from a Stream Deck / keyboard: every command gets a new
    seq; each open page applies it once. `manual` stops the automatic page
    change (also after a page reload) until "auto" is sent."""

    CMDS = ("next", "prev", "first", "auto", "auto_on", "auto_off")

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"seq": 0, "cmd": "", "manual": False}

    def send(self, cmd):
        with self._lock:
            st = self.state
            if cmd == "auto":                         # toggle
                st["manual"] = not st["manual"]
            elif cmd in ("auto_on", "auto_off"):
                st["manual"] = cmd == "auto_off"
            else:                                     # next / prev / first
                st["manual"] = True
            st["seq"] += 1
            st["cmd"] = cmd
            return dict(st)

    def get(self):
        with self._lock:
            return dict(self.state)


def title_track(name, config):
    return " ".join(x for x in (name, config) if x)


def event_title(ev: dict, track: str, round_no=None, extra: str = "", round_word: str = "LAUF") -> str:
    """'LAUF 5 - CAS GT3 WCT - INDIANAPOLIS ROAD COURSE' (missing parts left out)."""
    parts = []
    n = round_no if round_no is not None else (ev or {}).get("round")
    if n:
        parts.append(f"{round_word} {n}{extra}")
    if (ev or {}).get("league"):
        parts.append(ev["league"])
    if track:
        parts.append(track)
    return " - ".join(parts).upper()


def fmt_gap(seconds) -> str:
    if seconds is None:
        return ""
    return f"+{seconds:.3f}"


def laps_text(n: int) -> str:
    return f"+{n} LAP" if n == 1 else f"+{n} LAPS"


def board_page(data_url: str) -> str:
    return BOARD_HTML.replace("__DATA_URL__", data_url)


BOARD_HTML = r"""<!DOCTYPE html>
<html lang="de"><head><meta charset="UTF-8"><title>Board</title>
<style>
:root { --blue: #3d8be8; --orange: #f2a531; --text: #f4f4f6; --muted: #d6d6de; }
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: rgba(0,0,0,0); overflow: hidden; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; color: var(--text); }
body.debug { background: #3a4a3a; }
.card { position: relative; width: 900px; height: 620px; padding: 16px 26px 0; transform-origin: 0 0; }
.card.veil { background: rgba(8,8,12,.55); }
.top { display: flex; gap: 12px; height: 34px; }
.tab { width: 200px; background: var(--blue); border-radius: 3px; display: flex; align-items: center;
       justify-content: center; font-size: 18px; font-weight: 800; font-style: italic; letter-spacing: .5px;
       white-space: nowrap; box-shadow: 0 1px 0 rgba(255,255,255,.25) inset; }
.title { flex: 1; min-width: 0; background: rgba(36,36,42,.88); display: flex; align-items: center; justify-content: center;
         font-size: 18px; font-weight: 800; letter-spacing: .4px; white-space: nowrap; overflow: hidden; padding: 0 14px; }
.rule { height: 3px; background: var(--orange); margin: 10px 0 8px; }
.pills { display: flex; gap: 6px; height: 20px; margin-bottom: 8px; }
.pill { padding: 0 10px; border-radius: 3px; background: var(--blue); opacity: .45; font-size: 11px; font-weight: 800;
        display: flex; align-items: center; white-space: nowrap; border-left: 4px solid transparent; }
.pill.on { opacity: 1; }
.pill.pg { margin-left: auto; opacity: .9; background: rgba(36,36,42,.9); }
.rows { display: flex; flex-direction: column; gap: 4px; }
.row { display: flex; align-items: center; height: 29px; gap: 0; }
.row.in { animation: rowIn .4s cubic-bezier(.2,.8,.2,1) both; }
@keyframes rowIn { from { opacity: 0; transform: translateX(-16px); } to { opacity: 1; transform: none; } }
.p { width: 30px; text-align: center; font-size: 15px; font-weight: 800; text-shadow: 0 1px 3px rgba(0,0,0,.8); }
.n { width: 36px; height: 29px; background: var(--blue); border-radius: 2px; display: flex; align-items: center;
     justify-content: center; font-size: 15px; font-weight: 800; margin-right: 10px; }
.n.none { background: transparent; }
.body { flex: 1; min-width: 0; height: 29px; display: flex; align-items: center; background: rgba(30,30,36,.62); padding: 0 12px; gap: 10px; }
.mk { width: 4px; height: 17px; border-radius: 1px; flex: none; }
.nm { flex: 1; min-width: 0; font-size: 14px; font-weight: 800; text-transform: uppercase; letter-spacing: .3px;
      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sub { width: 250px; flex: none; font-size: 12px; font-weight: 700; text-transform: uppercase; color: var(--muted);
       white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fl { width: 9px; height: 9px; border-radius: 50%; background: #b44dff; flex: none; box-shadow: 0 0 6px #b44dff; }
.v { width: 100px; flex: none; text-align: right; font-size: 14px; font-weight: 700; font-style: italic; font-variant-numeric: tabular-nums; }
.pt { width: 70px; flex: none; text-align: right; font-size: 14px; font-weight: 800; color: var(--orange); font-variant-numeric: tabular-nums; }
.foot { position: absolute; left: 0; right: 0; bottom: 0; height: 120px; border-top: 2px solid rgba(255,255,255,.08);
        display: flex; align-items: center; justify-content: center; }
.foot .lb { height: 96px; padding: 10px 40px; background: rgba(30,30,34,.85); display: flex; align-items: center; }
.foot img { max-height: 76px; max-width: 420px; }
.empty-msg { height: 120px; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 700;
             color: #c8c8d0; background: rgba(36,36,42,.7); }
.hint { position: absolute; right: 8px; bottom: 4px; font-size: 11px; color: #ffd166; display: none; }
body.debug .hint { display: block; }
</style></head>
<body>
<div class="card" id="card">
  <div class="top"><div class="tab" id="tab">RESULTS</div><div class="title" id="title">—</div></div>
  <div class="rule"></div>
  <div class="pills" id="pills"></div>
  <div class="rows" id="rows"></div>
  <div class="foot" id="foot" style="visibility:hidden"><div class="lb"><img id="logo" alt=""></div></div>
  <div class="hint" id="hint"></div>
</div>
<script>
const DATA_URL = "__DATA_URL__";
const QS = new URLSearchParams(location.search);
const PER = Math.max(3, parseInt(QS.get("per") || "10", 10));
const SECS = Math.max(2, parseFloat(QS.get("secs") || "10"));
const AUTO_PARAM = QS.get("auto") === "1";          // pages turn only by the Stream Deck keys / arrows; ?auto=1 = old timer
// page commands come from the grid server (Stream Deck keys 5001/nav/...)
const NAV = (QS.get("nav") || "http://" + (location.hostname || "localhost") + ":5001").replace(/\/$/, "");
const card = document.getElementById("card");
if (QS.get("veil") !== "0") card.classList.add("veil");
const zoom = parseFloat(QS.get("zoom") || "1");
if (zoom && zoom !== 1) card.style.transform = "scale(" + zoom + ")";
let debug = QS.get("debug") === "1";
document.body.classList.toggle("debug", debug);

let data = null, pages = [], page = 0, sig = "", manual = !AUTO_PARAM, navSeq = null, timer = null;
function fitTitle() {           // long track names: shrink instead of cutting off
  const el = document.getElementById("title");
  let fs = 18; el.style.fontSize = fs + "px";
  while (el.scrollWidth > el.clientWidth && fs > 11) el.style.fontSize = (--fs) + "px";
}
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function buildPages(d) {
  const out = [];
  let only = QS.get("section");                      // ?section=PRO pins one section
  (d.sections || []).forEach((s, si) => {
    if (only && s.label.toUpperCase() !== only.toUpperCase()) return;
    const rows = s.rows || [];
    for (let i = 0; i < Math.max(rows.length, 1); i += PER)
      out.push({ si, rows: rows.slice(i, i + PER), from: i + 1, to: Math.min(i + PER, rows.length), n: rows.length });
  });
  return out;
}
function draw() {
  const rowsEl = document.getElementById("rows"), pillsEl = document.getElementById("pills");
  if (!pages.length) {
    pillsEl.innerHTML = "";
    rowsEl.innerHTML = '<div class="empty-msg">' + esc(data && data.connected === false ? "Warte auf iRacing …" : ((data && data.empty) || "Noch keine Daten")) + '</div>';
    return;
  }
  page = ((page % pages.length) + pages.length) % pages.length;
  const pg = pages[page], secs = data.sections || [];
  const shown = [...new Set(pages.map(p => p.si))];
  let pills = shown.map(si => '<div class="pill' + (si === pg.si ? ' on' : '') + '"' +
      (secs[si].color ? ' style="border-left-color:' + secs[si].color + '"' : '') + '>' + esc(secs[si].label) + '</div>').join("");
  if (pg.n > PER) pills += '<div class="pill pg">' + pg.from + "–" + pg.to + " / " + pg.n + '</div>';
  pillsEl.innerHTML = shown.length > 1 || pg.n > PER ? pills : "";
  const anyPts = pg.rows.some(r => r.pts !== undefined && r.pts !== null && r.pts !== "");
  const anySub = pg.rows.some(r => r.sub);
  const anyVal = pg.rows.some(r => r.value);
  rowsEl.innerHTML = pg.rows.map((r, i) =>
    '<div class="row in" style="animation-delay:' + (i * 0.04).toFixed(2) + 's">' +
    '<div class="p">' + esc(r.pos) + '</div>' +
    '<div class="n' + (r.num ? '' : ' none') + '">' + esc(r.num || "") + '</div>' +
    '<div class="body">' + (r.mark ? '<div class="mk" style="background:' + r.mark
      + (r.mark === '#000000' ? ';box-shadow:0 0 0 1px #fff' : '') + '"></div>' : '') +
    '<div class="nm"' + (String(r.name || "").length > 30 ? ' style="font-size:12px"' : '') + '>' + esc(r.name || "—") + '</div>' +
    (r.fl ? '<div class="fl" title="fastest lap"></div>' : '') +
    (anySub ? '<div class="sub">' + esc(r.sub || "") + '</div>' : '') +
    (anyVal ? '<div class="v">' + esc(r.value || "") + '</div>' : '') +
    (anyPts ? '<div class="pt">' + (r.pts === null || r.pts === undefined || r.pts === "" ? "" : esc(r.pts) + " PKT") + '</div>' : '') +
    '</div></div>').join("");
  document.getElementById("hint").textContent = (manual ? "MANUELL" : "AUTO " + SECS + " s") + " · Seite " + (page + 1) + "/" + pages.length;
}
function go(p) { page = p; draw(); schedule(); }
function schedule() {
  clearTimeout(timer);
  if (!manual && pages.length > 1) timer = setTimeout(() => go(page + 1), SECS * 1000);
}
async function tick() {
  try { data = await (await fetch(DATA_URL, { cache: "no-store" })).json(); } catch (e) { return; }
  document.getElementById("tab").textContent = QS.get("tab") || data.tab || "RESULTS";
  document.getElementById("title").textContent = QS.get("title") || data.title || "—";
  fitTitle();
  const logo = document.getElementById("logo"), foot = document.getElementById("foot");
  const src = QS.get("logo") === "0" ? "" : (QS.get("logo") ||
      (data.slug ? "https://league.simracing-hub.com/logos/" + data.slug + ".webp" : ""));
  if (!src) foot.style.visibility = "hidden";
  else if (logo.dataset.src !== src) {
    logo.dataset.src = src; foot.style.visibility = "hidden";
    logo.onload = () => foot.style.visibility = ""; logo.onerror = () => foot.style.visibility = "hidden";
    logo.src = src;
  }
  const s = JSON.stringify([data.sections, data.empty, data.connected]);
  if (s === sig) return;
  const first = sig === "";
  sig = s;
  pages = buildPages(data);
  if (first) page = 0;
  draw(); if (first) schedule();
}
async function pollNav() {
  let st;
  try { st = await (await fetch(NAV + "/nav", { cache: "no-store" })).json(); } catch (e) { return; }
  const wasManual = manual;
  manual = !AUTO_PARAM || st.manual;
  if (navSeq === null) { navSeq = st.seq; if (manual !== wasManual) { schedule(); draw(); } return; }
  if (st.seq !== navSeq) {
    navSeq = st.seq;
    if (st.cmd === "next") return go(page + 1);
    if (st.cmd === "prev") return go(page - 1);
    if (st.cmd === "first") return go(0);
  }
  if (manual !== wasManual) { schedule(); draw(); }
}
function send(cmd) { fetch(NAV + "/nav/" + cmd, { method: "POST" }).then(pollNav).catch(() => {}); }
document.addEventListener("keydown", e => {
  const k = e.key;
  if (k === "h" || k === "H") { debug = !debug; document.body.classList.toggle("debug", debug); }
  else if (k === "ArrowRight" || k === "PageDown" || k === " " || k === "n" || k === "N") send("next");
  else if (k === "ArrowLeft" || k === "PageUp" || k === "p" || k === "P") send("prev");
  else if (k === "Home" || k === "1") send("first");
  else if (k === "a" || k === "A") send("auto");
  else return;
  e.preventDefault();
});
function restart() { if (pages.length) go(0); }
window.addEventListener("obsSourceVisibleChanged", e => { if (!e.detail || e.detail.visible) restart(); });
window.addEventListener("obsSourceActiveChanged", e => { if (!e.detail || e.detail.active) restart(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) restart(); });
if (QS.get("demo") === "1") {
  window.fetch = (u => async (url, o) => {
    if (String(url).indexOf("/nav") >= 0) return { json: async () => ({ seq: 0, cmd: "", manual: false }) };
    if (String(url) === DATA_URL) {
      const N = ["Mike Zocher","Maurice Becker","Yannick Wonnenberg","Tim Freudling","Marius Becker","Dennis Richter","Andre Brechmann","Klaus Oberlaender","Thomas Kuebler","Andreas Bastian","Remo Cursio","Bastian Kübler"];
      const C = ["McLaren 720S GT3 EVO","Porsche 911 GT3 R (992)","McLaren 720S GT3 EVO","Mercedes-AMG GT3 2020","McLaren 720S GT3 EVO","McLaren 720S GT3 EVO","Lamborghini Huracan GT3 EVO","McLaren 720S GT3 EVO","Mercedes-AMG GT3 2020","Porsche 911 GT3 R (992)","BMW M4 GT3","Audi R8 LMS EVO II GT3"];
      const NUM = ["01","49","03","11","14","63","82","51","173","921","7","22"];
      const PA = [1,1,1,1,1,1,1,0,0,1,0,0];
      const rows = N.map((n, i) => ({ pos: i + 1, num: NUM[i], name: n, sub: C[i], value: i ? "+" + (i * 2.137 + 18).toFixed(3) : "39 LAPS",
        mark: PA[i] ? "#e5484d" : "#30c45a", fl: i === 1 }));
      const cls = (want) => rows.filter((r, i) => PA[i] === want).map((r, i) => ({ ...r, pos: i + 1, mark: "" }));
      return { json: async () => ({ connected: true, tab: "RESULTS", title: "LAUF 5 - CAS GT3 WCT - INDIANAPOLIS ROAD COURSE", slug: "cas-gt3-wct",
        sections: [{ label: "GESAMT", rows }, { label: "PRO", color: "#e5484d", rows: cls(1) }, { label: "AM", color: "#30c45a", rows: cls(0) }] }) };
    }
    return u(url, o);
  })(window.fetch);
}
tick(); setInterval(tick, 3000);
pollNav(); setInterval(pollNav, 300);
</script></body></html>
"""
