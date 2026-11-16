"""Offline checks for twitch_schedule.py against a fake Twitch: python3 test_twitch_schedule.py"""
import json, os, tempfile, urllib.parse
import stream_countdown as sc
import twitch_schedule as ts

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok += bool(cond); fail += not cond

class FakeTwitch:
    def __init__(self):
        self.segs = {}; self.n = 0; self.calls = []; self.polls = 0; self.refresh_ok = True
        self.token = None
    def __call__(self, method, url, headers=None, data=None, form=None, timeout=15):
        u = urllib.parse.urlparse(url); q = dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((method, u.path))
        if u.path == "/oauth2/device":
            assert form["scopes"] == ts.SCOPE
            return 200, {"device_code": "DC", "user_code": "ABCD-EFGH", "verification_uri": "https://www.twitch.tv/activate",
                         "interval": 5, "expires_in": 1800}
        if u.path == "/oauth2/token":
            if form["grant_type"].endswith("device_code"):
                self.polls += 1
                if self.polls < 2:
                    return 400, {"status": 400, "message": "authorization_pending"}
                self.token = "AT1"; return 200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 14000}
            if form["grant_type"] == "refresh_token":
                if not self.refresh_ok:
                    return 400, {"message": "Invalid refresh token"}
                assert form["refresh_token"] == "RT1" and "client_secret" not in form
                self.token = "AT2"; return 200, {"access_token": "AT2", "refresh_token": "RT1", "expires_in": 14000}
        if u.path == "/oauth2/validate":
            return 200, {"user_id": "583034073", "login": "sim_racing_broadcast"}
        assert headers["Authorization"] == "Bearer " + self.token and headers["Client-Id"] == "CID"
        if u.path == "/helix/games":
            return 200, {"data": [{"id": "19554", "name": "iRacing"}]}
        if u.path == "/helix/schedule" and method == "GET":
            if not self.segs:
                return 404, {"message": "segments not found"}
            return 200, {"data": {"segments": [dict(s, id=i) for i, s in self.segs.items()]}}
        if u.path == "/helix/schedule/segment":
            assert q["broadcaster_id"] == "583034073"
            if method == "POST":
                if getattr(self, "no_affiliate", False):
                    return 403, {"error": "Forbidden", "status": 403, "message": "single segment creation not authorized"}
                assert data["is_recurring"] is False and data["timezone"] == "Europe/Berlin" and data["category_id"] == "19554"
                self.n += 1; i = f"seg{self.n}"; self.segs[i] = dict(data)
                return 200, {"data": {"segments": [dict(s, id=k) for k, s in self.segs.items()]}}
            if method == "PATCH":
                if q["id"] not in self.segs: return 404, {}
                self.segs[q["id"]].update(data); return 200, {"data": {"segments": []}}
            if method == "DELETE":
                self.segs.pop(q["id"], None); return 204, {}
        return 500, {"message": "unexpected " + method + " " + url}

NOW = sc._iso("2026-11-16T12:00:00Z")
clock = [NOW]
def ev(league, rnd, race_iso, track, streamer="Thomas"):
    t = sc._iso(race_iso)
    return {"t": sc.stream_time(t, league), "race_t": t, "source": "cls", "league": league,
            "league_name": "", "round": rnd, "track": track, "title": "", "streamer": streamer}
EVENTS = [ev("cas-gt3-wct", 9, "2026-11-17T18:00:00Z", "Fuji International Speedway"),
          ev("cas-pccd", 9, "2026-11-19T19:00:00Z", "Virginia International Raceway"),
          ev("cas-iec", 4, "2026-11-28T16:00:00Z", "Michelin Raceway Road Atlanta"),
          ev("cas-fun-league", 3, "2026-11-20T19:00:00Z", "Lime Rock"),        # no stream time -> not planned
          ev("cas-gt3-wct", 20, "2027-03-01T18:00:00Z", "Far away"),         # beyond the horizon
          ev("cas-sfl-cup", 1, "2026-11-25T19:00:00Z", "Road Atlanta", "Andreas"),  # Andreas streams it
          ev("cas-combined-cup", 7, "2026-11-18T19:00:00Z", "Oschersleben", None)]  # nobody on the calendar
twitch_ev = dict(EVENTS[0], source="twitch")

p = ts.plan(EVENTS + [twitch_ev], NOW)
check("plan: only leagues with a stream time, within 6 weeks, CLS only", sorted(p) == ["cas-gt3-wct:9", "cas-iec:4", "cas-pccd:9"])
check("plan: streamer None = every race", "cas-sfl-cup:1" in ts.plan(EVENTS, NOW, streamer=None))
check("plan: name case-insensitive", sorted(ts.plan(EVENTS, NOW, streamer="andreas")) == ["cas-sfl-cup:1"])
ce = sc.cls_events("cas-pccd", {"league": {"slug": "cas-pccd"}, "rounds": [
    {"number": 9, "startsAt": "2026-11-19T19:00:00.000Z", "status": "UPCOMING", "track": "VIR", "streamer": "Andreas"},
    ]})
ce_old = sc.cls_events("cas-pccd", {"league": {"slug": "cas-pccd"}, "rounds": [
    {"number": 9, "startsAt": "2026-11-19T19:00:00.000Z", "status": "UPCOMING", "track": "VIR"}]})
check("countdown events carry the CLS streamer (only when CLS sends it)",
      ce[0]["streamer"] == "Andreas" and "streamer" not in ce_old[0] and ts.has_streamers(ce) and not ts.has_streamers(ce_old))
check("plan: PCC stream 19:40 CET, title + duration", p["cas-pccd:9"]["start"] == sc._iso("2026-11-19T18:40:00Z")
      and p["cas-pccd:9"]["title"] == "CAS Porsche Community Cup · Lauf 9 · Virginia International Raceway"
      and p["cas-iec:4"]["duration"] == 300)

tmp = os.path.join(tempfile.mkdtemp(), "twitch_schedule.json")
fake = FakeTwitch()
t = ts.TwitchSchedule(path=tmp, http=fake, clock=lambda: clock[0])
check("default client id preset", t.status()["client_id"] == ts.DEFAULT_CLIENT_ID)
t.set_client("")
try:
    t.start_login(wait=False); check("login without client id refused", False)
except ValueError:
    check("login without client id refused", True)
t.set_client("CID")
pend = t.start_login(wait=False)
check("device login shows the code", pend["user_code"] == "ABCD-EFGH")
check("poll: pending first", t.poll_login_once() is False)
check("poll: then logged in", t.poll_login_once() is True and t.status()["logged_in"] and t.status()["login"] == "sim_racing_broadcast")

dry = t.sync(EVENTS, dry=True)
check("dry run: 3 new, nothing sent", len(dry) == 3 and all(a.startswith("NEU") for a in dry) and not fake.segs)
old_cls = [{k: v for k, v in e.items() if k != "streamer"} for e in EVENTS]
na = t.sync(old_cls)
check("CLS without stream calendar: nothing sent, clear line", len(na) == 1 and "Stream-Kalender" in na[0]
      and not any(c == ("POST", "/helix/schedule/segment") for c in fake.calls))
fake.no_affiliate = True
na = t.sync(EVENTS)
check("403 per segment: reported per race, auto stays on", all(a.startswith("FEHLER 403") for a in na) and len(na) == 3
      and t.status()["auto"] and not fake.segs)
fake.no_affiliate = False
acts = t.sync(EVENTS)
check("sync: 3 segments created", len(fake.segs) == 3 and len(acts) == 3 and len(t.st["segments"]) == 3)
check("sync: ids stored per round", t.st["segments"]["cas-iec:4"]["id"] in fake.segs)
check("sync: second run changes nothing", t.sync(EVENTS) == [] and len(fake.segs) == 3)
handed = [dict(e, streamer="Andreas") if e["league"] == "cas-pccd" else e for e in EVENTS]
acts = t.sync(handed)
check("race handed to Andreas -> removed from the schedule", acts == ["ENTFERNT CAS Porsche Community Cup · Lauf 9 · Virginia International Raceway"]
      and len(fake.segs) == 2)
acts = t.sync(EVENTS)
check("handed back -> created again", len(acts) == 1 and acts[0].startswith("NEU") and len(fake.segs) == 3)

moved = [dict(EVENTS[0], t=EVENTS[0]["t"] + 86400)] + EVENTS[1:]
acts = t.sync(moved)
sid = t.st["segments"]["cas-gt3-wct:9"]["id"]
check("CLS moves a round -> PATCH", acts and acts[0].startswith("GEÄNDERT")
      and fake.segs[sid]["start_time"] == "2026-11-18T18:00:00Z")
acts = t.sync([e for e in moved if e["league"] != "cas-pccd"])
check("round gone from CLS -> segment removed", any(a.startswith("ENTFERNT") for a in acts) and len(fake.segs) == 2)

fake.segs.pop(sid)                                   # deleted by hand on Twitch
acts = t.sync(moved)
check("segment deleted on Twitch -> created again (+ PCC back)", sum(a.startswith("NEU") for a in acts) == 2 and len(fake.segs) == 3)

state_lost = ts.TwitchSchedule(path=tmp + ".2", http=fake, clock=lambda: clock[0])
state_lost.st = {k: v for k, v in t.st.items() if k != "segments"}
acts = state_lost.sync(moved)
check("lost state file: existing segments adopted, no duplicates", acts == [] and len(fake.segs) == 3
      and len(state_lost.st["segments"]) == 3)

clock[0] = NOW + 5 * 3600                            # token expiring -> refresh
t.sync(moved)
check("token refreshed before expiry (public client, no secret)", fake.token == "AT2" and t.st["access_token"] == "AT2")
clock[0] = NOW + 30 * 86400
t.sync(moved)
check("past segments forgotten, not deleted", "cas-gt3-wct:9" not in t.st["segments"] and len(fake.segs) == 3)

fake.refresh_ok = False; clock[0] += 5 * 3600
try:
    t.sync(moved); check("dead refresh token -> clear error + logged out", False)
except ValueError as e:
    check("dead refresh token -> clear error + logged out", "neu anmelden" in str(e) and not t.status()["logged_in"])
check("state file has no token after that", "refresh_token" not in json.load(open(tmp)))
t.set_client("OTHER")
check("other client id drops the login", not t.status()["logged_in"] and t.status()["client_id"] == "OTHER")

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
