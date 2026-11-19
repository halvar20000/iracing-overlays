"""Offline checks for stream_countdown.py (no network): python3 test_countdown.py"""
import json
import stream_countdown as sc

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok += bool(cond); fail += not cond

ICAL = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
DTSTART:20261117T183000Z\r
DTEND:20261117T213000Z\r
SUMMARY:CAS GT3 WCT Fuji\\, live\r
RRULE:FREQ=WEEKLY;INTERVAL=1\r
CATEGORIES:iRacing\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;TZID=Europe/Berlin:20261119T193000\r
SUMMARY:PCC VIR\r
END:VEVENT\r
END:VCALENDAR\r
"""
now = sc._iso("2026-11-16T12:00:00Z")
ev = sc.ical_events(ICAL, now=now)
t0 = sc._iso("2026-11-17T18:30:00Z")
check("ical: first event in UTC", ev[0]["t"] == t0 and ev[0]["title"] == "CAS GT3 WCT Fuji, live")
check("ical: weekly rule expanded", any(e["t"] == t0 + 7 * 86400 for e in ev) and len(ev) > 5)
check("ical: TZID local time", any(e["t"] == sc._iso("2026-11-19T18:30:00Z") for e in ev))
ev2 = sc.ical_events("BEGIN:VEVENT\r\nDTSTART;TZID=/Europe/Berlin:20261119T194000\r\nSUMMARY:x\r\nEND:VEVENT\r\n")
check("ical: Twitch's TZID=/Europe/Berlin (leading slash) = Berlin, not UTC", ev2 and ev2[0]["t"] == sc._iso("2026-11-19T18:40:00Z"))

RI = {"league": {"slug": "cas-gt3-wct", "name": "CAS GT3 WCT"},
      "rounds": [{"number": 8, "status": "COMPLETED", "startsAt": "2026-11-10T18:00:00.000Z", "track": "Spa"},
                 {"number": 9, "status": "UPCOMING", "startsAt": "2026-11-17T18:00:00.000Z",
                  "track": "Fuji International Speedway"},
                 {"number": 10, "status": "UPCOMING", "startsAt": None, "track": "Misano"}]}
ce = sc.cls_events("cas-gt3-wct", RI)
check("cls: only upcoming rounds with a date", [e["round"] for e in ce] == [9])
check("cls: WCT counts to the 19:00 stream start (CET)", ce[0]["t"] == sc._iso("2026-11-17T18:00:00Z")
      and ce[0]["race_t"] == sc._iso("2026-11-17T18:00:00Z"))
from zoneinfo import ZoneInfo
B = ZoneInfo("Europe/Berlin")
race = sc._iso("2026-11-19T19:00:00Z")                       # PCC: race 20:00 CET
check("stream_time: PCC 19:40 CET", sc.stream_time(race, "cas-pccd", tz=B) == sc._iso("2026-11-19T18:40:00Z"))
check("stream_time: IEC 17:00 CET", sc.stream_time(sc._iso("2026-11-28T16:00:00Z"), "cas-iec", tz=B)
      == sc._iso("2026-11-28T16:00:00Z"))
check("stream_time: summer time (CEST)", sc.stream_time(sc._iso("2027-06-10T18:00:00Z"), "cas-sfl-cup", tz=B)
      == sc._iso("2027-06-10T17:40:00Z"))
check("stream_time: league without entry = race start", sc.stream_time(race, "cas-fun-league", tz=B) == race)
check("cls: title", ce[0]["title"] == "CAS GT3 WCT · LAUF 9 · FUJI INTERNATIONAL SPEEDWAY")

pages = {
    "https://x/api/overlay/leagues": json.dumps({"leagues": [{"slug": "cas-gt3-wct"}, {"slug": "cas-iec"}]}),
    "https://x/api/overlay/round-info?league=cas-gt3-wct": json.dumps(RI),
    "https://decapi.me/twitch/id/chan": "583034073",
    "https://api.twitch.tv/helix/schedule/icalendar?broadcaster_id=583034073": ICAL,
}
def getter(url):
    if url not in pages:
        raise OSError("404 " + url)
    return pages[url]
cal = sc.Calendar(channel="chan", cls_url="https://x", getter=getter)
cal.fetch()
evs = cal.events(now=now)
check("calendar: both sources, sorted", {e["source"] for e in evs} == {"cls", "twitch"}
      and [e["t"] for e in evs] == sorted(e["t"] for e in evs))
check("calendar: a failing league is reported, others kept", "cls:cas-iec" in cal.errors and evs)
p = sc.pick(evs, now)
check("pick: next = WCT round 9 (stream 19:00 CET = 18:00Z)", p["round"] == 9 and not p["live"])
p = sc.pick(evs, now, offset_s=-30 * 60)
check("pick: offset -30 min", p["target"] == sc._iso("2026-11-17T17:30:00Z"))
p = sc.pick(evs, sc._iso("2026-11-17T18:10:00Z"), source="cls")
check("pick: 10 min after the start = live (end text)", p["round"] == 9 and p["live"])
p = sc.pick(evs, sc._iso("2026-11-17T18:40:00Z"), source="cls")
check("pick: after the live window -> next event (none left in cls)", p is None)
p = sc.pick(evs, now, source="twitch")
check("pick: source twitch", p["source"] == "twitch" and p["t"] == t0)
p = sc.pick(evs, now, league="cas-iec")
check("pick: league filter without events = None", p is None)
pages.pop("https://x/api/overlay/leagues")
cal.fetch()
check("calendar: CLS down keeps its last CLS events and reports it", "cls" in cal.errors
      and {e["source"] for e in cal.events(now=now)} == {"cls", "twitch"} and len(cal.events(now=now)) == len(evs))
print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
