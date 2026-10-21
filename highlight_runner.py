"""
highlight_runner.py — plays the scenes of highlights.py from the iRacing
replay and records them with OBS into ONE video.

Per scene: replay paused -> seek to the scene start (SessionTime, the log's
session_num) -> camera on the car (a TV camera; never Scenic / Cockpit /
any Chase cam, race_director.NEVER_CAMS) -> play 1x -> OBS ResumeRecord
(StartRecord at the first scene) -> wait until the replay's SessionTime
reaches the scene end, rotating the TV camera in long scenes -> PauseRecord.
StopRecord at the end; OBS writes one MP4 (its own recording path / format).

Optional per run:
  grid   — the "grid" scene (starting grid, highlights.py) shows the OBS
           source sources["grid"] ("Startaufstellung" = obs_loaders/grid.html,
           the quali result) on the race scene while the pace lap plays, then
           hides it. A scene that starts where the previous one ended (grid ->
           start) runs on without seek or record pause.
  fade   — scenes are joined by a CROSSFADE (default on): at a scene's end
           OBS saves a still of the program, shows it (image source
           sources["still"] in the scene scenes["black"] "Highlight Schwarz",
           both created if missing; Cut, invisible), the record pauses, the
           replay jumps, the record goes on with the still and OBS's fade
           transition dissolves it into the new scene — no black between
           scenes (Thomas, 2026-10-21: first hard cuts were "choppy", then the
           black dip "visible every time"). The video still fades in from and
           out to black at its very start / end; without a still (screenshot
           failed) a scene change dips to black. Moderator <-> race the same
           way. OBS's transition is put back afterwards.
  tables — after the finish, before the outro: final result and championship
           table on the Moderator scene (the Abspann sources "Ergebnis" /
           "Meisterschaft"), split PRO -> AM in a Pro/Am field (opts["proam"]),
           the class pinned through `pin` (5010 /api/prerace_pin, which 5002
           /solid follows too), table_s each, crossfaded (Thomas, 2026-10-21)
  card   — the title card page (/highlights/card on the dashboard) shows the
           scene's title + line for the first CARD_S seconds of each scene
           (it reads status()["card"]; nothing to switch in OBS)
  voice  — each scene's line is spoken (Commentator.say_show + edge-tts
           mood): through the OBS voice page, so it lands in the recording
  gabi   — intro / outro by Gabi (avatar_sync.AvatarRenderer videos on the
           Moderator scene, ordered ahead with prepare_avatar()); without a
           finished video the voice says it, without voice it is skipped
  record — False = rehearsal: plays the scenes, OBS untouched

Everything external is injected, so the whole run is testable with a fake
replay and fake_obs.py:
  replay: prepare() / restore() / hold() / connected() / camera_groups()
          / session_time() / seek(session_num, t) / play() / pause()
          / switch(car_number, group_id)          (PollerReplay = dashboard)
  obs:    obs_ws.ObsClient (call / set_scene / set_visible / set_media_file
          / media_restart / media_status)
  speaker: say(text, mood) -> id, is_done(id)
  avatar: avatar_sync.AvatarRenderer (ensure / get / available / status)
"""
from __future__ import annotations

import os
import tempfile
import threading
import time

from race_director import NEVER_CAMS

DEFAULTS = {
    "scenes": {"race": "iRacing", "moderator": "Moderator", "black": "Highlight Schwarz"},
    "sources": {"avatar_talk": "Gabi", "avatar_idle": "Gabi Ruhe", "grid": "Startaufstellung",
                "still": "Highlight Standbild",
                # the Abspann pages on the Moderator scene (results_solid / champ_solid loaders)
                "results": "Ergebnis", "standings": "Meisterschaft"},
    "table_s": 10.0,        # each results / standings page after the finish
    "preroll_s": 1.5,       # seek this much before the scene: camera settles off the record
    "card_s": 5.0,          # title card on screen at the start of each scene
    "cam_every_s": 12.0,    # next TV camera inside a long scene
    "stall_s": 4.0,         # replay time not moving this long -> kick play again
    "settle_s": 0.4,        # after a camera switch before recording
    "fade_s": 0.6,          # fade through black between scenes (OBS fade transition); 0 = hard cuts
}

# camera plans per scene kind (names) — TV only: the in-car Gyro showed a dark
# cockpit in the first real reel (2026-10-21); never Scenic / Cockpit / Chase
HL_CAMS = {
    "grid":    ["Blimp", "Chopper", "TV2", "TV1"],
    "start":   ["TV1", "TV2", "TV3"],
    "restart": ["TV1", "TV2", "TV3"],
    "caution": ["TV2", "TV1", "TV3"],
    "finish":  ["TV1", "TV3", "TV2"],
    "class_finish": ["TV1", "TV2"],
    "pass":    ["TV1", "TV2", "TV3"],
    "contact": ["TV1", "TV2", "TV3"],
    "spin":    ["TV1", "TV2", "TV3"],
    "fastest": ["TV1", "TV2", "TV3"],
    "dotd":    ["TV1", "TV2", "TV3"],
}

MOOD = {"fastest": "normal", "caution": "hype", "restart": "hype", "start": "hype", "finish": "hype"}


def _norm(s) -> str:
    return (s or "").upper().replace(" ", "")


def pick_group(kind: str, groups: list, k: int = 0):
    """Camera group for a scene kind, k-th of its plan; never Scenic / Cockpit / Chase."""
    by_name = {}
    for g in groups or []:
        n = _norm(g.get("name"))
        if n and not any(x in n for x in NEVER_CAMS):
            by_name.setdefault(n, g)
    names = [n for n in HL_CAMS.get(kind, ["TV1"]) if _norm(n) in by_name]
    if not names:
        return next(iter(by_name.values()), None)
    return by_name[_norm(names[k % len(names)])]


def default_texts(info: dict) -> dict:
    track = info.get("track") or "der Strecke"
    win = info.get("winner")
    intro = f"Willkommen zu den Highlights vom Rennen in {track}! Hier sind die besten Szenen."
    outro = (f"Das war's — {win} gewinnt in {track}. Danke fürs Zuschauen, bis zum nächsten Rennen!"
             if win else "Das waren die Highlights. Danke fürs Zuschauen, bis zum nächsten Rennen!")
    return {"intro": intro, "outro": outro}


class HighlightRunner:
    def __init__(self, replay, obs=None, speaker=None, avatar=None, cfg: dict | None = None,
                 clock=time.time, sleep=time.sleep, pin=None):
        self.replay, self.obs, self.speaker, self.avatar = replay, obs, speaker, avatar
        self.pin = pin                 # pin(cls or None): Pro/Am class of the 5002 / 5010 pages
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.cfg["scenes"] = {**DEFAULTS["scenes"], **((cfg or {}).get("scenes") or {})}
        self.cfg["sources"] = {**DEFAULTS["sources"], **((cfg or {}).get("sources") or {})}
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.state = "idle"           # idle / running / done / stopped / error
        self.phase = ""
        self.scenes: list = []
        self.opts: dict = {}
        self.current = -1
        self.log: list = []
        self.error = ""
        self.output = ""
        self.started_at = None
        self.ended_at = None
        self.rec_started = False
        self.rec_paused = False
        self.rec_seconds = 0.0
        self._rec_from = None
        self._fade_ok, self._fade_saved, self._program = False, None, None
        self._fade_name = self._cut_name = None
        self._still_ok, self._still_n, self._canvas = False, 0, (1920, 1080)
        self._card_until = 0.0
        self._card = None

    # ------------------------------------------------------------- public
    def start(self, scenes: list, opts: dict | None = None) -> tuple[bool, str]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False, "läuft schon"
            scenes = [dict(s) for s in scenes if float(s["t_end"]) > float(s["t_start"])]
            if not scenes:
                return False, "keine Szenen"
            self.scenes = scenes
            for i, s in enumerate(self.scenes):
                s["id"] = i + 1
                s["state"] = "wartet"
            self.opts = {"record": True, "card": True, "voice": False, "gabi": False, "fade": True,
                         "tables": True, "proam": False,
                         "intro": "", "outro": "", **(opts or {})}
            self._stop.clear()
            self.state, self.phase, self.current, self.error, self.output = "running", "start", -1, "", ""
            self.log = []
            self.rec_started = self.rec_paused = False
            self.rec_seconds, self._rec_from = 0.0, None
            self.started_at, self.ended_at = self.clock(), None
            self._fade_ok, self._fade_saved, self._program = False, None, None
            self._thread = threading.Thread(target=self._run, daemon=True, name="Highlights")
            self._thread.start()
        return True, "gestartet"

    def stop(self) -> None:
        self._stop.set()

    def wait(self, timeout=None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def prepare_avatar(self, intro: str, outro: str) -> None:
        """Order Gabi's intro / outro videos now (sync.so takes minutes)."""
        if self.avatar is None or not self.avatar.available():
            return
        for seg, text in (("intro", intro), ("outro", outro)):
            if (text or "").strip():
                self.avatar.ensure("highlights", seg, text)

    def check_avatar(self, intro: str, outro: str) -> None:
        """Look for videos made earlier for these texts (free) — shown as ready."""
        if self.avatar is None:
            return
        for seg, text in (("intro", intro), ("outro", outro)):
            if (text or "").strip():
                self.avatar.get("highlights", seg, text)

    def status(self) -> dict:
        now = self.clock()
        rec = self.rec_seconds + ((now - self._rec_from) if self._rec_from is not None else 0.0)
        card = self._card if (self._card and now < self._card_until) else None
        return {
            "state": self.state, "phase": self.phase, "current": self.current,
            "scenes": [{k: s.get(k) for k in ("id", "kind", "title", "driver", "car_number", "t_start",
                                              "t_end", "text", "state")} for s in self.scenes],
            "total_s": round(sum(s["t_end"] - s["t_start"] for s in self.scenes), 1),
            "recorded_s": round(rec, 1),
            "recording": bool(self.rec_started and not self.rec_paused),
            "output": self.output, "error": self.error,
            "elapsed_s": round((self.ended_at or now) - self.started_at, 1) if self.started_at else 0,
            "log": self.log[-40:], "opts": {k: v for k, v in self.opts.items() if k not in ("intro", "outro")},
            "card": card,
            "avatar": self.avatar.status("highlights") if (self.avatar is not None) else {},
        }

    # ------------------------------------------------------------- run
    def _note(self, msg: str) -> None:
        t = self.clock() - (self.started_at or self.clock())
        self.log.append(f"{t:6.1f}s  {msg}")
        print(f"[highlights] {msg}")

    def _obs(self, fn, what: str, required=False):
        if self.obs is None:
            if required:
                raise RuntimeError("kein OBS")
            return None
        try:
            return fn()
        except Exception as e:
            self._note(f"OBS: {what} fehlgeschlagen: {e}")
            if required:
                raise
            return None

    def _run(self):
        rec = bool(self.opts.get("record")) and self.obs is not None
        prepared = False
        try:
            if not self.replay.connected():
                raise RuntimeError("iRacing nicht verbunden — Replay des Rennens öffnen")
            if rec:
                st = self._obs(lambda: self.obs.call("GetRecordStatus"), "Aufnahme-Status", required=True)
                if st.get("outputActive"):
                    raise RuntimeError("OBS nimmt schon auf — erst die laufende Aufnahme beenden")
                why = self._pause_blocked()
                if why:
                    raise RuntimeError(why)
            self.replay.prepare()
            prepared = True
            for sc in self.scenes:
                if sc.get("overlay"):
                    self._overlay(sc, False)
            self._tables_off()
            self._note(f"{len(self.scenes)} Szenen, {sum(s['t_end'] - s['t_start'] for s in self.scenes):.0f} s"
                       + (" – Aufnahme" if rec else " – Probelauf ohne Aufnahme"))
            self._fade_setup()
            if self._fade_ok:
                self._trans_now = self._fade_name
                self._show("black", wait=False)       # everything starts out of black
            if self.opts.get("intro") and (self.opts.get("gabi") or self.opts.get("voice")):
                self._moderation("intro", self.opts["intro"], rec)
            for i, sc in enumerate(self.scenes):
                if self._stop.is_set():
                    break
                self.current = i
                nxt = self.scenes[i + 1] if i + 1 < len(self.scenes) else None
                self._scene(sc, i, rec, cont_prev=self._contiguous(self.scenes[i - 1] if i else None, sc),
                            cont_next=self._contiguous(sc, nxt),
                            last=nxt is None and not self._outro_planned() and not self._tables_planned())
            if not self._stop.is_set() and self._tables_planned():
                self._tables(rec)
            if not self._stop.is_set() and self.opts.get("outro") and (self.opts.get("gabi") or self.opts.get("voice")):
                self._moderation("outro", self.opts["outro"], rec)
            self.state = "stopped" if self._stop.is_set() else "done"
        except Exception as e:
            self.error = f"{e}"
            self.state = "error"
            self._note(f"FEHLER: {e}")
        finally:
            self._card = None
            if (rec and self.rec_started and self._fade_ok and self._program != "black"
                    and not self.rec_paused and self.state != "error"):
                self._leave(last=True)                # fade out at the very end
            if rec and self.rec_started:
                self._rec_resume_clock(False)
                res = self._obs(lambda: self.obs.call("StopRecord"), "Aufnahme beenden") or {}
                self.output = res.get("outputPath") or ""
                self._note("Aufnahme beendet" + (f": {self.output}" if self.output else ""))
            if prepared:
                try:
                    self.replay.pause()
                except Exception:
                    pass
                try:
                    self.replay.restore()
                except Exception as e:
                    self._note(f"Dashboard zurücksetzen: {e}")
            self._tables_off()
            if self._fade_ok:
                if self._program == "black":          # never leave the program on black
                    self._show("race", wait=False)
                if self._still_ok:
                    self._obs(lambda: self.obs.set_visible(self.cfg["scenes"]["black"],
                                                           self.cfg["sources"]["still"], False), "Standbild aus")
                self._fade_restore()
            self.phase = ""
            self.current = -1
            self.ended_at = self.clock()

    # fades: OBS fade transition through an empty black scene
    def _show(self, key: str, wait: bool = True):
        """Program scene by key (race / moderator / black); with fades on the
        switch IS the fade and we wait it out."""
        if self.obs is None or self._program == key:
            return
        name = self.cfg["scenes"][key]
        self._obs(lambda: self.obs.set_scene(name), f"Szene {name}")
        self._program = key
        if wait and self._fade_ok:
            self.sleep(float(self.cfg["fade_s"]) + 0.1)

    def _fade_setup(self):
        self._fade_ok = False
        fade_s = float(self.cfg.get("fade_s") or 0)
        if self.obs is None or not self.opts.get("fade") or fade_s <= 0:
            return
        try:
            lst = self.obs.call("GetSceneTransitionList")
            fade = next((t["transitionName"] for t in lst.get("transitions") or []
                         if t.get("transitionKind") == "fade_transition"), None)
            self._cut_name = next((t["transitionName"] for t in lst.get("transitions") or []
                                   if t.get("transitionKind") == "cut_transition"), None)
            self._fade_name = fade
            if not fade:
                self._note("Kein Überblenden-Übergang in OBS – harte Schnitte")
                return
            cur = self.obs.call("GetCurrentSceneTransition")
            self._fade_saved = (cur.get("transitionName"), cur.get("transitionDuration"))
            black = self.cfg["scenes"]["black"]
            if black not in self.obs.scenes():
                self.obs.call("CreateScene", {"sceneName": black})
                self._note(f"OBS-Szene „{black}“ angelegt (leer = schwarz, für die Blenden)")
            self.obs.call("SetCurrentSceneTransition", {"transitionName": fade})
            self.obs.call("SetCurrentSceneTransitionDuration", {"transitionDuration": int(fade_s * 1000)})
            self._fade_ok = True
            self._still_setup(black)
        except Exception as e:
            self._note(f"Blenden aus ({e}) – harte Schnitte")
            self._fade_restore()

    def _still_setup(self, black: str):
        """The image source the crossfade shows the last frame with."""
        self._still_ok = False
        still = (self.cfg["sources"] or {}).get("still")
        if not still or not self._cut_name:
            return
        try:
            v = self.obs.call("GetVideoSettings")
            self._canvas = (int(v.get("baseWidth") or 1920), int(v.get("baseHeight") or 1080))
            if still not in self.obs.scene_items(black):
                self.obs.call("CreateInput", {"sceneName": black, "inputName": still, "inputKind": "image_source",
                                              "inputSettings": {}, "sceneItemEnabled": False})
                self._note(f"OBS-Bildquelle „{still}“ in „{black}“ angelegt (Standbild für die Überblendung)")
            self.obs.set_visible(black, still, False)
            self._still_ok = True
        except Exception as e:
            self._note(f"Standbild-Überblendung aus ({e}) – Blende über Schwarz")

    def _set_transition(self, name):
        if name and name != getattr(self, "_trans_now", None):
            self._obs(lambda: self.obs.call("SetCurrentSceneTransition", {"transitionName": name}), "Übergang")
            self._trans_now = name

    def _leave(self, last: bool):
        """End of a shot, record still running: hold its last frame (the next
        shot dissolves out of it) — or, at the very end / without a still, fade
        to black."""
        if not self._fade_ok or self._stop.is_set():
            return
        black = self.cfg["scenes"]["black"]
        still = self.cfg["sources"].get("still")
        if not last and self._still_ok and self._program in ("race", "moderator"):
            self._still_n += 1
            path = os.path.join(tempfile.gettempdir(), f"cas_highlight_still_{self._still_n % 2}.jpg")
            src = self.cfg["scenes"][self._program]
            w, h = self._canvas
            ok = self._obs(lambda: self.obs.call("SaveSourceScreenshot", {
                "sourceName": src, "imageFormat": "jpg", "imageFilePath": path, "imageWidth": w,
                "imageHeight": h, "imageCompressionQuality": 95}), "Standbild") is not None
            if ok:
                self._obs(lambda: self.obs.call("SetInputSettings", {"inputName": still,
                                                                    "inputSettings": {"file": path}}), "Standbild laden")
                self._obs(lambda: self.obs.set_visible(black, still, True), "Standbild zeigen")
                self.sleep(0.1)                           # image loaded before the cut
                self._set_transition(self._cut_name)      # same picture: the cut is invisible
                self._show("black", wait=False)
                self.sleep(0.1)
                self._set_transition(self._fade_name)     # the next scene dissolves out of it
                return
        if still and self._still_ok:
            self._obs(lambda: self.obs.set_visible(black, still, False), "Standbild aus")
        self._set_transition(self._fade_name)
        self._show("black")                               # fade to black, still recording

    def _fade_restore(self):
        if not self._fade_saved:
            return
        name, dur = self._fade_saved
        self._fade_saved = None
        if name:
            self._obs(lambda: self.obs.call("SetCurrentSceneTransition", {"transitionName": name}),
                      "Übergang zurücksetzen")
        if dur:
            self._obs(lambda: self.obs.call("SetCurrentSceneTransitionDuration",
                                            {"transitionDuration": int(dur)}), "Übergangsdauer zurücksetzen")

    # recording on / off around each scene
    # OBS starts / pauses its recording output ASYNCHRONOUSLY: StartRecord
    # answers before the output runs, and a PauseRecord sent in that moment is
    # lost (first real run: Gabi's intro ended at once -> ResumeRecord "503 not
    # paused"). Every step therefore waits until GetRecordStatus shows it.
    def _pause_blocked(self) -> str:
        """OBS cannot PAUSE a recording that shares the stream encoder (Simple
        output: Recording Quality "Same as stream"; Advanced: recording encoder
        "Use stream encoder") — PauseRecord is accepted and nothing happens, so
        every seek between scenes lands in the video (real run 2026-10-21).
        Read from the active OBS profile; '' = fine or unknown."""
        def param(cat, name):
            try:
                d = self.obs.call("GetProfileParameter", {"parameterCategory": cat, "parameterName": name})
                return d.get("parameterValue") or d.get("defaultParameterValue") or ""
            except Exception:
                return None
        mode = param("Output", "Mode")
        if mode is None:
            return ""
        if (mode or "Simple").lower() == "simple":
            if (param("SimpleOutput", "RecQuality") or "") == "Stream":
                return ("OBS kann so nicht pausieren: Einstellungen → Ausgabe → Aufnahme → Aufnahmequalität "
                        "steht auf „Gleich wie Stream“. Bitte z. B. „Hohe Qualität, mittlere Dateigröße“ "
                        "wählen, dann neu starten.")
        elif (param("AdvOut", "RecEncoder") or "") == "none":
            return ("OBS kann so nicht pausieren: Einstellungen → Ausgabe → Aufnahme → Video-Encoder steht auf "
                    "„Stream-Encoder verwenden“. Bitte einen eigenen Encoder wählen, dann neu starten.")
        return ""

    def _rec_status(self):
        st = self._obs(lambda: self.obs.call("GetRecordStatus"), "Aufnahme-Status") or {}
        return bool(st.get("outputActive")), bool(st.get("outputPaused"))

    def _rec_wait(self, active=None, paused=None, timeout=5.0) -> bool:
        t0 = self.clock()
        while True:
            a, p = self._rec_status()
            if (active is None or a == active) and (paused is None or p == paused):
                return True
            if self.clock() - t0 > timeout:
                return False
            self.sleep(0.1)

    def _rec_on(self, rec):
        if not rec:
            return
        if not self.rec_started:
            self._obs(lambda: self.obs.call("StartRecord"), "Aufnahme starten", required=True)
            self.rec_started = True
            if not self._rec_wait(active=True):
                raise RuntimeError("OBS-Aufnahme startet nicht (Aufnahmepfad / Encoder in OBS prüfen)")
            self._note("Aufnahme gestartet")
        elif self.rec_paused:
            a, p = self._rec_status()
            if p:
                self._obs(lambda: self.obs.call("ResumeRecord"), "Aufnahme fortsetzen", required=True)
                self._rec_wait(paused=False, timeout=3.0)
            elif not a:
                raise RuntimeError("OBS-Aufnahme läuft nicht mehr")
            else:
                self._note("Aufnahme lief schon weiter (Pause war nicht angekommen)")
        self.rec_paused = False
        self._rec_resume_clock(True)

    def _rec_off(self, rec):
        if not rec or not self.rec_started or self.rec_paused:
            return
        self._rec_wait(active=True)
        self._obs(lambda: self.obs.call("PauseRecord"), "Aufnahme pausieren", required=True)
        if not self._rec_wait(paused=True, timeout=3.0):
            self._note("OBS meldet die Pause nicht")
        self.rec_paused = True
        self._rec_resume_clock(False)

    def _rec_resume_clock(self, on):
        now = self.clock()
        if on and self._rec_from is None:
            self._rec_from = now
        elif not on and self._rec_from is not None:
            self.rec_seconds += now - self._rec_from
            self._rec_from = None

    def _tables_planned(self) -> bool:
        src = self.cfg["sources"]
        return bool(self.opts.get("tables") and self.obs is not None and (src.get("results") or src.get("standings")))

    def _pages(self) -> list:
        """[(source key, class or None, spoken line)] after the finish."""
        out = []
        classes = ["PRO", "AM"] if self.opts.get("proam") else [None]
        for key, what in (("results", "Das Endergebnis"), ("standings", "Die Meisterschaft nach diesem Rennen")):
            if not self.cfg["sources"].get(key):
                continue
            for c in classes:
                out.append((key, c, f"{what}" + (f" in der Klasse {'Pro' if c == 'PRO' else 'A M'}." if c else ".")))
        return out

    def _pin(self, cls):
        if self.pin is None:
            return
        try:
            self.pin(cls)
        except Exception as e:
            self._note(f"Klasse anheften ({cls}): {e}")

    def _tables_off(self):
        if self.obs is None:
            return
        mod = self.cfg["scenes"]["moderator"]
        for key in ("results", "standings"):
            name = self.cfg["sources"].get(key)
            if name:
                try:
                    self.obs.set_visible(mod, name, False)
                except Exception:
                    pass
        if self.opts.get("tables"):
            self._pin(None)

    def _tables(self, rec):
        """Final result + championship, PRO then AM, on the Moderator scene:
        while the record is paused the class is pinned and the page shown
        (the pages poll the pin ~1 s), then record on and crossfade in."""
        mod = self.cfg["scenes"]["moderator"]
        src = self.cfg["sources"]
        pages = self._pages()
        if src.get("avatar_idle"):
            self._obs(lambda: self.obs.set_visible(mod, src["avatar_idle"], True), "Gabi Ruhe")
        shown = None
        for n, (key, cls, line) in enumerate(pages):
            if self._stop.is_set():
                break
            self.phase = ("Ergebnis" if key == "results" else "Meisterschaft") + (f" {cls}" if cls else "")
            self._pin(cls)
            if shown != key:
                if shown:
                    self._obs(lambda: self.obs.set_visible(mod, src[shown], False), f"{src[shown]} aus")
                ok = self._obs(lambda: (self.obs.set_visible(mod, src[key], True), True)[1], f"Quelle {src[key]}")
                if ok is None:
                    self._note(f"  (Quelle „{src[key]}“ fehlt in Szene „{mod}“ – Seite übersprungen)")
                    continue
                shown = key
            self.sleep(2.0)                               # the page follows the pin (polls ~1 s)
            self._rec_on(rec)
            self._show("moderator")                       # crossfade in from the still
            self._note(f"Tafel {self.phase}")
            if self.opts.get("voice") and self.speaker is not None:
                try:
                    self.speaker.say(line, "normal")
                except Exception as e:
                    self._note(f"Stimme: {e}")
            t_end = self.clock() + float(self.cfg["table_s"])
            while not self._stop.is_set() and self.clock() < t_end:
                self.sleep(0.1)
            last = n == len(pages) - 1 and not self._outro_planned()
            self._leave(last)
            self._rec_off(rec)
        if shown:
            self._obs(lambda: self.obs.set_visible(mod, src[shown], False), f"{src[shown]} aus")
        self._pin(None)

    def _outro_planned(self) -> bool:
        return bool(self.opts.get("outro") and (self.opts.get("gabi") or self.opts.get("voice")))

    @staticmethod
    def _contiguous(a, b) -> bool:
        """b starts where a ended (same session): play on, no seek / pause."""
        return bool(a and b and a.get("session_num") == b.get("session_num")
                    and 0 <= float(b["t_start"]) - float(a["t_end"]) <= 1.0)

    def _overlay(self, sc, on: bool):
        """The scene's own OBS source (grid scene: the starting grid)."""
        key = sc.get("overlay")
        name = (self.cfg["sources"] or {}).get(key) if key else None
        if not name or self.obs is None:
            return
        race = self.cfg["scenes"]["race"]
        if self._obs(lambda: (self.obs.set_visible(race, name, on), True)[1], f"Quelle {name}") is None and on:
            self._note(f"  (Quelle „{name}“ in Szene „{race}“ anlegen: obs_loaders/grid.html)")

    def _scene(self, sc, i, rec, cont_prev=False, cont_next=False, last=False):
        sn = sc.get("session_num")
        t0, t1 = float(sc["t_start"]), float(sc["t_end"])
        groups = self.replay.camera_groups()
        k = 0
        g = pick_group(sc["kind"], groups, k)
        if g is None:
            raise RuntimeError("keine Kameragruppe (TV1 …) gefunden")
        if cont_prev and self.rec_started and not self.rec_paused:
            self.replay.switch(sc["car_number"], int(g["id"]))     # runs straight on from the last scene
        else:
            sc["state"] = "spult"
            self.phase = f"Szene {i + 1}: spulen"
            if self.replay.seek(sn, max(0.0, t0 - float(self.cfg["preroll_s"]))) is False:
                raise RuntimeError(f"Replay springt nicht zu Session {sn}, {t0:.0f} s — ist das richtige "
                                   f"Rennen als Replay offen? (iRacing-Replay einmal anklicken / Play)")
            self.replay.switch(sc["car_number"], int(g["id"]))
            self.replay.play()
            self.replay.switch(sc["car_number"], int(g["id"]))   # again while playing: a paused replay may ignore it
            # record from the scene start: stop waiting a camera-settle early
            # (and a fade earlier, so the fade-in has finished at t_start)
            fade = float(self.cfg["fade_s"]) if self._fade_ok else 0.0
            self._wait_time(t0 - 0.2 - float(self.cfg["settle_s"]) - fade, float(self.cfg["preroll_s"]) + 8.0)
            self.sleep(float(self.cfg["settle_s"]))
        if self._stop.is_set():
            sc["state"] = "abgebrochen"
            return
        self._overlay(sc, True)
        self._rec_on(rec)
        self._show("race", wait=False)                   # fade in from black (or the cut)
        sc["state"] = "läuft"
        self.phase = f"Szene {i + 1}: {sc.get('title')}"
        self._note(f"Szene {i + 1} {sc['kind']} #{sc['car_number']} {sc.get('driver', '')} "
                   f"{t0:.0f}-{t1:.0f} s, Kamera {g.get('name')}")
        if self.opts.get("card") and not sc.get("overlay"):     # the grid graphic is its own title
            self._card = {"title": sc.get("title"), "text": sc.get("text"), "driver": sc.get("driver"),
                          "number": sc.get("car_number"), "kind": sc["kind"], "n": i + 1, "of": len(self.scenes)}
            self._card_until = self.clock() + float(self.cfg["card_s"])
        if self.opts.get("voice") and self.speaker is not None and sc.get("text"):
            try:
                self.speaker.say(sc["text"], MOOD.get(sc["kind"], "hype"))
            except Exception as e:
                self._note(f"Stimme: {e}")
        # play to the end; next TV camera every cam_every_s (not right before the end)
        ok = self._wait_time(t1, (t1 - t0) * 1.4 + 10.0)
        if self._stop.is_set():
            sc["state"] = "abgebrochen"
        else:
            sc["state"] = "fertig" if ok else "Zeit überschritten"
        if not (cont_next and ok and not self._stop.is_set()):
            self._leave(last)                            # hold the last frame (or fade out), recording
            self._overlay(sc, False)
            self._rec_off(rec)
        else:
            self._overlay(sc, False)

    def _wait_time(self, target: float, timeout: float) -> bool:
        """Wait until the replay's SessionTime reaches `target`. Kicks play
        again when the time stalls, rotates the camera in long scenes."""
        start = self.clock()
        last_t, last_move = None, start
        sc = self.scenes[self.current] if 0 <= self.current < len(self.scenes) else None
        recording = self.state == "running" and sc is not None and sc.get("state") == "läuft"
        cam_at, k = start, 0
        while not self._stop.is_set():
            try:
                self.replay.hold()
                t = self.replay.session_time()
            except Exception:
                t = None
            now = self.clock()
            if t is not None and t >= target:
                return True
            if t is not None and (last_t is None or abs(t - last_t) > 0.01):
                last_t, last_move = t, now
            elif now - last_move > float(self.cfg["stall_s"]):
                self._note("Replay steht – Play erneut")
                self.replay.play()
                last_move = now
            if recording and t is not None and now - cam_at >= float(self.cfg["cam_every_s"]) \
                    and target - t > 5.0:
                k += 1
                g = pick_group(sc["kind"], self.replay.camera_groups(), k)
                if g is not None:
                    self.replay.switch(sc["car_number"], int(g["id"]))
                    self._note(f"  Kamera {g.get('name')}")
                cam_at = now
            if now - start > timeout:
                self._note(f"Zeitlimit: Replay bei {t if t is not None else '?'} s, Ziel {target:.0f} s")
                return False
            self.sleep(0.1)
        return False

    def _play_talk(self, mod: str, talk: str, path) -> bool:
        """Play Gabi's video once in the media source `talk`; True = it played.

        Real OBS (2026-10-21, checked by hand on Thomas's OBS): after a
        MediaStop the same file does NOT start again with RESTART — OBS reports
        PLAYING but the cursor stays at 0 (the second run was silent). Loading
        the file afresh (empty path, then the file) and activating the source
        (restart_on_activate) plays it; RESTART only as a second try. The
        source is only HIDDEN afterwards, never stopped. "Playing" = the
        cursor moves; done at the clip's length (the source may loop, then it
        never says ended), when the cursor jumps back, or it stalls."""
        o = self.obs
        self._obs(lambda: o.set_visible(mod, talk, False), "Gabi verstecken")
        self._obs(lambda: o.call("SetInputSettings", {"inputName": talk, "inputSettings": {"local_file": ""}}),
                  "Gabi-Quelle leeren")
        self._obs(lambda: o.set_media_file(talk, str(path)), "Gabi-Video laden")
        self._obs(lambda: o.set_visible(mod, talk, True), "Gabi zeigen")
        t0, start, dur, last_cur, moved_at, kicked = self.clock(), None, None, None, None, False
        try:
            while not self._stop.is_set() and self.clock() - t0 < 90:
                now = self.clock()
                st = self._obs(lambda: o.media_status(talk), "Gabi-Video Status") or {}
                state, cur = st.get("state"), st.get("cursor_s")
                if (st.get("duration_s") or 0) > 1.0:
                    dur = st["duration_s"]
                if start is None:
                    # a FRESH start: the first readings can still carry the old
                    # playback's cursor (e.g. 6.9 s of the last run)
                    if cur is not None and 0.05 < cur < 1.5 and last_cur is not None and cur > last_cur:
                        start, last_cur, moved_at = now - cur, cur, now
                    elif now - t0 > 1.2 and not kicked:
                        self._obs(lambda: o.media_restart(talk), "Gabi-Video neu starten")
                        kicked = True
                    elif now - t0 > 6.0:
                        self._note("Gabi-Video startet nicht (OBS-Quelle hängt)")
                        return False
                    last_cur = cur if cur is not None else 0.0
                    self.sleep(0.1)
                    continue
                if state in ("OBS_MEDIA_STATE_ENDED", "OBS_MEDIA_STATE_STOPPED",
                             "OBS_MEDIA_STATE_ERROR", "OBS_MEDIA_STATE_NONE"):
                    break
                if dur and now - start >= dur - 0.15:
                    break                        # a looping source never says "ended"
                if cur is not None and last_cur is not None and cur + 0.5 < last_cur:
                    break                        # looped back to the start
                if cur is not None and cur != last_cur:
                    last_cur, moved_at = cur, now
                elif now - moved_at > 2.0:
                    break                        # stalled
                self.sleep(0.1)
            return start is not None
        finally:
            self._obs(lambda: o.set_visible(mod, talk, False), "Gabi ausblenden")

    def _moderation(self, seg: str, text: str, rec: bool) -> bool:
        """Intro / outro on the Moderator scene: Gabi's video, else the voice."""
        path = None
        if self.opts.get("gabi") and self.avatar is not None:
            path = self.avatar.get("highlights", seg, text)
            if path is None:
                self._note(f"Gabi-Video {seg} nicht fertig – " + ("nur Stimme" if self.opts.get("voice") else "übersprungen"))
        if path is None and not (self.opts.get("voice") and self.speaker is not None):
            return False
        mod = self.cfg["scenes"]["moderator"]
        src = self.cfg["sources"]
        self.phase = "Intro" if seg == "intro" else "Outro"
        if src.get("avatar_idle"):
            self._obs(lambda: self.obs.set_visible(mod, src["avatar_idle"], True), "Gabi Ruhe")
        self._card = None
        self._rec_on(rec)
        self._show("moderator")                           # fade in from black (or the cut)
        self._note(f"{self.phase}: " + ("Gabi-Video" if path else "Stimme"))
        played = False
        if path is not None and src.get("avatar_talk"):
            played = self._play_talk(mod, src["avatar_talk"], path)
            if not played:
                self._note("Gabi-Video lief nicht" + (" – Stimme" if self.opts.get("voice") else ""))
        if not played and self.opts.get("voice") and self.speaker is not None:
            lid = self.speaker.say(text, "normal")
            deadline = self.clock() + len(text.split()) / 2.4 * 1.6 + 10
            while not self._stop.is_set() and self.clock() < deadline:
                if self.speaker.is_done(lid):
                    break
                self.sleep(0.2)
            self.sleep(0.6)
        self._leave(last=(seg == "outro" or not self.scenes))
        self._rec_off(rec)
        return True


class PollerReplay:
    """The dashboard's TelemetryPoller as the runner's replay: same recipe as
    its REGIE replays (pause -> seek -> wait -> play 1x), its camera list and
    HUD-hide. While the reel runs REGIE, auto-follow, focus modes and the
    auto-replays are off and the lost-camera watchdog is held back; restore()
    puts them back as they were."""

    # CamCameraState bits (irsdk CameraState). NOTE: the dashboard's own
    # CAM_UI_HIDDEN = 0x0002 / CAM_USE_AUTO_SHOT = 0x0004 are NOT these —
    # 0x0002 is "scenic active" (read-only), 0x0004 is "cam tool active".
    CAM_TOOL = 0x0004
    UI_HIDDEN = 0x0008
    AUTO_SHOT = 0x0010
    CAM_CHANGEABLE = 0x01FC          # bits a broadcast may set
    UI_CHECK_S = 1.0

    def __init__(self, poller, sleep=time.sleep):
        self.p = poller
        self.sleep = sleep
        self._saved = None
        self._cam_state0 = None
        self._ui_at = 0.0

    def _cam_state(self):
        try:
            v = self.p.ir["CamCameraState"]
            return int(v) if v is not None else None
        except Exception:
            return None

    def ensure_ui_hidden(self, force=False) -> bool:
        """iRacing's UI hidden and its own auto camera ("Most Exciting") off,
        read from CamCameraState — set only when it is not so already.
        Checked before every shot and every UI_CHECK_S while recording
        (Thomas, 2026-10-21: the replay bar was in the video)."""
        now = time.time()
        if not force and now - self._ui_at < self.UI_CHECK_S:
            return True
        self._ui_at = now
        v = self._cam_state()
        if v is None:
            return False
        if (v & self.UI_HIDDEN) and not (v & self.AUTO_SHOT):
            return True
        want = ((v & self.CAM_CHANGEABLE) & ~self.CAM_TOOL & ~self.AUTO_SHOT) | self.UI_HIDDEN
        try:
            self.p.ir.cam_set_state(want)
        except Exception:
            return False
        return False

    def connected(self) -> bool:
        return bool(self.p.connected)

    def camera_groups(self) -> list:
        return list(self.p._camera_groups or [])

    def session_time(self):
        v = self.p.ir["SessionTime"]
        return float(v) if v is not None else None

    def prepare(self):
        p = self.p
        self._saved = {k: getattr(p, k, False) for k in
                       ("director_enabled", "auto_follow", "focus_leader", "focus_crashes",
                        "auto_replay", "auto_replay_overtakes")}
        if p.director_enabled:
            p.set_director(False)
        p.auto_follow = p.focus_leader = p.focus_crashes = False
        p.auto_replay = p.auto_replay_overtakes = False
        try:
            p.set_auto_camera(False)
        except Exception:
            pass
        p.highlights_active = True
        p._own_replay_from = time.time()
        self._cam_state0 = self._cam_state()
        self.ensure_ui_hidden(force=True)
        self.hold()

    def hold(self):
        self.ensure_ui_hidden()
        until = time.time() + 30.0
        self.p._manual_override_until = until      # watchdog / auto modes keep off
        self.p._own_replay_until = until           # detectors' replay noise is not news

    def restore(self):
        p = self.p
        p.highlights_active = False
        p._manual_override_until = time.time() + 2.0
        p._own_replay_until = time.time() + 2.0
        s = self._saved or {}
        for k in ("auto_follow", "focus_leader", "focus_crashes", "auto_replay", "auto_replay_overtakes"):
            if k in s:
                setattr(p, k, s[k])
        if s.get("director_enabled"):
            p.set_director(True)
        self._saved = None
        v0, v = self._cam_state0, self._cam_state()
        if v0 is not None and v is not None and (v0 & self.AUTO_SHOT) and not (v & self.AUTO_SHOT):
            try:                          # iRacing's own auto camera back as it was (UI stays hidden)
                self.p.ir.cam_set_state(((v & self.CAM_CHANGEABLE) & ~self.CAM_TOOL) | self.AUTO_SHOT)
            except Exception:
                pass

    LAND_TOL_S = 3.0      # the playhead is "there" within this many seconds
    LAND_WAIT_S = 6.0     # per try; a jump into ANOTHER session takes iRacing ~2.5 s

    def seek(self, session_num, t) -> bool:
        """Pause, jump, and WAIT until iRacing really is at (session, time).

        Real run 2026-10-21 (replay opened in practice, reel in the race =
        session 3): a jump into another session takes iRacing ~2.5 s, and a
        play / camera command sent meanwhile (we sent them after 0.3 s) left
        the replay stuck — speed 1, time frozen, still in practice. Nothing
        else is sent until the playhead has landed; one retry. False = never
        landed (the caller stops the run instead of recording a still)."""
        ir = self.p.ir
        sn = int(session_num) if session_num is not None else int(ir["SessionNum"] or 0)
        target = max(0.0, float(t))
        ir.replay_set_play_speed(0, False)
        self.sleep(0.10)
        for _ in range(2):
            ir.replay_search_session_time(sn, int(target * 1000))
            waited = 0.0
            while waited < self.LAND_WAIT_S:
                self.sleep(0.25)
                waited += 0.25
                try:
                    cur_sn, cur_t = ir["SessionNum"], ir["SessionTime"]
                except Exception:
                    continue
                if cur_sn == sn and cur_t is not None and abs(float(cur_t) - target) <= self.LAND_TOL_S:
                    self.sleep(0.3)      # frames at the new playhead loaded
                    return True
        return False

    def play(self):
        self.p.ir.replay_set_play_speed(1, False)
        self.ensure_ui_hidden(force=True)

    def pause(self):
        self.p.ir.replay_set_play_speed(0, False)

    def switch(self, car_number, group_id):
        self.p.ir.cam_switch_num(str(car_number), int(group_id), 0)
        self.p._current_cam_group = int(group_id)
        self.ensure_ui_hidden(force=True)
