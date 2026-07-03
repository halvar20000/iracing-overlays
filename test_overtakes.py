"""Offline scenario tests for the dashboard overtake detection."""
import sys, types

irsdk_stub = types.ModuleType("irsdk")
class _FakeIRSDK:
    def __init__(self): self.fields = {}
    def freeze_var_buffer_latest(self): pass
    def __getitem__(self, k): return self.fields.get(k)
irsdk_stub.IRSDK = _FakeIRSDK
sys.modules["irsdk"] = irsdk_stub
flask_stub = types.ModuleType("flask")
class _App:
    def __init__(self,*a,**k): pass
    def after_request(self,f): return f
    def route(self,*a,**k): return lambda f: f
flask_stub.Flask=_App; flask_stub.Response=object
flask_stub.render_template_string=lambda s,**k:s
flask_stub.jsonify=lambda *a,**k:None
flask_stub.request=types.SimpleNamespace(json=None,args={})
sys.modules["flask"]=flask_stub
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import iracing_dashboard as dash

L, DT = 4000.0, 0.1
def base_fields(t, pcts, surfs, pits, flags):
    n=len(pcts)
    return {"SessionTime":t,"SessionNum":4,"SessionState":4,
            "WeekendInfo":{"TrackLength":"4.00 km"},
            "DriverInfo":{"Drivers":[{"CarIdx":i,"CarNumber":str(10+i),
              "UserName":f"D{i}","CurDriverIncidentCount":-1} for i in range(n)]},
            "CarIdxTrackSurface":list(surfs),"CarIdxSessionFlags":list(flags),
            "CarIdxLapDistPct":[p%1.0 for p in pcts],
            "CarIdxClassPosition":list(range(1,n+1)),
            "CarIdxOnPitRoad":list(pits),"CarIdxLap":[int(p)+1 for p in pcts],
            "SessionInfo":{"Sessions":[{"SessionNum":4,"SessionType":"Race",
                                        "SessionLaps":"unlimited"}]}}

class Sim:
    def __init__(self,p,v_fn,pit_fn=None,flag_fn=None,start=None):
        self.p,self.v_fn=p,v_fn
        self.pit_fn=pit_fn or (lambda t,i:False)
        self.flag_fn=flag_fn or (lambda t,i:0)
        self.pcts=list(start or [2.502,2.500,2.30,2.10])  # car0 just ahead of car1
        self.t=0.0
    def run(self,seconds):
        for _ in range(int(seconds/DT)):
            self.t+=DT
            for i in range(len(self.pcts)):
                self.pcts[i]+=self.v_fn(self.t,i)*DT/L
            self.p.ir.fields=base_fields(self.t,self.pcts,[3]*4,
                [self.pit_fn(self.t,i) for i in range(4)],
                [self.flag_fn(self.t,i) for i in range(4)])
            self.p._update_incidents()

failures=[]
def check(name,cond,detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond: failures.append(name)
def overtakes(p): return [(o["car_idx"],o["details"]) for o in p._overtakes]

# O1: clean pass that sticks -> exactly one entry, P1
p=dash.TelemetryPoller(poll_hz=10)
def v1(t,i):
    if i==1 and t>=4.0: return 51.0
    return 45.0
Sim(p,v1).run(16.0)
ot=overtakes(p)
check("O1 one overtake", len(ot)==1, str(ot))
check("O1 right car & position", len(ot)==1 and ot[0][0]==1 and "for P1" in ot[0][1], str(ot))

# O2: side-by-side flicker (re-passed within confirm window) -> nothing
p=dash.TelemetryPoller(poll_hz=10)
def v2(t,i):
    if i==1:
        if 4.0<=t<5.8: return 51.0    # noses ahead briefly
        if 5.8<=t<8.0: return 39.0    # drops back behind again
    return 45.0
Sim(p,v2).run(14.0)
check("O2 flicker: no overtake", len(overtakes(p))==0, str(overtakes(p)))

# O3: position gained while other car is on pit road -> nothing
p=dash.TelemetryPoller(poll_hz=10)
Sim(p,v1,pit_fn=lambda t,i: (i==0)).run(16.0)
check("O3 pit cycle: no overtake", len(overtakes(p))==0, str(overtakes(p)))

# O4: overtaken car has the blue flag -> nothing
p=dash.TelemetryPoller(poll_hz=10)
Sim(p,v1,flag_fn=lambda t,i: 0x0020 if i==0 else 0).run(16.0)
check("O4 blue flag: no overtake", len(overtakes(p))==0, str(overtakes(p)))

# O5: passing a crawling (spun) car -> no overtake (incident's job)
p=dash.TelemetryPoller(poll_hz=10)
def v5(t,i):
    if i==0: return 45.0 if t<3.0 else 3.0     # spins, crawls
    return 45.0
Sim(p,v5).run(16.0)
check("O5 crawling car: no overtake", len(overtakes(p))==0, str(overtakes(p)))

print()
print("ALL PASS" if not failures else f"FAILURES: {failures}")
sys.exit(1 if failures else 0)
