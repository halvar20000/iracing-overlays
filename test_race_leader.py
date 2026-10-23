"""
test_race_leader.py — headless checks for the New Race Leader logic.

Exercises LeaderState (the pure state machine inside
iracing_race_leader.py) without needing iRacing, Flask or a browser.

Run:  python test_race_leader.py
"""

from iracing_race_leader import LeaderState


def feed(state, is_racing, idx, name="", times=1):
    """Feed the same reading `times` times; return list of fire results."""
    fires = []
    for _ in range(times):
        fires.append(state.update(is_racing, idx, name, now=0.0))
    return fires


def test_silent_init():
    s = LeaderState(stable_polls=3)
    fires = feed(s, True, 5, "Pole Sitter", times=3)
    assert fires == [False, False, False], fires
    assert s.leader_idx == 5
    assert s.change_id == 0            # green-flag leader is NOT an event
    print("ok  silent init at green flag (no banner)")


def test_real_lead_change_fires_once():
    s = LeaderState(stable_polls=3)
    feed(s, True, 5, "Leader A", times=3)          # silent init
    # Car 8 passes for the lead and holds it.
    fires = feed(s, True, 8, "Leader B", times=3)
    assert fires == [False, False, True], fires    # fires on the 3rd stable poll
    assert s.change_id == 1
    assert s.event_name == "Leader B"
    # Keeps leading — must NOT fire again.
    more = feed(s, True, 8, "Leader B", times=5)
    assert not any(more), more
    assert s.change_id == 1
    print("ok  real lead change fires exactly once")


def test_jitter_rejected():
    s = LeaderState(stable_polls=3)
    feed(s, True, 5, "A", times=3)                 # silent init on car 5
    # Nose-to-tail flicker 5<->8, never stable for 3 in a row.
    seq = [(8, "B"), (5, "A"), (8, "B"), (5, "A"), (8, "B"), (5, "A")]
    fires = [s.update(True, idx, nm, now=0.0) for idx, nm in seq]
    assert not any(fires), fires
    assert s.change_id == 0
    assert s.leader_idx == 5
    print("ok  side-by-side jitter does not fire")


def test_not_racing_never_fires():
    s = LeaderState(stable_polls=3)
    # Parade / formation: is_racing False even though a car leads.
    fires = feed(s, False, 5, "A", times=5)
    assert not any(fires), fires
    assert s.leader_idx is None
    assert s.change_id == 0
    print("ok  nothing fires outside a green race")


def test_session_reset():
    s = LeaderState(stable_polls=3)
    feed(s, True, 5, "A", times=3)
    feed(s, True, 8, "B", times=3)                 # change_id -> 1
    assert s.change_id == 1
    s.reset()                                      # new session
    assert s.leader_idx is None and s.change_id == 0
    # New race: first leader silent again.
    fires = feed(s, True, 2, "C", times=3)
    assert not any(fires), fires
    assert s.change_id == 0 and s.leader_idx == 2
    print("ok  session reset re-initialises silently")


def test_leader_out_of_world_then_back():
    """If the front-runner briefly drops out (idx None), we must not
    re-fire when the same leader returns."""
    s = LeaderState(stable_polls=3)
    feed(s, True, 5, "A", times=3)
    feed(s, True, 8, "B", times=3)                 # change_id -> 1
    feed(s, True, None, "", times=2)               # leader momentarily gone
    fires = feed(s, True, 8, "B", times=3)         # same leader returns
    assert not any(fires), fires
    assert s.change_id == 1
    print("ok  same leader returning after a gap does not re-fire")


def test_no_banner_in_first_two_laps():
    """Thomas 2026-10-23: never in laps 1-2. A lead change there is tracked
    silently; the leader at the end of lap 2 is not news afterwards."""
    from iracing_sdk_base import StartLapGate
    s, g = LeaderState(stable_polls=1), StartLapGate(2.0)
    fires = [s.update(True, i, n, now=0.0, quiet=g.early(p))
             for i, n, p in ((1, "A", 1.01), (2, "B", 1.6), (2, "B", 3.1), (3, "C", 3.4))]
    assert fires == [False, False, False, True], fires
    assert s.change_id == 1 and s.event_name == "C"
    g0 = StartLapGate(2.0)                            # opening lap counted as 0
    assert [g0.early(p) for p in (0.02, 1.9, 2.0)] == [True, True, False]
    assert StartLapGate().early(7.3) is False          # attached mid-race: no gate
    print("ok  no new-leader banner in laps 1-2")


if __name__ == "__main__":
    test_silent_init()
    test_real_lead_change_fires_once()
    test_jitter_rejected()
    test_not_racing_never_fires()
    test_session_reset()
    test_leader_out_of_world_then_back()
    test_no_banner_in_first_two_laps()
    print("\nALL TESTS PASSED")
