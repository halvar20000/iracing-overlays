import zipfile, json, uuid, shutil, math
SRC="template.zip"; OUT="/sessions/great-happy-einstein/mnt/SimHub"; OUTPUTS="/sessions/great-happy-einstein/mnt/outputs"; NAME="ProDash Round"
G="DataCorePlugin.GameData."; T="DataCorePlugin.GameRawData.Telemetry."; P="ProDashPlugin.ProDash."
z=zipfile.ZipFile(SRC); base=json.loads(z.read('TEST\\TEST.djson').decode()); meta=json.loads(z.read('TEST\\TEST.djson.metadata').decode()); js=z.read('TEST\\JavascriptExtensions\\sample.js')
def BS(r=0,bt=0,bc=None):
    if bt>0:
        st={"BorderTop":bt,"BorderBottom":bt,"BorderLeft":bt,"BorderRight":bt,"RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
        if bc: st["BorderColor"]=bc
        return st
    return {"BorderColor":"#00000000","RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
def bnd(e,t): return {"Formula":{"JSExt":1,"Interpreter":1,"Expression":e},"Mode":2,"TargetPropertyName":t}
def TX(name,top,left,w,h,static="",size=30,color="#FFFFFFFF",align=1,valign=1,rot=0,te=None,ce=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.TextItem, SimHub.Plugins","IsTextItem":True,"Font":"Roboto","FontWeight":"Bold","FontSize":float(size),"Text":static,"TextColor":color,"HorizontalAlignment":align,"VerticalAlignment":valign,"BackgroundColor":"#00000000","BorderStyle":BS(),"Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Rotation":float(rot),"Bindings":{}}
    if te: it["Bindings"]["Text"]=bnd(te,"Text")
    if ce: it["Bindings"]["TextColor"]=bnd(ce,"TextColor")
    return it
def RC(name,top,left,w,h,color,r=0,bt=0,rot=0,bc=None,ce=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.RectangleItem, SimHub.Plugins","IsRectangleItem":True,"BackgroundColor":color,"BorderStyle":BS(r,bt,bc),"Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Rotation":float(rot),"Bindings":{}}
    if ce: it["Bindings"]["BackgroundColor"]=bnd(ce,"BackgroundColor")
    return it
def ELL(name,top,left,w,h,fill,ecolor="#00000000",ethick=0,ce=None,ee=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.EllipseItem, SimHub.Plugins","IsEllipseItem":True,"FillColor":fill,"EllipseColor":ecolor,"EllipseThickness":float(ethick),"BackgroundColor":"#00FFFFFF","Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Bindings":{}}
    if ce: it["Bindings"]["FillColor"]=bnd(ce,"FillColor")
    if ee: it["Bindings"]["EllipseColor"]=bnd(ee,"EllipseColor")
    return it
def CGA(name,top,left,sz,prop,vmin,vmax,minang,maxang,fill,track,thick=30,ce=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.CircularGaugeItem, SimHub.Plugins","StrokeThickness":float(thick),"Value":0.0,"Steps":360.0,"ValueEx":0.0,"MinValue":float(vmin),"MinAngle":float(minang),"MaxValue":float(vmax),"MaxAngle":float(maxang),"CircleGaugeBackgroundColor":track,"CircleGaugeColor":fill,"BackgroundColor":"#00FFFFFF","Height":float(sz),"Left":float(left),"Top":float(top),"Visible":True,"BlinkPhasisInverted":False,"Width":float(sz),"Name":name,"RenderingSkip":0,"MinimumRefreshIntervalMS":0.0,"Bindings":{"Value":bnd(f"return $prop('{prop}')","Value")}}
    if ce: it["Bindings"]["CircleGaugeColor"]=bnd(ce,"CircleGaugeColor")
    return it
BLK="#FF000000"; FACE="#FF0D0D0D"; LBL="#FF808080"; AMB="#FFFFB020"; GRN="#FF39D98A"; YEL="#FFF2C94C"; RED="#FFFF5D5D"; WHT="#FFFFFFFF"; CYAN="#FF35C4E8"
I=[RC("bg",0,0,800,800,BLK)]
# --- rim: RPM sweep ring (open at bottom for the control fan) ---
I.append(CGA("rpm",30,30,740,f"{G}NewData.FilteredRpms",0,9000,210,510,GRN,"#FF161616",24,
    ce=f"var r=$prop('{G}NewData.FilteredRpms');return r>8000?'#FFFF3030':(r>6500?'#FFF2C94C':'#FF39D98A')"))
# --- throttle (outer) + brake (inner) fill arcs — grow as you press the pedal ---
I.append(CGA("throttle",64,64,672,f"{G}Throttle",0,100,210,510,GRN,"#2639D98A",13))
I.append(CGA("brake",88,88,624,f"{G}Brake",0,100,210,510,RED,"#26FF5D5D",13))
I.append(ELL("rimline",8,8,784,784,"#00FFFFFF","#33FFFFFF",2))
# --- inner face disc (dark; flashes deep red on redline) ---
I.append(ELL("face",108,108,584,584,FACE,"#FFFFFFFF",4,
    ce=f"var x=$prop('{G}CarSettings_RPMRedLineReached');return (x&&x>0)?'#FF3A0000':'#FF0D0D0D'",
    ee=f"var x=$prop('{G}CarSettings_RPMRedLineReached');return (x&&x>0)?'#FFFA0000':'#FFFFFFFF'"))
# --- centre: gear, speed, unit, rpm digits ---
I.append(TX("gear",250,200,400,300,"N",230,WHT,1,1,te=f"var g=$prop('{G}Gear');return (g===''||g===null||g==='0')?'N':g"))
I.append(TX("spd",488,298,200,60,"0",64,WHT,1,1,te=f"return Math.round($prop('{G}SpeedKmh'))"))
I.append(TX("spdu",552,370,60,18,"KMH",16,LBL,1,1))
I.append(TX("rpmd",590,300,200,34,"0",30,LBL,1,1,te=f"return Math.round($prop('{G}NewData.FilteredRpms'))"))
# --- position / field count (left mid, away from the fuel stack) ---
I.append(TX("pos",356,40,150,42,"P -/-",34,WHT,1,1,te=f"var p=$prop('{G}Position');var n=$prop('{P}Field.CarCount');return 'P '+((p&&p>0)?p:'-')+'/'+((n&&n>0)?n:'-')"))
# --- delta plate + segmented tab fan at top (LSR silhouette) ---
tabs=[(303,12,40,98,-14),(335,7,40,98,-5),(365,5,70,98,0),(426,7,40,98,5),(457,12,40,98,14)]
for i,(L,Tp,W,H,rt) in enumerate(tabs):
    I.append(RC(f"tab{i}",Tp,L,W,H,"#FF141414",6,1,rot=rt))
I.append(RC("deltabg",25,310,180,86,YEL,18,0,
    ce=f"var d=$prop('{T}LapDeltaToSessionBestLap');if(d===null||isNaN(d))return '{YEL}';return d<-0.001?'{GRN}':(d>0.001?'{RED}':'{YEL}')"))
I.append(TX("delta",30,320,160,50,"0.000",44,BLK,1,1,te=f"var d=$prop('{T}LapDeltaToSessionBestLap');if(d===null||isNaN(d))return '0.000';return (d>0?'+':'')+d.toFixed(3)"))
# --- fuel readouts (right side, rotated to follow the circle) ---
I.append(TX("fuel",263,683,110,60,"0.0",46,WHT,1,1,rot=-15,te=f"var v=$prop('{G}Fuel');return v==null?'0.0':v.toFixed(1)"))
I.append(TX("fuelL",312,717,60,17,"LITRES",16,LBL,1,1,rot=-14))
I.append(RC("favgbg",356,694,108,84,FACE,16,1,bc="#33FFFFFF"))
I.append(TX("favg",356,694,108,84,"0.00",44,WHT,1,1,te=f"var v=$prop('{G}Fuel_LitersPerLap');if(v==null)v=$prop('DataCorePlugin.Computed.Fuel_LitersPerLap');return v==null?'0.00':v.toFixed(2)"))
I.append(TX("favgL",416,715,60,17,"AVG",16,LBL,1,1))
I.append(TX("laps",465,686,110,60,"0",46,WHT,1,1,rot=16,te=f"var l=$prop('DataCorePlugin.Computed.Fuel_RemainingLaps');return (l&&l>0)?Math.floor(l):'0'"))
I.append(TX("lapsL",521,702,60,17,"LAPS",16,LBL,1,1,rot=18))
# --- control-box fan along the bottom: rounded pills drawn INSIDE each 155x88 slot ---
OX,OY=55,558
mods=[
 ("TC", CYAN, f"var v=$prop('{G}TCLevel');return v==null?'-':v",                          0,   27, 50),
 ("TC2",CYAN, f"var v=$prop('{T}dcTractionControl2');return v==null?'-':v",              120,  120, 25),
 ("MAP",GRN,  f"var m=$prop('{G}EngineMap');return (m===null||m<0)?'-':m",              267.5,153,  0),
 ("ABS",YEL,  f"var v=$prop('{G}ABSLevel');return v==null?'-':v",                        415, 120,-25),
 ("BB", RED,  f"var v=$prop('{G}BrakeBias');return v==null?'-':(''+v.toFixed(1))",        535,  27,-50),
]
BW,BH,BR=128,74,30   # visible pill size + radius (rounded, not rectangular)
for lab,acc,expr,L,Tp,rt in mods:
    cx=OX+L+77.5; cy=OY+Tp+44          # slot centre
    x=cx-BW/2; y=cy-BH/2
    I.append(RC("box"+lab,y,x,BW,BH,FACE,BR,3,rot=rt,bc=acc))
    I.append(TX("lab"+lab,y+8,x,BW,20,lab,15,acc,1,1,rot=rt))          # label top
    I.append(TX("val"+lab,y+24,x,BW,44,"-",40,WHT,1,1,rot=rt,te=expr)) # value below
d=json.loads(json.dumps(base)); d["Id"]=str(uuid.uuid4()); d["BaseWidth"]=800; d["BaseHeight"]=800; d["BackgroundColor"]=BLK; d["Screens"][0]["ScreenId"]=str(uuid.uuid4()); d["Screens"][0]["Items"]=I
m=json.loads(json.dumps(meta)); m["Title"]=NAME; m["Width"]=800.0; m["Height"]=800.0
out=f"{NAME}.simhubdash"
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as zo:
    zo.writestr(f"{NAME}\\{NAME}.djson",json.dumps(d)); zo.writestr(f"{NAME}\\{NAME}.djson.metadata",json.dumps(m,indent=2)); zo.writestr(f"{NAME}\\JavascriptExtensions\\sample.js",js)
shutil.copy(out,f"{OUT}/{NAME}.simhubdash"); shutil.copy(out,f"{OUTPUTS}/{NAME}.simhubdash")
print("items:",len(I),"-> round v9 (baked user tweak: speed+KMH nudged down)")
