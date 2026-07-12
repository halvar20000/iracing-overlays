import zipfile, json, uuid, shutil
SRC="template.zip"; OUT="/sessions/great-happy-einstein/mnt/SimHub"; OUTPUTS="/sessions/great-happy-einstein/mnt/outputs"; NAME="ProDash Relative"
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
BG="#FF000000"; PANEL="#FF000000"; BORD="#FF232B33"; LBL="#FF6E7681"; AMB="#FFFFB020"; GRN="#FF39D98A"; RED="#FFFF5D5D"; WHT="#FFFFFFFF"; GRY="#FFCFD3D8"
def irfmt(p): return f"var r=$prop('{p}');return r>0?(r>=1000?(r/1000).toFixed(1)+'k':String(r)):''"
# gap: LapsDiff!=0 -> "+NL"/"-NL"; else seconds with sign
def gapexpr(b): return (f"var L=$prop('{b}LapsDiff');var g=$prop('{b}Gap');var v=$prop('{b}Valid');var pl=$prop('{b}IsPlayer');"
                        f"if(!v)return '';if(pl)return '';if(L>0)return '+'+L+'L';if(L<0)return L+'L';"
                        f"return (g>0?'+':'')+g.toFixed(1)")
W,H=800,480
I=[RC("bg",0,0,W,H,BG)]
I.append(RC("panel",6,6,W-12,H-12,PANEL,10,BORD,1))
# header strip: SoF + car count (relative has no title row to save height)
I.append(TX("sofL",12,20,60,24,"SoF",15,LBL,0)); I.append(TX("sofV",10,72,120,30,"0",26,WHT,0,te=f"return $prop('{PP}Field.SoF')"))
I.append(TX("carsV",12,W-150,130,26,"0",20,GRY,2,te=f"return $prop('{PP}Field.CarCount')+' CARS'"))
ROWS=9; y0=46; rh=47
for n in range(1,ROWS+1):
    ry=y0+(n-1)*rh; b=f"{PP}Rel.{n}."; valid=f"return $prop('{b}Valid')"
    isPl=f"return $prop('{b}IsPlayer')"
    I.append(RC(f"rbg{n}",ry,14,W-28,rh-4,"#00000000",6,ve=valid,ce=f"return $prop('{b}IsPlayer')?'#33FFB020':(({n}%2==0)?'#10FFFFFF':'#00000000')"))
    I.append(RC(f"cs{n}",ry,14,6,rh-4,"#00000000",0,ve=valid,ce=f"var c=$prop('{b}ClassColor');return c?c:'#00000000'"))
    pcol=f"return $prop('{b}IsPlayer')?'#FFFFB020':'#FFFFFFFF'"
    # POS | # | DRIVER | iR | GAP
    I.append(TX(f"r{n}pos",ry,28,54,rh,size=24,color=WHT,align=1,ve=valid,te=f"var p=$prop('{b}Position');return p>0?p:''",ce=pcol))
    I.append(TX(f"r{n}num",ry,88,66,rh,size=22,color=GRY,align=1,ve=valid,te=f"var c=$prop('{b}CarNumber');return c?('#'+c):''"))
    I.append(TX(f"r{n}name",ry,166,360,rh,size=24,color=WHT,align=0,ve=valid,te=f"return $prop('{b}Name')",
                ce=f"if($prop('{b}IsPlayer'))return '#FFFFB020';return $prop('{b}InPit')?'#FF6E7681':'#FFFFFFFF'"))
    I.append(TX(f"r{n}ir",ry,520,110,rh,size=19,color=GRY,align=2,ve=valid,te=irfmt(b+'IRating')))
    I.append(TX(f"r{n}gap",ry,636,146,rh,size=26,color=WHT,align=2,ve=valid,te=gapexpr(b),
                ce=f"var pl=$prop('{b}IsPlayer');if(pl)return '#FFFFB020';var g=$prop('{b}Gap');return g>0?'#FF39D98A':'#FFFF5D5D'"))
d=json.loads(json.dumps(base)); d["Id"]=str(uuid.uuid4()); d["BaseWidth"]=W; d["BaseHeight"]=H; d["BackgroundColor"]=BG; d["Screens"][0]["ScreenId"]=str(uuid.uuid4()); d["Screens"][0]["Items"]=I
m=json.loads(json.dumps(meta)); m["Title"]=NAME; m["Width"]=float(W); m["Height"]=float(H)
out=f"{NAME}.simhubdash"
with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as zo:
    zo.writestr(f"{NAME}\\{NAME}.djson",json.dumps(d)); zo.writestr(f"{NAME}\\{NAME}.djson.metadata",json.dumps(m,indent=2)); zo.writestr(f"{NAME}\\JavascriptExtensions\\sample.js",js)
shutil.copy(out,f"{OUT}/{NAME}.simhubdash"); shutil.copy(out,f"{OUTPUTS}/{NAME}.simhubdash")
print("items:",len(I),"-> Relative DDU 800x480 (Rel.1..9, player centred row 5)")
