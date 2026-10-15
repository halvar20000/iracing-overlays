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
        {"rank": 1, "name": "Andreas Bastian", "points": 144, "teamName": "NEON"},
        {"rank": 2, "name": "Maurice Becker", "points": 131, "teamName": "REVORYX"},
        {"rank": 3, "name": "Mike Zocher", "points": 120, "teamName": None}]}}

# --- facts ---------------------------------------------------------------------
facts = sr.build_facts(ROUNDINFO, {"name": "Circuit de Spa-Francorchamps", "config": "Grand Prix",
                                   "weather": {"luft": "18 °C"}}, "Eau Rouge ist berühmt.", 95.0)
check("facts: league / round / track", facts["liga"] == "CAS GT3 WCT" and facts["runde"] == 5
      and "Spa" in facts["strecke"], facts)
lr = facts["letzte_runde"]["rennen"][0]
check("facts: last race top + fastest lap", lr["top5"][0]["name"] == "Maurice Becker"
      and lr["schnellste_runde"]["name"] == "Elias Schleich" and lr["schnellste_runde"]["zeit"] == "1:49,595", lr)
check("facts: DotD", facts["letzte_runde"]["fahrer_des_tages"] == "Gregor Micewski")
check("facts: table + gap", facts["tabelle"]["abstand_p1_p2"] == 13
      and facts["tabelle"]["top5"][0]["name"] == "Andreas Bastian" and "pro_am" not in facts, facts["tabelle"])
check("facts: notes + video", facts["streckennotizen"] and facts["video_sekunden"] == 95)

empty = sr.build_facts({"ok": True, "info": {"season": {"completedRounds": 0}, "nextRound": {"track": "Road Atlanta"}},
                        "standings": {"standings": []}}, {}, None, None)
cfg = sr.load_config()
check("season start: no last race / table segments",
      sr.available_segments(cfg, empty) == ["intro", "track", "outro"], sr.available_segments(cfg, empty))
check("no CLS at all still gives a show", sr.available_segments(cfg, sr.build_facts(None, {"name": "X"}, None, None))
      == ["intro", "track", "outro"])

# --- Pro/Am season (WCT GT3): everything per class -------------------------------
PA = json.loads(json.dumps(ROUNDINFO))
PA["standings"]["season"] = {"proAmEnabled": True}   # like CLS: round-info's season has no flag
for r, c in zip(PA["info"]["lastRound"]["races"][0]["results"], ("PRO", "AM", "AM")):
    r["proAmClass"] = c
PA["standings"]["standings"] = [
    {"rank": 1, "name": "Andreas Bastian", "points": 144, "proAmClass": "AM"},
    {"rank": 2, "name": "Maurice Becker", "points": 131, "proAmClass": "PRO"},
    {"rank": 3, "name": "Mike Zocher", "points": 120, "proAmClass": "PRO"},
    {"rank": 4, "name": "Gregor Micewski", "points": 100, "proAmClass": "AM"}]
pa = sr.build_facts(PA, {"name": "Motegi"}, None, None)
pk = pa["letzte_runde"]["rennen"][0]["klassen"]
check("pro/am: classes", pa.get("pro_am") == ["PRO", "AM"])
check("pro/am: class winners + class positions", pk["PRO"]["top3"][0]["name"] == "Maurice Becker"
      and pk["AM"]["top3"][0]["name"] == "Elias Schleich" and pk["AM"]["top3"][1]["platz"] == 2
      and pk["AM"]["top3"][1]["gesamtplatz"] == 3, pk)
check("pro/am: class fastest lap by time", pk["AM"]["schnellste_runde"]["name"] == "Elias Schleich"
      and pk["PRO"]["schnellste_runde"]["name"] == "Maurice Becker", pk)
tk = pa["tabelle"]["klassen"]
check("pro/am: table per class, no mixed list", "top5" not in pa["tabelle"]
      and tk["PRO"]["top5"][0]["name"] == "Maurice Becker" and tk["PRO"]["abstand_p1_p2"] == 11
      and tk["AM"]["top5"][0]["name"] == "Andreas Bastian" and tk["AM"]["top5"][1]["rang"] == 2, tk)
pseg = sr.available_segments(sr.load_config(), pa)
check("pro/am: segments split", pseg == ["intro", "last_race_pro", "last_race_am", "standings_pro",
                                          "standings_am", "track", "outro"], pseg)
ptpl = sr.template_script(sr.load_config(), pa, pseg)
check("pro/am: template per class", set(ptpl) == set(pseg) and "Maurice Becker" in ptpl["standings_pro"]
      and "Andreas Bastian" in ptpl["standings_am"] and "Andreas Bastian" not in ptpl["standings_pro"]
      and "Elias Schleich" in ptpl["last_race_am"], ptpl)

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
check("track brief: spoken before the video", "70-110 Wörter" in brief["segmente"]["track"]
      and "danach" in brief["segmente"]["track"], brief["segmente"]["track"])
calls.clear()
sr.claude_script(dict(cfg, video_after_speech=False), {"api_key": "k"}, facts, ["track"], post=fake_post)
check("track brief over the video sized to it (95 s -> 190-205 words)",
      "190-205 Wörter" in json.loads(calls[-1]["messages"][0]["content"])["segmente"]["track"])
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
vid_at = []  # track lines done when the video came up
def watch():
    r.tick()
    s = r.seg["id"] if r.seg else None
    vis = tuple(sorted(k for k, v in obs.scenes["Moderator"].items() if v and k != "Avatar"))
    if not seen or seen[-1] != (s, vis):
        if s == "track" and "Streckenvideo" in vis and not vid_at:
            vid_at.append([sp.is_done(i) for i, sg, _ in sp.lines if sg == "track"])
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
check("video never outside track", all(s == "track" for s, v in seen if "Streckenvideo" in v), seen)
trk = [v for s, v in seen if s == "track"]
check("track: text first, then the video", len(trk) == 2 and "Streckenvideo" not in trk[0]
      and "Streckenvideo" in trk[1], trk)
check("track: all its lines spoken before the video", vid_at and all(vid_at[0]), vid_at)
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

# Pro/Am run: the page is pinned per class, the source stays up from PRO to AM
pins = []
obs7 = FakeObs(SCENES, media={"Streckenvideo": 0.2})
c7 = sr.load_config()
c7["obs"] = {"host": "127.0.0.1", "port": obs7.port}
r7 = sr.ShowRunner(c7, ObsClient(port=obs7.port), Speaker(), facts_fn=lambda: pa,
                   script_fn=lambda f, s: (sr.template_script(c7, f, s), "template"),
                   director_on=lambda: None, pin=pins.append)
hides = []
r7.start()
def w7():
    r7.tick()
    hides.append((r7.seg["id"] if r7.seg else None, obs7.scenes["Moderator"]["Letztes Rennen"],
                  obs7.scenes["Moderator"]["Tabelle"]))
check("pro/am run completes", wait_for(lambda: r7.state == "done", 20, tick=w7), r7.status()["log"])
check("pro/am: pins pro, am, released", [p for p in pins if p] == ["pro", "am", "pro", "am"]
      and pins[-1] is None and pins[0] is None, pins)
check("pro/am: results stay visible through both classes",
      all(lr_vis for sid, lr_vis, _ in hides if sid in ("last_race_pro", "last_race_am")), hides)
shows = [c[1] for c in obs7.calls if c[0] == "SetSceneItemEnabled" and c[1].get("sceneItemEnabled")]
check("pro/am: each page shown once", len(shows) == 3, shows)   # results, table, video

# own source per class: PRO page only while PRO is talked about, AM page only for AM
SC4 = {"Moderator": {"Letztes Rennen": False, "Tabelle": False, "Streckenvideo": False,
                     "Letztes Rennen PRO": False, "Letztes Rennen AM": True, "Tabelle PRO": False,
                     "Tabelle AM": False}, "iRacing": {}}
obs9 = FakeObs(SC4, media={"Streckenvideo": 0.2})
c9 = sr.load_config(); c9["obs"] = {"host": "127.0.0.1", "port": obs9.port}
r9 = sr.ShowRunner(c9, ObsClient(port=obs9.port), Speaker(), facts_fn=lambda: pa,
                   script_fn=lambda f, s: (sr.template_script(c9, f, s), "template"), director_on=lambda: None)
vis9 = []
def w9():
    r9.tick()
    v = tuple(sorted(k for k, x in obs9.scenes["Moderator"].items() if x))
    sid = r9.seg["id"] if r9.seg else None
    if not vis9 or vis9[-1] != (sid, v):
        vis9.append((sid, v))
r9.start()
check("class sources: run completes", wait_for(lambda: r9.state == "done", 20, tick=w9), r9.status()["log"])
want = {"last_race_pro": "Letztes Rennen PRO", "last_race_am": "Letztes Rennen AM",
        "standings_pro": "Tabelle PRO", "standings_am": "Tabelle AM"}
bad = [(sid, v) for sid, v in vis9 if sid in want and v and v != (want[sid],)]
check("class sources: exactly the class page per segment (stale AM page hidden at start)",
      not bad and all(any(sid == k and v == (n,) for sid, v in vis9) for k, n in want.items()), vis9)
check("class sources: plain pages never used", not any("Letztes Rennen" in v or "Tabelle" in v for _, v in vis9))

check("source names: case / spaces ignored", sr.match_name("Tabelle PRO", {"tabelle  Pro ", "Tabelle"}) == "tabelle  Pro "
      and sr.match_name("Tabelle PRO", {"Tabelle"}) is None)
SC5 = {"Moderator": {"Letztes Rennen": False, "Tabelle": False, "Streckenvideo": False,
                     "Letztes Rennen Pro": False, "letztes rennen AM": False, "Tabelle Pro ": False,
                     "Tabelle Am": False}, "iRacing": {}}
obs10 = FakeObs(SC5, media={"Streckenvideo": 0.2})
c10 = sr.load_config(); c10["obs"] = {"host": "127.0.0.1", "port": obs10.port}
r10 = sr.ShowRunner(c10, ObsClient(port=obs10.port), Speaker(), facts_fn=lambda: pa,
                    script_fn=lambda f, s: (sr.template_script(c10, f, s), "template"), director_on=lambda: None)
seen10 = set()
def w10():
    r10.tick()
    for k, x in obs10.scenes["Moderator"].items():
        if x:
            seen10.add((r10.seg["id"] if r10.seg else None, k))
r10.start()
wait_for(lambda: r10.state == "done", 20, tick=w10)
check("class sources found despite other spelling", ("standings_pro", "Tabelle Pro ") in seen10
      and ("last_race_am", "letztes rennen AM") in seen10 and not any(k == "Tabelle" for _, k in seen10), seen10)

# hand edits: kept, empty segment skipped, survive a restart, never rewritten automatically
with tempfile.TemporaryDirectory() as td:
    store = Path(td) / "show_script.json"
    obs8 = FakeObs(SCENES, media={"Streckenvideo": 0.2})
    c8 = sr.load_config(); c8["obs"] = {"host": "127.0.0.1", "port": obs8.port}
    writes = []
    def sfn(f, s):
        writes.append(1)
        return sr.template_script(c8, f, s), "claude"
    sp8 = Speaker()
    r8 = sr.ShowRunner(c8, ObsClient(port=obs8.port), sp8, facts_fn=lambda: facts, script_fn=sfn,
                       director_on=lambda: None, store=store)
    r8.prepare()
    check("edit: no script before -> error", sr.ShowRunner(c8, None, sp8, lambda: {}, sfn, lambda: None)
          .set_script({"intro": "x"}) is not None)
    err = r8.set_script({"intro": "Servus Leute, hier ist die WCT.", "standings": "  "})
    check("edit saved, emptied segment dropped", err is None and r8.script["intro"].startswith("Servus")
          and "standings" not in r8.segments and r8.status()["order"][2] == "standings" and r8.edited, r8.segments)
    r8b = sr.ShowRunner(c8, ObsClient(port=obs8.port), Speaker(), facts_fn=lambda: facts, script_fn=sfn,
                        director_on=lambda: None, store=store)
    check("edit survives a restart", r8b.edited and r8b.script["intro"].startswith("Servus")
          and "standings" not in r8b.segments)
    n = len(writes)
    clock8 = {"now": dt.datetime(2026, 10, 15, 19, 36, 0)}
    r8b.local_now = lambda: clock8["now"]
    r8b.tick(); time.sleep(0.1)
    check("edited script not rewritten at 19:35", len(writes) == n)
    r8b.start()
    check("start uses the edited script", wait_for(lambda: r8b.state == "done", 20, tick=r8b.tick)
          and len(writes) == n, r8b.status()["log"])
    said = [t for _, sg, t in r8b.speaker.lines]
    check("edited text spoken, dropped segment silent", any("Servus" in t for t in said)
          and not any(sg == "standings" for _, sg, _ in r8b.speaker.lines), said)
    r8b.state = "running"
    check("no edits while running", r8b.set_script({"intro": "x"}) is not None)
    r8b.state = "idle"
    r8b.prepare()
    check("manual rewrite clears the edit flag", not r8b.edited and "standings" in r8b.segments)

# missing OBS source: show goes on, error visible
obs6 = FakeObs({"Moderator": {}, "iRacing": {}}, media={})
r6, ev6 = make(obs6, Speaker())
r6.start()
check("missing sources: completes", wait_for(lambda: r6.state == "done", 15, tick=r6.tick) and ev6 == ["regie"])
check("missing sources: error reported", "Letztes Rennen" in r6.last_error or "Streckenvideo" in r6.last_error
      or any("Letztes Rennen" in e["msg"] for e in r6.log), r6.last_error)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
