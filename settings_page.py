"""
EINSTELLUNGEN - one page for the overlays' config files (2026-11-10, Thomas:
"I always have to remember which json to open"). Served by the launcher
(launch_gui.py starts it in a thread, button "Einstellungen") on
http://127.0.0.1:5090 - only on this PC (API keys live in these files), POSTs
from another origin are refused. Standalone: `python settings_page.py`.

Every file gets a form built from FILES below: known keys as fields (help +
default shown), every other key of the file kept as it is (editable as JSON
under "Weitere Schlüssel"), secrets never sent to the page (empty = keep).
A key is written when the file already has it or the value differs from the
default, so files stay small. Written via temp file + os.replace.
Defaults here mirror the modules' own DEFAULT dicts - test_settings_page.py
checks that they agree.

Stdlib only (the launcher has no Flask).
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
PORT = 5090
SECRET_SET = "__set__"           # what the page sees for a secret that has a value


def F(key, kind, default, label, help="", options=None):
    return {"key": key, "kind": kind, "default": default, "label": label,
            "help": help, "options": options}


# kinds: bool, int, float, intopt / textopt (empty = null), text, secret, select, time,
# list (strings, comma separated on the page), json (any JSON value)
FILES = [
    {"id": "show", "file": "show_config.json", "title": "KI-Show (Moderation + Abspann)",
     "desc": "Moderation vor dem Rennen, Abspann danach, Gabi, OBS-Szenen und -Quellen, "
             "Grafiken während REGIE.",
     "uses": ["dashboard"],
     "applies": "Der Hauptschalter wirkt sofort, alles andere nach einem Neustart des Dashboards (5000).",
     "groups": [
        {"title": "Hauptschalter", "fields": [
            F("ki_show", "bool", True, "KI-Show an",
              "Aus = keine Moderation, kein Abspann, keine Gabi-Videos, auch nicht per Knopf."),
        ]},
        {"title": "Moderation vor dem Rennen", "fields": [
            F("auto_start", "bool", True, "Automatisch starten"),
            F("start_at", "time", "19:40", "Startzeit", "Uhrzeit (HH:MM) dieses PCs."),
            F("start_window_min", "int", 20, "Startfenster (Minuten)",
              "Automatischer Start nur so lange nach der Startzeit."),
            F("only_while_streaming", "bool", True, "Nur wenn OBS streamt"),
            F("director_after", "bool", True, "Danach REGIE einschalten"),
            F("host_name", "text", "", "Name der KI-Moderatorin", "Leer = stellt sich nicht mit Namen vor."),
            F("model", "text", "claude-sonnet-5-5", "Claude-Modell für das Skript"),
            F("segments", "list", ["intro", "last_race", "standings", "schedule", "track", "outro"],
              "Abschnitte", "Reihenfolge, durch Komma getrennt."),
            F("video_after_speech", "bool", True, "Streckenvideo erst nach dem Text",
              "Aus = Text über das laufende Video."),
            F("track_video_dir", "text", "show_notes/videos", "Ordner der Streckenvideos"),
            F("notes_dir", "text", "show_notes", "Ordner der Streckennotizen"),
            F("as_of_round", "intopt", None, "Stand vor Lauf",
              "Leer = automatisch (Replay eines früheren Laufs wird erkannt), 0 = aus, "
              "N = Tabelle / Ergebnisse wie vor Lauf N."),
        ]},
        {"title": "Abspann nach dem Rennen", "fields": [
            F("post_auto", "bool", True, "Automatisch starten", "Nur während REGIE läuft."),
            F("post_delay_s", "int", 120, "Sekunden nach der Zielflagge"),
            F("post_end_scene", "text", "", "Szene am Ende", "Leer = auf der Moderator-Szene bleiben."),
            F("post_segments", "list", ["race_summary", "results", "standings_after", "next_round", "goodbye"],
              "Abschnitte"),
        ]},
        {"title": "Gabi (Avatar-Videos über sync.so)", "fields": [
            F("avatar.enabled", "bool", True, "Gabi-Videos an",
              "Der sync.so-Schlüssel steht unter „KI-Kommentar, Stimme …“."),
            F("avatar.segments", "list", ["intro", "outro", "goodbye"], "Abschnitte mit Gabi im Bild"),
            F("avatar.prepare_min", "int", 20, "Minuten vor der Startzeit vorbereiten"),
            F("avatar.max_words", "int", 45, "Höchstens Wörter pro Video", "Länger = nur Stimme."),
        ]},
        {"title": "OBS", "fields": [
            F("obs.host", "text", "127.0.0.1", "OBS-Adresse"),
            F("obs.port", "int", 4455, "Websocket-Port"),
            F("obs.password", "secret", "", "Websocket-Passwort"),
            F("scenes.moderator", "text", "Moderator", "Szene Moderation"),
            F("scenes.iracing", "text", "iRacing", "Szene Rennen"),
        ]},
        {"title": "OBS-Quellen (Namen wie in OBS)", "fields": [
            F("sources.last_race", "text", "Letztes Rennen", "Letztes Rennen"),
            F("sources.last_race_pro", "text", "Letztes Rennen PRO", "Letztes Rennen PRO"),
            F("sources.last_race_am", "text", "Letztes Rennen AM", "Letztes Rennen AM"),
            F("sources.standings", "text", "Tabelle", "Tabelle"),
            F("sources.standings_pro", "text", "Tabelle PRO", "Tabelle PRO"),
            F("sources.standings_am", "text", "Tabelle AM", "Tabelle AM"),
            F("sources.track_video", "text", "Streckenvideo", "Streckenvideo"),
            F("sources.track_location", "text", "Streckenort", "Streckenort (Karte)"),
            F("sources.avatar_talk", "text", "Gabi", "Gabi spricht"),
            F("sources.avatar_idle", "text", "Gabi Ruhe", "Gabi Ruhe-Schleife"),
            F("sources.results", "text", "Ergebnis", "Abspann: Ergebnis"),
            F("sources.standings_after", "text", "Meisterschaft", "Abspann: Meisterschaft"),
            F("schedule_sources", "json", {"cas-gt3-wct": "WCT"}, "Saisonplan je Liga",
              'CLS-Liga → OBS-Quelle, z. B. {"cas-gt3-wct": "WCT"}. Liga ohne Eintrag = kein Saisonplan.'),
        ]},
        {"title": "Während des Rennens (REGIE)", "fields": [
            F("points_count", "int", 15, "Anzahl Gesprächsthemen"),
            F("points_quiet_s", "int", 30, "Thema nach so vielen Sekunden Ruhe"),
            F("points_every_s", "int", 150, "Höchstens alle … Sekunden ein Thema"),
            F("quali_camera_split_min", "intopt", None, "WCT-Quali: Kamera-Wechsel AM → PRO nach Minuten",
              "Leer = wie die Liga (15)."),
            F("graphics.enabled", "bool", True, "Grafiken einblenden (Wetter, Duell, Fahrer des Tages)"),
            F("graphics.scene", "text", "iRacing", "Szene der Grafiken"),
            F("graphics.gap_s", "float", 45.0, "Sekunden zwischen zwei Grafiken"),
            F("graphics.weather.source", "text", "Wetter", "Quelle Wetter"),
            F("graphics.battle.source", "text", "Duell", "Quelle Duell"),
            F("graphics.dotd.source", "text", "Fahrer des Tages", "Quelle Fahrer des Tages"),
        ]},
     ]},
    {"id": "commentary", "file": "commentary_config.json", "title": "KI-Kommentar, Stimme, Gabi, Frag Claude",
     "desc": "Claude-Schlüssel, Stimme der Kommentare, sync.so für Gabi, die Frag-Claude-Taste.",
     "uses": ["dashboard"],
     "applies": "Wirkt nach einem Neustart des Dashboards (5000).",
     "groups": [
        {"title": "Claude", "fields": [
            F("api_key", "secret", "", "Claude API-Schlüssel"),
            F("workspace_id", "text", "", "Workspace-ID", "Nur für einen Schlüssel ohne Workspace."),
            F("mode", "select", "auto", "Kommentar",
              options=[["auto", "Claude, sonst Vorlagen"], ["template", "nur Vorlagen (kostet nichts)"]]),
            F("model", "text", "claude-haiku-5-5", "Modell für den Kommentar"),
            F("timeout_s", "float", 6.0, "Zeitlimit (Sekunden)"),
        ]},
        {"title": "Stimme", "fields": [
            F("tts", "select", "auto", "Sprachausgabe",
              options=[["auto", "auto (Edge, bei Fehler Windows)"], ["edge", "Edge (Internet)"],
                       ["sapi", "Windows-Stimme (offline)"], ["off", "aus"]]),
            F("tts_voice", "text", "de-DE-ConradNeural", "Edge-Stimme", "z. B. de-DE-KatjaNeural"),
            F("tts_rate", "text", "+10%", "Tempo", "z. B. +10%"),
            F("sapi_voice", "text", "", "Windows-Stimme (Teil des Namens)"),
            F("mouth_gain", "float", 1.0, "Mund-Öffnung (VTube Studio)", "1 = normal, 2,5 = ganz offen."),
            F("aussprache", "json", {}, "Aussprache", 'z. B. {"CAS": "Kas"}'),
            F("moods", "json", {}, "Stimmungen", 'z. B. {"hype": {"rate": 25}}. Leer = Standard.'),
        ]},
        {"title": "Gabi (sync.so)", "fields": [
            F("sync_api_key", "secret", "", "sync.so API-Schlüssel"),
            F("sync_model", "text", "lipsync-2-pro", "sync.so-Modell"),
        ]},
        {"title": "Frag Claude (Stream-Deck-Taste)", "fields": [
            F("qa_model", "text", "claude-haiku-5-5", "Modell"),
            F("qa_web_search", "bool", True, "Websuche erlauben"),
            F("qa_hold", "bool", True, "Antwort erst auf Tastendruck abspielen"),
            F("qa_hold_max_s", "float", 300.0, "Wartende Antwort verfällt nach Sekunden"),
            F("qa_mic", "text", "", "Mikrofon", "Teil des Gerätenamens oder Nummer, leer = Standard."),
            F("qa_max_record_s", "float", 20.0, "Höchstens Sekunden aufnehmen"),
            F("qa_stt_model", "select", "small", "Spracherkennung",
              options=[["base", "base (schnell)"], ["small", "small"], ["medium", "medium (genauer, langsam)"]]),
            F("qa_timeout_s", "float", 25.0, "Zeitlimit (Sekunden)"),
            F("qa_log", "bool", True, "Fragen und Antworten in logs/ speichern"),
            F("qa_cls_url", "text", "https://league.simracing-hub.com", "CLS-Adresse"),
        ]},
     ]},
    {"id": "champ", "file": "championship_config.json", "title": "Meisterschaft (5010)",
     "desc": "Welche Liga / Saison die Meisterschafts-Overlays zeigen.",
     "uses": ["champ"],
     "applies": "Wirkt nach einem Neustart der Meisterschaft (5010). Liga und Saison gehen "
                "bequemer (und sofort) auf http://localhost:5010/.",
     "link": "http://localhost:5010/",
     "groups": [
        {"title": "Liga", "fields": [
            F("league_mode", "select", "auto", "Liga erkennen",
              options=[["auto", "automatisch am Starterfeld"], ["manual", "fest (unten)"]]),
            F("league_slug", "text", "cas-gt3-wct", "Liga (CLS-Kürzel)", "z. B. cas-gt3-wct, cas-pccd, cas-iec"),
            F("season_id", "textopt", None, "Saison-ID", "Leer = aktuelle Saison der Liga."),
            F("refresh_seconds", "int", 60, "Neu laden alle … Sekunden"),
        ]},
        {"title": "Punkte (was CLS nicht mitliefert)", "fields": [
            F("race_points_min_distance_pct", "int", 50, "Punkte ab … % der Distanz des Siegers"),
            F("participation_min_distance_pct", "int", 75, "Teilnahmepunkt ab … % der Distanz"),
            F("participation_in_combined", "bool", False, "Teilnahmepunkt auch in der Gesamtwertung"),
            F("points_table_race2", "json", None, "Punktetabelle Rennen 2",
              'Leer = wie Rennen 1. Sonst z. B. {"1": 41, "2": 35}'),
            F("api_base", "text", "https://league.simracing-hub.com", "CLS-Adresse"),
        ]},
     ]},
    {"id": "proam", "file": "proam_config.json", "title": "PRO / AM (WCT)",
     "desc": "Rote / grüne Balken für PRO und AM: Tower, Fahrerkarte, Streckenkarte, Ergebnisse, "
             "Duell, Dashboard, iCAS.",
     "uses": ["standings", "driver", "trackmap", "results", "catch", "livery", "dashboard", "racectrl"],
     "applies": "Wirkt nach einem Neustart der betroffenen Overlays.",
     "groups": [
        {"title": "PRO / AM", "fields": [
            F("mode", "select", "auto", "Anzeigen",
              options=[["auto", "automatisch (nur im WCT-Feld)"], ["on", "immer"], ["off", "nie"]]),
            F("league_slug", "text", "cas-gt3-wct", "Liga (CLS-Kürzel)"),
            F("season_id", "textopt", None, "Saison-ID", "Leer = aktuelle Saison."),
            F("refresh_seconds", "int", 300, "Neu laden alle … Sekunden"),
            F("api_base", "text", "https://league.simracing-hub.com", "CLS-Adresse"),
        ]},
     ]},
    {"id": "rc", "file": "rc_config.json", "title": "Race-Control-Banner (5019)",
     "desc": "Empfang von iRaceControl (ATVO), iRacing-Verwarnungen, andere Quellen ausblenden.",
     "uses": ["rcmsg"],
     "applies": "Wirkt nach einem Neustart des Race-Control-Banners (5019).",
     "groups": [
        {"title": "iRaceControl (ATVO)", "fields": [
            F("atvo_enabled", "bool", True, "ATVO-Empfang an"),
            F("atvo_port", "int", 5020, "Port"),
            F("atvo_password", "secret", "", "Passwort"),
        ]},
        {"title": "Banner", "fields": [
            F("iracing_warnings", "bool", False, "iRacings Verwarnung (schwarze Flagge eingerollt) zeigen"),
            F("hide_sources", "list", [], "Diese OBS-Quellen ausblenden, solange ein Banner läuft",
              "Namen durch Komma getrennt."),
        ]},
     ]},
    {"id": "replay", "file": "replay_pc_config.json", "title": "Replay auf dem zweiten PC",
     "desc": "Replays laufen auf dem Sim-Rig, das Bild kommt per NDI (REPLAY_PC.md).",
     "uses": ["dashboard"],
     "applies": "Wirkt sofort.", "live": True,
     "groups": [
        {"title": "Replay-PC", "fields": [
            F("url", "text", "", "Adresse des Replay-PCs", "z. B. 192.168.86.73. Leer = aus."),
            F("scene", "text", "Replay", "OBS-Szene während des Replays", "Leer = Szene nicht wechseln."),
            F("source", "text", "", "Stattdessen diese Quelle(n) einblenden",
              "In der Szene, die gerade läuft, z. B. .73remoteview, .73Live_Replay, .73Owncarlivery "
              "(durch Komma getrennt). Leer = Szene wechseln."),
            F("hide_sources", "list", [], "Diese Quellen solange ausblenden",
              "Nur mit Quelle(n) oben, z. B. Owncarlivery, Replay/Live. Danach wieder an."),
            F("back_scene", "text", "", "Feste Szene danach", "Leer = die Szene vor dem Replay."),
            F("transition", "text", "", "OBS-Übergang nur für diese Wechsel",
              "z. B. Replay Stinger (Name wie im Dock Szenenübergänge). Die Szenen dann OHNE "
              "„Übergang überschreiben“. Leer = der normale Übergang."),
            F("follow", "bool", True, "Replays am Replay-PC selbst auch einblenden"),
            F("fallback_local", "bool", True, "Replay-PC nicht erreichbar → Replay hier"),
            F("extra_s", "float", 20.0, "Höchstens so lange nach der Replay-Länge warten"),
        ]},
     ]},
    {"id": "logger", "file": "league_manager.json", "title": "Race Logger → CLS",
     "desc": "Upload der Rennlogs und der Live-Boxenstopps zur Liga-Seite.",
     "uses": ["logger"],
     "applies": "Wirkt sofort.", "live": True,
     "link": "http://localhost:5009/league",
     "groups": [
        {"title": "Upload", "fields": [
            F("url", "text", "https://league.simracing-hub.com", "CLS-Adresse"),
            F("token", "secret", "", "Upload-Token"),
            F("auto", "bool", True, "Nach dem Rennen automatisch hochladen"),
            F("live", "bool", True, "Eigene Boxenstopps live senden"),
        ]},
     ]},
]
# pages that already have their own settings
LINKS = [
    {"title": "YouTube-Bild (5005)", "url": "http://localhost:5005/youtube/setup",
     "desc": "Link einfügen, stumm ja / nein."},
    {"title": "iCASControl (8080)", "url": "http://localhost:8080/",
     "desc": "Auto Steward (Regeln, Profile), iRacing-Chat, Stream-Banner, Stewards - alles in der Oberfläche."},
]
BY_ID = {f["id"]: f for f in FILES}


# -- reading / writing ----------------------------------------------------------
def _get(d, path):
    for p in path.split("."):
        if not isinstance(d, dict) or p not in d:
            return False, None
        d = d[p]
    return True, d


def _set(d, path, value):
    parts = path.split(".")
    for p in parts[:-1]:
        if not isinstance(d.get(p), dict):
            d[p] = {}
        d = d[p]
    d[parts[-1]] = value


def _fields(spec):
    return [f for g in spec["groups"] for f in g["fields"]]


def _known_top(spec):
    return {f["key"].split(".")[0] for f in _fields(spec)}


def _extra(spec, data, path=""):
    """Keys of the file the form does not cover (kept, editable as JSON)."""
    known = {f["key"] for f in _fields(spec)}
    out = {}
    for k, v in data.items():
        full = f"{path}{k}"
        if full in known:
            continue
        if isinstance(v, dict) and any(x.startswith(full + ".") for x in known):
            sub = _extra(spec, v, full + ".")
            if sub:
                out[k] = sub
        else:
            out[k] = v
    return out


def _drop(d, extra, known, path=""):
    for k, v in extra.items():
        full = path + k
        if isinstance(v, dict) and isinstance(d.get(k), dict) and \
                any(x.startswith(full + ".") for x in known):
            _drop(d[k], v, known, full + ".")    # a dict that also holds form fields
        else:
            d.pop(k, None)


def _merge(d, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            _merge(d[k], v)
        else:
            d[k] = v


def read_file(spec, base: Path = HERE):
    """-> (data dict, error text or '')."""
    path = base / spec["file"]
    if not path.exists():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        return {}, f"{spec['file']} ist kein gültiges JSON: {exc}"
    if not isinstance(data, dict):
        return {}, f"{spec['file']} enthält kein Objekt"
    return data, ""


def view(spec, base: Path = HERE) -> dict:
    """What the page shows: values (secrets masked), defaults, extra keys."""
    data, err = read_file(spec, base)
    values = {}
    for f in _fields(spec):
        has, v = _get(data, f["key"])
        if f["kind"] == "secret":
            values[f["key"]] = SECRET_SET if (has and v) else ""
        else:
            values[f["key"]] = v if has else copy.deepcopy(f["default"])
    path = base / spec["file"]
    return {"id": spec["id"], "file": spec["file"], "exists": path.exists(), "error": err,
            "values": values, "extra": _extra(spec, data)}


def _convert(f, raw):
    k, kind = f["key"], f["kind"]
    if kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError("ja / nein erwartet")
        return raw
    if kind in ("int", "float", "intopt"):
        if raw in (None, "") and kind == "intopt":
            return None
        try:
            num = float(str(raw).replace(",", "."))
        except ValueError:
            raise ValueError("Zahl erwartet")
        if kind == "float":
            return num
        if num != int(num):
            raise ValueError("ganze Zahl erwartet")
        return int(num)
    if kind == "select":
        if raw not in [o[0] for o in f["options"]]:
            raise ValueError("ungültige Auswahl")
        return raw
    if kind == "time":
        raw = str(raw or "").strip()
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError("Uhrzeit HH:MM erwartet")
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    if kind == "list":
        if isinstance(raw, list):
            items = raw
        else:
            items = str(raw or "").split(",")
        return [str(x).strip() for x in items if str(x).strip()]
    if kind == "json":
        if isinstance(raw, str):
            if not raw.strip():
                return copy.deepcopy(f["default"])
            try:
                return json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"kein gültiges JSON ({exc})")
        return raw
    if kind == "textopt":
        return str(raw or "").strip() or None
    # text
    return str(raw if raw is not None else "").strip()


def save(spec, values: dict, extra=None, base: Path = HERE) -> tuple[bool, dict]:
    """Merge the page's values into the file. -> (ok, {"errors": {key: msg}}
    or {"written": [...]}). Unknown keys are kept; `extra` (dict) replaces
    the keys the form does not cover."""
    data, err = read_file(spec, base)
    if err:
        return False, {"errors": {"_file": err + " - erst die Datei reparieren oder löschen."}}
    out = copy.deepcopy(data)
    errors = {}
    if extra is not None:
        if isinstance(extra, str):
            try:
                extra = json.loads(extra or "{}")
            except ValueError as exc:
                errors["_extra"] = f"kein gültiges JSON ({exc})"
                extra = None
        if extra is not None and not isinstance(extra, dict):
            errors["_extra"] = "ein JSON-Objekt { … } erwartet"
            extra = None
    for f in _fields(spec):
        k = f["key"]
        if k not in values:
            continue
        has, old = _get(data, k)
        if f["kind"] == "secret":
            raw = values[k]
            if raw == SECRET_SET or raw is None:
                continue                         # unchanged
            if raw == "" and not values.get(k + "__clear"):
                continue                         # empty field = keep
            _set(out, k, str(raw).strip())
            continue
        if f["kind"] == "select" and has and values[k] == old:
            continue                             # a value the page has no option for: kept
        try:
            v = _convert(f, values[k])
        except ValueError as exc:
            errors[k] = str(exc)
            continue
        if has or v != f["default"]:
            _set(out, k, v)
    if errors:
        return False, {"errors": errors}
    if extra is not None:
        # the extra keys are replaced as a whole: old ones out, the page's in
        _drop(out, _extra(spec, out), {f["key"] for f in _fields(spec)})
        _merge(out, extra)
    path = base / spec["file"]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return True, {"written": spec["file"]}


# -- web page -------------------------------------------------------------------
def page_spec() -> dict:
    """FILES for the page (without the defaults of secrets)."""
    files = []
    for spec in FILES:
        s = copy.deepcopy(spec)
        for g in s["groups"]:
            for f in g["fields"]:
                if f["kind"] == "secret":
                    f["default"] = ""
        files.append(s)
    return {"files": files, "links": LINKS}


PAGE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Overlay-Einstellungen</title>
<style>
:root { --bg:#0f0f15; --panel:#181820; --panel2:#1f1f29; --line:#2c2c3a; --text:#e8e8f0;
  --muted:#9a9aab; --acc:#9146ff; --ok:#4ade80; --err:#ff6b6b; --warn:#ffd166; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px "Segoe UI", system-ui, sans-serif; }
header { padding:14px 20px; border-bottom:1px solid var(--line); display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
header h1 { margin:0; font-size:20px; }
header span { color:var(--muted); font-size:13px; }
.wrap { display:flex; min-height:calc(100vh - 56px); }
nav { width:270px; flex:none; border-right:1px solid var(--line); padding:10px; }
nav button { display:block; width:100%; text-align:left; background:none; color:var(--text);
  border:0; border-radius:6px; padding:9px 10px; font:inherit; cursor:pointer; margin-bottom:2px; }
nav button:hover { background:var(--panel2); }
nav button.on { background:var(--acc); color:#fff; }
nav button small { display:block; color:var(--muted); font-size:12px; }
nav button.on small { color:#e6dcff; }
nav h3 { font-size:12px; text-transform:uppercase; color:var(--muted); margin:16px 10px 6px; letter-spacing:.06em; }
nav a { display:block; color:#7cc4ff; padding:6px 10px; font-size:14px; text-decoration:none; }
nav a small { display:block; color:var(--muted); font-size:12px; }
main { flex:1; padding:18px 24px 80px; max-width:980px; }
main h2 { margin:0 0 4px; }
.desc { color:var(--muted); margin:0 0 6px; }
.applies { color:var(--warn); font-size:13px; margin:0 0 4px; }
.fileinfo { color:var(--muted); font-size:12px; margin-bottom:14px; }
fieldset { border:1px solid var(--line); border-radius:8px; background:var(--panel); margin:0 0 14px; padding:10px 14px 4px; }
legend { padding:0 6px; color:#cdb8ff; font-weight:600; }
.row { display:grid; grid-template-columns: 300px 1fr; gap:4px 14px; align-items:start; padding:7px 0; border-top:1px solid #23232e; }
.row:first-of-type { border-top:0; }
.row label.l { padding-top:6px; }
.help { color:var(--muted); font-size:12px; margin-top:3px; }
.changed label.l { color:var(--warn); }
input[type=text], input[type=password], input[type=number], select, textarea {
  width:100%; background:var(--panel2); color:var(--text); border:1px solid var(--line);
  border-radius:5px; padding:6px 8px; font:inherit; }
textarea { font-family:Consolas, monospace; font-size:13px; min-height:60px; }
input[type=checkbox] { width:20px; height:20px; margin-top:5px; accent-color:var(--acc); }
.err { color:var(--err); font-size:13px; margin-top:3px; }
.bar { position:fixed; bottom:0; left:270px; right:0; background:#13131b; border-top:1px solid var(--line);
  padding:10px 24px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
.bar button { font:inherit; border-radius:6px; padding:8px 18px; cursor:pointer; border:1px solid var(--line);
  background:var(--panel2); color:var(--text); }
.bar button.save { background:var(--acc); border-color:var(--acc); color:#fff; font-weight:600; }
.bar button.restart { background:#2a1d08; border-color:#ffb020; color:#ffd27a; }
#msg { font-size:14px; }
#msg.ok { color:var(--ok); } #msg.err { color:var(--err); }
.secret-state { font-size:12px; color:var(--ok); margin-top:3px; }
@media (max-width: 760px) {
  .wrap { display:block; } nav { width:auto; border-right:0; border-bottom:1px solid var(--line); }
  .row { grid-template-columns: 1fr; } .bar { left:0; }
}
</style></head><body>
<header><h1>⚙ Overlay-Einstellungen</h1><span>Statt JSON-Dateien von Hand: hier ändern, speichern, ggf. neu starten.</span></header>
<div class="wrap"><nav id="nav"></nav><main id="main"></main></div>
<div class="bar"><button class="save" id="save">Speichern</button>
  <button id="reset">Änderungen verwerfen</button>
  <button class="restart hidden" id="restart" style="display:none"></button>
  <span id="msg"></span></div>
<script>
let SPEC = null, CUR = null, VIEW = null, RUNNING = [], CAN_RESTART = false, NAMES = {};
const $ = (s) => document.querySelector(s);
const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function fmtDefault(f) {
  const d = f.default;
  if (f.kind === 'secret') return '';
  if (d === null || d === undefined || d === '') return 'leer';
  if (f.kind === 'bool') return d ? 'an' : 'aus';
  if (f.kind === 'list') return d.join(', ') || 'leer';
  if (f.kind === 'json') return JSON.stringify(d);
  if (f.kind === 'select') { const o = f.options.find((o) => o[0] === d); return o ? o[1] : d; }
  return String(d).replace('.', ',');
}
function inputFor(f, v) {
  const id = 'f_' + f.key;
  if (f.kind === 'bool') return `<input type="checkbox" id="${id}" ${v ? 'checked' : ''}>`;
  if (f.kind === 'select') {
    const opts = f.options.some((o) => o[0] === v) ? f.options : f.options.concat([[v, v + ' (aus der Datei)']]);
    return `<select id="${id}">${opts.map((o) =>
      `<option value="${esc(o[0])}" ${o[0] === v ? 'selected' : ''}>${esc(o[1])}</option>`).join('')}</select>`;
  }
  if (f.kind === 'secret') return `<input type="password" id="${id}" autocomplete="off" placeholder="${
      v ? 'gesetzt - leer lassen = behalten' : 'nicht gesetzt'}">`
    + (v ? `<div class="secret-state">✓ gesetzt · <label><input type="checkbox" id="${id}__clear"> löschen</label></div>` : '');
  if (f.kind === 'json') return `<textarea id="${id}" spellcheck="false">${esc(
      v === null || v === undefined || (typeof v === 'object' && !Object.keys(v).length) ? '' : JSON.stringify(v, null, 1))}</textarea>`;
  if (f.kind === 'list') return `<input type="text" id="${id}" value="${esc((v || []).join(', '))}">`;
  if (f.kind === 'int' || f.kind === 'float' || f.kind === 'intopt')
    return `<input type="number" id="${id}" step="${f.kind === 'float' ? 'any' : '1'}" value="${v === null || v === undefined ? '' : v}">`;
  return `<input type="text" id="${id}" value="${esc(v ?? '')}">`;
}
function readField(f) {
  const el = document.getElementById('f_' + f.key);
  if (f.kind === 'bool') return el.checked;
  if (f.kind === 'secret') return el.value;
  if (f.kind === 'int' || f.kind === 'float' || f.kind === 'intopt') return el.value === '' ? (f.kind === 'intopt' ? null : '') : el.value;
  return el.value;
}
function renderNav() {
  $('#nav').innerHTML = SPEC.files.map((s) =>
    `<button data-id="${s.id}" class="${CUR === s.id ? 'on' : ''}">${esc(s.title)}<small>${esc(s.file)}</small></button>`).join('')
    + '<h3>Eigene Einstellungsseiten</h3>'
    + SPEC.links.map((l) => `<a href="${esc(l.url)}" target="_blank">${esc(l.title)} ↗<small>${esc(l.desc)}</small></a>`).join('');
  document.querySelectorAll('#nav button').forEach((b) => b.onclick = () => openFile(b.dataset.id));
}
async function openFile(id) {
  CUR = id; renderNav(); msg('');
  try { localStorage.setItem('settings_file', id); } catch (e) {}
  const r = await fetch('/api/file/' + id); VIEW = await r.json();
  const s = SPEC.files.find((x) => x.id === id);
  let h = `<h2>${esc(s.title)}</h2><p class="desc">${esc(s.desc)}</p><p class="applies">${esc(s.applies)}</p>`
    + `<div class="fileinfo">Datei: ${esc(s.file)}${VIEW.exists ? '' : ' (gibt es noch nicht - wird beim Speichern angelegt)'}`
    + (s.link ? ` · auch hier: <a href="${esc(s.link)}" target="_blank" style="color:#7cc4ff">${esc(s.link)}</a>` : '') + '</div>';
  if (VIEW.error) h += `<p class="err">${esc(VIEW.error)}</p>`;
  for (const g of s.groups) {
    h += `<fieldset><legend>${esc(g.title)}</legend>`;
    for (const f of g.fields) {
      const v = VIEW.values[f.key];
      const dflt = fmtDefault(f);
      h += `<div class="row" id="r_${esc(f.key)}"><label class="l" for="f_${esc(f.key)}">${esc(f.label)}</label><div>${inputFor(f, v)}`
        + `<div class="help">${esc(f.help)}${f.help && dflt ? ' · ' : ''}${dflt ? 'Standard: ' + esc(dflt) : ''}</div>`
        + `<div class="err" id="e_${esc(f.key)}"></div></div></div>`;
    }
    h += '</fieldset>';
  }
  const extra = VIEW.extra && Object.keys(VIEW.extra).length ? JSON.stringify(VIEW.extra, null, 1) : '';
  h += `<fieldset><legend>Weitere Schlüssel (JSON)</legend><div class="help">Was oben nicht vorkommt, bleibt erhalten. Für Fortgeschrittene: hier als JSON ändern oder ergänzen.</div>`
    + `<textarea id="extra" spellcheck="false" style="min-height:${extra ? 120 : 50}px">${esc(extra)}</textarea><div class="err" id="e__extra"></div></fieldset>`;
  $('#main').innerHTML = h;
  $('#main').querySelectorAll('input,select,textarea').forEach((el) => el.addEventListener('input', () => {
    const row = el.closest('.row'); if (row) row.classList.add('changed'); }));
  showRestart(false);
}
function msg(t, cls) { const m = $('#msg'); m.textContent = t; m.className = cls || ''; }
function showRestart(saved) {
  const s = SPEC.files.find((x) => x.id === CUR);
  const b = $('#restart');
  const tags = (s.uses || []).filter((t) => RUNNING.includes(t));
  if (!saved || !CAN_RESTART || !tags.length || s.live) { b.style.display = 'none'; return; }
  b.style.display = '';
  b.textContent = '↻ Neu starten: ' + tags.map((t) => NAMES[t] || t).join(', ');
  b.onclick = async () => {
    if (!confirm('Jetzt neu starten: ' + tags.map((t) => NAMES[t] || t).join(', ')
        + '?\nDie Overlays sind dabei ein paar Sekunden weg.')) return;
    const r = await (await fetch('/api/restart', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({tags})})).json();
    msg(r.ok ? 'Wird neu gestartet: ' + r.tags.join(', ') : (r.error || 'Fehler'), r.ok ? 'ok' : 'err');
    b.style.display = 'none';
  };
}
async function doSave() {
  const s = SPEC.files.find((x) => x.id === CUR);
  const values = {};
  document.querySelectorAll('.err').forEach((e) => e.textContent = '');
  for (const g of s.groups) for (const f of g.fields) {
    values[f.key] = readField(f);
    const c = document.getElementById('f_' + f.key + '__clear');
    if (c && c.checked) { values[f.key] = ''; values[f.key + '__clear'] = true; }
  }
  const r = await (await fetch('/api/file/' + CUR, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({values, extra: $('#extra').value})})).json();
  if (!r.ok) {
    const errs = r.errors || {};
    for (const [k, t] of Object.entries(errs)) {
      const e = document.getElementById('e_' + k); if (e) e.textContent = t;
    }
    msg(errs._file || 'Nicht gespeichert - bitte die markierten Felder prüfen.', 'err');
    return;
  }
  await refreshRunning();
  await openFile(CUR);
  msg('Gespeichert ✓ ' + s.applies, 'ok');
  showRestart(true);
}
async function refreshRunning() {
  try { const r = await (await fetch('/api/running')).json(); RUNNING = r.running || []; CAN_RESTART = !!r.can_restart; NAMES = r.names || {}; }
  catch (e) { RUNNING = []; }
}
$('#save').onclick = doSave;
$('#reset').onclick = () => openFile(CUR);
(async () => {
  SPEC = await (await fetch('/api/spec')).json();
  await refreshRunning();
  let first = SPEC.files[0].id;
  try { const s = localStorage.getItem('settings_file'); if (SPEC.files.some((f) => f.id === s)) first = s; } catch (e) {}
  renderNav(); openFile(first);
})();
</script></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "OverlaySettings/1"
    base: Path = HERE
    running_cb = None            # () -> {tag: name} of running overlays
    restart_cb = None            # (tags) -> list of restarted tags

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (
            body.encode("utf-8") if isinstance(body, str) else
            json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _local_origin(self) -> bool:
        """POSTs only from this page (another website could otherwise send
        settings to 127.0.0.1)."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urlsplit(origin).hostname or ""
        return host in ("127.0.0.1", "localhost") and urlsplit(origin).port == self.server.server_address[1]

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/spec":
            return self._send(200, page_spec())
        if path == "/api/running":
            run = self.running_cb() if self.running_cb else {}
            return self._send(200, {"running": sorted(run), "names": run,
                                    "can_restart": self.restart_cb is not None})
        if path.startswith("/api/file/"):
            spec = BY_ID.get(path.rsplit("/", 1)[-1])
            if not spec:
                return self._send(404, {"ok": False, "error": "unbekannt"})
            return self._send(200, view(spec, self.base))
        return self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._local_origin():
            return self._send(403, {"ok": False, "error": "nur von dieser Seite"})
        path = urlsplit(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            return self._send(400, {"ok": False, "error": "kein JSON"})
        if path.startswith("/api/file/"):
            spec = BY_ID.get(path.rsplit("/", 1)[-1])
            if not spec:
                return self._send(404, {"ok": False, "error": "unbekannt"})
            ok, res = save(spec, body.get("values") or {}, body.get("extra"), self.base)
            if ok:
                print(f"[settings] {spec['file']} saved")
            return self._send(200, {"ok": ok, **res})
        if path == "/api/restart":
            if not self.restart_cb:
                return self._send(200, {"ok": False, "error": "Neustart nur aus dem Launcher"})
            tags = [str(t) for t in (body.get("tags") or [])]
            return self._send(200, {"ok": True, "tags": self.restart_cb(tags)})
        return self._send(404, {"ok": False, "error": "not found"})


class _Server(ThreadingHTTPServer):
    # on Windows SO_REUSEADDR lets a SECOND server bind the same port without
    # an error (the old launcher during "Update & restart") - so not here
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def make_server(port: int = PORT, base: Path = HERE, running_cb=None, restart_cb=None):
    handler = type("H", (_Handler,), {"base": base, "running_cb": staticmethod(running_cb) if running_cb else None,
                                      "restart_cb": staticmethod(restart_cb) if restart_cb else None})
    return _Server(("127.0.0.1", port), handler)


class Background:
    """For the launcher: serves in a thread; a busy port (the old launcher
    still closing after an update) is tried again for `wait_s`."""

    def __init__(self, port=PORT, running_cb=None, restart_cb=None, wait_s=20.0, base=HERE):
        self.port, self.srv, self.error, self._stop = port, None, "", False
        threading.Thread(target=self._run, args=(running_cb, restart_cb, wait_s, base),
                         daemon=True).start()

    def _run(self, running_cb, restart_cb, wait_s, base):
        import time
        end = time.monotonic() + wait_s
        while not self._stop:
            try:
                self.srv = make_server(self.port, base, running_cb, restart_cb)
                break
            except OSError as exc:
                self.error = f"port {self.port} busy: {exc}"
                if time.monotonic() > end:
                    print(f"[settings] {self.error}")
                    return
                time.sleep(0.5)
        if self.srv is not None and not self._stop:
            self.error = ""
            self.srv.serve_forever()

    def close(self):
        self._stop = True
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()


def start_in_thread(port: int = PORT, running_cb=None, restart_cb=None, wait_s=20.0):
    return Background(port, running_cb, restart_cb, wait_s)


def url(port: int = PORT) -> str:
    return f"http://127.0.0.1:{port}/"


if __name__ == "__main__":
    srv = make_server()
    print(f"Overlay-Einstellungen: {url()}  (Strg+C beendet)")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
