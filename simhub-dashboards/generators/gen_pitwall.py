import zipfile, json, uuid, shutil
SRC="template.zip"; OUT="/sessions/great-happy-einstein/mnt/SimHub"; OUTPUTS="/sessions/great-happy-einstein/mnt/outputs"; NAME="ProDash Pit Wall"
PP="ProDashPlugin.ProDash."; G="DataCorePlugin.GameData."; T="DataCorePlugin.GameRawData.Telemetry."
z=zipfile.ZipFile(SRC); base=json.loads(z.read('TEST\\TEST.djson').decode()); meta=json.loads(z.read('TEST\\TEST.djson.metadata').decode()); js=z.read('TEST\\JavascriptExtensions\\sample.js')
def BS(r=0,bc="#00000000",bt=0):
    if bt>0:
        return {"BorderTop":bt,"BorderBottom":bt,"BorderLeft":bt,"BorderRight":bt,"RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
    return {"BorderColor":bc,"RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
def bnd(e,t): return {"Formula":{"Interpreter":1,"Expression":e},"Mode":2,"TargetPropertyName":t}
def TX(name,top,left,w,h,static="",size=26,color="#FFFFFFFF",align=0,weight="Bold",te=None,ce=None,ve=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.TextItem, SimHub.Plugins","IsTextItem":True,"Font":"Roboto","FontWeight":weight,"FontSize":float(size),"Text":static,"TextColor":color,"HorizontalAlignment":align,"VerticalAlignment":1,"BackgroundColor":"#00000000","BorderStyle":BS(),"Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Bindings":{}}
    if te: it["Bindings"]["Text"]=bnd(te,"Text")
    if ce: it["Bindings"]["TextColor"]=bnd(ce,"TextColor")
    if ve: it["Bindings"]["Visible"]=bnd(ve,"Visible")
    return it
def RC(name,top,left,w,h,color,r=0,bc="#00000000",bt=0,ce=None,ve=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.RectangleItem, SimHub.Plugins","IsRectangleItem":True,"BackgroundColor":color,"BorderStyle":BS(r,bc,bt),"Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Bindings":{}}
    if ce: it["Bindings"]["BackgroundColor"]=bnd(ce,"BackgroundColor")
    if ve: it["Bindings"]["Visible"]=bnd(ve,"Visible")
    return it
def CHART(name,top,left,w,h,prop,color,vmin,vmax,pts=280,thick=2):
    return {"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.ChartItem, SimHub.Plugins","ChartSuspended":False,"ChartEnabled":True,"CurrentValue":0.0,"Minimum":float(vmin),"UseMinimum":True,"UseMaximum":True,"LineColor":color,"LineTickness":thick,"Maximum":float(vmax),"PointsCount":float(pts),"BackgroundColor":"#00FFFFFF","Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"BlinkPhasisInverted":False,"Width":float(w),"Name":name,"RenderingSkip":0,"MinimumRefreshIntervalMS":0.0,"Bindings":{"CurrentValue":bnd(f"return $prop('{prop}')","CurrentValue")}}
def STATICMAP(name,top,left,w,h):
    return {"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.GeneratedStaticMapItem, SimHub.Plugins","AlternateTrackSectorColor":"#FF3A424C","CursorColor":"#FFFF0000","DisplayScale":1.0,"MapShadow":False,"OverrideColorsWithCarClassColors":False,"DisplayPerClassPosition":False,"KeepMapDefinedClassColorsInSingleClass":False,"DisableAutomaticPlayerClassStyle":True,"MinimumTrackBorderWidth":0.0,"MinimumTrackWidth":6.0,"OpponentStyle":{"LabelFont":"Roboto","LabelFontSize":14.0,"LabelColor":"#FF000000","DotColor":"#FFCFD3D8","DotBorderThickness":2.0,"DotBordercolor":"#FF12171D","DotRadius":14.0},"PlayerStyle":{"LabelFont":"Roboto","LabelFontSize":16.0,"LabelColor":"#FF000000","DotColor":"#FFFFB020","DotBorderThickness":2.0,"DotBordercolor":"#FF000000","DotRadius":18.0},"StartLine":{"Color":"#FFFFFFFF","Enabled":True,"Height":40.0,"Width":6.0},"TrackBorderColor":"#FFFFFFFF","TrackBorderWidth":0.0,"TrackColor":"#FF5A6470","TrackWidth":4.0,"BackgroundColor":"#00FFFFFF","Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"BlinkDelay":249.0,"BlinkPhasisInverted":False,"IsFreezed":False,"Name":name,"RenderingSkip":0,"MinimumRefreshIntervalMS":0.0,"Bindings":{}}
BG="#FF000000"; PANEL="#FF000000"; BORD="#FF232B33"; LBL="#FF6E7681"; AMB="#FFFFB020"; GRN="#FF39D98A"; YEL="#FFF2C94C"; MAG="#FFD44BC8"; RED="#FFFF5D5D"; WHT="#FFFFFFFF"; GRY="#FFCFD3D8"; CYAN="#FF35C4E8"
def irfmt(p): return f"var r=$prop('{p}');return r>0?(r>=1000?(r/1000).toFixed(1)+'k':String(r)):''"
def tcol(p): return f"var t=$prop('{p}');if(t<60)return '#FF5AA9E8';if(t>105)return '#FFFF5D5D';if(t>95)return '#FFF2C94C';return '#FF39D98A'"
I=[RC("bg",0,0,1920,1080,BG)]
# ===== LEADERBOARD (left, narrower) =====
LX=16; LW=760
I.append(RC("lbpanel",8,LX,LW,812,PANEL,10,BORD,1)); I.append(TX("lbtitle",16,LX+14,300,30,"LEADERBOARD",22,AMB,0))
cols=[("POS",20,48,1),("#",70,58,1),("DRIVER",134,300,0),("iR",436,92,2),("BEST",530,130,2),("GAP",666,86,2)]
for lab,cx,cw,al in cols: I.append(TX("h_"+lab,62,LX+cx,cw,24,lab,15,LBL,al))
ROWS=18; y0=96; rh=39
for n in range(1,ROWS+1):
    ry=y0+(n-1)*rh; b=f"{PP}Board.{n}."; valid=f"return $prop('{b}Valid')"
    stripe="#12FFFFFF" if n%2==0 else "#00000000"
    I.append(RC(f"lrbg{n}",ry,LX+8,LW-16,rh-3,"#00000000",5,ve=valid,ce=f"return $prop('{b}IsPlayer')?'#33FFB020':'{stripe}'"))
    pcol=f"return $prop('{b}IsPlayer')?'#FFFFB020':'#FFFFFFFF'"
    exprs={"Pos":f"return $prop('{b}Pos')","CarNumber":f"var c=$prop('{b}CarNumber');return c?('#'+c):''","Name":f"return $prop('{b}Name')","IRating":irfmt(b+'IRating'),"BestLap":f"return $prop('{b}BestLap')","Gap":f"return $prop('{b}Gap')"}
    colcolor={"IRating":GRY,"BestLap":MAG,"Gap":GRN}
    for lab,cx,cw,al in cols:
        f={"POS":"Pos","#":"CarNumber","DRIVER":"Name","iR":"IRating","BEST":"BestLap","GAP":"Gap"}[lab]
        I.append(TX(f"r{n}{f}",ry,LX+cx,cw,rh,size=21,color=colcolor.get(f,"#FFFFFFFF"),align=al,ve=valid,te=exprs[f],ce=(pcol if f in("Pos","Name") else None)))
# ===== CONTROL ROW (TC/TC CUT/ABS/BB/MAP) + SECTORS below the leaderboard =====
cby=828; cbh=84; bw=144; gap=8; cbx0=LX+6
ctrls=[("TC",CYAN,f"return $prop('{G}TCLevel')"),("TC CUT",CYAN,f"var v=$prop('{T}dcTractionControl2');return v==null?'—':v"),("ABS",AMB,f"return $prop('{G}ABSLevel')"),("BB",RED,f"var v=$prop('{G}BrakeBias');return v==null?'—':v.toFixed(1)"),("MAP",GRN,f"var m=$prop('{G}EngineMap');return (m===null||m<0)?'N/A':m")]
for i,(lab,acc,expr) in enumerate(ctrls):
    x=cbx0+i*(bw+gap)
    I.append(RC("cb"+str(i),cby,x,bw,cbh,PANEL,10,acc,1))
    I.append(TX("cbl"+str(i),cby+8,x,bw,20,lab,15,acc,1))
    I.append(TX("cbv"+str(i),cby+28,x,bw,52,"—",40,WHT,1,te=expr))
scy=920; I.append(RC("secp",scy,LX,LW,150,PANEL,10,BORD,1)); I.append(TX("secT",scy+8,LX+14,200,22,"SECTORS",15,LBL,0))
I.append(TX("seccl",scy+34,LX+16,320,54,"-:--.---",44,YEL,0,te=f"return $prop('{PP}Sector.CurrentLap')"))
I.append(TX("seccll",scy+92,LX+16,320,20,"CURRENT LAP",13,LBL,0))
for i in range(1,4):
    sx=LX+360+(i-1)*132
    I.append(TX(f"secS{i}l",scy+38,sx,44,24,f"S{i}",18,LBL,0))
    I.append(TX(f"secS{i}",scy+38,sx+36,96,24,"--.---",20,WHT,0,te=f"return $prop('{PP}Sector.S{i}')"))
    I.append(TX(f"secS{i}d",scy+66,sx+36,96,20,"",15,GRY,0,te=f"return $prop('{PP}Sector.S{i}Delta')",ce=f"var d=$prop('{PP}Sector.S{i}Delta');return d.charAt(0)=='-'?'#FF39D98A':(d.charAt(0)=='+'?'#FFFF5D5D':'#FF6E7681')"))
# ===== MIDDLE COLUMN: tyres + charts =====
MX=790; MW=352
I.append(RC("typ",8,MX,MW,352,PANEL,10,BORD,1)); I.append(TX("tyT",16,MX+14,200,24,"TYRES",16,LBL,0))
cxL=MX+18; cxR=MX+MW-138; barL=MX+MW//2-24; barR=MX+MW//2+12
def tyre(cn,side,top):
    bx=barL if side=='L' else barR
    I.append(RC("bar"+cn,top,bx,12,112,"#FF333A42",4,ce=tcol("TyreTemperature"+cn)))
    al=0 if side=='L' else 2; cx=cxL if side=='L' else cxR
    I.append(TX("tp"+cn,top,cx,120,44,"—",34,WHT,al,te=f"return $prop('TyrePressure{cn}').toFixed(1)"))
    I.append(TX("ptl"+cn,top+42,cx,120,18,"PSI",13,LBL,al))
    I.append(TX("tt"+cn,top+64,cx,120,26,"—",22,GRY,al,te=f"return Math.round($prop('TyreTemperature{cn}'))+'°'",ce=tcol("TyreTemperature"+cn)))
    I.append(TX("tw"+cn,top+94,cx,120,20,"—",16,LBL,al,te=f"return Math.round($prop('TyreWear{cn}'))+'%'"))
tyre("FrontLeft","L",62); tyre("FrontRight","R",62); tyre("RearLeft","L",208); tyre("RearRight","R",208)
I.append(RC("cmpp",176,MX+MW//2-46,92,34,"#FF2A323C",16)); I.append(TX("cmp",182,MX+MW//2-46,92,24,"DRY",18,GRY,1))
def chartpanel(name,top,label,prop,color,vmin,vmax,extra=None):
    h=196; I.append(RC(name+"p",top,MX,MW,h,PANEL,10,BORD,1)); I.append(TX(name+"L",top+8,MX+14,200,22,label,15,LBL,0))
    I.append(TX(name+"V",top+8,MX+MW-120,110,26,"",22,color,2,te=f"return Math.round($prop('{prop}'))"))
    I.append(CHART(name+"C",top+40,MX+12,MW-24,h-52,prop,color,vmin,vmax))
    if extra: 
        pr,cl=extra; I.append(CHART(name+"C2",top+40,MX+12,MW-24,h-52,pr,cl,0,100))
chartpanel("spd",368,"SPEED km/h",f"{G}SpeedKmh",CYAN,0,300)
chartpanel("rpm",572,"RPM",f"{G}NewData.FilteredRpms",AMB,0,9000)
I.append(RC("tbp",776,MX,MW,196,PANEL,10,BORD,1)); I.append(TX("tbL",784,MX+14,200,22,"THROTTLE / BRAKE",15,LBL,0))
I.append(CHART("thC",808,MX+12,MW-24,148,f"{G}Throttle",GRN,0,100))
I.append(CHART("brC",808,MX+12,MW-24,148,f"{G}Brake",RED,0,100))
# ===== RIGHT COLUMN =====
RX=1150; RW=754
I.append(RC("sofp",8,RX,RW,84,PANEL,10,BORD,1)); I.append(TX("sofL",18,RX+18,120,32,"SoF",24,LBL,0)); I.append(TX("sofV",14,RX+120,180,54,"0",50,WHT,0,te=f"return $prop('{PP}Field.SoF')"))
I.append(TX("carsV",22,RX+330,120,40,"0",34,WHT,1,te=f"return $prop('{PP}Field.CarCount')")); I.append(TX("carsL",58,RX+330,120,20,"CARS",15,LBL,1))
I.append(TX("avgV",22,RX+480,150,40,"0",34,WHT,1,te=irfmt(PP+'Field.AvgIRating'))); I.append(TX("avgL",58,RX+480,150,20,"AVG iR",15,LBL,1))
I.append(TX("flag",20,RX+RW-50,34,44,"■",30,GRN,1,ce=f"return $prop('Flag_Yellow')?'#FFF2C94C':'#FF39D98A'"))
# STATUS (engine vitals) — replaces the relative view
sty0=98; I.append(RC("stp",sty0,RX,RW,278,PANEL,10,BORD,1)); I.append(TX("stT",sty0+8,RX+16,300,24,"STATUS",16,LBL,0))
def vital(nm,r,c,label,prop,expr):
    cw=(RW-24)//3; cx=RX+12+c*cw; cy=sty0+46+r*116
    I.append(TX("v"+nm,cy,cx,cw,52,"—",38,WHT,1,te=f"var v=$prop('{prop}');return (v===null)?'—':({expr})"))
    I.append(TX("vl"+nm,cy+56,cx,cw,20,label,14,LBL,1))
vital("oilt",0,0,"OIL TEMP",f"{G}OilTemperature","Math.round(v)+'°'")
vital("oilp",0,1,"OIL PRESS",f"{T}OilPress","v.toFixed(1)")
vital("oill",0,2,"OIL LEVEL",f"{T}OilLevel","v.toFixed(1)")
vital("fp",1,0,"FUEL PRESS",f"{T}FuelPress","v.toFixed(1)")
vital("wt",1,1,"WAT TEMP",f"{G}WaterTemperature","Math.round(v)+'°'")
vital("bat",1,2,"BATT",f"{T}Voltage","v.toFixed(1)")
fy=384; I.append(RC("fuelp",fy,RX,RW,132,PANEL,10,AMB,1)); I.append(TX("fuelT",fy+8,RX+16,200,24,"FUEL",16,AMB,0))
I.append(TX("fL",fy+44,RX+30,180,60,"—",48,WHT,0,te="var v=$prop('DataCorePlugin.GameData.Fuel');return v==null?'—':v.toFixed(1)")); I.append(TX("fLu",fy+104,RX+30,180,22,"LITRES",15,LBL,0))
_fc=[("PER LAP","var v=$prop('DataCorePlugin.Computed.Fuel_LitersPerLap');return v==null?'—':v.toFixed(2)",250),("LAPS LEFT","var v=$prop('DataCorePlugin.Computed.Fuel_RemainingLaps');return v>0?v.toFixed(1):'—'",430),("TO ADD",f"var v=$prop('{PP}Fuel.ToAdd');return v>0?v.toFixed(1):'—'",600)]
for lab,te,dx in _fc:
    I.append(TX("f_"+lab,fy+48,RX+dx,150,46,"—",34,WHT,1,te=te)); I.append(TX("fu_"+lab,fy+100,RX+dx,150,22,lab,14,LBL,1))
I.append(TX("fstat",fy+8,RX+RW-160,150,30,"—",22,GRN,2,te=f"return $prop('{PP}Fuel.Status')",ce=f"var s=$prop('{PP}Fuel.Status');return s=='OK'?'#FF39D98A':(s=='PIT'?'#FFFF5D5D':'#FFF2C94C')"))
sy=524; I.append(RC("sesp",sy,RX,RW,58,PANEL,10,BORD,1))
I.append(TX("spos",sy+12,RX+20,140,40,"P—",32,AMB,0,te=f"var p=$prop('{G}Position');return p>0?('P'+p):'P—'"))
I.append(TX("slap",sy+16,RX+170,220,32,"LAP —/—",24,WHT,0,te=f"return 'LAP '+$prop('{G}CurrentLap')+'/'+$prop('{G}TotalLaps')"))
I.append(TX("sclk",sy+12,RX+420,180,36,"--:--",30,WHT,1,te=f"var s=$prop('{T}SessionTimeRemain');if(s<=0||s>360000)return '--:--';var m=Math.floor(s/60),ss=Math.floor(s%60),h=Math.floor(m/60);m=m%60;return (h>0?h+':':'')+((m<10&&h>0)?'0':'')+m+':'+(ss<10?'0':'')+ss"))
I.append(TX("strk",sy+16,RX+610,130,32,"TRK —",22,GRY,2,te=f"return 'TRK '+Math.round($prop('{G}RoadTemperature'))+'°'"))
my=590; mh=482; I.append(RC("mapp",my,RX,RW,mh,PANEL,10,BORD,1)); I.append(TX("mapT",my+8,RX+16,200,24,"TRACK",16,LBL,0))
I.append(STATICMAP("trackmap",629,1161,731,429))  # user-tuned size
d=json.loads(json.dumps(base)); d["Id"]=str(uuid.uuid4()); d["BaseWidth"]=1920; d["BaseHeight"]=1080; d["BackgroundColor"]=BG; d["Screens"][0]["ScreenId"]=str(uuid.uuid4()); d["Screens"][0]["Items"]=I
m=json.loads(json.dumps(meta)); m["Title"]=NAME; m["Width"]=1920.0; m["Height"]=1080.0
out=f"{NAME}.simhubdash"
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as zo:
    zo.writestr(f"{NAME}\\{NAME}.djson",json.dumps(d)); zo.writestr(f"{NAME}\\{NAME}.djson.metadata",json.dumps(m,indent=2)); zo.writestr(f"{NAME}\\JavascriptExtensions\\sample.js",js)
shutil.copy(out,f"{OUT}/{NAME}.simhubdash"); shutil.copy(out,f"{OUTPUTS}/{NAME}.simhubdash")
print("items:",len(I),"-> pit wall v2 (tyres + speed/rpm/throttle-brake charts)")
