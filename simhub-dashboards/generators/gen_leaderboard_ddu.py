import zipfile, json, uuid, shutil
SRC="template.zip"; OUT="/sessions/great-happy-einstein/mnt/SimHub"; OUTPUTS="/sessions/great-happy-einstein/mnt/outputs"; NAME="ProDash Leaderboard"
PP="ProDashPlugin.ProDash."
z=zipfile.ZipFile(SRC); base=json.loads(z.read('TEST\\TEST.djson').decode()); meta=json.loads(z.read('TEST\\TEST.djson.metadata').decode()); js=z.read('TEST\\JavascriptExtensions\\sample.js')
def BS(r=0,bc="#00000000",bt=0):
    if bt>0: return {"BorderTop":bt,"BorderBottom":bt,"BorderLeft":bt,"BorderRight":bt,"RadiusTopLeft":r,"RadiusTopRight":r,"RadiusBottomLeft":r,"RadiusBottomRight":r}
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
BG="#FF000000"; PANEL="#FF000000"; BORD="#FF232B33"; LBL="#FF6E7681"; AMB="#FFFFB020"; GRN="#FF39D98A"; MAG="#FFD44BC8"; WHT="#FFFFFFFF"; GRY="#FFCFD3D8"
def irfmt(p): return f"var r=$prop('{p}');return r>0?(r>=1000?(r/1000).toFixed(1)+'k':String(r)):''"
W,H=800,480
I=[RC("bg",0,0,W,H,BG)]
I.append(RC("panel",6,6,W-12,H-12,PANEL,10,BORD,1))
I.append(TX("title",14,20,300,28,"LEADERBOARD",20,AMB,0))
I.append(TX("carsV",14,W-150,130,28,"0",20,GRY,2,te=f"return $prop('{PP}Field.CarCount')+' CARS'"))
# columns (class strip | POS | # | DRIVER | iR | BEST | GAP)
cols=[("POS",14,44,1),("#",70,54,1),("DRIVER",126,300,0),("iR",436,64,2),("BEST",520,120,2),("GAP",660,104,2)]
for lab,cx,cw,al in cols: I.append(TX("h_"+lab,50,20+cx,cw,20,lab,13,LBL,al))
ROWS=11; y0=76; rh=35
for n in range(1,ROWS+1):
    ry=y0+(n-1)*rh; b=f"{PP}Board.{n}."; valid=f"return $prop('{b}Valid')"
    stripe="#10FFFFFF" if n%2==0 else "#00000000"
    I.append(RC(f"rbg{n}",ry,14,W-28,rh-3,"#00000000",5,ve=valid,ce=f"return $prop('{b}IsPlayer')?'#33FFB020':'{stripe}'"))
    I.append(RC(f"cs{n}",ry,14,5,rh-3,"#00000000",0,ve=valid,ce=f"var c=$prop('{b}ClassColor');return c?c:'#00000000'"))
    pcol=f"return $prop('{b}IsPlayer')?'#FFFFB020':'#FFFFFFFF'"
    exprs={"POS":f"return $prop('{b}Pos')","#":f"var c=$prop('{b}CarNumber');return c?('#'+c):''","DRIVER":f"return $prop('{b}Name')","iR":irfmt(b+'IRating'),"BEST":f"return $prop('{b}BestLap')","GAP":f"return $prop('{b}Gap')"}
    colcolor={"iR":GRY,"BEST":MAG,"GAP":GRN}
    for lab,cx,cw,al in cols:
        I.append(TX(f"r{n}{lab}",ry,20+cx,cw,rh,size=19,color=colcolor.get(lab,WHT),align=al,ve=valid,te=exprs[lab],ce=(pcol if lab in("POS","DRIVER") else None)))
d=json.loads(json.dumps(base)); d["Id"]=str(uuid.uuid4()); d["BaseWidth"]=W; d["BaseHeight"]=H; d["BackgroundColor"]=BG; d["Screens"][0]["ScreenId"]=str(uuid.uuid4()); d["Screens"][0]["Items"]=I
m=json.loads(json.dumps(meta)); m["Title"]=NAME; m["Width"]=float(W); m["Height"]=float(H)
out=f"{NAME}.simhubdash"
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as zo:
    zo.writestr(f"{NAME}\\{NAME}.djson",json.dumps(d)); zo.writestr(f"{NAME}\\{NAME}.djson.metadata",json.dumps(m,indent=2)); zo.writestr(f"{NAME}\\JavascriptExtensions\\sample.js",js)
shutil.copy(out,f"{OUT}/{NAME}.simhubdash"); shutil.copy(out,f"{OUTPUTS}/{NAME}.simhubdash")
print("items:",len(I),"-> Leaderboard DDU 800x480 (Board.1..11, Pit-Wall design, GAP inside frame)")
