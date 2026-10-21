"""CamCameraState bits on the dashboard (2026-10-21): the constants were
shifted by one bit, so MOST EXCITING (0x0004) switched on the camera TOOL and
the "hide UI" mirror read "scenic active". Real bits (irsdk.CameraState):
cam tool 0x0004, ui hidden 0x0008, auto shot 0x0010, changeable 0x01FC.

Run:  python test_cam_state.py [path to the project folder]
"""
import sys, types
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
irsdk_stub = types.ModuleType("irsdk")
class _FakeIRSDK:
    """CamCameraState like iRacing keeps it: read-only bits 0x0003 stay,
    cam_set_state replaces the changeable ones."""
    def __init__(self):
        self.fields = {"CamCameraState": 0}
        self.writes = []
    def freeze_var_buffer_latest(self): pass
    def __getitem__(self, k): return self.fields.get(k)
    def cam_set_state(self, state):
        self.writes.append(state)
        old = self.fields.get("CamCameraState") or 0
        self.fields["CamCameraState"] = (old & 0x0003) | (state & 0x01FC)
    def cam_switch_num(self, *a): pass
    def replay_set_play_speed(self, *a): pass
irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub
flask_stub = types.ModuleType("flask")
class _App:
    def __init__(self, *a, **k): pass
    def after_request(self, f): return f
    def route(self, *a, **k): return lambda f: f
flask_stub.Flask = _App; flask_stub.Response = object
flask_stub.render_template_string = lambda s, **k: s
flask_stub.jsonify = lambda *a, **k: None
flask_stub.request = types.SimpleNamespace(json=None, args={})
sys.modules["flask"] = flask_stub
sys.path.insert(0, str(root))
import iracing_dashboard as dash

keys = []
dash.send_key_to_iracing = lambda vk=0x20: (keys.append(vk), (True, "sent"))[1]

failures = []
def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)

def poller(state):
    p = dash.TelemetryPoller(poll_hz=10)
    p.connected = True
    p.ir.fields["CamCameraState"] = state
    p.ir.writes.clear()
    keys.clear()
    return p

T = dash.TelemetryPoller
check("constants match irsdk.CameraState",
      (T.CAM_TOOL_ACTIVE, T.CAM_UI_HIDDEN, T.CAM_USE_AUTO_SHOT, T.CAM_CHANGEABLE)
      == (0x0004, 0x0008, 0x0010, 0x01FC))

# MOST EXCITING on: only the auto-shot bit, UI stays hidden, read-only bits untouched
p = poller(0x0001 | 0x0008)                       # session screen + UI hidden
ok = p.set_auto_camera(True)
st = p.ir.fields["CamCameraState"]
check("auto on: one write", ok and len(p.ir.writes) == 1, hex(st))
check("auto on: auto-shot set, UI still hidden", st & 0x0010 and st & 0x0008, hex(st))
check("auto on: camera tool NOT set", not (p.ir.writes[0] & 0x0004), hex(p.ir.writes[0]))
check("auto on: no read-only bit written", not (p.ir.writes[0] & 0x0003))
check("is_auto_camera_active reads 0x0010", p.is_auto_camera_active())

# idempotent
p.ir.writes.clear()
p.set_auto_camera(True)
check("auto on again: no write", p.ir.writes == [])

# off, with the camera tool already on (e.g. left over from the old code) -> cleared
p = poller(0x0004 | 0x0010 | 0x0020)
p.set_auto_camera(False)
st = p.ir.fields["CamCameraState"]
check("auto off: auto-shot cleared", not (st & 0x0010), hex(st))
check("auto off: camera tool cleared", not (st & 0x0004), hex(st))
check("auto off: other changeable bit kept (temp edits)", st & 0x0020, hex(st))
p.ir.writes.clear()
p.set_auto_camera(False)
check("auto off again: no write", p.ir.writes == [])

# Hide-UI toggle reads the REAL state, no spacebar
p = poller(0x0000)
ok, msg = p.toggle_ui_hidden()
st = p.ir.fields["CamCameraState"]
check("toggle: UI hidden via bit 0x0008", ok and st & 0x0008 and p.iracing_ui_hidden, msg)
check("toggle: no spacebar", keys == [])
ok, msg = p.toggle_ui_hidden()
st = p.ir.fields["CamCameraState"]
check("toggle again: UI shown", ok and not (st & 0x0008) and not p.iracing_ui_hidden, msg)

# already hidden in iRacing (user pressed space there) -> toggle shows it,
# even though our mirror still said "not hidden"
p = poller(0x0008)
p.toggle_ui_hidden()
check("toggle follows iRacing, not the mirror",
      not (p.ir.fields["CamCameraState"] & 0x0008) and not p.iracing_ui_hidden)

# reassert after camera actions: sets the bit only when cleared
p = poller(0x0008)
p.iracing_ui_hidden = True
p.set_play_speed(1)
check("reassert: bit already set -> no write", p.ir.writes == [])
p.ir.fields["CamCameraState"] = 0x0000             # something showed the UI
p.set_play_speed(1)
check("reassert: bit cleared -> set again", p.ir.fields["CamCameraState"] & 0x0008
      and len(p.ir.writes) == 1 and keys == [])
p.iracing_ui_hidden = False
p.ir.fields["CamCameraState"] = 0x0000
p.ir.writes.clear()
p.set_play_speed(1)
check("reassert: operator did not ask -> nothing", p.ir.writes == [])

# state unreadable -> old spacebar path
p = poller(None)
ok, msg = p.toggle_ui_hidden()
check("unreadable state: spacebar fallback", ok and keys == [0x20] and p.iracing_ui_hidden)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
