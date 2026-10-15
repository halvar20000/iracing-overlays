"""Offline checks for race_graphics.py (GRAFIK during REGIE).
Run: python test_race_graphics.py
"""
import race_graphics as rg

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


def run(g, t0, t1, state_fn, step=1.0):
    """Tick from t0 to t1; returns [(t, action, key)]."""
    out, t = [], t0
    while t <= t1:
        for a, k in g.tick(t, state_fn(t)):
            out.append((t, a, k))
        t += step
    return out


def race(t, **kw):
    s = {"session": "race", "racing": True, "replay": False, "shot": None, "shot_since": None,
         "progress": t / 2400.0, "dotd_ready": True}
    s.update(kw)
    return s


# 1) weather once, shortly after the green, hidden after `secs`
g = rg.GraphicsDirector()
ev = run(g, 0, 300, lambda t: race(t, dotd_ready=False))
shows = [(t, k) for t, a, k in ev if a == "show"]
check("weather once ~40 s after the green", shows == [(40.0, "weather")], shows)
check("weather hidden after 12 s", (52.0, "hide", "weather") in ev, ev)

# 2) battle: only when REGIE has shown a battle for a few seconds, then not again for every_s
g = rg.GraphicsDirector({"weather": {"source": ""}})
ev = run(g, 0, 600, lambda t: race(t, shot="battle" if 100 <= t < 500 else "leader",
                                   shot_since=100.0 if 100 <= t < 500 else 0, dotd_ready=False))
bt = [t for t, a, k in ev if a == "show" and k == "battle"]
check("battle after 6 s of a battle shot, then every 180 s", bt == [106.0, 286.0, 466.0], bt)
check("no battle graphic outside battle shots", all(100 <= t < 500 for t in bt))

# 3) provisional DotD in the last third, once
g = rg.GraphicsDirector({"weather": {"source": ""}})
ev = run(g, 0, 2400, lambda t: race(t))
dd = [t for t, a, k in ev if a == "show" and k == "dotd"]
check("dotd once at 2/3 of the race", len(dd) == 1 and abs(dd[0] / 2400 - 0.67) < 0.01, dd)
g = rg.GraphicsDirector({"weather": {"source": ""}})
ev = run(g, 0, 2400, lambda t: race(t, dotd_ready=False))
check("no dotd without a provisional winner", not any(k == "dotd" for _, a, k in ev))

# 4) one at a time + gap; replay hides and blocks; not in quali
g = rg.GraphicsDirector()
ev = run(g, 0, 200, lambda t: race(t, shot="battle", shot_since=0.0, dotd_ready=False))
on, last_hide, ok = None, -1e9, True
for t, a, k in ev:
    if a == "show":
        ok &= on is None and t - last_hide >= 45
        on = k
    else:
        on, last_hide = None, t
check("one graphic at a time, 45 s apart", ok and len([1 for _, a, _ in ev if a == "show"]) >= 2, ev)
g = rg.GraphicsDirector()
ev = run(g, 0, 60, lambda t: race(t, replay=45 <= t < 50, dotd_ready=False))
check("our replay takes the graphic down", (45.0, "hide", "weather") in ev, ev)
g = rg.GraphicsDirector()
ev = run(g, 0, 300, lambda t: race(t, session="quali", shot="battle", shot_since=0.0))
check("nothing in qualifying", ev == [], ev)

# 5) missing source / disabled
g = rg.GraphicsDirector()
g.mark_missing("weather")
ev = run(g, 0, 100, lambda t: race(t, dotd_ready=False))
check("source missing in OBS -> skipped", not any(k == "weather" for _, a, k in ev))
g = rg.GraphicsDirector({"enabled": False})
check("graphics off", run(g, 0, 300, lambda t: race(t, shot="battle", shot_since=0.0)) == [])
check("config merge keeps defaults", rg.merge_config({"battle": {"source": "Catch"}})["battle"]
      == {"source": "Catch", "secs": 10.0, "after_s": 6.0, "every_s": 180.0})

# 6) lines
check("weather line dry", rg.weather_line({"connected": True, "air_temp": 21.4, "track_temp": 33.6, "wetness": "Dry"})
      == "Ein Blick aufs Wetter: 21 Grad Luft und 34 Grad auf dem Asphalt, trocken.")
check("weather line wet", rg.weather_line({"connected": True, "air_temp": 15, "precip": 30, "declared_wet": True})
      .endswith("nass."))
check("weather line without data", rg.weather_line({"connected": False}) is None)
check("dotd line provisional", "Gregor Micewski" in (rg.dotd_line({"ok": True, "provisional": True,
                                                                  "winner": {"name": "Gregor Micewski"}}) or ""))
check("dotd line nothing yet", rg.dotd_line({"ok": False, "error": "no lap"}) is None)

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
