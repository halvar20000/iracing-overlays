import zipfile, json, uuid, shutil
SRC="template.zip"; OUTDIR="/sessions/great-happy-einstein/mnt/SimHub"; OUTPUTS="/sessions/great-happy-einstein/mnt/outputs"; NAME="ProDash DDU"
PP="ProDashPlugin.ProDash."; G="DataCorePlugin.GameData."; T="DataCorePlugin.GameRawData.Telemetry."
z=zipfile.ZipFile(SRC); base=json.loads(z.read('TEST\\TEST.djson').decode()); meta=json.loads(z.read('TEST\\TEST.djson.metadata').decode()); js=z.read('TEST\\JavascriptExtensions\\sample.js')
def bs(r=0,bc="#00000000",bt=0):
    if bt>0:
        return {"BorderTop":bt,"BorderBottom":bt,"BorderLeft":bt,"BorderRight":bt,"RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
    return {"BorderColor":bc,"RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
def bind(e,t):return {"Formula":{"Interpreter":1,"Expression":e},"Mode":2,"TargetPropertyName":t}
def text(name,top,left,w,h,static="",size=30,color="#FFFFFFFF",align=1,weight="Bold",te=None,ce=None,ve=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.TextItem, SimHub.Plugins","IsTextItem":True,"Font":"Roboto","FontWeight":weight,"FontSize":float(size),"Text":static,"TextColor":color,"HorizontalAlignment":align,"VerticalAlignment":1,"BackgroundColor":"#00000000","BorderStyle":bs(),"Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Bindings":{}}
    if te: it["Bindings"]["Text"]=bind(te,"Text")
    if ce: it["Bindings"]["TextColor"]=bind(ce,"TextColor")
    if ve: it["Bindings"]["Visible"]=bind(ve,"Visible")
    return it
def rect(name,top,left,w,h,color,r=0,bc="#00000000",bt=0,ce=None,ve=None):
    it={"$type":"SimHub.Plugins.OutputPlugins.GraphicalDash.Models.RectangleItem, SimHub.Plugins","IsRectangleItem":True,"BackgroundColor":color,"BorderStyle":bs(r,bc,bt),"Height":float(h),"Left":float(left),"Top":float(top),"Visible":True,"Width":float(w),"Name":name,"Bindings":{}}
    if ce: it["Bindings"]["BackgroundColor"]=bind(ce,"BackgroundColor")
    if ve: it["Bindings"]["Visible"]=bind(ve,"Visible")
    return it
PANEL="#FF000000"; BORDER="#FF232B33"; LBL="#FF6E7681"; AMBER="#FFFFB020"; GREEN="#FF39D98A"; YEL="#FFF2C94C"; MAG="#FFD44BC8"; RED="#FFFF5D5D"; CYAN="#FF35C4E8"; WHITE="#FFFFFFFF"; GRY="#FFCFD3D8"
TFMT=lambda x:("var s=$prop('%s');if(s===null||isNaN(s)||s<=0)return '-:--.---';var m=Math.floor(s/60);var r=s-m*60;return m+':'+(r<10?'0':'')+r.toFixed(3)"%x)
tempcol=lambda x:("var t=$prop('%s');if(t<60)return '#FF5AA9E8';if(t>105)return '#FFFF5D5D';if(t>95)return '#FFF2C94C';return '#FF39D98A'"%x)
I=[rect("bg",0,0,800,480,"#FF000000")]
# top strip
I+=[rect("topbar",8,8,784,44,PANEL,8,BORDER,1),
 text("pos",14,20,150,32,"P—",26,AMBER,0,te=f"var p=$prop('{G}Position');return p>0?('P'+p):'P—'"),
 text("lap",14,150,220,32,"LAP —/—",24,WHITE,0,te=f"return 'LAP '+$prop('{G}CurrentLap')+'/'+$prop('{G}TotalLaps')"),
 text("clk",12,300,200,36,"--:--",30,WHITE,1,te=f"var s=$prop('{T}SessionTimeRemain');if(s<=0||s>360000)return '--:--';var m=Math.floor(s/60),ss=Math.floor(s%60),h=Math.floor(m/60);m=m%60;return (h>0?h+':':'')+((m<10&&h>0)?'0':'')+m+':'+(ss<10?'0':'')+ss"),
 text("trk",14,540,110,32,"TRK —",22,GRY,0,te=f"return 'TRK '+Math.round($prop('{G}RoadTemperature'))+'°'"),
 text("air",14,650,110,32,"AIR —",22,GRY,0,te=f"return 'AIR '+Math.round($prop('{G}AirTemperature'))+'°'"),
 text("flag",12,760,26,36,"■",30,GREEN,1,ce=f"return $prop('Flag_Yellow')?'#FFF2C94C':'#FF39D98A'")]
# centre
I+=[rect("deltabg",65,291,169,43,PANEL,8,BORDER,1),
 text("delta",68,298,153,35,"+0.00",34,GREEN,1,te=f"var d=$prop('{T}LapDeltaToSessionBestLap');if(d===null||isNaN(d))return '0.00';return (d>0?'+':'')+d.toFixed(2)",ce=f"var d=$prop('{T}LapDeltaToSessionBestLap');return d<0?'#FF39D98A':(d>0?'#FFFF5D5D':'#FFCCCCCC')"),
 text("gear",114,293,164,189,"N",150,WHITE,1,te=f"var g=$prop('{G}Gear');return (g===null||g==='')?'N':g"),
 text("spd",285,284,181,54,"0",48,WHITE,1,te=f"return Math.round($prop('{G}SpeedKmh'))"),
 text("spdu",335,293,159,26,"km/h",20,LBL,1)]
# LEFT: tyres (new LSR-aligned design)
mx=12; mw=268
I.append(rect("tyrepanel",60,mx,mw,300,PANEL,10,BORDER,1)); I.append(text("tyrelbl",68,mx,mw,22,"TYRES",16,LBL,1))
cxL=mx+8; cxR=mx+mw-92; barL=mx+mw//2-18; barR=mx+mw//2+6
def dtyre(cn,side,top):
    bx=barL if side=='L' else barR
    I.append(rect("dbar"+cn,top,bx,10,80,"#FF333A42",4,ce=tempcol("TyreTemperature"+cn)))
    al=0 if side=='L' else 2; cx=cxL if side=='L' else cxR
    I.append(text("dtp"+cn,top,cx,84,36,"—",26,WHITE,al,te=f"return $prop('TyrePressure{cn}').toFixed(1)"))
    I.append(text("dpl"+cn,top+32,cx,84,16,"PSI",11,LBL,al))
    I.append(text("dtt"+cn,top+50,cx,84,22,"—",18,GRY,al,te=f"return Math.round($prop('TyreTemperature{cn}'))+'°'",ce=tempcol("TyreTemperature"+cn)))
    I.append(text("dtw"+cn,top+74,cx,84,18,"—",13,LBL,al,te=f"return Math.round($prop('TyreWear{cn}'))+'%'"))
dtyre("FrontLeft","L",98); dtyre("FrontRight","R",98); dtyre("RearLeft","L",214); dtyre("RearRight","R",214)
I.append(rect("dcmpp",178,mx+mw//2-38,76,30,"#FF2A323C",14)); I.append(text("dcmp",183,mx+mw//2-38,76,20,"DRY",15,GRY,1))
# RIGHT: lap times
I.append(rect("ltpanel",60,472,316,300,PANEL,10,BORDER,1))
def laprow(name,label,top,prop,color): return [text(name+"l",top,486,290,24,label,18,LBL,0), text(name+"v",top+22,486,290,52,"-:--.---",46,color,0,te=TFMT(prop))]
I+=laprow("pred","PREDICTED",70,"PersistantTrackerPlugin.EstimatedLapTime",WHITE)
I+=laprow("last","LAST LAP",166,f"{T}LapLastLapTime",YEL)
I+=laprow("best","BEST LAP",262,f"{T}LapBestLapTime",MAG)
# bottom bar
by=372; bh=96
def ctrlbox(name,x,w,label,accent,ve): return [rect(name+"bg",by,x,w,bh,PANEL,10,accent,1), text(name+"l",by+8,x,w,22,label,17,accent,1), text(name+"v",by+30,x,w,60,"—",46,WHITE,1,te=ve)]
I+=ctrlbox("tc",12,120,"TC",CYAN,f"return $prop('{G}TCLevel')")
I+=ctrlbox("abs",140,120,"ABS",AMBER,f"return $prop('{G}ABSLevel')")
I+=ctrlbox("bb",268,120,"BB",RED,f"return $prop('{G}BrakeBias').toFixed(1)")
I+=ctrlbox("map",396,120,"MAP",GREEN,f"var m=$prop('{G}EngineMap');return (m===null||m<0)?'—':m")
I.append(rect("fuelbg",by,524,264,bh,PANEL,10,AMBER,1)); I.append(text("fuell",by+6,524,264,22,"FUEL",17,AMBER,1))
I.append(text("fL",by+30,530,120,50,"—",40,WHITE,0,te="var v=$prop('DataCorePlugin.GameData.Fuel');return v==null?'—':v.toFixed(1)")); I.append(text("fLu",by+66,530,120,20,"LITRES",14,LBL,0))
I.append(text("fA",by+30,650,60,40,"—",30,WHITE,1,te="var v=$prop('DataCorePlugin.Computed.Fuel_LitersPerLap');return v==null?'—':v.toFixed(1)")); I.append(text("fAu",by+66,650,60,20,"AVG",14,LBL,1))
I.append(text("fLap",by+30,712,64,40,"—",30,WHITE,1,te="var l=$prop('DataCorePlugin.Computed.Fuel_RemainingLaps');return l>0?Math.floor(l):'—'")); I.append(text("fLapu",by+66,712,64,20,"LAPS",14,LBL,1))
d=json.loads(json.dumps(base)); d["Id"]=str(uuid.uuid4()); d["BaseWidth"]=800; d["BaseHeight"]=480; d["BackgroundColor"]="#FF0B0D10"; d["Screens"][0]["ScreenId"]=str(uuid.uuid4()); d["Screens"][0]["Items"]=I
m=json.loads(json.dumps(meta)); m["Title"]=NAME; m["Width"]=800.0; m["Height"]=480.0
out=f"{NAME}.simhubdash"
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as zo:
    zo.writestr(f"{NAME}\\{NAME}.djson",json.dumps(d)); zo.writestr(f"{NAME}\\{NAME}.djson.metadata",json.dumps(m,indent=2)); zo.writestr(f"{NAME}\\JavascriptExtensions\\sample.js",js)
shutil.copy(out,f"{OUTDIR}/{NAME}.simhubdash"); shutil.copy(out,f"{OUTPUTS}/{NAME}.simhubdash")
print("items:",len(I),"-> DDU rebuilt with new tyre panel")
