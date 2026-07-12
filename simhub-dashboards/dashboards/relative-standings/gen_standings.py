#!/usr/bin/env python3
"""
Generator for the "ProDash Standings" SimHub dashboard (Relative & Standings, landscape).

Produces an importable .simhubdash (a zip of <Name>\\<Name>.djson + .metadata + a JS folder)
bound to the SimHub Pro Dash plugin's computed properties.

KEY FACTS learned while building this (2026-07-05), so future dashboards are not blind:
  * A .simhubdash is a ZIP. Entry paths use BACKSLASHES: "<Name>\\<Name>.djson",
    "<Name>\\<Name>.djson.metadata", "<Name>\\JavascriptExtensions\\sample.js".
  * The .djson top level: Version 2, Id (GUID), BaseWidth/BaseHeight, BackgroundColor,
    Screens[]. Each Screen has Items[] (controls). Nested controls live under "Childrens".
  * A TextItem: $type "...Models.TextItem, SimHub.Plugins", IsTextItem true, Font/FontWeight/
    FontSize, Text (static), TextColor, HorizontalAlignment (0 left,1 center,2 right),
    VerticalAlignment, BackgroundColor, BorderStyle, Top/Left/Width/Height, Visible, Name,
    Bindings (dict).
  * A RectangleItem: $type "...Models.RectangleItem, SimHub.Plugins", IsRectangleItem true,
    BackgroundColor, BorderStyle{RadiusTopLeft..., BorderColor, BorderThickness}, geometry.
  * A binding: Bindings["<Target>"] = {"Formula":{"Interpreter":1,"Expression":"return ..."},
    "Mode":2, "TargetPropertyName":"<Target>"}. Interpreter 1 = JavaScript; use $prop('NAME').
    Optional "FormatString". Bind Text, TextColor, Visible, BackgroundColor, etc.
  * PROPERTY NAMING (verified empirically): SimHub prefixes plugin properties with the
    PLUGIN CLASS NAME. Our plugin class is ProDashPlugin, and we registered "ProDash.*", so
    the reference is  ProDashPlugin.ProDash.<...>  e.g. $prop('ProDashPlugin.ProDash.Field.SoF').
  * Import workflow: drop the .simhubdash into Documents\\SimHub, then SimHub ->
    Dash Studio -> Dashboards -> Import dashboard.

Run:  python3 gen_standings.py [output_dir]
(needs a base template zip; export any empty dashboard from SimHub as TEST.simhubdash and
 point SRC at it, or reuse this repo's copy.)
"""
import zipfile, json, uuid, shutil, sys, os

SRC = os.environ.get("SIMHUB_TEMPLATE", "TEST.simhubdash")  # an exported empty dashboard
OUTDIR = sys.argv[1] if len(sys.argv) > 1 else "."
NAME = "ProDash Standings"
PP = "ProDashPlugin.ProDash."  # <-- the verified property prefix

z = zipfile.ZipFile(SRC)
_djson = [n for n in z.namelist() if n.endswith(".djson")][0]
_meta = [n for n in z.namelist() if n.endswith(".metadata")][0]
_js = [n for n in z.namelist() if n.endswith(".js")]
base = json.loads(z.read(_djson).decode("utf-8"))
meta = json.loads(z.read(_meta).decode("utf-8"))
samplejs = z.read(_js[0]) if _js else b"function sample(){return \"ok\";}"


def borderless():
    return {"BorderColor": "#00000000", "RadiusTopLeft": 0, "RadiusTopRight": 0,
            "RadiusBottomLeft": 0, "RadiusBottomRight": 0, "BorderThickness": 0}


def _bind(expr, target, fmt=None):
    b = {"Formula": {"Interpreter": 1, "Expression": expr}, "Mode": 2, "TargetPropertyName": target}
    if fmt is not None:
        b["FormatString"] = fmt
    return b


def text(name, top, left, width, height, static="", size=30, color="#FFFFFFFF", align=0,
         weight="Bold", text_expr=None, color_expr=None, visible_expr=None, fmt=None):
    it = {"$type": "SimHub.Plugins.OutputPlugins.GraphicalDash.Models.TextItem, SimHub.Plugins",
          "IsTextItem": True, "Font": "Roboto", "FontWeight": weight, "FontSize": float(size),
          "Text": static, "TextColor": color, "HorizontalAlignment": align, "VerticalAlignment": 1,
          "BackgroundColor": "#00000000", "BorderStyle": borderless(),
          "Height": float(height), "Left": float(left), "Top": float(top), "Visible": True,
          "Width": float(width), "Name": name, "Bindings": {}}
    if text_expr:
        it["Bindings"]["Text"] = _bind(text_expr, "Text", fmt)
    if color_expr:
        it["Bindings"]["TextColor"] = _bind(color_expr, "TextColor")
    if visible_expr:
        it["Bindings"]["Visible"] = _bind(visible_expr, "Visible")
    return it


def rect(name, top, left, width, height, color, radius=0, visible_expr=None, color_expr=None):
    bs = {"BorderColor": "#00000000", "RadiusTopLeft": radius, "RadiusTopRight": radius,
          "RadiusBottomLeft": radius, "RadiusBottomRight": radius, "BorderThickness": 0}
    it = {"$type": "SimHub.Plugins.OutputPlugins.GraphicalDash.Models.RectangleItem, SimHub.Plugins",
          "IsRectangleItem": True, "BackgroundColor": color, "BorderStyle": bs,
          "Height": float(height), "Left": float(left), "Top": float(top), "Visible": True,
          "Width": float(width), "Name": name, "Bindings": {}}
    if visible_expr:
        it["Bindings"]["Visible"] = _bind(visible_expr, "Visible")
    if color_expr:
        it["Bindings"]["BackgroundColor"] = _bind(color_expr, "BackgroundColor")
    return it


items = [rect("bg", 0, 0, 1280, 720, "#FF0B0D10")]

# header
items += [
    text("title", 26, 30, 400, 54, "RELATIVE", 44, "#FFFFB020", 0),
    text("avgLbl", 92, 30, 170, 28, "AVG iRATING", 22, "#FF6E7681", 0),
    text("avgVal", 86, 205, 120, 30, "0", 26, "#FFFFFFFF", 0,
         text_expr=f"var r=$prop('{PP}Field.AvgIRating');return r>0?(r>=1000?(r/1000).toFixed(1)+'k':String(r)):'-'"),
    text("carsLbl", 92, 360, 90, 28, "CARS", 22, "#FF6E7681", 0),
    text("carsVal", 86, 450, 80, 30, "0", 26, "#FFFFFFFF", 0, text_expr=f"return $prop('{PP}Field.CarCount')"),
    text("sofLbl", 34, 900, 110, 30, "SoF", 26, "#FF6E7681", 2),
    text("sofVal", 18, 1010, 250, 66, "0", 62, "#FFFFFFFF", 2, text_expr=f"return $prop('{PP}Field.SoF')"),
]

# relative box
ROWS, PLAYER, top0, rh, gap = 7, 4, 150, 60, 4
cols = {"pos": (30, 70, 1), "num": (110, 95, 1), "name": (215, 430, 0),
        "ir": (650, 150, 2), "lic": (810, 150, 1), "gap": (970, 285, 2)}
for i in range(1, ROWS + 1):
    rt = top0 + (i - 1) * (rh + gap)
    n = i
    valid = f"return $prop('{PP}Rel.{n}.Valid')"
    if i == PLAYER:
        items.append(rect(f"row{n}bg", rt, 20, 1240, rh, "#33FFB020", 6, visible_expr=valid))
    elif i % 2 == 0:
        items.append(rect(f"row{n}bg", rt, 20, 1240, rh, "#14FFFFFF", 6, visible_expr=valid))
    ccexpr = (f"var c=$prop('{PP}Rel.{n}.ClassColor');if(!c)return '#00000000';"
              f"c=String(c).replace('0x','').replace('#','');"
              f"if(c.length>=6)return '#FF'+c.slice(-6);return '#00000000'")
    items.append(rect(f"row{n}cc", rt + 8, 20, 8, rh - 16, "#00000000", 2, visible_expr=valid, color_expr=ccexpr))
    pc = "#FFFFB020" if i == PLAYER else "#FFFFFFFF"
    l, w, a = cols["pos"]; items.append(text(f"r{n}pos", rt, l, w, rh, size=30, color=pc, align=a, visible_expr=valid,
        text_expr=f"var p=$prop('{PP}Rel.{n}.Position');return p>0?String(p):''"))
    l, w, a = cols["num"]; items.append(text(f"r{n}num", rt, l, w, rh, size=28, color="#FF9AA0A6", align=a, visible_expr=valid,
        text_expr=f"var c=$prop('{PP}Rel.{n}.CarNumber');return c?('#'+c):''"))
    l, w, a = cols["name"]; items.append(text(f"r{n}name", rt, l, w, rh, size=30, color=pc, align=a, visible_expr=valid,
        text_expr=f"return $prop('{PP}Rel.{n}.Name')"))
    l, w, a = cols["ir"]; items.append(text(f"r{n}ir", rt, l, w, rh, size=27, color="#FFCFD3D8", align=a, visible_expr=valid,
        text_expr=f"var r=$prop('{PP}Rel.{n}.IRating');return r>0?(r>=1000?(r/1000).toFixed(1)+'k':String(r)):''"))
    l, w, a = cols["lic"]; items.append(text(f"r{n}lic", rt, l, w, rh, size=25, color="#FFCFD3D8", align=a, visible_expr=valid,
        text_expr=f"return $prop('{PP}Rel.{n}.License')"))
    l, w, a = cols["gap"]; items.append(text(f"r{n}gap", rt, l, w, rh, size=34, align=a, visible_expr=valid,
        text_expr=f"var g=$prop('{PP}Rel.{n}.Gap');if(g===0)return '—';return (g>0?'+':'')+g.toFixed(1)",
        color_expr=f"var g=$prop('{PP}Rel.{n}.Gap');return g>0?'#FF39D98A':(g<0?'#FFFF5D5D':'#FFCCCCCC')"))

# proximity strip
py = 612
items += [
    rect("proxbg", py, 20, 1240, 86, "#1AFFFFFF", 8),
    text("proxLbl", py + 8, 40, 220, 28, "PROXIMITY", 22, "#FF6E7681", 0),
    text("proxL", py + 18, 360, 60, 54, "◀", 44, "#FFFF5D5D", 2, visible_expr=f"return $prop('{PP}Prox.CarLeft')"),
    text("proxState", py + 16, 430, 420, 56, "CLEAR", 40, "#FFFFFFFF", 1,
         text_expr=f"return $prop('{PP}Prox.State')",
         color_expr=f"var s=$prop('{PP}Prox.State');return s=='CLEAR'?'#FF39D98A':'#FFFFB020'"),
    text("proxR", py + 18, 860, 60, 54, "▶", 44, "#FFFF5D5D", 0, visible_expr=f"return $prop('{PP}Prox.CarRight')"),
    text("proxAhead", py + 22, 950, 140, 40, "", 26, "#FFCFD3D8", 2,
         text_expr=f"var g=$prop('{PP}Prox.AheadGap');return '▲ '+g.toFixed(1)+'s'"),
    text("proxBehind", py + 22, 1100, 150, 40, "", 26, "#FFCFD3D8", 2,
         text_expr=f"var g=$prop('{PP}Prox.BehindGap');return '▼ '+g.toFixed(1)+'s'"),
]

d = json.loads(json.dumps(base))
d["Id"] = str(uuid.uuid4()); d["BaseWidth"] = 1280; d["BaseHeight"] = 720
d["BackgroundColor"] = "#FF0B0D10"
d["Screens"][0]["ScreenId"] = str(uuid.uuid4())
d["Screens"][0]["Items"] = items
m = json.loads(json.dumps(meta)); m["Title"] = NAME; m["Width"] = 1280.0; m["Height"] = 720.0

out = os.path.join(OUTDIR, f"{NAME}.simhubdash")
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
    zo.writestr(f"{NAME}\\{NAME}.djson", json.dumps(d))
    zo.writestr(f"{NAME}\\{NAME}.djson.metadata", json.dumps(m, indent=2))
    zo.writestr(f"{NAME}\\JavascriptExtensions\\sample.js", samplejs)
print(f"Wrote {out}  ({len(items)} items)")
