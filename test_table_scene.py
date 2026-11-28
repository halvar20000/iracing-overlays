"""Tests for table_scene.py (tables on their own OBS scene, stinger + title
card). python3 test_table_scene.py"""
import table_scene
from table_scene import TableScene

FAILS = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


class FakeObs:
    def __init__(self):
        self.scene = "Moderator"
        self.transition = "Fade"
        self.transitions = ["Cut", "Fade", "Stinger", "Stinger Tabellen"]
        self.items = {
            "Moderator": {"MyCAM": True, "Last_RACE_WCT": False},
            "Tabellen": {"Background": True, "Titelkarte": True, "Last_RACE_WCT": False,
                         "Standing_WCT_PRO_AM": True, "Teams_Standing_WCT": False,
                         "commentary_voice": True, "Letztes Rennen": False},
        }
        self.log = []                 # (action, ...) in order
        self.no_create = False

    def current_scene(self):
        return self.scene

    def set_scene(self, name):
        self.log.append(("scene", name, self.transition))
        self.scene = name

    def current_transition(self):
        return self.transition

    def set_transition(self, name):
        self.log.append(("transition", name))
        self.transition = name

    def call(self, rt, data=None):
        if rt == "CreateSceneItem":
            if self.no_create:
                raise RuntimeError("create refused")
            self.log.append(("create", data["sceneName"], data["sourceName"]))
            self.items[data["sceneName"]][data["sourceName"]] = True
            return {"sceneItemId": 99}
        assert rt == "GetSceneTransitionList"
        return {"transitions": [{"transitionName": n} for n in self.transitions]}

    def scene_items(self, scene):
        return {n: (i, on) for i, (n, on) in enumerate(self.items[scene].items())}

    def set_visible(self, scene, source, visible):
        self.log.append(("vis", scene, source, visible))
        self.items[scene][source] = visible


def make(cfg=None):
    obs = FakeObs()
    later = []                        # spawned jobs, run by hand
    sleeps = []
    waits = []
    def wait(ev, t):                  # the card page "reports" at once
        waits.append(t)
        return True
    ts = TableScene(obs_factory=lambda: obs, cfg_fn=lambda: cfg or {}, sub_fn=lambda: "CAS IEC",
                    sleep=sleeps.append, spawn=lambda fn, *a: later.append((fn, a)),
                    clock=lambda: 0.0, wait=wait)
    return ts, obs, later, sleeps


def run(later):
    while later:
        fn, a = later.pop(0)
        fn(*a)


# 1. Moderator -> key "lastrace": page on, others off, title, stinger, switch
ts, obs, later, sleeps = make()
st0 = ts.state()["seq"]
ok, msg = ts.toggle("lastrace")
t = obs.items["Tabellen"]
check("T1 ok", ok)
check("T1 page on, other page off", t["Last_RACE_WCT"] and not t["Standing_WCT_PRO_AM"])
check("T1 non-page items untouched", t["Background"] and t["Titelkarte"] and t["commentary_voice"])
check("T1 card put on Moderator too", ("create", "Moderator", "Titelkarte") in obs.log
      and obs.items["Moderator"]["Titelkarte"])
check("T1 on Tabellen with a Cut under the card (no stinger)", obs.scene == "Tabellen"
      and ("scene", "Tabellen", "Cut") in obs.log)
s = ts.state()
check("T1 title card RESULTATE + league", s["seq"] > st0 and s["title"] == "RESULTATE" and s["sub"] == "CAS IEC")
check("T1 card covering before the cut", sleeps[:1] == [0.4])
vis_i = max(i for i, x in enumerate(obs.log) if x[0] == "vis")
check("T1 page set before the switch", vis_i < obs.log.index(("scene", "Tabellen", "Cut")))
run(later)                                         # transition restored
check("T1 transition back to Fade", obs.transition == "Fade")

# 2. other key while on Tabellen: no scene switch, swap after the card covers
n_scene = sum(1 for x in obs.log if x[0] == "scene")
ok, msg = ts.toggle("teams")
check("T2 swap without scene switch", ok and sum(1 for x in obs.log if x[0] == "scene") == n_scene)
check("T2 teams on, lastrace off", t["Teams_Standing_WCT"] and not t["Last_RACE_WCT"])
check("T2 title TEAMWERTUNG", ts.state()["title"] == "TEAMWERTUNG")
check("T2 waited swap_s", 0.45 in sleeps)

# 3. same key again = back to the scene before, stinger, page hidden later
ok, msg = ts.toggle("teams")
check("T3 back to Moderator with a Cut under a blank card", ok and obs.scene == "Moderator"
      and obs.log[-1] == ("scene", "Moderator", "Cut"))
s3 = ts.state()
check("T3 blank card, no word", s3["title"] == "" and s3["blank"] and s3["hold_s"] == 0.25)
check("T3 card not created twice", sum(1 for x in obs.log if x[0] == "create") == 1)
check("T3 page still up during the stinger", t["Teams_Standing_WCT"])
run(later)
check("T3 page hidden afterwards", not t["Teams_Standing_WCT"])
check("T3 transition back", obs.transition == "Fade")

# 3b. "Stinger Tabellen" missing in OBS: Fade, never the REPLAY stinger
ts, obs, later, sleeps = make({"tables": {"wipe": False}})
obs.transitions = ["Cut", "Fade", "Stinger"]
obs.transition = "Cut"
ts.toggle("lastrace")
check("T3b fallback Fade + note", obs.log[-1] == ("scene", "Tabellen", "Fade") and "fehlt" in ts.note)

# 4. shown again before the hide fires: stays up
ts, obs, later, sleeps = make()
ts.toggle("lastrace"); later.clear()
ts.back()
ts.toggle("lastrace")
run(later)
check("T4 re-shown page not hidden by the old timer", obs.items["Tabellen"]["Last_RACE_WCT"])

# 4b. wipe off = the old stinger + card
ts, obs, later, sleeps = make({"tables": {"wipe": False}})
ts.toggle("lastrace")
check("T4b wipe off: Stinger, card not added to Moderator", obs.log[-1] == ("scene", "Tabellen", "Stinger Tabellen")
      and "Titelkarte" not in obs.items["Moderator"] and sleeps[:1] == [0.15])
ts.toggle("lastrace")
check("T4b back: Stinger, card cleared (not blank)", obs.log[-1] == ("scene", "Moderator", "Stinger Tabellen")
      and not ts.state()["blank"])

# 4c. the card cannot be put on the scene: falls back to the stinger
ts, obs, later, sleeps = make()
obs.no_create = True
ok, _ = ts.toggle("lastrace")
check("T4c no card on Moderator -> Stinger, note", ok and obs.log[-1] == ("scene", "Tabellen", "Stinger Tabellen")
      and "Titelkarte" in ts.note)

# 4d. the card page reports "covered" (only for the current seq); no report = cut after cover_max_s + note
import threading as _th
got = []
ts = TableScene(obs_factory=lambda: FakeObs(), cfg_fn=lambda: {"tables": {"cover_max_s": 2}},
                sleep=lambda s: None, spawn=lambda *a: None)
ev_seen = []
ts._wait = lambda ev, t: (ev_seen.append(ev), ts.covered(ts.state()["seq"] - 1), ts.covered(ts.state()["seq"]), ev.is_set())[-1]
ok, _ = ts.toggle("lastrace")
check("T4d covered: old seq refused, current accepted, cut follows", ok and ev_seen and ev_seen[0].is_set()
      and ts.covered(-5) is False and ts.note == "")
ts = TableScene(obs_factory=lambda: FakeObs(), cfg_fn=dict, sleep=lambda s: None, spawn=lambda *a: None,
                wait=lambda ev, t: False)
ok, _ = ts.toggle("lastrace")
check("T4d no report: still switches, note says why", ok and "nicht gemeldet" in ts.note)
check("T9c page reports covered", "/tables/covered?seq=" in table_scene.TITLE_HTML)

# 5. errors
ts, obs, later, sleeps = make()
ok, msg = ts.toggle("nope")
check("T5 unknown key", not ok and "unbekannt" in msg)
ok, msg = ts.toggle("dotd")                        # Last_DOTD not on the scene
check("T5 missing source named", not ok and "Last_DOTD" in msg and obs.scene == "Moderator")

# 6. config: own items, removed item, no transition
cfg = {"tables": {"transition": "", "wipe": False, "items": {"lastrace": {"source": "Letztes Rennen"},
                                              "rsvp": None, "x": {"source": "Teams_Standing_WCT", "title": "X"}}}}
ts, obs, later, sleeps = make(cfg)
c = ts.cfg()
check("T6 merged items", c["items"]["lastrace"] == {"source": "Letztes Rennen", "title": "RESULTATE"}
      and "rsvp" not in c["items"] and c["items"]["x"]["title"] == "X")
ts.toggle("lastrace")
check("T6 no transition change", not any(x[0] == "transition" for x in obs.log)
      and obs.scene == "Tabellen" and obs.items["Tabellen"]["Letztes Rennen"])

# 7. show() for the AI show: any source on the scene; has()
ts, obs, later, sleeps = make()
check("T7 has()", ts.has("Letztes Rennen") and not ts.has("Tabelle"))
ok, _ = ts.show("Letztes Rennen", "RESULTATE PRO")
check("T7 show by name", ok and obs.items["Tabellen"]["Letztes Rennen"]
      and not obs.items["Tabellen"]["Standing_WCT_PRO_AM"])
ok, _ = ts.show("Last_RACE_WCT", "X")
check("T7 a name shown before is known (hidden on the next)", not obs.items["Tabellen"]["Letztes Rennen"])
ts.back(scene="Moderator")
check("T7 back to the given scene", obs.scene == "Moderator")

# 8. OBS down
ts = TableScene(obs_factory=lambda: None, cfg_fn=dict, sleep=lambda s: None, spawn=lambda *a: None)
ok, msg = ts.toggle("lastrace")
check("T8 OBS down = error, no crash", not ok and "OBS" in msg)

check("T9b card plays a blank wipe", "s.title || s.blank" in table_scene.TITLE_HTML)
check("T9 title page has the poll + demo", "/tables/state" in table_scene.TITLE_HTML and "demo" in table_scene.TITLE_HTML)

# 10. the AI show: page segments go to the Tabellen scene (real ShowRunner,
#     real obs_ws against fake_obs)
import threading, time
import show_runner as sr
from fake_obs import FakeObs
from obs_ws import ObsClient

class Speaker:
    def __init__(self):
        self.lines, self.done, self.cancelled, self.n = [], set(), set(), 0
        threading.Thread(target=self._play, daemon=True).start()
    def say(self, text, seg):
        self.n += 1; self.lines.append((self.n, seg, text)); return self.n
    def is_done(self, i):
        return i in self.done or i in self.cancelled
    def cancel(self, ids):
        self.cancelled.update(ids)
    def _play(self):
        while True:
            for i, seg, text in list(self.lines):
                if not self.is_done(i):
                    time.sleep(0.1); self.done.add(i); break
            time.sleep(0.02)

fo = FakeObs({"Moderator": {"Gabi Ruhe": True, "Letztes Rennen": False, "Tabelle": False,
                            "Streckenvideo": False},
              "Tabellen": {"Titelkarte": True, "Letztes Rennen": False, "Tabelle": False},
              "iRacing": {}}, media={"Streckenvideo": 0.3})
c = sr.load_config()
c["obs"] = {"host": "127.0.0.1", "port": fo.port}
c["segments"] = ["intro", "last_race", "standings", "track", "outro"]
c["avatar"] = {"enabled": False, "segments": []}
c["tables"] = {"swap_s": 0.05, "hide_after_s": 0.05, "enter_delay_s": 0, "cover_s": 0.05, "cover_max_s": 0.05}
tsc = TableScene(obs_factory=lambda: ObsClient(port=fo.port), cfg_fn=lambda: c)
facts = {"liga": "CAS IEC", "strecke": "Fuji", "runde": 5, "letzte_runde": {"strecke": "Spa"}, "tabelle": [{"pos": 1}]}
script = {"intro": "Hallo.", "last_race": "Das Ergebnis.", "standings": "Die Tabelle.",
          "track": "Die Strecke.", "outro": "Los gehts."}
r = sr.ShowRunner(c, ObsClient(port=fo.port), Speaker(), facts_fn=lambda: facts,
                  script_fn=lambda f, s_: (script, "template"), director_on=lambda: None,
                  tables=tsc)
seen = []
r.start()
t0 = time.time()
while r.state != "done" and time.time() - t0 < 20:
    r.tick()
    sid = r.seg["id"] if r.seg else None
    snap = (sid, fo.current, tsc.state()["title"])
    if not seen or seen[-1] != snap:
        seen.append(snap)
    time.sleep(0.05)
check("T10 show done", r.state == "done")
scenes = {s: {sc for (x, sc, _) in seen if x == s} for s in script}
check("T10 last_race + standings on Tabellen", scenes["last_race"] == {"Tabellen"} and scenes["standings"] == {"Tabellen"})
check("T10 intro / track on Moderator", scenes["intro"] == {"Moderator"} and "Tabellen" not in scenes["track"])
titles = [t for (_, _, t) in seen if t]
check("T10 title cards RESULTATE then MEISTERSCHAFT", titles[:1] == ["RESULTATE"] and "MEISTERSCHAFT" in titles)
check("T10 entered + left with a Cut under the card, ONE entry (standings swapped in place)",
      [x for x in fo.switches if x[0] == "Tabellen"] == [("Tabellen", "Cut")]
      and ("Moderator", "Cut") in fo.switches and fo.scenes["Moderator"].get("Titelkarte") is True)
check("T10 pages never put on the Moderator scene",
      not any(rd.get("sceneName") == "Moderator" and rd.get("sceneItemEnabled") is True
              and fo.ids.get(("Moderator", "Letztes Rennen")) == rd.get("sceneItemId")
              for rt, rd in fo.calls if rt == "SetSceneItemEnabled"))
check("T10 hand-over to iRacing at the end", fo.current == "iRacing")

print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED'}")
raise SystemExit(1 if FAILS else 0)
