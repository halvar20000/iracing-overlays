"""Offline checks for race_qa.py ("Frag Claude") — no network, no microphone,
no iRacing: recorder, speech to text, overlay sources and the Messages API are
all fakes.
Run: python test_race_qa.py
"""
import json
import random
import tempfile
import time
from pathlib import Path

import race_qa as qa
from race_commentator import Commentator

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


def wait_idle(q, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if q.state == "idle":
            return True
        time.sleep(0.02)
    return False


ROUNDINFO = {"lastRound": {"name": "Runde 3 Zandvoort", "races": [
    {"raceNumber": 1, "results": [
        {"position": 1, "name": "Maurice Becker", "points": 35, "carColor": "#ff0000",
         "logoUrl": "http://x/logo.svg", "bestLapMs": 95123.4567},
        {"position": 2, "name": "Remo Cursio", "points": 30}]}]}}
SESSION = {"strecke": "Indianapolis Motor Speedway", "layout": "Road Course",
           "fahrer": ["Maurice Becker", "Remo Cursio", "Bastian Kübler"]}


class FakeAPI:
    """Scripted Messages API: a list of responses, every request kept."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, payload):
        self.calls.append(json.loads(json.dumps(payload)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_use(name, uid="tu_1"):
    return {"stop_reason": "tool_use",
            "content": [{"type": "text", "text": "Ich schaue nach."},
                        {"type": "tool_use", "id": uid, "name": name, "input": {}}]}


def text(t):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": t}]}


class FakeRecorder:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        return [0.0] * 16000


class FakeSTT:
    def __init__(self, result="Welchen Platz hatte Maurice Becker im letzten Rennen?"):
        self.result = result
        self.hints = []

    def transcribe(self, audio, hint=""):
        self.hints.append(hint)
        return self.result


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


CFG = {"api_key": "k", "qa_model": "claude-haiku-5-5", "qa_web_search": True, "qa_max_record_s": 20}


def make(responses, cfg=None, **kw):
    api = FakeAPI(responses)
    spoken = []
    fetched = []

    def fetch(url):
        fetched.append(url)
        if "roundinfo" in url:
            return ROUNDINFO
        raise OSError("connection refused")

    q = qa.RaceQA(dict(CFG, **(cfg or {})), speak=lambda t: spoken.append(t) or len(spoken),
                  session_fn=lambda: dict(SESSION), fetch=fetch, api_post=api, **kw)
    return q, api, spoken, fetched


# 1) compaction: no colours / images / urls, floats rounded, lists cut
c = qa.compact({"rows": [{"name": "A", "carColor": "#fff", "logoUrl": "u", "gap": 1.234567, "x": None}] * 50})
row = c["rows"][0]
check("compact drops colour + url keys", "carColor" not in row and "logoUrl" not in row, row)
check("compact drops empty values", "x" not in row, row)
check("compact rounds floats", row["gap"] == 1.235, row)
check("compact cuts long lists", len(c["rows"]) == 41 and "weitere" in c["rows"][-1], len(c["rows"]))
deep = {"info": {"lastRound": {"races": [{"results": [{"name": "Maurice Becker", "position": 1}]}]}}}
check("compact keeps a 7-level result row (CLS round info)",
      qa.compact(deep)["info"]["lastRound"]["races"][0]["results"][0]["name"] == "Maurice Becker")
check("compact drops ids + solo team name", qa.compact({"name": "A B", "team_name": "A B", "userId": "x",
                                                        "car_idx": 3, "position": 2}) == {"name": "A B", "position": 2})
check("tool result capped", len(qa._dumps({"a": "x" * 50000})) < qa.TOOL_RESULT_MAX + 30)

# 2) whisper hint carries the driver names and the track
h = qa.stt_hint(SESSION)
check("stt hint has names + track", "Bastian Kübler" in h and "Indianapolis" in h, h)
check("stt hint without a session", qa.stt_hint({}).startswith("Frage an Claude"))

# 3) a typed question: tool round trip, answer spoken
q, api, spoken, fetched = make([tool_use("round_info"), text("Maurice Becker hat das erste Rennen gewonnen.")])
msg = q.ask_text("Wer hat letztes Mal gewonnen?", wait=True)
item = q.items[0]
check("answer from the tool loop", item["answer"] == "Maurice Becker hat das erste Rennen gewonnen.", item)
check("answer spoken once", spoken == [item["answer"]], spoken)
check("line id kept", item["line_id"] == 1, item)
check("tool listed as source", item["tools"] == ["round_info"], item["tools"])
check("round info fetched from 5010", fetched and fetched[0].endswith(":5010/api/roundinfo"), fetched)
res = api.calls[1]["messages"][-1]["content"][0]
check("tool_result answers the tool_use id", res["type"] == "tool_result" and res["tool_use_id"] == "tu_1", res)
check("tool_result carries the data, compacted", "Maurice Becker" in res["content"]
      and "carColor" not in res["content"] and "logoUrl" not in res["content"], res["content"][:200])
check("assistant turn replayed before the result", api.calls[1]["messages"][1]["role"] == "assistant"
      and api.calls[1]["messages"][1]["content"][1]["type"] == "tool_use")
check("thinking switched off", api.calls[0].get("thinking") == {"type": "disabled"}, api.calls[0].get("thinking"))
check("system prompt forbids inventing", "Erfinde nie" in api.calls[0]["system"])
check("question carries today's date", "Heute ist der" in api.calls[0]["messages"][0]["content"])
names = [t.get("name") for t in api.calls[0]["tools"]]
check("all data tools offered", all(n in names for n in ("live_session", "live_standings", "race_results",
                                                         "championship", "round_info", "league_table",
                                                         "track_notes")), names)
ws = [t for t in api.calls[0]["tools"] if t.get("name") == "web_search"]
check("haiku gets the basic web search", ws and ws[0]["type"] == "web_search_20250305", ws)
check("state back to idle", q.state == "idle")

# 4) web search variant by model, and off by config
q, api, *_ = make([text("ok")], cfg={"qa_model": "claude-sonnet-5-5"})
q.ask_text("x", wait=True)
ws = [t for t in api.calls[0]["tools"] if t.get("name") == "web_search"]
check("sonnet gets the dynamic web search", ws and ws[0]["type"] == "web_search_20260209", ws)
q, api, *_ = make([text("ok")], cfg={"qa_web_search": False})
q.ask_text("x", wait=True)
check("web search off by config", all(t.get("name") != "web_search" for t in api.calls[0]["tools"]))

# 5) an overlay that is not running -> is_error result, Claude still answers
q, api, spoken, _ = make([tool_use("live_standings"), text("Das habe ich leider nicht in meinen Daten.")])
q.ask_text("Wer führt?", wait=True)
res = api.calls[1]["messages"][-1]["content"][0]
check("unreachable source -> is_error", res.get("is_error") is True and "nicht erreichbar" in res["content"], res)
check("'don't know' answer is spoken", spoken == ["Das habe ich leider nicht in meinen Daten."], spoken)

# 6) web search reported as a source; pause_turn continued
q, api, spoken, _ = make([
    {"stop_reason": "pause_turn", "content": [{"type": "server_tool_use", "id": "s1", "name": "web_search",
                                              "input": {"query": "Formel 1 Indianapolis"}}]},
    text("Das letzte Formel-1-Rennen in Indianapolis war 2007.")])
q.ask_text("Wann fuhr die Formel 1 zuletzt hier?", wait=True)
check("pause_turn resumed with the assistant content", api.calls[1]["messages"][-1]["role"] == "assistant", api.calls[1]["messages"])
check("web search listed as source", "web_search" in q.items[0]["tools"], q.items[0]["tools"])
check("answer after pause_turn", "2007" in q.items[0]["answer"])

# 7) a model that rejects thinking:disabled -> one retry without it
q, api, spoken, _ = make([RuntimeError("HTTP 400: thinking type disabled is not supported"), text("Antwort.")])
q.ask_text("x", wait=True)
check("thinking 400 -> retried without thinking", len(api.calls) == 2 and "thinking" not in api.calls[1], api.calls[-1].keys())
check("retry answer spoken", spoken == ["Antwort."], spoken)

# 8) other API errors -> error on the item, nothing spoken, idle again
q, api, spoken, _ = make([RuntimeError("HTTP 529: overloaded")])
q.ask_text("x", wait=True)
check("api error kept on the item", "529" in q.items[0]["error"] and not spoken and q.state == "idle", q.items[0])
check("api error in status", "529" in q.status()["last_error"])

# 9) no key -> says so, nothing spoken, no call
q, api, spoken, _ = make([], cfg={"api_key": ""})
q.ask_text("x", wait=True)
check("no key -> error, no call", "API-Schlüssel" in q.items[0]["error"] and not api.calls and not spoken)

# 10) refusal is an error, not silence-with-text
q, api, spoken, _ = make([{"stop_reason": "refusal", "content": []}])
q.ask_text("x", wait=True)
check("refusal -> error", "abgelehnt" in q.items[0]["error"] and not spoken)

# 11) endless tool use stops
q, api, spoken, _ = make([tool_use("live_session", f"t{i}") for i in range(qa.MAX_ROUNDS)])
q.ask_text("x", wait=True)
check("tool rounds capped", "zu viele" in q.items[0]["error"] and len(api.calls) == qa.MAX_ROUNDS)

# 12) push-to-talk toggle: record -> transcribe (with hint) -> answer -> speak
clock = Clock()
rec, stt = FakeRecorder(), FakeSTT()
q, api, spoken, _ = make([tool_use("round_info"), text("Becker wurde Erster.")], recorder=rec, stt=stt, clock=clock)
check("toggle starts listening", q.toggle() == "hört zu" and q.state == "recording" and rec.started == 1)
check("status shows recording seconds", q.status()["recording_s"] == 0.0)
clock.t += 3.0
check("busy while recording (typed question refused)", q.ask_text("x").startswith("beschäftigt"))
check("second press answers", q.toggle() == "verarbeite" and rec.stopped == 1)
check("pipeline back to idle", wait_idle(q))
check("transcribed question kept", q.items[0]["question"] == stt.result and not q.items[0]["typed"], q.items[0])
check("whisper got the names hint", stt.hints and "Maurice Becker" in stt.hints[0], stt.hints)
check("spoken answer from voice question", spoken == ["Becker wurde Erster."], spoken)

# 13) hold mode: start / stop; a mis-press too short to mean anything
clock = Clock()
rec = FakeRecorder()
q, api, spoken, _ = make([], recorder=rec, stt=FakeSTT(), clock=clock)
q.start_listen()
clock.t += 0.1
check("too short -> nothing", q.stop_listen() == "zu kurz" and q.state == "idle" and not api.calls)
check("stop without start", q.stop_listen().startswith("nicht am Zuhören"))
check("double start refused", q.start_listen() == "hört zu" and q.start_listen().startswith("beschäftigt"))
check("cancel while recording", q.cancel() == "abgebrochen (recording)" and q.state == "idle" and not q.items)

# 14) nothing understood
clock = Clock()
q, api, spoken, _ = make([], recorder=FakeRecorder(), stt=FakeSTT(""), clock=clock)
q.start_listen(); clock.t += 2; q.stop_listen()
check("empty transcription -> error, idle", wait_idle(q) and "Nichts verstanden" in q.last_error and not api.calls)

# 15) microphone failure is reported, not raised
class BrokenRec(FakeRecorder):
    def start(self):
        raise RuntimeError("Mikrofon 'Yeti' nicht gefunden")
q, *_ = make([], recorder=BrokenRec(), stt=FakeSTT())
r = q.start_listen()
check("broken mic -> message + idle", "Yeti" in r and q.state == "idle", r)

# 16) auto stop after qa_max_record_s
q, api, spoken, _ = make([text("Auto.")], cfg={"qa_max_record_s": 0.5}, recorder=FakeRecorder(), stt=FakeSTT())
q.start_listen()
time.sleep(0.7)
check("stops by itself", wait_idle(q) and spoken == ["Auto."], (q.state, spoken))

# 16b) 5010 /api/roundinfo is split: round_info = last round, league_table = table before it
split = {"info": {"lastRound": {"name": "R3"}}, "standings": {"standings": [{"rank": 1, "name": "Andreas Bastian"}],
                                                              "rounds": [{"n": 1}]}}
qs = qa.RaceQA(dict(CFG), session_fn=lambda: {}, fetch=lambda u: split)
ri, lt = qs.run_tool("round_info"), qs.run_tool("league_table")
check("round_info = last round only", "R3" in ri and "Bastian" not in ri, ri)
check("league_table = table, no duplicate rounds", "Bastian" in lt and "R3" not in lt and '"rounds"' not in lt, lt)

# 16c) any CAS league straight from CLS (5010 only knows one league — 2026-10-22:
#      "the WCT race at Indianapolis" was answered from PCCD data)
seen = []
def cls_fetch(url):
    seen.append(url)
    if "/api/overlay/leagues" in url:
        return {"ok": True, "leagues": [{"slug": "cas-gt3-wct", "name": "CAS GT3 WCT",
                                         "seasons": [{"id": "s14", "name": "GT3 WCT 14th Season"}]}]}
    if "/api/overlay/round-info" in url:
        return {"league": {"slug": "cas-gt3-wct"}, "lastRound": {"name": "Round 5",
                "track": "Indianapolis Motor Speedway", "races": [{"results": [
                    {"position": 1, "name": "Mike Zocher", "proAmClass": "PRO"}]}]}}
    if "/api/overlay/standings" in url:
        return {"standings": [{"rank": 1, "name": "Andreas Bastian", "points": 182}],
                "rounds": [{"number": 1}], "scoring": {"pointsTable": [35, 30]}}
    if "5010/api/roundinfo" in url:
        return {"info": {"league": {"slug": "cas-pccd"}}}
    raise OSError("nicht erreichbar")
qc = qa.RaceQA(dict(CFG), session_fn=lambda: {}, fetch=cls_fetch)
out = qc.run_tool("cls_leagues")
check("cls_leagues lists the leagues", "cas-gt3-wct" in out and seen[-1].endswith("/api/overlay/leagues"), seen[-1:])
out = qc.run_tool("round_info", {"liga": "cas-gt3-wct"})
check("round_info with liga asks CLS directly", seen[-1] == "https://league.simracing-hub.com/api/overlay/"
      "round-info?league=cas-gt3-wct" and "Mike Zocher" in out, seen[-1])
qc.run_tool("round_info", {"liga": "cas-gt3-wct", "runde": 4})
check("runde 4 = beforeRound 5", seen[-1].endswith("league=cas-gt3-wct&beforeRound=5"), seen[-1])
out = qc.run_tool("league_table", {"liga": "cas-gt3-wct", "runde": 3})
check("league_table with liga + runde -> CLS standings after that round",
      "/api/overlay/standings?league=cas-gt3-wct&beforeRound=4" in seen[-1] and "Bastian" in out
      and '"rounds"' not in out, (seen[-1], out[:120]))
qc.run_tool("league_table", {"runde": 2})
check("runde without liga = the league 5010 is on", "5010/api/roundinfo" in seen[-2]
      and seen[-1].endswith("standings?league=cas-pccd&beforeRound=3"), seen[-2:])
qn = qa.RaceQA(dict(CFG), session_fn=lambda: {}, fetch=lambda u: {"info": {}})
try:
    qn.run_tool("round_info", {"runde": 2})
    check("no league known -> error", False)
except ValueError as e:
    check("no league known -> error", "liga" in str(e))
seen.clear()
qc.run_tool("round_info", {})
check("no liga / runde = old path via 5010", seen == ["http://127.0.0.1:5010/api/roundinfo"], seen)
qo = qa.RaceQA(dict(CFG, qa_cls_url="http://cls.test/"), session_fn=lambda: {}, fetch=cls_fetch)
qo.run_tool("cls_leagues")
check("qa_cls_url override", seen[-1] == "http://cls.test/api/overlay/leagues", seen[-1])
tl = {t["name"]: t for t in qa.TOOLS}
check("round_info / league_table take liga + runde", all(
    set(tl[n]["input_schema"]["properties"]) == {"liga", "runde"} for n in ("round_info", "league_table")))
check("prompt explains several leagues", "cls_leagues" in qa.SYSTEM_PROMPT and "liga" in qa.SYSTEM_PROMPT)
# the whole loop: Claude asks for the WCT round, the answer is spoken
seen.clear()
api = FakeAPI([{"stop_reason": "tool_use", "content": [
                  {"type": "tool_use", "id": "a", "name": "cls_leagues", "input": {}}]},
               {"stop_reason": "tool_use", "content": [
                  {"type": "tool_use", "id": "b", "name": "round_info", "input": {"liga": "cas-gt3-wct"}}]},
               text("Im WCT-Rennen in Indianapolis hat Mike Zocher gewonnen.")])
spoken2 = []
ql = qa.RaceQA(dict(CFG), speak=lambda t: spoken2.append(t) or 1, session_fn=lambda: {},
               fetch=cls_fetch, api_post=api)
ql.ask_text("Wer hat das WCT-Rennen in Indianapolis gewonnen?", wait=True)
check("loop: leagues, then the WCT round from CLS", any("round-info?league=cas-gt3-wct" in u for u in seen)
      and ql.items[0]["tools"] == ["cls_leagues", "round_info"], (seen, ql.items[0]["tools"]))
check("loop: tool result carries the WCT winner", "Mike Zocher" in api.calls[2]["messages"][-1]["content"][0]["content"])
check("loop: answer spoken", spoken2 == ["Im WCT-Rennen in Indianapolis hat Mike Zocher gewonnen."], spoken2)

# 17) track notes tool (show_runner.find_notes) + live_session
with tempfile.TemporaryDirectory() as d:
    Path(d, "indianapolis-notes.md").write_text("Die F1 fuhr hier von 2000 bis 2007.", encoding="utf-8")
    q, *_ = make([], notes_dir=Path(d))
    check("track notes found by track word", "2007" in q.run_tool("track_notes"))
    q2, *_ = make([], notes_dir=Path(d) / "nope")
    check("no notes -> says so", "Keine Notizen" in q2.run_tool("track_notes"))
check("live_session tool", "Indianapolis" in q.run_tool("live_session"))
try:
    q.run_tool("rm_rf")
    check("unknown tool raises", False)
except ValueError:
    check("unknown tool raises", True)

# 18) with the real Commentator: the answer becomes a queued voice line
com = Commentator(random.Random(1), {"mode": "template", "api_key": ""})
heard = []
com.on_line = lambda ln: heard.append(ln)
q = qa.RaceQA(dict(CFG), speak=lambda t: com.say_show(t, "frage"), session_fn=lambda: dict(SESSION),
              fetch=lambda u: ROUNDINFO, api_post=FakeAPI([text("Remo Cursio wurde Zweiter.")]))
q.ask_text("Und Cursio?", wait=True)
ln = com.lines[0]
check("answer is a queued show line (played in full)", ln["text"] == "Remo Cursio wurde Zweiter." and ln.get("queue")
      and ln.get("segment") == "frage", ln)
check("voice callback got it", heard and heard[0]["id"] == ln["id"] == q.items[0]["line_id"])

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
