"""Tests for settings_page.py (the config files as one page).
Run: python3 test_settings_page.py"""

import json
import re
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import settings_page as sp

PASS = FAIL = 0


def check(name, cond, info=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {info}")


def field(spec_id, key):
    return next(f for f in sp._fields(sp.BY_ID[spec_id]) if f["key"] == key)


def dget(d, path):
    return sp._get(d, path)[1]


print("Defaults agree with the modules")
import show_runner, race_commentator, race_qa, race_graphics, cls_proam, remote_replay  # noqa: E402
mods = {"show": show_runner.DEFAULT_CONFIG, "proam": cls_proam.DEFAULT_CONFIG,
        "replay": remote_replay.DEFAULTS}
for sid, defaults in mods.items():
    bad = [f["key"] for f in sp._fields(sp.BY_ID[sid])
           if sp._get(defaults, f["key"])[0] and dget(defaults, f["key"]) != f["default"]
           and f["kind"] != "secret"]
    check(f"D1 {sid}: same defaults as the module", not bad, bad)
cbad = [k for k, v in {**race_commentator.DEFAULT_CONFIG, **race_qa.DEFAULTS}.items()
        if any(f["key"] == k for f in sp._fields(sp.BY_ID["commentary"]))
        and field("commentary", k)["default"] != v and field("commentary", k)["kind"] != "secret"]
check("D2 commentary + Frag Claude: same defaults", not cbad, cbad)
g = race_graphics.DEFAULTS
check("D3 graphics defaults", field("show", "graphics.gap_s")["default"] == g["gap_s"]
      and field("show", "graphics.weather.source")["default"] == g["weather"]["source"]
      and field("show", "graphics.battle.source")["default"] == g["battle"]["source"]
      and field("show", "graphics.dotd.source")["default"] == g["dotd"]["source"])
src = Path("iracing_championship.py").read_text(encoding="utf-8")
block = src[src.index("DEFAULT_CONFIG = {"):src.index("\n}\n", src.index("DEFAULT_CONFIG = {"))]
for key, want in [("refresh_seconds", 60), ("race_points_min_distance_pct", 50),
                  ("participation_min_distance_pct", 75), ("league_mode", "auto")]:
    m = re.search(rf'"{key}":\s*("?[\w-]+"?)', block)
    check(f"D4 championship {key}", m and json.loads(m.group(1)) == want
          and field("champ", key)["default"] == want, m and m.group(1))
lg = Path("iracing_race_logger.py").read_text(encoding="utf-8")
check("D5 race logger config keys exist", all(f'"{k}"' in lg for k in ("url", "token", "auto", "live")))
rc = Path("iracing_rc_banner.py").read_text(encoding="utf-8")
check("D6 rc banner keys used", all(f'"{k}"' in rc for k in
                                    ("atvo_port", "atvo_enabled", "atvo_password", "hide_sources", "iracing_warnings")))
check("D7 every file is updater-PROTECTED (never overwritten by an update)",
      all(f'"{s["file"]}"' in Path("updater.py").read_text() for s in sp.FILES),
      [s["file"] for s in sp.FILES if f'"{s["file"]}"' not in Path("updater.py").read_text()])
check("D8 every file is gitignored",
      all(s["file"] in Path(".gitignore").read_text() for s in sp.FILES),
      [s["file"] for s in sp.FILES if s["file"] not in Path(".gitignore").read_text()])

print("Reading / saving")
base = Path(tempfile.mkdtemp())
show = sp.BY_ID["show"]
(base / "show_config.json").write_text(json.dumps({
    "start_at": "19:05", "obs": {"password": "geheim", "host": "127.0.0.1"},
    "graphics": {"weather": {"secs": 9}}, "highlights": {"fade_s": 0.6},
    "custom_thing": 1}), encoding="utf-8")
v = sp.view(show, base)
check("R1 values from the file, defaults for the rest",
      v["values"]["start_at"] == "19:05" and v["values"]["post_delay_s"] == 120)
check("R2 secret never sent to the page", v["values"]["obs.password"] == sp.SECRET_SET
      and "geheim" not in json.dumps(v), v["values"]["obs.password"])
check("R3 unknown keys shown as extra (nested too)",
      v["extra"] == {"graphics": {"weather": {"secs": 9}}, "highlights": {"fade_s": 0.6},
                     "custom_thing": 1}, v["extra"])
vals = dict(v["values"])
vals["start_at"] = "19:40"
vals["auto_start"] = False
vals["obs.password"] = ""
vals["segments"] = "intro, track ,outro"
ok, res = sp.save(show, vals, json.dumps(v["extra"]), base)
d = json.loads((base / "show_config.json").read_text(encoding="utf-8"))
check("S1 saved", ok, res)
check("S2 changed values written, list split", d["start_at"] == "19:40" and d["auto_start"] is False
      and d["segments"] == ["intro", "track", "outro"], d)
check("S3 empty secret = kept", d["obs"]["password"] == "geheim")
check("S4 unknown keys kept", d["custom_thing"] == 1 and d["highlights"] == {"fade_s": 0.6}
      and d["graphics"]["weather"]["secs"] == 9, d)
check("S5 untouched defaults NOT written (file stays small)",
      "post_delay_s" not in d and "sources" not in d and "avatar" not in d, sorted(d))
vals["obs.password"] = "neu"
sp.save(show, vals, None, base)
d = json.loads((base / "show_config.json").read_text(encoding="utf-8"))
check("S6 new secret written", d["obs"]["password"] == "neu")
vals["obs.password"] = ""
vals["obs.password__clear"] = True
sp.save(show, vals, None, base)
d = json.loads((base / "show_config.json").read_text(encoding="utf-8"))
check("S7 secret cleared on request", d["obs"]["password"] == "")
ok, res = sp.save(show, dict(vals, start_at="25:00", post_delay_s="abc", schedule_sources="{bad"), None, base)
check("S8 bad values refused, file unchanged",
      not ok and set(res["errors"]) == {"start_at", "post_delay_s", "schedule_sources"}
      and json.loads((base / "show_config.json").read_text())["start_at"] == "19:40", res)
ok, res = sp.save(show, vals, '{"highlights": {"fade_s": 1.0}}', base)
d = json.loads((base / "show_config.json").read_text(encoding="utf-8"))
check("S9 extra JSON replaces the extra keys (removed ones go)",
      ok and d["highlights"] == {"fade_s": 1.0} and "custom_thing" not in d
      and "secs" not in d.get("graphics", {}).get("weather", {}) and d["start_at"] == "19:40", d)
ok, res = sp.save(show, vals, '[1, 2]', base)
check("S10 extra must be an object", not ok and "_extra" in res["errors"])
vals["graphics.weather.source"] = "Wetter neu"
sp.save(show, vals, None, base)
d = json.loads((base / "show_config.json").read_text(encoding="utf-8"))
check("S11 nested field written into its dict", d["graphics"]["weather"]["source"] == "Wetter neu")
merged = race_graphics.merge_config(show_runner.load_config.__globals__["json"].loads(
    json.dumps(d))["graphics"])
check("S12 race_graphics still merges it (other weather keys stay default)",
      merged["weather"]["source"] == "Wetter neu" and merged["weather"]["secs"] == 12.0, merged["weather"])

champ = sp.BY_ID["champ"]
(base / "championship_config.json").write_text(json.dumps({"season_id": "abc", "league_slug": "cas-pccd"}))
vv = sp.view(champ, base)["values"]
vv["season_id"] = ""
sp.save(champ, vv, None, base)
d = json.loads((base / "championship_config.json").read_text())
check("T1 empty season = null (current season), not \"\"", d["season_id"] is None, d)
vv = sp.view(champ, base)["values"]
check("T2 json field default null shows as null", vv["points_table_race2"] is None)

com = sp.BY_ID["commentary"]
(base / "commentary_config.json").write_text(json.dumps({"mode": "ai", "api_key": "sk-x"}))
vv = sp.view(com, base)["values"]
ok, _ = sp.save(com, vv, None, base)
d = json.loads((base / "commentary_config.json").read_text())
check("T3 a select value the page has no option for is kept", ok and d["mode"] == "ai", d)
ok, res = sp.save(com, dict(vv, mode="nonsense"), None, base)
check("T4 ...but a new unknown one is refused", not ok and "mode" in res["errors"])
check("T5 api key kept", d["api_key"] == "sk-x")

(base / "rc_config.json").write_text("{ broken")
check("T6 broken file: error shown", "kein gültiges JSON" in sp.view(sp.BY_ID["rc"], base)["error"])
ok, res = sp.save(sp.BY_ID["rc"], {"atvo_port": 5021}, None, base)
check("T7 broken file never overwritten", not ok and (base / "rc_config.json").read_text() == "{ broken")
ok, _ = sp.save(sp.BY_ID["replay"], {"url": "192.168.86.73", "scene": "Replay"}, None, base)
d = json.loads((base / "replay_pc_config.json").read_text())
check("T8 new file created with only what differs", ok and d == {"url": "192.168.86.73"}, d)
check("T9 the remote_replay module reads it",
      remote_replay.RemoteReplay(config_file=base / "replay_pc_config.json").config()["url"]
      == "http://192.168.86.73:5000")

print("HTTP")
restarted = []
srv = sp.make_server(0, base, running_cb=lambda: {"dashboard": "Live Telemetry Dashboard"},
                     restart_cb=lambda tags: restarted.extend(tags) or tags)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
U = f"http://127.0.0.1:{port}"


def req(path, body=None, origin=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(U + path, data=data, method="POST" if data else "GET",
                               headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


st, page = req("/")
check("H1 page served", st == 200 and "Overlay-Einstellungen" in page)
st, spec = req("/api/spec")
check("H2 spec has every file, no secret defaults",
      st == 200 and [f["id"] for f in spec["files"]] == [f["id"] for f in sp.FILES])
st, vw = req("/api/file/commentary")
check("H3 file view, key masked", st == 200 and vw["values"]["api_key"] == sp.SECRET_SET
      and "sk-x" not in json.dumps(vw))
st, r = req("/api/file/replay", {"values": {"url": "192.168.86.80"}}, origin=f"http://127.0.0.1:{port}")
check("H4 save from the page", st == 200 and r["ok"]
      and json.loads((base / "replay_pc_config.json").read_text())["url"] == "192.168.86.80", r)
st, r = req("/api/file/replay", {"values": {"url": "evil"}}, origin="https://example.com")
check("H5 POST from another website refused", st == 403
      and json.loads((base / "replay_pc_config.json").read_text())["url"] == "192.168.86.80")
st, r = req("/api/running")
check("H6 running overlays + restart available", r == {"running": ["dashboard"],
      "names": {"dashboard": "Live Telemetry Dashboard"}, "can_restart": True}, r)
st, r = req("/api/restart", {"tags": ["dashboard"]}, origin=f"http://localhost:{port}")
check("H7 restart goes to the launcher", r["ok"] and restarted == ["dashboard"], r)
st, r = req("/api/file/nope")
check("H8 unknown file 404", st == 404)
check("H9 only bound to this PC", srv.server_address[0] == "127.0.0.1")
srv.shutdown()

print("Launcher helper")
import socket, time  # noqa: E402
blocker = sp.make_server(0, base)
bport = blocker.server_address[1]
bg = sp.Background(bport, wait_s=10.0, base=base)
time.sleep(1.2)
check("L1 busy port: waits instead of giving up", bg.srv is None and "busy" in bg.error, bg.error)
blocker.server_close()
end = time.time() + 5
while bg.srv is None and time.time() < end:
    time.sleep(0.1)
check("L2 ...and takes the port once the old one is gone", bg.srv is not None)
with urllib.request.urlopen(f"http://127.0.0.1:{bport}/api/spec", timeout=5) as r:
    check("L3 serving", r.status == 200)
bg.close()
s2 = socket.socket()
try:
    s2.bind(("127.0.0.1", bport))
    freed = True
except OSError:
    freed = False
s2.close()
check("L4 close() frees the port (for the relaunched launcher)", freed)

print(f"\n{PASS} passed, {FAIL} failed")
