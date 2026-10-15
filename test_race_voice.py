"""Offline checks for race_voice.py — fake engines, no network.
Run: python test_race_voice.py          (--live also asks the real edge-tts service)
"""
import sys
import time

import race_voice as rv

fails = 0


def check(name, ok, extra=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        fails += 1


def wait_for(cond, secs=2.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


calls = []


def edge_ok(text, voice, rate):
    calls.append(("edge", text, voice, rate))
    return b"MP3" + text.encode(), "audio/mpeg"


def edge_bad(text, voice, rate):
    calls.append(("edge", text))
    raise OSError("no internet")


def sapi_ok(text, voice, rate):
    calls.append(("sapi", text, voice, rate))
    return b"WAV" + text.encode(), "audio/wav"


# 1) nothing is made while no voice page listens
v = rv.Voice({"tts": "auto"}, {"edge": edge_ok, "sapi": sapi_ok})
v.say(1, "Hallo")
time.sleep(0.15)
check("no listener -> no audio", not v.has(1) and not calls)

# 2) listener -> edge clip with the default voice + rate
v.listening()
v.say(2, "Becker führt.")
check("clip made", wait_for(lambda: v.has(2)))
check("edge used with default voice", calls and calls[-1][0] == "edge" and calls[-1][2] == rv.DEFAULT_VOICE
      and calls[-1][3] == rv.DEFAULT_RATE, calls[-1:])
check("get returns bytes + mime", v.get(2) == (b"MP3Becker f\xc3\xbchrt.", "audio/mpeg"))
check("status engine edge", v.status()["engine"] == "edge" and v.status()["listening"])

# 3) edge fails -> SAPI, and SAPI goes first for a while afterwards
calls.clear()
v2 = rv.Voice({"tts": "auto", "sapi_voice": "Hedda"}, {"edge": edge_bad, "sapi": sapi_ok})
v2.listening()
v2.say(1, "Eins")
check("fallback to sapi", wait_for(lambda: v2.has(1)) and v2.get(1)[1] == "audio/wav")
check("sapi got the configured voice", calls[-1][0] == "sapi" and calls[-1][2] == "Hedda", calls)
n_edge = sum(1 for c in calls if c[0] == "edge")
v2.say(2, "Zwei")
wait_for(lambda: v2.has(2))
check("edge not retried within EDGE_RETRY_S", sum(1 for c in calls if c[0] == "edge") == n_edge, calls)
check("order after failure", v2._order(time.time()) == ["sapi", "edge"]
      and v2._order(time.time() + rv.EDGE_RETRY_S + 1) == ["edge", "sapi"])

# 4) all engines fail -> error visible, no clip
v3 = rv.Voice({"tts": "edge"}, {"edge": edge_bad, "sapi": sapi_ok})
v3.listening()
v3.say(1, "x")
check("edge-only failure reported", wait_for(lambda: "no internet" in v3.status()["last_error"]) and not v3.has(1))

# 5) mode off
v4 = rv.Voice({"tts": "off"}, {"edge": edge_ok, "sapi": sapi_ok})
v4.listening()
calls.clear()
v4.say(1, "x")
time.sleep(0.1)
check("tts off -> nothing", not calls and not v4.has(1))

# 6) backlog: only the newest line is spoken
slow_calls = []


def edge_slow(text, voice, rate):
    slow_calls.append(text)
    time.sleep(0.2)
    return b"x", "audio/mpeg"


v5 = rv.Voice({"tts": "edge"}, {"edge": edge_slow})
v5.listening()
v5.say(1, "eins")
time.sleep(0.05)
for i in range(2, 6):
    v5.say(i, f"line{i}")
wait_for(lambda: v5.has(5), 3)
check("backlog collapsed to the newest", slow_calls == ["eins", "line5"], slow_calls)

# 7) a line that waited too long is dropped
v6 = rv.Voice({"tts": "edge"}, {"edge": edge_ok})
v6.listening(now=time.time())
calls.clear()
v6.say(1, "alt", now=time.time() - rv.MAX_AGE_S - 1)
v6._listen_until = time.time() + 10
time.sleep(0.15)
check("stale line not spoken", not v6.has(1))

# 8) clip store bounded
v7 = rv.Voice({"tts": "edge"}, {"edge": edge_ok})
for i in range(rv.KEEP_CLIPS + 5):
    data, mime, _ = v7.synth(f"s{i}")
    v7._clips[i] = (data, mime, "edge")
    while len(v7._clips) > rv.KEEP_CLIPS:
        v7._clips.popitem(last=False)
check("keeps the last clips", len(v7._clips) == rv.KEEP_CLIPS and v7.has(rv.KEEP_CLIPS + 4) and not v7.has(0))

# 9) SAPI rate mapping
check("sapi rate", rv._sapi_rate("+10%") == 1 and rv._sapi_rate("-30%") == -3 and rv._sapi_rate("x") == 0
      and rv._sapi_rate("+200%") == 10)

# 10) the commentator hands every published line to the voice
import random
import race_commentator as rc
got = []
c = rc.Commentator(random.Random(1), {"mode": "template", "api_key": ""})
c.on_line = got.append
c.feed([{"t": 1, "kind": "leader", "car_idx": 0, "name": "Maurice Becker", "info": {"position": 1}}], now=1.0)
check("on_line called with id + text", got and got[0]["id"] == 1 and "Becker" in got[0]["text"], got)

# 11) keep lines: all made, in order, even with commentary in between
order = []
def edge_rec(text, voice, rate):
    order.append(text)
    time.sleep(0.05)
    return b"x", "audio/mpeg"
v8 = rv.Voice({"tts": "edge"}, {"edge": edge_rec})
v8.listening()
v8.say(1, "warm")
time.sleep(0.01)
v8.say(2, "m1", keep=True)
v8.say(3, "c1")
v8.say(4, "m2", keep=True)
v8.say(5, "c2")
v8.say(6, "m3", keep=True)
wait_for(lambda: all(v8.has(i) for i in (2, 4, 6)), 3)
check("moderation lines all made, in order", [t for t in order if t.startswith("m")] == ["m1", "m2", "m3"], order)
check("commentary between them collapsed", "c1" not in order and "c2" in order, order)
check("requested ids tracked", {2, 4, 6} <= v8.requested)
v9 = rv.Voice({"tts": "edge"}, {"edge": edge_rec})
v9.say(7, "nobody listens", keep=True)
check("not requested while nobody listens", 7 not in v9.requested)
check("speakable lap time", rv.speakable("Pole mit 1:39,512!") == "Pole mit 1 39 Komma 5 1 2!"
      and rv.speakable("0,44 Sekunden") == "0 Komma 4 4 Sekunden")
check("speakable classes", rv.speakable("Die AM-Klasse, am Ende PRO und AM.") == "Die A M-Klasse, am Ende Pro und A M.",
      rv.speakable("Die AM-Klasse, am Ende PRO und AM."))

if "--live" in sys.argv:
    t = time.time()
    data, mime = rv._edge_synth("Mike Zocher lauert auf Dennis Richter.", rv.DEFAULT_VOICE, rv.DEFAULT_RATE)
    check(f"live edge-tts ({time.time() - t:.1f} s, {len(data)} bytes)", len(data) > 5000 and mime == "audio/mpeg")

print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
raise SystemExit(1 if fails else 0)
