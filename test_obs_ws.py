"""Checks for obs_ws.py against fake_obs.FakeObs (local websocket, no OBS).
Run: python test_obs_ws.py
"""
import time

from fake_obs import FakeObs
from obs_ws import ObsClient, ObsError

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


obs = FakeObs({"Intro": {"Song": True}, "Moderator": {"Tabelle": False, "Streckenvideo": False},
               "iRacing": {}}, media={"Streckenvideo": 0.5})
c = ObsClient(port=obs.port)
check("scene list", c.scenes() == ["Intro", "Moderator", "iRacing"], c.scenes())
c.set_scene("Moderator")
check("switch scene", c.current_scene() == "Moderator" and obs.current == "Moderator")
items = c.scene_items("Moderator")
check("scene items", set(items) == {"Tabelle", "Streckenvideo"} and items["Tabelle"][1] is False, items)
c.set_visible("Moderator", "Tabelle", True)
check("show source", obs.scenes["Moderator"]["Tabelle"] is True)
c.set_visible("Moderator", "Tabelle", False)
check("hide source", obs.scenes["Moderator"]["Tabelle"] is False)
try:
    c.set_visible("Moderator", "Gibtsnicht", True)
    check("missing source raises", False)
except ObsError as e:
    check("missing source raises ObsError with OBS's reason", "No scene items" in str(e), str(e))
c.media_restart("Streckenvideo")
st = c.media_status("Streckenvideo")
check("media playing + duration", st["state"] == "OBS_MEDIA_STATE_PLAYING" and st["duration_s"] == 0.5, st)
time.sleep(0.6)
check("media ended", c.media_status("Streckenvideo")["state"] == "OBS_MEDIA_STATE_ENDED")
check("stream status", c.streaming() is False)
try:
    c.set_scene("Gibtsnicht")
    check("unknown scene raises", False)
except ObsError:
    check("unknown scene raises", True)
check("still usable after an error", c.current_scene() == "Moderator")

# password
obs2 = FakeObs({"A": {}}, password="geheim")
check("password ok", ObsClient(port=obs2.port, password="geheim").scenes() == ["A"])
try:
    ObsClient(port=obs2.port, password="falsch").scenes()
    check("wrong password raises", False)
except ObsError:
    check("wrong password raises", True)
try:
    ObsClient(port=obs2.port).scenes()
    check("missing password raises", False)
except ObsError as e:
    check("missing password explains", "Passwort" in str(e), str(e))

# OBS not running
try:
    ObsClient(port=1, timeout=0.5).scenes()
    check("OBS down raises", False)
except ObsError as e:
    check("OBS down raises ObsError", "nicht erreichbar" in str(e), str(e))

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
