# Track map coverage — gap list (2026-06-04)

The trackmap overlay loads `tracks/<TrackName>.json`, where TrackName is
iRacing's internal slug from `WeekendInfo.TrackName` (spaces → `_`).
Bundled: 205 SIMRacingApps tracks (frozen Sept 2024) + Monza (hand-drawn)
+ the OSM-built tracks below. iRacing has ~140 facilities / 400+ configs.

## Added 2026-06-04 (OSM workflow, slug confirmed from race logs)

| File | Length check | Source |
|------|--------------|--------|
| okayama_full.json | 3704 m vs 3703 m | OSM |
| phillipisland.json (+ _2019 copy) | 4459 m vs 4448 m | OSM |
| thruxton.json | 3771 m vs 3790 m | OSM |
| brandshatch_grandprix.json | 3900 m vs 3908 m | OSM |
| miami_gp.json | 5416 m vs 5412 m | OSM |
| zandvoort_2023_* (5 configs) | — | copies of old-scan files (same circuit) |

## NOT buildable from OSM → hand-draw in gpx.studio + gpx_to_json.py

- **stpete** (slug confirmed) — temporary street circuit, roads not
  tagged as raceway in OSM
- **adelaide** (slug confirmed) — parklands street circuit, only a stub
  in OSM
- Chicago Street Course — temporary street circuit (check whether the
  bundled `chicago.json` is actually this track at the first session)
- iRacing Superspeedway, Centripetal Circuit — fictional, no real-world
  geometry anywhere
- Oran Park — demolished (housing estate), gone from OSM

## Buildable from OSM, slug UNCONFIRMED (build on demand)

Road: Sebring, Road America, Laguna Seca, Mid-Ohio, Road Atlanta,
Barber, Sonoma (GP/Cup configs), Canadian Tire Motorsport Park,
Bathurst/Mount Panorama, Interlagos, Mexico City, Circuit
Gilles-Villeneuve, Motegi, Donington, Oulton Park, Zolder, The Bend,
Portland, Brands Hatch Indy (geometry already cached from the GP build).

Ovals: Daytona, Talladega, Texas, Bristol, Martinsville, Richmond,
Dover, Kansas, Michigan, Las Vegas, Homestead, Darlington, Auto Club,
Kentucky, Chicagoland, New Hampshire, Milwaukee, Gateway, Rockingham,
Indianapolis oval, Atlanta/Echo Park, plus assorted short ovals.

Dirt ovals: mostly unmapped or too small in OSM — case by case.

## Workflow for a newly scheduled track

1. Tell Claude the track → OSM extraction via Claude-in-Chrome
   (Overpass API; sandbox web_fetch can NOT reach OSM services),
   segment stitching, pit-midpoint S/F placement, length sanity check,
   rendered preview. ~3 minutes per circuit.
2. The filename needs iRacing's slug: if unknown, the trackmap console
   prints the filename it looks for on first load — rename the JSON
   (or save under 2-3 plausible names like phillipisland did).
3. Street circuits: hand-trace in https://gpx.studio/ (start at S/F,
   draw in driving direction), then `python gpx_to_json.py <file> <slug>`.

Slugs follow iRacing's content folders, with rescans getting year
infixes: `silverstone 2019 gp`, `spa 2024 up`, `zandvoort 2023 gp`.
Every raced track's exact slug is recorded in `logs/*.jsonl`
(session_start → `track_name`).
