"""Connection-blink scenarios for the dashboard incident detection."""
import sys, types

irsdk_stub = types.ModuleType("irsdk")
class _FakeIRSDK:
    def __init__(self): self.fields = {}
    def freeze_var_buffer_latest(self): pass
    def __getitem__(self, k): return self.fields.get(k)
    def startup(self): return True
    def shutdown(self): pass
    is_initialized = True
    is_connected = True
irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub
flask_stub = types.ModuleType("flask")
class _App:
    def __init__(self,*a,**k): pass
    def after_request(self,f): return f
    def route(self,*a,**k): return lambda f: f
    def run(self,*a,**k): pass
flask_stub.Flask=_App; flask_stub.Response=object
flask_stub.render_template_string=lambda s,**k:s
flask_stub.jsonify=lambda *a,**k:None
flask_stub.request=types.SimpleNamespace(json=None,args={})
sys.modules["flask"]=flask_stub
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import iracing_dashboard as dash

L, DT = 4000.0, 0.1
def base_fields(t, pcts, surfs, pits):
    n=len(pcts)
    return {"SessionTime":t,"SessionNum":4,"SessionState":4,
            "WeekendInfo":{"TrackLength":"4.00 km"},
            "DriverInfo":{"Drivers":[{"CarIdx":i,"CarNumber":str(10+i),
              "UserName":f"D{i}","CurDriverIncidentCount":-1} for i in range(n)]},
            "CarIdxTrackSurface":list(surfs),"CarIdxSessionFlags":[0]*n,
            "CarIdxLapDistPct":[p%1.0 for p in pcts],
            "CarIdxClassPosition":list(range(1,n+1)),
            "CarIdxOnPitRoad":list(pits),"CarIdxLap":[int(p)+1 for p in pcts]}

class Sim:
    def __init__(self,p,v_fn,surf_fn=None):
        self.p,self.v_fn=p,v_fn
        self.surf_fn=surf_fn or (lambda t,i:3)
        self.pcts=[0.10,0.40,0.70,0.90]; self.t=0.0
    def run(self,seconds):
        for _ in range(int(seconds/DT)):
            self.t+=DT
            for i in range(4):
                self.pcts[i]+=self.v_fn(self.t,i)*DT/L
            self.p.ir.fields=base_fields(self.t,self.pcts,
                [self.surf_fn(self.t,i) for i in range(4)],[False]*4)
            self.p._update_incidents()

failures=[]
def check(name,cond,detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond: failures.append(name)
def incidents(p): return [(i["car_idx"],i["type"],i["details"]) for i in p._incidents]

# ── S10: flaky connection — freeze→blink-out→return, 3 cycles → NOTHING ─────
p=dash.TelemetryPoller(poll_hz=10)
def v10(t,i):
    if i!=1: return 50.0
    # freeze windows before each blink-out
    for f0 in (4.0,10.0,16.0):
        if f0<=t<f0+1.0: return 0.0          # telemetry frozen
        if f0+1.0<=t<f0+4.0: return 0.0      # out of world (pct frozen too)
    return 45.0
def s10(t,i):
    if i!=1: return 3
    for f0 in (4.0,10.0,16.0):
        if f0+1.0<=t<f0+4.0: return -1       # blinked out
    return 3
Sim(p,v10,s10).run(26.0)
check("S10 flaky car: zero incidents", len(incidents(p))==0, str(incidents(p)))

# ── S11: real crash (decel, stop, towed 8 s later, stays gone) → 1 report ───
p=dash.TelemetryPoller(poll_hz=10)
def v11(t,i):
    if i!=1: return 50.0
    if t<4.0: return 40.0
    if t<5.2: return max(2.0,40.0-(t-4.0)*32.0)
    if t<6.0: return 2.0
    return 0.0
def s11(t,i):
    if i!=1: return 3
    return -1 if t>=12.0 else 3
Sim(p,v11,s11).run(25.0)
inc=incidents(p)
check("S11 crash+tow: exactly one report",
      len(inc)==1 and inc[0][0]==1 and inc[0][1]=="lost_control", str(inc))

# ── S12: instant freeze then PERMANENT disconnect → 1 report after confirm ──
p=dash.TelemetryPoller(poll_hz=10)
def v12(t,i):
    if i!=1: return 50.0
    return 45.0 if t<4.0 else 0.0            # frozen from t=4
def s12(t,i):
    if i!=1: return 3
    return -1 if t>=6.0 else 3               # gone for good at t=6
Sim(p,v12,s12).run(20.0)
inc=incidents(p)
check("S12 permanent vanish: one collision, after confirm window",
      len(inc)==1 and inc[0][1]=="collision" and "vanished" in inc[0][2],
      str(inc))

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
