"""TABLES ON THEIR OWN OBS SCENE, with the stinger and a title card.

2026-11-27, Andreas / Thomas: the result / table pages were switched on over
the Moderator picture. Now, like the replay: a Stream Deck key (or the AI
show) puts the page on the OBS scene "Tabellen", switches there with the
"Stinger" transition, a full-screen title card ("RESULTATE") covers the cut
and wipes away to reveal the page. The same key again = back to the scene
before (stinger again). Another table key while on "Tabellen" swaps the page
UNDER the title card (no scene switch, nothing to stinger).

OBS setup (once): scene "Tabellen" holding the table sources (the SAME inputs
as on the Moderator scene, laid out full screen), the audio sources (mic,
Discord, the AI voice page - a source not in the program scene is silent)
and on top the browser source "Titelkarte" = http://localhost:5000/tables/title
(1920 x 1080). The scenes need NO transition override (an override wins over
the transition set here).

Transition "Stinger Tabellen" = a stinger in OBS with stingers/table_stinger.
webm (no word on it; make_replay_stinger.py --text ""), transition point 500 ms.

Config: show_config.json "tables" (DEFAULTS below, merged; re-read on every
call). Dashboard routes: /streamdeck/table/<key> (toggle), /streamdeck/
table_back, /tables (status), /tables/state (title card polls it),
/tables/title (the card).
"""
from __future__ import annotations

import threading
import time

DEFAULTS = {
    "scene": "Tabellen",
    "back_scene": "Moderator",       # when the scene before is unknown
    # own stinger WITHOUT a word (stingers/table_stinger.webm; the replay
    # stinger says REPLAY - 2026-11-27). Missing in OBS -> fallback.
    "transition": "Stinger Tabellen",  # only with "wipe": false
    "fallback_transition": "Fade",
    # 2026-11-27 evening, Thomas: stinger (brown, no word) THEN the blue card =
    # two transitions. "wipe": the card itself is the transition - it wipes in
    # over the scene on air (the same browser input is put on that scene too),
    # a Cut under the fully covering card, the card wipes away on the page.
    # false = the old stinger + card.
    "wipe": True,
    "cover_s": 0.4,                   # minimum wait before the cut (its wipe in is .32 s)
    # the card PAGE reports when it covers the picture (/tables/covered) - in
    # OBS it started 0.2-0.3 s late (poll + render), a fixed 0.4 s cut too early
    # (2026-11-28, measured on Thomas's OBS). No report after this long = cut anyway.
    "cover_max_s": 1.5,
    "back_hold_s": 0.25,              # blank card held this long on the way back
    "title_source": "Titelkarte",
    "title_s": 1.6,                   # card held this long (s) before it wipes away
    "enter_delay_s": 0.15,            # card up before the switch (the stinger cuts at 0.5 s)
    "swap_s": 0.45,                   # in-scene swap: page changed this long after the card started
    "hide_after_s": 1.5,              # page hidden this long after leaving (stinger over)
    "items": {
        "lastrace": {"source": "Last_RACE_WCT", "title": "RESULTATE"},
        "table":    {"source": "Standing_WCT_PRO_AM", "title": "MEISTERSCHAFT"},
        "teams":    {"source": "Teams_Standing_WCT", "title": "TEAMWERTUNG"},
        "dotd":     {"source": "Last_DOTD", "title": "FAHRER DES TAGES"},
        "rsvp":     {"source": "RSVP", "title": "ANMELDUNGEN"},
        "stats":    {"source": "Statistic", "title": "STATISTIK"},
        # 2026-11-28 (Thomas): the forecast pictures + track layout image
        # (image sources, the pictures change per event in OBS)
        "wetter_temp":  {"source": "Wetter Temperaturen", "title": "TEMPERATUREN"},
        "wetter_regen": {"source": "Wetter Feuchtigkeit", "title": "FEUCHTIGKEIT"},
        "strecke":      {"source": "Streckenführung", "title": "STRECKE"},
    },
}
TRANSITION_RESTORE_S = 2.5
# other spellings a Stream Deck key may carry (2026-11-28: "wetter_regen" worked
# from the browser, not from the key - accept the obvious variants)
ALIASES = {
    "wetter_feuchte": "wetter_regen", "wetter_feuchtigkeit": "wetter_regen", "feuchte": "wetter_regen",
    "feuchtigkeit": "wetter_regen", "regen": "wetter_regen", "wetter_rain": "wetter_regen",
    "wetter_temperatur": "wetter_temp", "wetter_temperaturen": "wetter_temp", "temperatur": "wetter_temp",
    "temperaturen": "wetter_temp", "streckenfuehrung": "strecke", "streckenführung": "strecke",
    "resultate": "lastrace", "meisterschaft": "table",
}


def norm_key(key: str, items: dict) -> str:
    k = (key or "").strip().strip("/").lower().replace("-", "_").replace(" ", "_")
    if k in items:
        return k
    return ALIASES.get(k, k)


def merge(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    t = (cfg or {}).get("tables") or {}
    out.update({k: v for k, v in t.items() if k != "items"})
    items = {k: dict(v) for k, v in DEFAULTS["items"].items()}
    for k, v in (t.get("items") or {}).items():
        if v is None:
            items.pop(k, None)
        elif isinstance(v, dict):
            items[k] = dict(items.get(k, {}), **v)
    out["items"] = items
    return out


class TableScene:
    def __init__(self, obs_factory, cfg_fn, sub_fn=None, clock=time.monotonic,
                 sleep=time.sleep, spawn=None, wait=None):
        self._obs_factory = obs_factory
        self._obs = None
        self._cfg_fn = cfg_fn
        self._sub_fn = sub_fn             # () -> line above the title (league), may fail
        self._clock, self._sleep = clock, sleep
        self._spawn = spawn or (lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True).start())
        self._lock = threading.RLock()
        # title state has its OWN lock: the card page polls /tables/state and
        # reports /tables/covered WHILE show() holds _lock and waits for it
        self._tlock = threading.Lock()
        self.seq = 0
        self.title = ""
        self.sub = ""
        self.hold = DEFAULTS["title_s"]
        self.blank = False                # card without a word (way back)
        self.shown = None                 # source name up on the table scene
        self.back_to = None               # scene to go back to
        self._known: set = set()          # every page source this ever showed
        self._gen = 0
        self._trans_prev = None
        self._trans_token = 0
        self.last = ""
        self.note = ""
        self.presses = []                 # [(time, key as sent, message)] newest last, 20 kept
        self._covered = threading.Event()
        self._covered_seq = None
        # wait(event, timeout) -> bool; tests inject one that does not block
        self._wait = wait or (lambda ev, t: ev.wait(t))

    # -- helpers --------------------------------------------------------------
    def cfg(self) -> dict:
        try:
            return merge(self._cfg_fn())
        except Exception:
            return merge({})

    def _o(self):
        if self._obs is None and self._obs_factory:
            self._obs = self._obs_factory()
        return self._obs

    def _call(self, fn):
        o = self._o()
        if o is None:
            raise RuntimeError("OBS nicht eingerichtet")
        return fn(o)

    def _title_on(self, title, cfg, hold=None, blank=False):
        sub = ""
        if self._sub_fn:
            try:
                sub = self._sub_fn() or ""
            except Exception:
                sub = ""
        with self._tlock:
            self._covered.clear()
            self.seq += 1
            self.title, self.sub = title or "", ("" if blank else sub)
            self.hold = float(hold if hold is not None else (cfg.get("title_s") or 1.6))
            self.blank = bool(blank)

    def _title_off(self):
        with self._tlock:
            self.seq += 1
            self.title = ""
            self.blank = False

    def covered(self, seq) -> bool:
        """The title card page: my wipe-in for `seq` now covers the picture."""
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            return False
        with self._tlock:             # not _lock: show() holds it while it waits for this
            ok = seq == self.seq
        if ok:
            self._covered_seq = seq
            self._covered.set()
        return ok

    def _wait_cover(self, cfg, min_s=None):
        """After _title_on: until the page reports the card covers the picture
        (at least cover_s, at most cover_max_s)."""
        t0 = self._clock()
        got = self._wait(self._covered, float(cfg.get("cover_max_s") or 1.5))
        if got:
            self.note = ""
        else:
            self.note = "Titelkarte hat nicht gemeldet (Quelle in OBS? Seite alt?) - Schnitt nach Zeit"
        rest = float(cfg.get("cover_s") or 0 if min_s is None else min_s) - (self._clock() - t0)
        if rest > 0:
            self._sleep(rest)

    def state(self) -> dict:
        with self._tlock:
            return {"seq": self.seq, "title": self.title, "sub": self.sub, "hold_s": self.hold,
                    "blank": self.blank}

    def _pick_transition(self, cfg) -> str:
        trans = cfg.get("transition") or ""
        if not trans:
            return ""
        try:
            names = [t.get("transitionName") for t in
                     self._call(lambda o: o.call("GetSceneTransitionList")).get("transitions", [])]
        except Exception:
            return trans
        if trans in names:
            return trans
        fb = cfg.get("fallback_transition") or ""
        self.note = f"Übergang '{trans}' fehlt in OBS - nehme '{fb or 'aktuellen'}'"
        print(f"[tables] {self.note}")
        return fb if fb in names else ""

    def _switch(self, scene, cfg):
        trans = self._pick_transition(cfg)
        if trans:
            try:
                prev = self._call(lambda o: o.current_transition())
            except Exception:
                prev = None
            if prev and prev != trans:
                with self._lock:
                    if self._trans_prev is None:
                        self._trans_prev = prev
                    self._trans_token += 1
                    token = self._trans_token
                self._call(lambda o: o.set_transition(trans))
                self._spawn(self._restore_transition, token)
        self._call(lambda o: o.set_scene(scene))

    def _ensure_card(self, scene, cfg) -> bool:
        """The title card input also on `scene` (on top, on). False = cannot."""
        src = cfg.get("title_source") or ""
        if not src or not scene:
            return False
        try:
            items = self._call(lambda o: o.scene_items(scene))
            if src not in items:
                self._call(lambda o: o.call("CreateSceneItem", {"sceneName": scene, "sourceName": src,
                                                                "sceneItemEnabled": True}))
                print(f"[tables] '{src}' in die Szene '{scene}' gelegt (Titelkarte als Übergang)")
            elif not items[src][1]:
                self._call(lambda o: o.set_visible(scene, src, True))
            return True
        except Exception as e:
            self.note = f"Titelkarte nicht in '{scene}': {e} - Stinger statt Wischer"
            print(f"[tables] {self.note}")
            return False

    def _cut(self, scene):
        """Scene switch with Cut (the card covers both pictures)."""
        try:
            prev = self._call(lambda o: o.current_transition())
        except Exception:
            prev = None
        if prev and prev != "Cut":
            with self._lock:
                if self._trans_prev is None:
                    self._trans_prev = prev
                self._trans_token += 1
                token = self._trans_token
            self._call(lambda o: o.set_transition("Cut"))
            self._spawn(self._restore_transition, token)
        self._call(lambda o: o.set_scene(scene))

    def _wipe_ok(self, cfg, *scenes) -> bool:
        return bool(cfg.get("wipe", True)) and all(self._ensure_card(s, cfg) for s in scenes)

    def _restore_transition(self, token):
        self._sleep(TRANSITION_RESTORE_S)
        with self._lock:
            if token != self._trans_token or self._trans_prev is None:
                return
            prev, self._trans_prev = self._trans_prev, None
        try:
            self._call(lambda o: o.set_transition(prev))
        except Exception as e:
            print(f"[tables] transition back: {e}")

    def _pages(self, cfg) -> set:
        return {v.get("source") for v in cfg["items"].values() if v.get("source")} | self._known

    def _only(self, scene, source, items, cfg):
        """`source` on, every other page source of the scene off."""
        for name in self._pages(cfg):
            if name == source or name not in items:
                continue
            if items[name][1]:
                self._call(lambda o, n=name: o.set_visible(scene, n, False))
        if not items[source][1]:
            self._call(lambda o: o.set_visible(scene, source, True))

    # -- public ---------------------------------------------------------------
    def on_scene(self) -> bool:
        try:
            return self._call(lambda o: o.current_scene()) == self.cfg()["scene"]
        except Exception:
            return False

    def has(self, source: str) -> bool:
        """Is this source on the table scene? (AI show: page there or not)"""
        try:
            return source in self._call(lambda o: o.scene_items(self.cfg()["scene"]))
        except Exception:
            return False

    def toggle(self, key: str) -> tuple[bool, str]:
        ok, msg = self._toggle(key)
        self.presses = (self.presses + [(time.strftime("%H:%M:%S"), repr(key), msg)])[-20:]
        print(f"[tables] Taste {key!r}: {msg}")
        return ok, msg

    def _toggle(self, key: str) -> tuple[bool, str]:
        cfg = self.cfg()
        key = norm_key(key, cfg["items"])
        it = cfg["items"].get(key)
        if not it or not it.get("source"):
            return False, f"Tabelle '{key}' unbekannt (show_config.json tables.items: {', '.join(cfg['items'])})"
        with self._lock:
            if self.shown == it["source"] and self.on_scene():
                return self.back()
            return self.show(it["source"], it.get("title") or "")

    def show(self, source: str, title: str = "") -> tuple[bool, str]:
        cfg = self.cfg()
        scene = cfg["scene"]
        with self._lock:
            try:
                items = self._call(lambda o: o.scene_items(scene))
                cur = self._call(lambda o: o.current_scene())
            except Exception as e:
                self.last = f"OBS: {e}"
                return False, self.last
            if source not in items:
                self.last = f"Quelle '{source}' fehlt in der Szene '{scene}'"
                return False, self.last
            self._known.add(source)
            self._gen += 1
            try:
                if cur == scene:
                    if self.shown == source:
                        self.last = f"{source} ist schon auf {scene}"
                        return True, self.last
                    self._title_on(title, cfg)
                    self._wait_cover(cfg, float(cfg.get("swap_s") or 0))   # card covers the page first
                    self._only(scene, source, items, cfg)
                else:
                    self.back_to = cur or cfg.get("back_scene")
                    self._only(scene, source, items, cfg)           # scene not on air yet
                    if self._wipe_ok(cfg, cur, scene):
                        self._title_on(title, cfg)                  # wipes in over `cur`
                        self._wait_cover(cfg)
                        self._cut(scene)                            # under the card
                    else:
                        self._title_on(title, cfg)
                        self._sleep(float(cfg.get("enter_delay_s") or 0))
                        self._switch(scene, cfg)
            except Exception as e:
                self.last = f"OBS: {e}"
                return False, self.last
            self.shown = source
            self.last = f"{title or source} auf {scene}"
            return True, self.last

    def back(self, scene: str | None = None) -> tuple[bool, str]:
        cfg = self.cfg()
        with self._lock:
            target = scene or self.back_to or cfg.get("back_scene") or "Moderator"
            try:
                cur = self._call(lambda o: o.current_scene())
            except Exception as e:
                self.last = f"OBS: {e}"
                return False, self.last
            if cur == cfg["scene"]:
                try:
                    if self._wipe_ok(cfg, cur, target):
                        self._title_on("", cfg, hold=cfg.get("back_hold_s", 0.25), blank=True)
                        self._wait_cover(cfg)
                        self._cut(target)
                    else:
                        self._title_off()
                        self._switch(target, cfg)
                except Exception as e:
                    self.last = f"OBS: {e}"
                    return False, self.last
            else:
                self._title_off()
            self._gen += 1
            gen, shown = self._gen, self.shown
            self.shown, self.back_to = None, None
        if shown:
            self._spawn(self._hide_later, gen, shown, cfg)
        self.last = f"zurück zu {target}"
        return True, self.last

    def _hide_later(self, gen, source, cfg):
        self._sleep(float(cfg.get("hide_after_s") or 0))
        with self._lock:
            if gen != self._gen:
                return                        # shown again meanwhile
            try:
                self._call(lambda o: o.set_visible(cfg["scene"], source, False))
            except Exception as e:
                print(f"[tables] hide {source}: {e}")

    def status(self) -> dict:
        cfg = self.cfg()
        return {"scene": cfg["scene"], "transition": cfg.get("transition"), "shown": self.shown,
                "back_to": self.back_to, "last": self.last, "note": self.note, "title": self.title,
                "presses": [{"t": t, "key": k, "msg": m} for t, k, m in reversed(self.presses)],
                "items": {k: v for k, v in cfg["items"].items()}}


TITLE_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>Titelkarte</title>
<style>
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:rgba(0,0,0,0)}
body.debug{background:#334}
#card{position:fixed;inset:0;display:none;overflow:hidden}
#bg{position:absolute;inset:-5% -20%;background:linear-gradient(115deg,#07122b 0%,#0d2557 55%,#081633 100%);
    transform:translateX(-120%) skewX(-12deg)}
#stripe{position:absolute;left:0;right:0;top:61%;height:10px;background:#ff7a00;transform:scaleX(0);transform-origin:left}
#txt{position:absolute;left:0;right:0;top:40%;text-align:center;font-family:'Segoe UI','Noto Sans',Arial,sans-serif;
     color:#fff;opacity:0;transform:translateX(-80px)}
#sub{font-size:38px;font-weight:700;letter-spacing:8px;color:#9fb4e8;margin-bottom:10px;text-transform:uppercase}
#title{font-size:150px;font-weight:900;font-style:italic;letter-spacing:6px;line-height:1;text-transform:uppercase;
       text-shadow:0 6px 30px rgba(0,0,0,.5)}
.in #bg{transition:transform .32s cubic-bezier(.2,.8,.2,1);transform:translateX(0) skewX(-12deg)}
.in #stripe{transition:transform .5s .12s cubic-bezier(.2,.8,.2,1);transform:scaleX(1)}
.in #txt{transition:opacity .25s .1s,transform .45s .1s cubic-bezier(.2,.8,.2,1);opacity:1;transform:none}
.out #bg{transition:transform .42s cubic-bezier(.6,0,.8,.4);transform:translateX(130%) skewX(-12deg)}
.out #stripe{transition:transform .3s;transform:scaleX(0);transform-origin:right}
.out #txt{transition:opacity .2s,transform .35s cubic-bezier(.6,0,.8,.4);opacity:0;transform:translateX(160px)}
</style></head><body>
<div id="card"><div id="bg"></div><div id="stripe"></div>
<div id="txt"><div id="sub"></div><div id="title"></div></div></div>
<script>
const qs = new URLSearchParams(location.search);
if (qs.get('debug') === '1') document.body.classList.add('debug');
const card = document.getElementById('card');
let seq = null, timer = null, outT = null;
function hide() { card.className = ''; card.style.display = 'none'; }
function play(s) {
  clearTimeout(timer); clearTimeout(outT);
  document.getElementById('title').textContent = s.title;
  document.getElementById('sub').textContent = s.sub || '';
  card.className = ''; card.style.display = 'block';
  void card.offsetWidth;                       // restart the animation
  card.className = 'in';
  const mySeq = s.seq;
  setTimeout(() => {                           // background wipe (.32 s) done = picture covered
    if (mySeq != null && !qs.get('demo'))
      fetch('/tables/covered?seq=' + mySeq, {cache: 'no-store'}).catch(() => {});
  }, 330);
  timer = setTimeout(() => {
    card.className = 'out';
    outT = setTimeout(hide, 500);
  }, 330 + (s.hold_s || 1.6) * 1000);
}
async function poll() {
  try {
    const r = await fetch('/tables/state' + (qs.get('demo') ? '?demo=1' : ''), {cache: 'no-store'});
    const s = await r.json();
    if (seq === null) seq = s.seq;              // a page (re)load shows nothing old
    else if (s.seq !== seq) {
      seq = s.seq;
      if (s.title || s.blank) play(s); else { clearTimeout(timer); clearTimeout(outT); hide(); }
    }
  } catch (e) {}
  setTimeout(poll, 100);
}
if (qs.get('demo')) play({title: qs.get('demo'), sub: qs.get('sub') || 'CAS IEC', hold_s: 30});
else poll();
</script></body></html>"""
