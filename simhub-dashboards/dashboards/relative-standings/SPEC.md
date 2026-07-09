# Dashboard spec — Relative & Standings

Build in Dash Studio against a live/AI iRacing session.

- **Landscape** (tablet / 2nd monitor): 1024 × 600.
- **DDU** (USB display): 800 × 480 (relative box needs the height; skip 480×272 here).

Original design — dark carbon `#0B0D10`, class-colour left edge per row (from
`ProDash.Rel.{n}.ClassColor`), player row highlighted amber `#FFB020`. No third-party UI copied.

## Zones (landscape)

1. **Header**: track + session, `ProDash.Field.SoF` (big), `ProDash.Field.AvgIRating`,
   `ProDash.Field.CarCount`.
2. **Relative box** (centre, the pro core): 7 rows (`ProDash.Rel.1..7`), player row 4 pinned
   and highlighted. Each row shows: `Position` · `CarNumber` · `Name` · `IRating` ·
   `License` · `Gap` (green if ahead/+, red if behind/−) · pit chip if `InPit`.
   Hide a row when `Rel.{n}.Valid` is false. Left edge tinted by `ClassColor`.
3. **Proximity strip** (bottom or side): `ProDash.Prox.State` big, left/right arrows lit by
   `CarLeft` / `CarRight`, `AheadGap` / `BehindGap` numerics.

## DDU variant (800 × 480)

5 rows (2 ahead / player / 2 behind — or keep 7 if legible), Position · # · Name(abbrev) ·
iRating · Gap. Keep the proximity left/right lights (safety-relevant). SoF in the header.

## Bindings cheat-sheet

| Widget | Binding |
|--------|---------|
| Row n valid | `[SimHubProDash.ProDash.Rel.{n}.Valid]` |
| Row n is player | `[SimHubProDash.ProDash.Rel.{n}.IsPlayer]` |
| Row n position | `[SimHubProDash.ProDash.Rel.{n}.Position]` |
| Row n number | `[SimHubProDash.ProDash.Rel.{n}.CarNumber]` |
| Row n name | `[SimHubProDash.ProDash.Rel.{n}.Name]` |
| Row n iRating | `[SimHubProDash.ProDash.Rel.{n}.IRating]` |
| Row n licence | `[SimHubProDash.ProDash.Rel.{n}.License]` |
| Row n gap | `[SimHubProDash.ProDash.Rel.{n}.Gap]` |
| Row n class colour | `[SimHubProDash.ProDash.Rel.{n}.ClassColor]` |
| Row n in pit | `[SimHubProDash.ProDash.Rel.{n}.InPit]` |
| SoF | `[SimHubProDash.ProDash.Field.SoF]` |
| Proximity state | `[SimHubProDash.ProDash.Prox.State]` |
| Left / right lights | `[SimHubProDash.ProDash.Prox.CarLeft]` / `.CarRight` |

## Import test

With iRacing + AI cars running, confirm rows populate around the player, `Gap` signs are
correct (car ahead positive), `SoF` looks sane vs iRacing's session SoF, and the left/right
lights fire when an AI car pulls alongside. Save as `relative-standings.simhubdash` and
`relative-standings-ddu.simhubdash`.
