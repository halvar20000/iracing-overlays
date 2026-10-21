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
check("facts: WCT quali format (AM first, then PRO)", "AM-Fahrern" in facts.get("qualifying_format", "")
      and "PRO" not in sr.template_script(sr.load_config(), facts, ["outro"])["outro"])   # Gabi's on-camera bit stays short
check("facts: no quali format for other leagues", "qualifying_format" not in sr.build_facts(
      {"ok": True, "info": {"league": {"slug": "cas-pccd"}, "season": {}}, "standings": {}}, {}, None, None))
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

# season schedule: own segment with the league's picture, tracks from show_notes/schedule-<slug>.md
SCH = json.loads(json.dumps(ROUNDINFO))
SCH["info"]["rounds"] = [{"number": n, "name": f"Round {n}", "startsAt": f"2026-10-{6 + 7 * (n - 1):02d}T18:00:00.000Z",
                          "status": "COMPLETED" if n < 5 else "UPCOMING"} for n in range(1, 4)] + \
    [{"number": n, "name": f"Round {n}", "startsAt": "2026-11-0%dT18:00:00.000Z" % (n - 3), "status": "UPCOMING"}
     for n in range(4, 7)]
SCH["info"]["rounds"][2]["status"] = "COMPLETED"
SCH["info"]["nextRound"] = {"number": 4, "track": "Spa"}
with tempfile.TemporaryDirectory() as td:
    (Path(td) / "schedule-cas-gt3-wct.md").write_text("# x\n02.11.26 Monza - GP\n03.11.26 Fuji - GP\n", encoding="utf-8")
    sf = sr.build_facts(SCH, {"name": "Spa"}, None, None, Path(td))
sp_ = sf.get("saisonplan") or {}
check("schedule facts: today, next, tracks by date, spoken dates",
      sp_.get("heute", {}).get("runde") == 4 and sp_["heute"]["strecke"] == "Spa"
      and sp_["naechste_termine"][0]["strecke"] == "Monza - GP" and sp_["finale"]["strecke"] == "Fuji - GP" and sp_["naechste_termine"][0]["datum"] == "2. November"
      and sp_["runden_gesamt"] == 6 and sp_["danach_noch"] == 2, sp_)
csch = sr.load_config()
check("schedule segment only with a source for the league", "schedule" in sr.available_segments(csch, sf)
      and "schedule" not in sr.available_segments(dict(csch, schedule_sources={}), sf))
check("schedule template (with the quali format, moved here from Gabi's hand-over)",
      "Runde 4 von 6" in sr.template_script(csch, sf, ["schedule"])["schedule"]
      and "AM-Fahrern" in sr.template_script(csch, sf, ["schedule"])["schedule"])
obs11 = FakeObs({"Moderator": {"Letztes Rennen": False, "Tabelle": False, "Streckenvideo": False, "WCT": False},
                 "iRacing": {}}, media={"Streckenvideo": 0.2})
c11 = sr.load_config(); c11["obs"] = {"host": "127.0.0.1", "port": obs11.port}
r11 = sr.ShowRunner(c11, ObsClient(port=obs11.port), Speaker(), facts_fn=lambda: sf,
                    script_fn=lambda f, s: (sr.template_script(c11, f, s), "template"), director_on=lambda: None)
seen11 = set()
def w11():
    r11.tick()
    if r11.seg:
        seen11.update((r11.seg["id"], k) for k, x in obs11.scenes["Moderator"].items() if x)
r11.start()
wait_for(lambda: r11.state == "done", 20, tick=w11)
check("schedule picture only during the schedule segment", ("schedule", "WCT") in seen11
      and all(sid == "schedule" for sid, k in seen11 if k == "WCT") and not obs11.scenes["Moderator"]["WCT"], seen11)

# track map: up while the track is talked about, down before the video
obs12 = FakeObs({"Moderator": {"Streckenvideo": False, "Streckenort": True}, "iRacing": {}}, media={"Streckenvideo": 0.3})
c12 = sr.load_config(); c12["obs"] = {"host": "127.0.0.1", "port": obs12.port}
sp12 = Speaker()
r12 = sr.ShowRunner(c12, ObsClient(port=obs12.port), sp12, facts_fn=lambda: facts,
                    script_fn=lambda f, s: ({"intro": "Hallo.", "track": "Motegi liegt in Japan. Eine Runde.",
                                             "outro": "Tschüss."}, "template"), director_on=lambda: None)
r12.segments, r12.script, r12.script_at = ["intro", "track", "outro"], {"intro": "Hallo.",
    "track": "Motegi liegt in Japan. Eine Runde.", "outro": "Tschüss."}, time.time()
st12 = []
def w12():
    r12.tick()
    v = (r12.seg["id"] if r12.seg else None, obs12.scenes["Moderator"]["Streckenort"],
         obs12.scenes["Moderator"]["Streckenvideo"])
    if not st12 or st12[-1] != v:
        st12.append(v)
r12.start()
check("map: hidden at the start (left on from a rehearsal)", obs12.scenes["Moderator"]["Streckenort"] is False)
wait_for(lambda: r12.state == "done", 20, tick=w12)
check("map during the track talk, then the video, never both", ("track", True, False) in st12
      and ("track", False, True) in st12 and not any(m and v for _, m, v in st12)
      and st12.index(("track", True, False)) < st12.index(("track", False, True))
      and all(not m for s_, m, _ in st12 if s_ != "track"), st12)
check("place in the facts", sr.build_facts(None, {"name": "Motegi", "place": "Motegi, Japan"}, None, None)["ort"]
      == "Motegi, Japan")

# replay of an earlier round: which round is it (tracks from CLS or the schedule file)
RR = {"info": {"league": {"slug": "cas-gt3-wct"}, "nextRound": {"number": 5, "track": "Indianapolis Motor Speedway"},
               "rounds": [{"number": 1, "status": "COMPLETED", "track": "Silverstone Circuit"},
                          {"number": 3, "status": "COMPLETED", "track": "Circuit Zandvoort"},
                          {"number": 4, "status": "COMPLETED", "track": "Mobility Resort Motegi"},
                          {"number": 7, "status": "UPCOMING", "track": "Circuit of the Americas"}]}}
check("replay round: Motegi = 4, Zandvoort = 3", sr.replay_round(RR, "Mobility Resort Motegi") == 4
      and sr.replay_round(RR, "Circuit Zandvoort") == 3)
check("replay round: today's track / future / unknown / generic words -> live",
      sr.replay_round(RR, "Indianapolis Motor Speedway") is None and sr.replay_round(RR, "Circuit of the Americas") is None
      and sr.replay_round(RR, "Okayama International Circuit") is None and sr.replay_round(None, "Motegi") is None)
RR2 = {"info": {"league": {"slug": "cas-gt3-wct"}, "nextRound": {"number": 5, "track": "Indy"},
                "rounds": [{"number": 4, "status": "COMPLETED", "startsAt": "2026-10-13T18:00:00.000Z"}]}}
with tempfile.TemporaryDirectory() as td:
    (Path(td) / "schedule-cas-gt3-wct.md").write_text("13.10.26 Mobility Resort Motegi - GP\n", encoding="utf-8")
    check("replay round from the schedule file when CLS has no tracks", sr.replay_round(RR2, "Mobility Resort Motegi", Path(td)) == 4)

# talking points: templates from the facts, Claude JSON, spoken once each, editable, kept
tp = sr.template_points(pa)
check("template points: per class leaders, winners, no None", len(tp) >= 4
      and any("Klasse PRO" in x and "Maurice Becker" in x for x in tp)
      and any("Klasse AM" in x and "Andreas Bastian" in x for x in tp) and not any("None" in x for x in tp), tp)
pcalls = []
def ppost(payload):
    pcalls.append(payload)
    return {"content": [{"type": "text", "text": '```json\n{"points": ["Eins.", " ", "Zwei."]}\n```'}]}
check("claude points parsed", sr.claude_points(cfg, {"api_key": "k"}, pa, 12, post=ppost) == ["Eins.", "Zwei."]
      and "12 Talking Points" in pcalls[0]["system"])
with tempfile.TemporaryDirectory() as td:
    store = Path(td) / "s.json"
    rp = sr.ShowRunner(cfg, None, Speaker(), facts_fn=lambda: pa,
                       script_fn=lambda f, s_: (sr.template_script(cfg, f, s_), "template"),
                       director_on=lambda: None, store=store, points_fn=lambda f: ["A.", "B.", "C."])
    rp.prepare()
    got = [rp.next_point(), rp.next_point()]
    rp1b = sr.ShowRunner(cfg, None, Speaker(), facts_fn=lambda: pa, script_fn=lambda f, s_: ({}, "t"),
                         director_on=lambda: None, store=store)
    check("an automatic script is not restored after a restart (written again)", not rp1b.script and not rp1b.points)
    rp.set_script({"intro": rp.script["intro"]})          # hand-edited -> kept over a restart
    rp2 = sr.ShowRunner(cfg, None, Speaker(), facts_fn=lambda: pa, script_fn=lambda f, s_: ({}, "t"),
                        director_on=lambda: None, store=store)
    check("points spoken in order, once; remembered across a restart", got == ["A.", "B."]
          and rp2.next_point() == "C." and rp2.next_point() is None, (got, rp2.points_used))
    rp2.set_script({}, ["Neu 1.", "", "Neu 2."])
    check("edited points replace the list and start over", rp2.points == ["Neu 1.", "Neu 2."]
          and rp2.next_point() == "Neu 1.")
    rp3 = sr.ShowRunner(cfg, None, Speaker(), facts_fn=lambda: pa,
                        script_fn=lambda f, s_: (sr.template_script(cfg, f, s_), "template"),
                        director_on=lambda: None, points_fn=lambda f: (_ for _ in ()).throw(RuntimeError("down")))
    rp3.prepare()
    check("points failure does not stop the script", rp3.script and rp3.points == [])

# ABSPANN after the race: facts, segments, templates, run
RES = {"results": [                     # the shape 5002 /results really serves
    {"position": 1, "name": "Maurice Becker", "car": "Porsche", "start_pos": 2, "position_change": 1,
     "fastest_lap": 109.5, "is_fastest_overall": True, "finished": True, "time_gap": 0},
    {"position": 2, "name": "Andreas Bastian", "car": "Porsche", "start_pos": 9, "position_change": 7,
     "fastest_lap": 110.1, "finished": True, "time_gap": 3.4},
    {"position": 3, "name": "Mike Zocher", "car": "BMW", "start_pos": 1, "position_change": -2,
     "fastest_lap": 109.9, "finished": True, "time_gap": 5.0},
    {"position": 4, "name": "Thomas Kuebler", "car": "Corvette", "start_pos": 5, "position_change": 1,
     "fastest_lap": 110.6, "finished": False, "reason_out": "Disconnected", "laps_behind": 3}]}
PROJ = {"season": {"proAmEnabled": True}, "champ_rows": [
    {"name": "Maurice Becker", "pro_am": "PRO", "proj_points": 153, "pre_points": 118, "rank": 3, "proj_rank": 1, "race_pts": 35, "in_race": True},
    {"name": "Mike Zocher", "pro_am": "PRO", "proj_points": 140, "pre_points": 110, "rank": 4, "proj_rank": 3, "race_pts": 30, "in_race": True},
    {"name": "Andreas Bastian", "pro_am": "AM", "proj_points": 144, "pre_points": 104, "rank": 2, "proj_rank": 2, "race_pts": 40, "in_race": True},
    {"name": "Thomas Kuebler", "pro_am": "AM", "proj_points": 120, "pre_points": 108, "rank": 1, "proj_rank": 4, "race_pts": 12, "in_race": True}]}
RIP = {"ok": True, "info": {"league": {"slug": "cas-gt3-wct", "name": "CAS GT3 WCT"}, "season": {"name": "14th"},
       "nextRound": {"number": 4, "track": "Mobility Resort Motegi"},
       "rounds": [{"number": n, "startsAt": f"2026-10-{6 + 7 * (n - 3):02d}T18:00:00.000Z",
                   "status": "COMPLETED" if n < 4 else "UPCOMING",
                   "track": {4: "Mobility Resort Motegi", 5: "Indianapolis Motor Speedway"}.get(n, "X")}
                  for n in (3, 4, 5)]}}
CL = {"Maurice Becker": "PRO", "Mike Zocher": "PRO", "Andreas Bastian": "AM", "Thomas Kuebler": "AM"}
pf = sr.build_post_facts(RES, PROJ, RIP, ["Zocher führt.", "Becker geht vorbei!"], {"name": "Motegi"}, CL)
check("post facts: result, classes, fastest lap, biggest gain, retirement",
      pf["ergebnis"]["top10"][0]["name"] == "Maurice Becker" and pf["ergebnis"]["schnellste_runde"]["zeit"] == "1:49,500"
      and pf["ergebnis"]["meiste_plaetze_gut"]["name"] == "Andreas Bastian"
      and pf["ergebnis"]["klassen"]["AM"]["top3"][0]["name"] == "Andreas Bastian"
      and pf["ergebnis"]["ausgefallen"] == ["Thomas Kuebler"], pf["ergebnis"])
check("post facts: championship per class incl. tonight, class rank before",
      pf["meisterschaft"]["PRO"][0]["name"] == "Maurice Becker" and pf["meisterschaft"]["AM"][0]["name"] == "Andreas Bastian"
      and pf["meisterschaft"]["AM"][0]["vorher_rang"] == 2 and pf["meisterschaft"]["AM"][0]["punkte_heute"] == 40,
      pf["meisterschaft"])
check("post facts: next round = the one after tonight's", pf["naechstes_rennen"]["runde"] == 5
      and pf["naechstes_rennen"]["strecke"] == "Indianapolis Motor Speedway" and pf["naechstes_rennen"]["datum"] == "20. Oktober",
      pf.get("naechstes_rennen"))
check("post facts: race story from the commentary", pf["verlauf"] == ["Zocher führt.", "Becker geht vorbei!"])
check("post facts: old 'rows' key still read", sr.build_post_facts({"rows": RES["results"]}, None, None, [], {})
      ["ergebnis"]["top10"][0]["name"] == "Maurice Becker")
psegs = sr.available_post_segments(sr.load_config(), pf)
check("post segments: Pro/Am result and table per class", psegs == [
    "race_summary", "results_pro", "results_am", "standings_after_pro", "standings_after_am", "next_round", "goodbye"], psegs)
pf_one = sr.build_post_facts(RES, {"season": {"proAmEnabled": False}, "champ_rows": PROJ["champ_rows"]}, RIP, [], {})
check("post segments: no classes -> one result, one table",
      sr.available_post_segments(sr.load_config(), pf_one)
      == ["race_summary", "results", "standings_after", "next_round", "goodbye"])
check("post segments without data", sr.available_post_segments(sr.load_config(), {}) == ["goodbye"])
ptpl = sr.template_post_script(sr.load_config(), pf, psegs)
check("post templates", set(ptpl) == set(psegs) and "Klasse AM gewinnt Andreas Bastian" in ptpl["results_am"]
      and "Andreas Bastian" not in ptpl["results_pro"] and "Klasse PRO gewinnt Maurice Becker" in ptpl["results_pro"]
      and "Runde 5 von 5" in ptpl["next_round"] and "kam Thomas Kuebler." in ptpl["race_summary"] and "Indianapolis" in ptpl["next_round"]
      and "vorläufig" in ptpl["standings_after_pro"] and "Andreas Bastian" in ptpl["standings_after_am"]
      and not any("None" in t for t in ptpl.values()), ptpl)
check("post briefs exist for every split segment", all(s in sr.POST_BRIEF for v in sr.POST_PROAM_SPLIT.values() for s in v))
pcalls2 = []
def post2(payload):
    pcalls2.append(payload)
    return {"content": [{"type": "text", "text": '{"segments": {"goodbye": "Tschüss!"}}'}]}
sr.claude_script(sr.load_config(), {"api_key": "k"}, pf, ["goodbye"], post=post2,
                 system=sr.POST_SYSTEM, briefs=sr.POST_BRIEF)
check("post script prompt is the Abspann one", "Abspann" in pcalls2[0]["system"]
      and "goodbye" in json.loads(pcalls2[0]["messages"][0]["content"])["segmente"])
obs13 = FakeObs({"Moderator": {"Ergebnis": False, "Meisterschaft": True}, "iRacing": {}}, media={})
obs13.current = "iRacing"
c13 = sr.load_config(); c13["obs"] = {"host": "127.0.0.1", "port": obs13.port}
ev13 = []
pin13 = [None]
r13 = sr.ShowRunner(c13, ObsClient(port=obs13.port), Speaker(), facts_fn=lambda: pf,
                    script_fn=lambda f, s_: (sr.template_post_script(c13, f, s_), "template"),
                    director_on=lambda: ev13.append("on"), kind="post", director_off=lambda: ev13.append("off"),
                    pin=lambda c: pin13.__setitem__(0, c))
r13.prepare()
vis13 = []
def w13():
    r13.tick()
    v = (r13.seg["id"] if r13.seg else None, obs13.scenes["Moderator"]["Ergebnis"],
         obs13.scenes["Moderator"]["Meisterschaft"], pin13[0])
    if not vis13 or vis13[-1] != v:
        vis13.append(v)
r13.start()
check("Abspann: REGIE off, Moderator scene, table left on hidden", ev13 == ["off"] and obs13.current == "Moderator"
      and obs13.scenes["Moderator"]["Meisterschaft"] is False)
check("Abspann runs to the end, stays on Moderator, no REGIE", wait_for(lambda: r13.state == "done", 20, tick=w13)
      and obs13.current == "Moderator" and ev13 == ["off"], r13.status()["log"])
check("Abspann: a picture for every voice segment (result from the summary on), none for the goodbye",
      ("race_summary", True, False, None) in vis13 and ("results_pro", True, False, "pro") in vis13
      and ("results_am", True, False, "am") in vis13 and ("standings_after_pro", False, True, "pro") in vis13
      and ("standings_after_am", False, True, "am") in vis13
      and all(not e and not t for s_, e, t, _ in vis13 if s_ in ("goodbye", None)), vis13)
check("Abspann: the result page stays up summary -> PRO -> AM (no blink), class released at the end",
      [e for s_, e, t, _ in vis13 if s_ in ("race_summary", "results_pro", "results_am")] == [True] * len(
          [1 for s_, *_ in vis13 if s_ in ("race_summary", "results_pro", "results_am")]) and pin13[0] is None, vis13)
# Gabi's idle loop: switched ON at the start (it was off) and left on to the end
# (Andreas, 2026-10-21: after the greeting she vanished from the studio)
obs18 = FakeObs({"Moderator": {"Ergebnis": False, "Meisterschaft": False, "Gabi Ruhe": False}, "iRacing": {}},
                media={})
c18 = sr.load_config(); c18["obs"] = {"host": "127.0.0.1", "port": obs18.port}
r18 = sr.ShowRunner(c18, ObsClient(port=obs18.port), Speaker(), facts_fn=lambda: pf,
                    script_fn=lambda f, s_: (sr.template_post_script(c18, f, s_), "template"),
                    director_on=lambda: None, kind="post", director_off=lambda: None)
r18.prepare()
idle18 = []
def w18():
    r18.tick()
    idle18.append(obs18.scenes["Moderator"]["Gabi Ruhe"])
r18.start()
check("idle loop switched on at the start and kept on to the end",
      obs18.scenes["Moderator"]["Gabi Ruhe"] is True and wait_for(lambda: r18.state == "done", 20, tick=w18)
      and all(idle18) and obs18.scenes["Moderator"]["Gabi Ruhe"] is True, idle18[:5])
r17 = sr.ShowRunner(dict(sr.load_config(), avatar={"segments": ["intro", "last_race", "track", "schedule", "outro"], "max_words": 5}),
                    None, Speaker(), lambda: {}, lambda f, s_: ({}, "t"), lambda: None)
r17.segments = ["intro", "last_race", "track", "schedule", "outro"]
r17.script = {"intro": "eins zwei drei", "outro": "eins zwei drei vier fünf sechs"}
check("avatar segments filtered to picture-less ones; word cap", r17._avatar_segs() == ["intro", "outro"]
      and r17._avatar_words_ok("intro") and not r17._avatar_words_ok("outro"))
r13.state = "idle"
check("Abspann never starts by itself", r13.tick() is None and r13.state == "idle")

# per-track video: the right file into the source, none when the folder has no video for the track
with tempfile.TemporaryDirectory() as td:
    vd = Path(td)
    (vd / "portimao-algarve.mp4").write_bytes(b"x"); (vd / "Mobility Motegi GP.mp4").write_bytes(b"x")
    check("video by track name", sr.find_track_video(vd, "Mobility Resort Motegi", "Full Road Course")[1].name
          == "Mobility Motegi GP.mp4" and sr.find_track_video(vd, "Autódromo Internacional do Algarve")[1].name
          == "portimao-algarve.mp4" and sr.find_track_video(vd, "Circuit Zandvoort") == (True, None))
    check("no video folder: (False, None)", sr.find_track_video(vd / "nope", "Motegi") == (False, None))
    for track, expect in (("Mobility Resort Motegi", "Mobility Motegi GP.mp4"), ("Circuit Zandvoort", None)):
        obs14 = FakeObs({"Moderator": {"Streckenvideo": False}, "iRacing": {}}, media={"Streckenvideo": 0.2})
        c14 = sr.load_config(); c14["obs"] = {"host": "127.0.0.1", "port": obs14.port}; c14["track_video_dir"] = str(vd)
        f14 = dict(facts, strecke=track)
        r14 = sr.ShowRunner(c14, ObsClient(port=obs14.port), Speaker(), facts_fn=lambda: f14,
                            script_fn=lambda f, s_: ({"track": "Text."}, "template"), director_on=lambda: None)
        r14.prepare(); r14.segments = ["track"]; r14.script = {"track": "Text."}
        r14.start()
        wait_for(lambda: r14.state == "done", 15, tick=r14.tick)
        played = any(c[0] == "TriggerMediaInputAction" for c in obs14.calls)
        got = (obs14.media["Streckenvideo"].get("file") or "").rsplit("/", 1)[-1] or None
        check(f"track video for {track.split()[-1]}: {expect or 'none'}",
              (played and got == expect) if expect else (not played), (played, got, r14.log[-3:]))
check("no video for the track: the moderation does not announce one",
      "kein Streckenvideo" in sr._track_words({"streckenvideo": False}, {})
      and "Schauen wir" not in sr.template_script(cfg, dict(facts, streckenvideo=False), ["track"])["track"])

# AVATAR: Gabi says the avatar segments as a video when it is ready, else the voice does
import avatar_sync
class FakeRenderer:
    def __init__(self, ready): self.ready, self.ensured = ready, []
    def available(self): return True
    def ensure(self, kind, seg, text): self.ensured.append((kind, seg, text))
    def get(self, kind, seg, text): return Path(f"/tmp/{kind}_{seg}.mp4") if seg in self.ready else None
    def status(self, kind): return {s: {"state": "ready"} for s in self.ready}
obs15 = FakeObs({"Moderator": {"Gabi": False}, "iRacing": {}}, media={"Gabi": 0.6})
c15 = sr.load_config(); c15["obs"] = {"host": "127.0.0.1", "port": obs15.port}
fr = FakeRenderer({"intro"})
sp15 = Speaker()
r15 = sr.ShowRunner(c15, ObsClient(port=obs15.port), sp15, facts_fn=lambda: facts,
                    script_fn=lambda f, s_: ({"intro": "Hallo, ich bin Gabi.", "track": "Spa.", "outro": "Tschüss."}, "t"),
                    director_on=lambda: None, avatar=fr)
r15.prepare()
check("avatar: videos ordered for the avatar segments after the script", sorted(x[1] for x in fr.ensured) == ["intro", "outro"]
      and all(x[0] == "pre" for x in fr.ensured), fr.ensured)
vis15 = []
def w15():
    r15.tick()
    v = (r15.seg["id"] if r15.seg else None, obs15.scenes["Moderator"]["Gabi"])
    if not vis15 or vis15[-1] != v:
        vis15.append(v)
r15.start()
check("avatar: intro played as Gabi's video (file set, restarted, visible)", obs15.media["Gabi"].get("file") == "/tmp/pre_intro.mp4"
      and ("intro", True) in vis15 + [(r15.seg["id"] if r15.seg else None, obs15.scenes["Moderator"]["Gabi"])])
wait_for(lambda: r15.state == "done", 20, tick=w15)
said15 = [sg for _, sg, _ in sp15.lines]
check("avatar: intro not spoken by the voice, outro (no video yet) is", "intro" not in said15 and "outro" in said15, said15)
check("avatar: Gabi's video hidden after the intro", obs15.scenes["Moderator"]["Gabi"] is False
      and all(not g for s_, g in vis15 if s_ != "intro"), vis15)
check("avatar: the show waited for the video to end", any("Gabi spricht" in e["msg"] for e in r15.log), r15.log)
r15.set_script({"outro": "Neuer Abschied."})
check("avatar: an edited segment is rendered again", ("pre", "outro", "Neuer Abschied.") in fr.ensured)
fr_off = FakeRenderer({"intro"}); fr_off.available = lambda: False
r16 = sr.ShowRunner(c15, None, Speaker(), facts_fn=lambda: facts, script_fn=lambda f, s_: ({"intro": "x"}, "t"),
                    director_on=lambda: None, avatar=fr_off)
r16.prepare()
check("avatar: without a sync.so key nothing is ordered, 5 min lead", not fr_off.ensured
      and r16._due(dt.datetime(2026, 10, 15, 19, 30)) is None and r16._due(dt.datetime(2026, 10, 15, 19, 36)) == "prepare")
check("avatar: with it the script is written 20 min ahead", r15._due(dt.datetime(2026, 10, 15, 19, 21)) == "prepare")
# the renderer itself
rd_calls = []
def fake_ls(data, name, out_name=None, **k):
    rd_calls.append(out_name)
    if "kaputt" in out_name:
        raise RuntimeError("sync.so FAILED")
    return Path(td_r) / f"{out_name}.mp4"
with tempfile.TemporaryDirectory() as td_r:
    vox = type("V", (), {"synth": lambda self, t: (b"a", "audio/mpeg", "edge")})()
    rr = avatar_sync.AvatarRenderer(vox, lipsync_fn=lambda d, n, out_name=None, **k:
                                    (fake_ls(d, n, out_name="kaputt" if "Fehler" in rr.jobs[("pre", "outro")]["text"] else out_name)
                                     if False else fake_ls(d, n, out_name=out_name)))
    rr.ensure("pre", "intro", "Hallo.")
    wait_for(lambda: rr.jobs[("pre", "intro")]["state"] == "ready", 5)
    (Path(td_r) / Path(rr.jobs[("pre", "intro")]["file"]).name).write_bytes(b"x")
    check("renderer: ready, found for the same text only", rr.get("pre", "intro", "Hallo.") is not None
          and rr.get("pre", "intro", "Anders.") is None)
    n = len(rd_calls); rr.ensure("pre", "intro", "Hallo."); time.sleep(0.1)
    check("renderer: same text not rendered twice", len(rd_calls) == n)
    rr.lipsync = lambda d, n_, out_name=None, **k: (_ for _ in ()).throw(RuntimeError("sync.so FAILED"))
    rr.ensure("pre", "outro", "Tschüss.")
    wait_for(lambda: rr.jobs[("pre", "outro")]["state"] == "failed", 5)
    check("renderer: a failure is reported, no video", rr.jobs[("pre", "outro")]["state"] == "failed"
          and "FAILED" in rr.jobs[("pre", "outro")]["error"] and rr.get("pre", "outro", "Tschüss.") is None)

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
