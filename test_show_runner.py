"""Checks for show_runner.py — real ObsClient against fake_obs.FakeObs, a fake
speaker, no Claude, no iRacing.
Run: python test_show_runner.py
"""
import datetime as dt
import json
import tempfile
import threading
import time
from pathlib import Path

import show_runner as sr
from fake_obs import FakeObs
from obs_ws import ObsClient

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


def wait_for(cond, secs=10.0, tick=None):
    end = time.time() + secs
    while time.time() < end:
        if tick:
            tick()
        if cond():
            return True
        time.sleep(0.05)
    return cond()


# --- a CLS sample (shape of /api/roundinfo on 5010) ----------------------------
ROUNDINFO = {"ok": True, "info": {
    "league": {"slug": "cas-gt3-wct", "name": "CAS GT3 WCT"},
    "season": {"name": "14th Season", "totalRounds": 12, "completedRounds": 4},
    "nextRound": {"number": 5, "track": "Spa", "trackConfig": "Grand Prix"},
    "lastRound": {"name": "Round 4", "track": "Motegi", "races": [{"raceNumber": 1, "results": [
        {"position": 1, "name": "Maurice Becker", "teamName": "REVORYX", "car": "Porsche", "startPosition": 1,
         "bestLapMs": 109400, "fastestLap": False},
        {"position": 2, "name": "Elias Schleich", "teamName": None, "car": "Mercedes", "startPosition": 6,
         "bestLapMs": 109595, "fastestLap": True},
        {"position": 3, "name": "Gregor Micewski", "teamName": None, "car": "BMW", "startPosition": 9}]}],
        "driverOfTheDay": {"winnerName": "Gregor Micewski"}}},
    "standings": {"standings": [
        {"rank": 1, "name": "Andreas Bastian", "points": 144, "teamName": "NEON", "proAmClass": "AM"},
        {"rank": 2, "name": "Maurice Becker", "points": 131, "teamName": "REVORYX", "proAmClass": "PRO"},
        {"rank": 3, "name": "Mike Zocher", "points": 120, "teamName": None, "proAmClass": "PRO"}]}}

# --- facts ---------------------------------------------------------------------
facts = sr.build_facts(ROUNDINFO, {"name": "Circuit de Spa-Francorchamps", "config": "Grand Prix",
                                   "weather": {"luft": "18 °C"}}, "Eau Rouge ist berühmt.", 95.0)
check("facts: league / round / track", facts["liga"] == "CAS GT3 WCT" and facts["runde"] == 5
      and "Spa" in facts["strecke"], facts)
lr = facts["letzte_runde"]["rennen"][0]
check("facts: last race top + fastest lap", lr["top5"][0]["name"] == "Maurice Becker"
      and lr["schnellste_runde"]["name"] == "Elias Schleich" and lr["schnellste_runde"]["zeit"] == "1:49,595", lr)
check("facts: DotD", facts["letzte_runde"]["fahrer_des_tages"] == "Gregor Micewski")
check("facts: table + gap + Pro/Am", facts["tabelle"]["abstand_p1_p2"] == 13
      and facts["tabelle"]["top3_pro"][0]["name"] == "Maurice Becker", facts["tabelle"])
check("facts: notes + video", facts["streckennotizen"] and facts["video_sekunden"] == 95)

empty = sr.build_facts({"ok": True, "info": {"season": {"completedRounds": 0}, "nextRound": {"track": "Road Atlanta"}},
                        "standings": {"standings": []}}, {}, None, None)
cfg = sr.load_config()
check("season start: no last race / table segments",
      sr.available_segments(cfg, empty) == ["intro", "track", "outro"], sr.available_segments(cfg, empty))
check("no CLS at all still gives a show", sr.available_segments(cfg, sr.build_facts(None, {"name": "X"}, None, None))
      == ["intro", "track", "outro"])

# --- scripts -------------------------------------------------------------------
segs = sr.available_segments(cfg, facts)
tpl = sr.template_script(cfg, facts, segs)
check("template script for every segment", set(tpl) == set(segs), tpl.keys())
check("template mentions winner and leader", "Maurice Becker" in tpl["last_race"] and "Andreas Bastian" in tpl["standings"])
check("template: no None / braces", not any("None" in t or "{" in t for t in tpl.values()), tpl)

calls = []
def fake_post(payload):
    calls.append(payload)
    if "thinking" in payload:
        raise RuntimeError("HTTP 400: thinking: Extra inputs are not permitted")
    return {"content": [{"type": "text", "text": 'Hier: {"segments": {"intro": "Hallo!", "track": "Spa.", "x": "y"}}'}]}
out = sr.claude_script(cfg, {"api_key": "k"}, facts, ["intro", "track", "outro"], post=fake_post)
check("claude script parsed (only asked segments)", out == {"intro": "Hallo!", "track": "Spa."}, out)
check("thinking fallback retried without it", len(calls) == 2 and "thinking" not in calls[1])
brief = json.loads(calls[0]["messages"][0]["content"])
check("track brief sized to the video (95 s -> 190-205 words)", "190-205 Wörter" in brief["segmente"]["track"],
      brief["segmente"]["track"])
check("facts go to Claude", brief["daten"]["letzte_runde"]["fahrer_des_tages"] == "Gregor Micewski")

check("split sentences", sr.split_sentences("Eins. Zwei! Drei?", 8) == ["Eins.", "Zwei!", "Drei?"]
      and sr.split_sentences("Eins. Zwei.") == ["Eins. Zwei."])

with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "spa-francorchamps-stream-notes-de.md").write_text("Eau Rouge", encoding="utf-8")
    (d / "monza.md").write_text("Parabolica", encoding="utf-8")
    check("notes found by track name", sr.find_notes(d, "Circuit de Spa-Francorchamps", "Grand Prix") == "Eau Rouge")
    check("no notes for another track", sr.find_notes(d, "Road Atlanta") is None)


# --- the run against a fake OBS --------------------------------------------------
class Speaker:
    """Speaks each line in 0.15 s, one after the other (like the voice page)."""
    def __init__(self, deaf=False):
        self.lines, self.done, self.cancelled, self.deaf = [], set(), set(), deaf
        self.n = 0
        threading.Thread(target=self._play, daemon=True).start()

    def say(self, text, seg):
        self.n += 1
        self.lines.append((self.n, seg, text))
        return self.n

    def is_done(self, i):
        return i in self.done or i in self.cancelled

    def cancel(self, ids):
        self.cancelled.update(ids)

    def _play(self):
        while True:
            for i, seg, text in list(self.lines):
                if not self.deaf and not self.is_done(i):
                    time.sleep(0.15)
                    self.done.add(i)
                    break
            time.sleep(0.02)


def make(obs, speaker, script=None, **cfgx):
    c = sr.load_config()
    c.update(cfgx)
    c["obs"] = {"host": "127.0.0.1", "port": obs.port}
    events = []
    r = sr.ShowRunner(c, ObsClient(port=obs.port), speaker,
                      facts_fn=lambda: facts,
                      script_fn=lambda f, s: (script or sr.template_script(c, f, s), "template"),
                      director_on=lambda: events.append("regie"))
    return r, events


SCENES = {"Intro": {}, "Moderator": {"Avatar": True, "Letztes Rennen": False, "Tabelle": True,
                                     "Streckenvideo": False}, "iRacing": {}}
obs = FakeObs(SCENES, media={"Streckenvideo": 0.8})
sp = Speaker()
r, ev = make(obs, sp)
seen = []   # (segment, visible sources)
def watch():
    r.tick()
    s = r.seg["id"] if r.seg else None
    vis = tuple(sorted(k for k, v in obs.scenes["Moderator"].items() if v and k != "Avatar"))
    if not seen or seen[-1] != (s, vis):
        seen.append((s, vis))
r.start()
check("moderator scene on start", obs.current == "Moderator")
check("table left on from a rehearsal is hidden at the start",
      r.seg["id"] == "intro" and obs.scenes["Moderator"]["Tabelle"] is False)
ok = wait_for(lambda: r.state == "done", 20, tick=watch)
check("show runs to the end", ok, r.status()["log"])
check("segment order", [s for s, _ in seen if s][:1] == ["intro"] and
      [s for i, (s, _) in enumerate(seen) if s and (i == 0 or seen[i - 1][0] != s)]
      == ["intro", "last_race", "standings", "track", "outro"], seen)
check("results only during last_race", all(("Letztes Rennen" in v) == (s == "last_race") for s, v in seen if s), seen)
check("table only during standings", all(("Tabelle" in v) == (s == "standings") for s, v in seen if s), seen)
check("video only during track", all(("Streckenvideo" in v) == (s == "track") for s, v in seen if s), seen)
check("video was restarted", any(c[0] == "TriggerMediaInputAction" and c[1]["mediaAction"].endswith("RESTART")
                                  for c in obs.calls))
check("hand-over: iRacing scene + REGIE", obs.current == "iRacing" and ev == ["regie"])
check("avatar untouched", obs.scenes["Moderator"]["Avatar"] is True)
spoken = [seg for _, seg, _ in sp.lines]
check("every segment spoken", set(spoken) == {"intro", "last_race", "standings", "track", "outro"}, spoken)

# track segment waits for the video even when the speech is shorter
obs2 = FakeObs(SCENES, media={"Streckenvideo": 2.0})
sp2 = Speaker()
r2, _ = make(obs2, sp2, script={"intro": "Hallo.", "track": "Kurz.", "outro": "Tschüss."},
             segments=["intro", "track", "outro"])
r2.start()
t_track = {}
def w2():
    r2.tick()
    if r2.seg and r2.seg["id"] == "track" and "in" not in t_track:
        t_track["in"] = time.time()
    if (not r2.seg or r2.seg["id"] != "track") and "in" in t_track and "out" not in t_track:
        t_track["out"] = time.time()
wait_for(lambda: r2.state == "done", 15, tick=w2)
check("track segment lasts as long as the video", t_track.get("out", 0) - t_track.get("in", 0) >= 1.8, t_track)

# next() skips, stop() aborts
obs3 = FakeObs(SCENES, media={"Streckenvideo": 5.0})
sp3 = Speaker(deaf=True)          # never finishes on its own
r3, ev3 = make(obs3, sp3)
r3.start()
check("deaf voice: still in intro", r3.seg["id"] == "intro")
r3.next()
check("next -> last_race, intro lines cancelled", r3.seg["id"] == "last_race" and
      all(sp3.is_done(i) for i, s, _ in sp3.lines if s == "intro"))
check("results shown", obs3.scenes["Moderator"]["Letztes Rennen"] is True)
r3.stop()
check("stop: results hidden, state stopped, no hand-over",
      obs3.scenes["Moderator"]["Letztes Rennen"] is False and r3.state == "stopped" and obs3.current == "Moderator"
      and not ev3)

# a voice nobody listens to: the deadline moves the show on
sr.DEADLINE_PAD_S = 0.3
sr.WORDS_PER_S = 1000.0
obs4 = FakeObs(SCENES, media={"Streckenvideo": 0.3})
r4, ev4 = make(obs4, Speaker(deaf=True))
r4.start()
check("deadline: show completes without a voice", wait_for(lambda: r4.state == "done", 15, tick=r4.tick)
      and ev4 == ["regie"], r4.status()["log"])
sr.DEADLINE_PAD_S, sr.WORDS_PER_S = 15.0, 2.4

# auto start: only in the window, only while streaming, once a day
obs5 = FakeObs(SCENES, media={"Streckenvideo": 0.2})
clock = {"now": dt.datetime(2026, 10, 15, 19, 34, 0)}
r5, _ = make(obs5, Speaker(), start_at="19:40")
r5.local_now = lambda: clock["now"]
prepared = []
r5.prepare = lambda: prepared.append(1) or setattr(r5, "_preparing", False)
r5.tick()
check("19:34: nothing yet", r5.state == "idle" and not prepared)
clock["now"] = dt.datetime(2026, 10, 15, 19, 36, 0)
r5.tick()
time.sleep(0.1)
check("19:36: script prepared ahead", prepared)
clock["now"] = dt.datetime(2026, 10, 15, 19, 40, 5)
r5.tick()
check("19:40 but OBS not streaming: no start", r5.state == "idle")
obs5.streaming = True
r5.script, r5.segments = {"intro": "Hallo."}, ["intro"]
r5.tick()
check("19:40 + streaming: started", r5.state in ("running", "done") and obs5.current == "Moderator")
wait_for(lambda: r5.state == "done", 10, tick=r5.tick)
r5.tick()
check("same day: no second start", r5.state == "done")
clock["now"] = dt.datetime(2026, 10, 16, 19, 41, 0)
r5.script = {}
r5.tick()
check("next day: starts again", r5.ran_on == dt.date(2026, 10, 16) and r5.state in ("running", "done"),
      (r5.state, r5.ran_on))
clock["now"] = dt.datetime(2026, 10, 17, 21, 0, 0)
r5b, _ = make(obs5, Speaker(), start_at="19:40")
r5b.local_now = lambda: clock["now"]
r5b.tick()
check("after the window: no start", r5b.state == "idle")

# missing OBS source: show goes on, error visible
obs6 = FakeObs({"Moderator": {}, "iRacing": {}}, media={})
r6, ev6 = make(obs6, Speaker())
r6.start()
check("missing sources: completes", wait_for(lambda: r6.state == "done", 15, tick=r6.tick) and ev6 == ["regie"])
check("missing sources: error reported", "Letztes Rennen" in r6.last_error or "Streckenvideo" in r6.last_error
      or any("Letztes Rennen" in e["msg"] for e in r6.log), r6.last_error)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
