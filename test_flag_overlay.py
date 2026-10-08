"""Offline tests for flag_overlay.py — no iRacing needed.

A stub `irsdk` module is injected before importing flag_overlay, a FakeIR
plays back scripted telemetry, and the module's `time` is replaced by a
fake clock so wall-clock guards (MIN_FINAL_LAP_S) are controllable.

The regression these were written for (2026-08-18): iRacing reports
SessionTimeRemain == -1.0 during gridding / the rolling start. The timed
white-flag trigger was `time_rem <= lap_estimate` with no lower bound, so
-1.0 <= 120.0 raised the WHITE FLAG on the leader's first S/F crossing;
the checkered followed one lap later with up to 57 minutes still on the
clock, and the overlay then sat in `done` for the rest of the race.
Confirmed in logs/flag_debug.jsonl on 2026-07-09, 2026-07-14 and
2026-08-13 (Oran Park race 1).
"""
import sys
import types

# --- stub pyirsdk ---------------------------------------------------------
_stub = types.ModuleType("irsdk")


class _StubIRSDK:
    def freeze_var_buffer_latest(self):
        pass

    def __getitem__(self, k):
        return None


_stub.IRSDK = _StubIRSDK
sys.modules.setdefault("irsdk", _stub)

import flag_overlay as F  # noqa: E402

PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
    else:
        FAIL.append(f"{name}: got {got!r}, want {want!r}")


# --- fake clock -----------------------------------------------------------
class FakeTime:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def strftime(self, fmt, *a):
        return "TEST"

    def sleep(self, s):
        self.now += s


CLOCK = FakeTime()
F.time = CLOCK


# --- fake telemetry -------------------------------------------------------
class FakeIR:
    def __init__(self, **kw):
        self.d = {
            "SessionNum": 1,
            "SessionState": 3,          # ParadeLaps
            "SessionFlags": 0,
            "SessionTime": 0.0,
            "SessionTimeRemain": 1500.0,
            "SessionLapsRemain": 32767,
            "EstLapTime": 0.0,
            "CarIdxLap": [0],
            "CarIdxLapDistPct": [0.0],
            "CarIdxClassPosition": [1],
            "DriverInfo": {"Drivers": [
                {"CarIdx": 0, "CarNumber": "1", "UserName": "Leader"}]},
            "SessionInfo": {"Sessions": [
                {"SessionNum": 1, "SessionType": "Race",
                 "SessionLaps": "unlimited"}]},
        }
        self.d.update(kw)

    def freeze_var_buffer_latest(self):
        pass

    def __getitem__(self, k):
        return self.d.get(k)

    def set(self, **kw):
        self.d.update(kw)
        return self


class Rig:
    """Drives a fresh FlagWatcher and records every debug line."""

    def __init__(self, total_laps=None, time_rem=1500.0, est_lap=0.0):
        self.w = F.FlagWatcher()
        self.w.ir = FakeIR()
        self.events = []
        self.w._dbg = lambda tag, **kw: self.events.append((tag, kw))
        sess = {"SessionNum": 1, "SessionType": "Race",
                "SessionLaps": str(total_laps) if total_laps else "unlimited"}
        self.w.ir.set(SessionInfo={"Sessions": [sess]},
                      SessionTimeRemain=time_rem, EstLapTime=est_lap)
        self.lap = 0

    # -- primitives --------------------------------------------------------
    def tick(self, n=1, dt=0.1, **kw):
        for _ in range(n):
            if kw:
                self.w.ir.set(**kw)
            CLOCK.now += dt
            self.w.ir.d["SessionTime"] = self.w.ir.d["SessionTime"] + dt
            self.w._tick()

    def cross(self, lap_time=90.0, **kw):
        """Advance the leader to mid-lap, then across the S/F line."""
        self.w.ir.set(CarIdxLapDistPct=[0.9], **kw)
        self.tick(dt=lap_time - 0.2)
        self.lap += 1
        self.w.ir.set(CarIdxLap=[self.lap], CarIdxLapDistPct=[0.05])
        self.tick(dt=0.2)

    # -- assertions --------------------------------------------------------
    def tags(self):
        return [t for t, _ in self.events]

    def first(self, tag):
        for t, kw in self.events:
            if t == tag:
                return kw
        return None

    def state(self):
        return self.w.state


def countdown(rig, laps, lap_time, start_rem, sess_state=4):
    """Run `laps` leader laps with the race clock counting down."""
    rem = start_rem
    for _ in range(laps):
        rem -= lap_time
        rig.cross(lap_time=lap_time, SessionTimeRemain=rem,
                  SessionState=sess_state)
    return rem


# =========================================================================
# 1. THE REGRESSION — SessionTimeRemain == -1.0 at the rolling start must
#    NOT raise the white flag on the leader's first crossing.
# =========================================================================
r = Rig(time_rem=1500.0)
r.tick(60, sess_state=3)                       # gridding: clock is valid
check("1a clock recognised as timed", r.w._timed_seen, True)
# Rolling start: iRacing drops SessionTimeRemain to -1.0, leader crosses.
r.cross(lap_time=90.0, SessionTimeRemain=-1.0, SessionState=4)
check("1b no white on the rolling start", "WHITE" in r.tags(), False)
check("1c overlay still idle", r.state(), "idle")
check("1d the block was logged", r.first("white_blocked") is not None, True)
check("1e ... with the right reason",
      r.first("white_blocked")["reason"], "invalid time_rem")

# Same, but with a REAL lap estimate available — still refused, because
# the reading itself is invalid, not the estimate.
r2 = Rig(time_rem=1500.0, est_lap=90.0)
r2.tick(60, sess_state=3)
r2.cross(lap_time=90.0, SessionTimeRemain=-1.0, SessionState=4)
check("1f invalid clock refused even with EstLapTime",
      "WHITE" in r2.tags(), False)

# =========================================================================
# 2. The invented 120 s lap estimate may never raise the white flag.
#    (Logged case 2026-08-05: time_rem=93.9s < 120.0s fired on lap 1 of a
#    circuit whose real lap is far shorter than 120 s.)
# =========================================================================
r = Rig(time_rem=1500.0)          # EstLapTime 0 -> default_120s
r.tick(60, sess_state=3)
r.cross(lap_time=60.0, SessionTimeRemain=93.9, SessionState=4)
check("2a guessed estimate cannot fire white", "WHITE" in r.tags(), False)
check("2b block reason", r.first("white_blocked")["reason"],
      "guessed lap estimate")

# =========================================================================
# 3. A NORMAL timed race still gets white then checkered, in that order.
# =========================================================================
r = Rig(time_rem=1500.0)
r.tick(60, sess_state=3)
rem = countdown(r, 17, 90.0, 1500.0)          # runs the clock down past 90 s
check("3a white raised", "WHITE" in r.tags(), True)
w = r.first("WHITE") or {"via": "", "cur_lap": 0, "time_rem": -1}
check("3b white came from the timed rule",
      w["via"].startswith("timed_last_crossing"), True)
check("3c white not on lap 1", w["cur_lap"] >= 2, True)
check("3d white while the clock still had a lap or less",
      0 <= w["time_rem"] <= 90.0, True)
# The finish: leader crosses again, clock now expired.
r.cross(lap_time=90.0, SessionTimeRemain=0.0, SessionState=5)
check("3e checkered raised", "CHECKERED" in r.tags(), True)
# (state may already have auto-advanced to "done" — the fake clock jumps a
#  full lap, which is longer than CHECKERED_DURATION.)
check("3f checkered latched", r.w._check_shown, True)
check("3g overlay is showing or has shown the checkered",
      r.state() in ("checkered", "done"), True)

# =========================================================================
# 4. Pure LAP race with iRacing's "unlimited" clock sentinel (604800).
#    It must not be mistaken for a timed race.
# =========================================================================
r = Rig(total_laps=3, time_rem=604800.0)
r.tick(60, sess_state=3)
check("4a unlimited sentinel is not a race clock", r.w._timed_seen, False)
r.cross(lap_time=65.0, SessionState=4, SessionTimeRemain=604800.0)
r.cross(lap_time=65.0, SessionTimeRemain=604800.0)
check("4b no white before the final lap", "WHITE" in r.tags(), False)
r.cross(lap_time=65.0, SessionTimeRemain=604800.0)     # leader starts lap 3/3
check("4c white on the final lap", r.first("WHITE")["via"], "lap_count 3/3")
CLOCK.now += 30.0
r.cross(lap_time=65.0, SessionTimeRemain=604800.0)
check("4d checkered on the finish line",
      r.first("CHECKERED")["via"], "lap_count 4>3")

# =========================================================================
# 5. SHORT LAP RACE inside a long time slot — a 3-lap heat with ~5 minutes
#    still on the session clock. The new end-of-race guard must NOT block
#    its checkered (this is why the lap-count rule is tested first).
#    Shape taken from the real 2026-08-13 19:51 session.
# =========================================================================
r = Rig(total_laps=3, time_rem=600.0)
r.tick(60, sess_state=3)
r.cross(lap_time=65.0, SessionTimeRemain=535.0, SessionState=4)
r.cross(lap_time=65.0, SessionTimeRemain=470.0)
r.cross(lap_time=65.0, SessionTimeRemain=405.0)         # starts lap 3/3
check("5a white on the final lap", r.first("WHITE")["via"], "lap_count 3/3")
CLOCK.now += 30.0
r.cross(lap_time=65.0, SessionTimeRemain=340.0)
check("5b checkered fires despite 340 s left",
      r.first("CHECKERED")["via"], "lap_count 4>3")

# =========================================================================
# 6. Belt and braces: if a white somehow gets raised early, the checkered
#    must NOT follow it while the race is clearly still running.
# =========================================================================
r = Rig(time_rem=1500.0)
r.tick(60, sess_state=3)
r.cross(lap_time=90.0, SessionTimeRemain=1410.0, SessionState=4)
r.w._white_shown = True                  # simulate a bad white
r.w._white_fired_at = CLOCK.now
r.w.state = "white_flag"
CLOCK.now += 60.0
r.cross(lap_time=90.0, SessionTimeRemain=1320.0)
check("6a checkered refused mid-race", "CHECKERED" in r.tags(), False)
check("6b the refusal was logged",
      r.first("checkered_blocked")["reason"], "race still running")
# ...and it DOES fire once the race is genuinely over.
CLOCK.now += 60.0
r.cross(lap_time=90.0, SessionTimeRemain=0.0, SessionState=5)
check("6c checkered fires at the real end", "CHECKERED" in r.tags(), True)

# =========================================================================
# 7. Timer-expiry safety net still works when nothing else caught the end.
# =========================================================================
r = Rig(time_rem=1500.0)
r.tick(60, sess_state=3)
r.cross(lap_time=90.0, SessionTimeRemain=1410.0, SessionState=4)
r.tick(60, SessionState=5, SessionTimeRemain=0.0)   # clock expires mid-lap
check("7a safety net raised the white",
      r.first("WHITE")["via"].startswith("timer_expiry"), True)

# =========================================================================
# 8. iRacing's own flag bits still win outright.
# =========================================================================
r = Rig(time_rem=1500.0)
r.tick(60, sess_state=3)
r.tick(5, SessionState=4, SessionFlags=F.FlagWatcher.FLAG_BIT_WHITE)
check("8a white bit honoured", r.first("WHITE")["via"], "SessionFlags white bit")
CLOCK.now += 30.0
r.tick(5, SessionFlags=F.FlagWatcher.FLAG_BIT_CHECKERED)
check("8b checkered bit honoured",
      r.first("CHECKERED")["via"], "SessionFlags checkered bit")

# =========================================================================
# 9. Session change resets the state machine (quali -> race).
# =========================================================================
r = Rig(time_rem=1500.0)
r.tick(60, sess_state=3)
r.w._white_shown = True
r.w._check_shown = True
r.w.state = "done"
r.w.ir.set(SessionNum=2, SessionInfo={"Sessions": [
    {"SessionNum": 2, "SessionType": "Race", "SessionLaps": "unlimited"}]})
r.tick(2, SessionState=4)
check("9a state machine reset for the new session", r.w.state, "idle")
check("9b white cleared", r.w._white_shown, False)

# =========================================================================
# 10. Flags only in QUALIFYING and RACE — never practice / warmup
#     (2026-10-08). iRacing raises the white / checkered bits in practice
#     too; the overlay must ignore them there.
# =========================================================================
def typed_rig(stype, sname="X"):
    rig = Rig(time_rem=1500.0)
    rig.w.ir.set(SessionInfo={"Sessions": [
        {"SessionNum": 1, "SessionType": stype, "SessionName": sname,
         "SessionLaps": "unlimited"}]})
    rig.tick(5, SessionState=4)
    rig.cross(lap_time=90.0, SessionFlags=0x0002, SessionState=4)   # white bit
    rig.tick(5)
    return rig

for stype, sname, want_shown in (
        ("Practice", "PRACTICE", False),
        ("Open Practice", "WARMUP", False),
        ("Warmup", "WARMUP", False),
        ("Offline Testing", "TESTING", False),
        ("Open Qualify", "QUALIFY", True),
        ("Lone Qualify", "QUALIFY", True),
        ("Race", "RACE", True)):
    rig = typed_rig(stype, sname)
    check(f"10 {stype}/{sname}: white shown={want_shown}",
          rig.state() == "white_flag", want_shown)

# Practice -> Race in the same weekend: the gate re-evaluates per session.
rig = typed_rig("Practice", "PRACTICE")
rig.w.ir.set(SessionNum=2, SessionFlags=0, SessionInfo={"Sessions": [
    {"SessionNum": 1, "SessionType": "Practice", "SessionLaps": "unlimited"},
    {"SessionNum": 2, "SessionType": "Race", "SessionLaps": "unlimited"}]})
rig.tick(5, SessionState=4)
rig.cross(lap_time=90.0, SessionFlags=0x0002, SessionState=4)
rig.tick(5)
check("10 practice -> race: flags come back for the race",
      rig.state(), "white_flag")

# -------------------------------------------------------------------------
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
