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

_LOGTMP = tempfile.mkdtemp(prefix="qa_log_")
qa.LOG_DIR = Path(_LOGTMP)   # never write into the real logs/
from race_commentator import Commentator

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


def wait_ready(q, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if q.state == "ready":
            return True
        time.sleep(0.02)
    return False


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


# qa_hold off: these checks are about the answer itself, spoken at once (hold: 19)
CFG = {"api_key": "k", "qa_model": "claude-haiku-5-5", "qa_web_search": True, "qa_max_record_s": 20,
       "qa_hold": False}


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

# 19) HOLD (default): the answer waits as "ready", the streamer plays it when it suits him
check("hold is the default", qa.DEFAULTS["qa_hold"] is True)
clock = Clock()
rec = FakeRecorder()
done = set()
qh, api, spoken, _ = make([text("Mike Zocher hat gewonnen."), text("Zweite Antwort.")],
                          cfg={"qa_hold": True}, recorder=rec, stt=FakeSTT("Wer hat gewonnen?"),
                          clock=clock)
qh.is_done = lambda lid: lid in done
qh.toggle(); clock.t += 2; qh.toggle()
check("hold: answer waits instead of being spoken", wait_ready(qh) and spoken == [], (qh.state, spoken))
st = qh.status()
check("hold: status ready + pending answer", st["state"] == "ready" and st["pending"]
      and st["pending"]["answer"] == "Mike Zocher hat gewonnen." and st["hold"] is True, st["pending"])
clock.t += 4
check("hold: waiting seconds counted", qh.status()["pending"]["waiting_s"] == 4.0, qh.status()["pending"])
check("hold: a new question is refused while one waits", qh.start_listen().startswith("Antwort wartet")
      and qh.ask_text("x").startswith("Antwort wartet") and rec.started == 1)
check("hold: same key plays it", qh.toggle() == "spielt ab" and spoken == ["Mike Zocher hat gewonnen."], spoken)
st = qh.status()
check("hold: speaking until the voice page played it", st["state"] == "speaking" and st["pending"] is None, st["state"])
check("hold: line id + spoken flag on the item", qh.items[0]["line_id"] == 1 and qh.items[0].get("spoken"))
done.add(1)
check("hold: idle once played", qh.status()["state"] == "idle")
check("hold: play with nothing waiting", qh.play().startswith("keine Antwort bereit"))
# cancel drops the waiting answer, nothing spoken
qh.ask_text("Noch eine?", wait=True)
check("hold: typed question waits too", qh.state == "ready")
check("hold: cancel drops it", qh.cancel() == "Antwort verworfen" and qh.state == "idle"
      and qh.items[0].get("discarded") and spoken == ["Mike Zocher hat gewonnen."], spoken)
# an answer nobody plays is dropped after qa_hold_max_s
qe, api, spoken3, _ = make([text("Alt.")], cfg={"qa_hold": True, "qa_hold_max_s": 60}, clock=clock)
qe.ask_text("x", wait=True)
clock.t += 61
check("hold: unplayed answer expires", qe.status()["state"] == "idle" and qe.items[0].get("expired")
      and qe.play().startswith("keine Antwort bereit") and spoken3 == [])
# speaking falls back to a timeout without a voice page report
qs, api, spoken4, _ = make([text("Kurz.")], cfg={"qa_hold": True}, clock=clock)
qs.is_done = lambda lid: False
qs.ask_text("x", wait=True); qs.play()
clock.t += qa.SPEAKING_MAX_S + 1
check("hold: speaking ends after SPEAKING_MAX_S", qs.status()["state"] == "idle")
# errors are not held
qx, api, spoken5, _ = make([RuntimeError("HTTP 529: overloaded")], cfg={"qa_hold": True}, clock=clock)
qx.ask_text("x", wait=True)
check("hold: an error does not wait", qx.state == "idle" and qx.status()["pending"] is None)

# 20) the Hungaroring stream (22.10.): what went wrong live
EN_PLAN = ("The user asks about Formel 1 Rennen in Ungarn this year. The context is the CAS Porsche "
           "Cup league, not F1. The track notes contain F1 history, but not this year's F1 result. "
           "The question is about a real world F1 race result in 2026 which I need to search.")
DE_ANSWER = "Das Formel-1-Rennen in Ungarn hat in diesem Jahr Lando Norris gewonnen."
search_reply = {"stop_reason": "end_turn", "content": [
    {"type": "text", "text": EN_PLAN},
    {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "F1 Ungarn 2026 Sieger"}},
    {"type": "web_search_tool_result", "tool_use_id": "s1", "content": [{"type": "web_search_result", "title": "x"}]},
    {"type": "text", "text": "Das Formel-1-Rennen in Ungarn hat in diesem Jahr "},
    {"type": "text", "text": "Lando Norris gewonnen."}]}
check("final_text: only the text after the last tool block",
      qa.final_text(search_reply["content"]) == DE_ANSWER, qa.final_text(search_reply["content"]))
check("final_text: no tool block = all text", qa.final_text([{"type": "text", "text": "Hallo."}]) == "Hallo.")
check("final_text: tool block last = no answer", qa.final_text(search_reply["content"][:3]) == "")
check("looks_english: the live plan is English", qa.looks_english(EN_PLAN))
for de in (DE_ANSWER, "Die Meisterschaft CAS PCC zeigt vor dem laufenden Rennen André Brechmann auf Rang 3 "
                      "mit 208 Punkten und Jan Tobias Schrader auf Rang 4 mit 206 Punkten.",
           "Norris hat gewonnen.", "Das Rennen in The Bend hat Mike Zocher gewonnen.",
           "Das habe ich leider nicht in meinen Daten."):
    check(f"looks_english: German stays German ({de[:30]})", not qa.looks_english(de))
api = FakeAPI([search_reply])
spoken6 = []
qf = qa.RaceQA(dict(CFG), speak=lambda t: spoken6.append(t) or 1, session_fn=lambda: {},
               fetch=lambda u: {}, api_post=api)
qf.ask_text("Gabi, wer hat dieses Jahr das Formel-1-Rennen in Ungarn gewonnen?", wait=True)
check("F1 question: only the German answer is spoken", spoken6 == [DE_ANSWER], spoken6)
check("F1 question: no English plan in the answer", "user asks" not in qf.items[0]["answer"])
check("F1 question: web search listed, no extra call", qf.items[0]["tools"] == ["web_search"]
      and len(api.calls) == 1, (qf.items[0]["tools"], len(api.calls)))
# an answer that comes back English is rewritten once, without tools
api = FakeAPI([text("Lando Norris won the Hungarian Grand Prix this year, and it was his third win of the season."),
               text("Lando Norris hat in diesem Jahr den Großen Preis von Ungarn gewonnen.")])
spoken7 = []
qg = qa.RaceQA(dict(CFG), speak=lambda t: spoken7.append(t) or 1, session_fn=lambda: {},
               fetch=lambda u: {}, api_post=api)
qg.ask_text("Wer hat in Ungarn gewonnen?", wait=True)
check("English answer -> German rewrite spoken", spoken7 == ["Lando Norris hat in diesem Jahr den Großen Preis von Ungarn gewonnen."], spoken7)
check("rewrite call: German prompt, no tools", api.calls[1]["system"] == qa.GERMAN_PROMPT
      and "tools" not in api.calls[1] and api.calls[1].get("thinking") == {"type": "disabled"}, api.calls[1].keys())
check("rewrite marked in the sources", "übersetzt" in qg.items[0]["tools"])
api = FakeAPI([text("Lando Norris won the Hungarian Grand Prix this year, and it was his third win of the season."),
               RuntimeError("HTTP 529: overloaded")])
qh2 = qa.RaceQA(dict(CFG), speak=lambda t: 1, session_fn=lambda: {}, fetch=lambda u: {}, api_post=api)
qh2.ask_text("x", wait=True)
check("rewrite fails -> original kept, no error", qh2.items[0]["answer"].startswith("Lando Norris won")
      and not qh2.items[0]["error"], qh2.items[0])
# the prompt carries the three live lessons
P_ = qa.SYSTEM_PROMPT
check("prompt: always German, translate sources", "IMMER auf Deutsch" in P_ and "übersetze" in P_)
check("prompt: no text before tools / no explaining", "KEINEN Text" in P_ and "zuordnest" in P_)
check("prompt: never just the driver on camera", "NIE einfach den Fahrer im Bild" in P_)
check("prompt: Tabelle = championship, not the live order", "Meisterschaftstabelle" in P_
      and "NICHT die Reihenfolge im laufenden Rennen" in P_)
check("prompt: old 'closest driver' line gone", "ordne sie dem ähnlichsten Fahrer aus den Daten zu" not in P_)

# 21) log of every question: answer / played / discarded, one file per day
import json as _json, shutil
logdir = Path(tempfile.mkdtemp(prefix="qa_log_t_"))
clock = Clock()
ql2 = qa.RaceQA(dict(CFG, qa_hold=True), speak=lambda t: 7, session_fn=lambda: {}, fetch=lambda u: ROUNDINFO,
                api_post=FakeAPI([tool_use("round_info"), text("Becker gewann."), text("Zweite.")]),
                clock=clock, log_dir=logdir)
ql2.ask_text("Wer gewann?", wait=True)
ql2.play()
ql2.ask_text("Und dann?", wait=True)
ql2.cancel()
files = list(logdir.glob("frag_claude_*.jsonl"))
recs = [_json.loads(l) for f in files for l in f.read_text(encoding="utf-8").splitlines()]
check("log: one file per day", len(files) == 1 and files[0].name.startswith("frag_claude_20"), files)
check("log: answer, played, answer, discarded", [r["event"] for r in recs] == ["answer", "played", "answer", "discarded"],
      [r["event"] for r in recs])
check("log: question, answer, sources, seconds, model", recs[0]["question"] == "Wer gewann?"
      and recs[0]["answer"] == "Becker gewann." and recs[0]["tools"] == ["round_info"]
      and "took_s" in recs[0] and recs[0]["model"] == "claude-haiku-5-5" and "t_wall" in recs[0], recs[0])
qe2 = qa.RaceQA(dict(CFG, qa_hold=True, qa_hold_max_s=10), speak=lambda t: 1, session_fn=lambda: {},
                fetch=lambda u: {}, api_post=FakeAPI([text("Alt.")]), clock=clock, log_dir=logdir)
qe2.ask_text("x", wait=True); clock.t += 11; qe2.status()
recs = [_json.loads(l) for f in logdir.glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
check("log: expired answer logged", recs[-1]["event"] == "expired", recs[-1])
qx2 = qa.RaceQA(dict(CFG), speak=lambda t: 1, session_fn=lambda: {}, fetch=lambda u: {},
                api_post=FakeAPI([RuntimeError("HTTP 529: overloaded")]), log_dir=logdir)
qx2.ask_text("x", wait=True)
recs = [_json.loads(l) for f in logdir.glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
check("log: a failed question keeps its error", recs[-1]["event"] == "answer" and "529" in recs[-1]["error"], recs[-1])
off = Path(tempfile.mkdtemp(prefix="qa_log_off_"))
qo2 = qa.RaceQA(dict(CFG, qa_log=False), speak=lambda t: 1, session_fn=lambda: {}, fetch=lambda u: {},
                api_post=FakeAPI([text("Ja.")]), log_dir=off)
qo2.ask_text("x", wait=True)
check("log: qa_log false writes nothing", not list(off.glob("*")))
bad = logdir / "frag_claude_blocker"
bad.write_text("x")
qb = qa.RaceQA(dict(CFG), speak=lambda t: 1, session_fn=lambda: {}, fetch=lambda u: {},
               api_post=FakeAPI([text("Ja.")]), log_dir=bad / "sub")
qb.ask_text("x", wait=True)
check("log: an unwritable folder never stops the answer", qb.items[0]["answer"] == "Ja." and qb.state == "idle")
check("tests never touch the real logs/", qa.LOG_DIR != qa.HERE / "logs")
shutil.rmtree(logdir, ignore_errors=True); shutil.rmtree(off, ignore_errors=True)

print()
print("ALL PASS" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
