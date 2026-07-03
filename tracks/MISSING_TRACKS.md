# Track map coverage — gap list (2026-06-04)

The trackmap overlay loads `tracks/<TrackName>.json`, where TrackName is
iRacing's internal slug from `WeekendInfo.TrackName` (spaces → `_`).
Bundled: 205 SIMRacingApps tracks (frozen Sept 2024) + Monza (hand-drawn)
+ the OSM-built tracks below. iRacing has ~140 facilities / 400+ configs.

## Added 2026-06-10 (iRacing 2026 S3 new content)

| File | Length check | Source |
|------|--------------|--------|
| coronado.json (+ qualcomm, qualcommcircuit slug variants) | 5466 m vs 5472 m (3.4 mi) | iRacing track-map SVG asset (NOT in OSM — temporary street circuit) |
| lagunaseca_2026.json (+ _2026_full, lagunaseca2026 variants) | copy of SRA lagunaseca_full | same circuit, 2026 rescan slug unconfirmed |

NEW METHOD for tracks missing from OSM: iRacing's own track-map assets.
From a logged-in members-ng browser session fetch
`/bff/pub/proxy/data/track/assets` (the bare members-ng `/data/...` API
rejects cookies — only the `/bff/pub/proxy/` path works from the web
app), take `track_map` + `active.svg` (two closed subpaths = ribbon
edges; sample ONE edge), `start-finish.svg` (rect = S/F line, polygon =
direction arrow), rescale to the official length. Exact sim geometry,
exact S/F, no OSM stitching. Slug still needs console confirmation.

## Added 2026-06-11 (slug confirmed live by the corner-cue overlay)

| File | Length check | Source |
|------|--------------|--------|
| watkinsglen_cupcircuit.json | 3924 m vs 3943 m (2.45 mi) | derived offline from watkinsglen_2021_fullcourse.json — boot section replaced by the short-course chute (tangent 250 m arc off the carousel + straight, tangent rejoin; cut chosen so total length matches official). Corner check: 90/esses/inner-loop R-L-R/carousel 137°/off-camber L/final R, all match. Preview in _previews/. NEW METHOD for alt configs of already-bundled facilities: cut + splice the existing loop, no OSM/browser needed. |

## Added 2026-06-04 (OSM workflow, slug confirmed from race logs)

| File | Length check | Source |
|------|--------------|--------|
| okayama_full.json | 3704 m vs 3703 m | OSM |
| phillipisland.json (+ _2019 copy) | 4459 m vs 4448 m | OSM |
| thruxton.json | 3771 m vs 3790 m | OSM |
| brandshatch_grandprix.json | 3900 m vs 3908 m | OSM |
| miami_gp.json | 5416 m vs 5412 m | OSM |
| zandvoort_2023_* (5 configs) | — | copies of old-scan files (same circuit) |

## Added 2026-06-05

| File | Length check | Source |
|------|--------------|--------|
| stpete.json (+ stpete.gpx) | 2903 m vs 2897 m | OSM relation 8668325 (ways tagged `disused:highway=raceway` — the 06-04 raceway-tag search missed them; pit lane included, S/F via pit-midpoint projection, direction verified against official track map — OSM "forward" roles are REVERSED there) |

## Added 2026-06-05 evening (BULK OSM BUILD — 8 parallel agents, 36 facilities)

All slugs UNCONFIRMED unless noted — each facility saved under 2-3
plausible names; delete whichever the trackmap console doesn't ask for.
Previews in `_previews/`. Lengths verified (±2% road / ±5% oval),
directions verified via shoelace + pit-lane flow. Full per-track build
reports archived in the session outputs (report_road_a-d, report_ovals_a-d).

Road: sebring_international+sebring, roadamerica_full+roadamerica,
lagunaseca_full+lagunaseca (NO pit in JSON — OSM "Pit Lane" way is
mis-tagged; S/F via front-straight hint), midohio_full+midohio,
roadatlanta_full+roadatlanta (raw OSM order was REVERSED),
barber_full+barber, sonoma_gp+sonoma_full, mosport+mosport_gp,
bathurst+mountpanorama (anti-clockwise), interlagos_gp+interlagos+
interlagos_grandprix (S/F via hint — OSM pit way includes long
entry/exit roads), mexico_gp+mexicocity_gp+hermanosrodriguez_gp (incl.
Foro Sol), montreal+montreal_gp+gillesvilleneuve, motegi_gp+
motegi_grandprix+twinring_gp, motegi_east, donington_gp+
donington_grandprix, donington_national, oulton_international+
oultonpark_international, oulton_island, zolder_gp+zolder_grandprix+
zolder, thebend_international+thebend_intl+thebend,
thebend_gt+thebend_gtcircuit, portland+portland_full (CLOCKWISE —
Wikipedia confirms; gap-list assumption of CCW was wrong),
brandshatch_indy, **adelaide** (slug confirmed; type=circuit relation
3121459 — the 06-04 "stub" classification was wrong),
**chicago_street** (relation 16546690; relation roles reversed vs
driving direction, St. Pete pattern).

Ovals (all counterclockwise): daytona_oval+daytona_2011_oval,
talladega+talladega_oval, texas_oval+texas, bristol_oval+bristol+
bristol_fullpit (S/F hint on the WEST straight — if the dot looks
wrong on stream, flip sf_hint to the east-straight midpoint and
rebuild), martinsville+martinsville_oval, richmond+richmond_oval,
dover+dover_oval, kansas_oval+kansas, michigan+michigan_oval,
lasvegas_oval+lasvegas, homestead_oval+homestead, darlington+
darlington_oval, kentucky_oval+kentucky, chicagoland+chicagoland_oval,
newhampshire_oval+loudon_oval+newhampshire, milwaukee+milwaukee_oval+
themilwaukeemile, gateway_oval+gateway, rockingham_oval+rockingham
(NC "The Rock" — if iRacing's bare `rockingham` slug is the UK track,
delete rockingham.json and build UK separately),
indianapolis_oval+indy_oval (S/F = OSM Yard-of-Bricks way, exact),
atlanta_oval+atlanta (post-2022 reprofile).

## NOT buildable from OSM → hand-draw in gpx.studio + gpx_to_json.py

- Auto Club Speedway / Fontana — demolished 2023, DELETED from OSM
  (exhaustive lifecycle-tag search found only stubs); needs OSM
  history data or hand-drawing
- Motegi superspeedway oval — demolished ~2021, deleted from OSM
- Sonoma Cup/NASCAR config — the chute connector is not mapped (GP
  loop built fine)
- Sebring Club/Modified, Road Atlanta Short, Mid-Ohio Short/Chicane —
  alt-config link roads are unconnected stubs in OSM
- iRacing Superspeedway, Centripetal Circuit — fictional, no
  real-world geometry anywhere
- Oran Park GP — DONE (tracks/oran_gp.json). Demolished IRL & gone from
  OSM, so built from iRacing's OWN active.svg track-map asset (pulled from
  a logged-in members-ng session via the browser console: /bff/pub/proxy/
  data/track/{get,assets}). Centerline = midpoint of the ribbon's two
  edges, rotated to S/F + direction from start-finish.svg, scaled to the
  official 2.6385 km. This iRacing-SVG method works for ANY demolished/
  unmapped track you own.

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
