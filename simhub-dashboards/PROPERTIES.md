# Property reference — SimHub Pro Dash

All properties are published under `ProDash.*`. In Dash Studio / NCalc they appear as
`SimHubProDash.ProDash.<...>`. Values refresh every frame during a live iRacing session and
hold their last value otherwise.

## Global

| Property | Type | Meaning |
|----------|------|---------|
| `ProDash.Active` | bool | True when a live iRacing session is feeding data |
| `ProDash.Version` | string | Plugin version |

## Fuel (`ProDash.Fuel.*`)

| Property | Type | Meaning |
|----------|------|---------|
| `Remaining` | L | Fuel in the tank now |
| `PerLap` | L | Learned average burn per clean (non-pit) lap |
| `LapsLeftOnFuel` | laps | How many laps the current fuel lasts at `PerLap` |
| `LapsToFinish` | laps | Laps left in the race (lap- or time-limited) |
| `ToFinish` | L | Fuel needed to reach the flag |
| `ToAdd` | L | Fuel to add now (incl. margin), 0 if already enough |
| `SaveTarget` | L | Litres/lap you must average to finish on current fuel |
| `MarginLaps` | laps | `LapsLeftOnFuel − LapsToFinish` (+ spare / − short) |
| `Status` | string | `OK` / `TIGHT` / `SAVE` / `PIT` / `--` |

## Relative + field (`ProDash.Rel.*`, `ProDash.Field.*`)

Rows `n = 1..Rows` (default 7: 3 ahead, player centred, 3 behind — set by
`RelativeAhead` / `RelativeBehind`). Top row is furthest ahead.

| Property | Type | Meaning |
|----------|------|---------|
| `Rel.{n}.Valid` | bool | Row has a car |
| `Rel.{n}.IsPlayer` | bool | This is the player's row |
| `Rel.{n}.CarIdx` | int | iRacing CarIdx |
| `Rel.{n}.Position` | int | Class position (falls back to overall) |
| `Rel.{n}.CarNumber` | string | Car number |
| `Rel.{n}.Name` | string | Driver name |
| `Rel.{n}.IRating` | int | Driver iRating |
| `Rel.{n}.License` | string | Licence + SR (e.g. `A 4.99`) |
| `Rel.{n}.ClassColor` | string | Car-class colour from iRacing |
| `Rel.{n}.Gap` | s | Time gap to player (+ ahead / − behind) |
| `Rel.{n}.LapsDiff` | int | Whole laps vs player (lapped/lapping) |
| `Rel.{n}.InPit` | bool | Car on pit road |
| `Field.SoF` | int | Strength of Field (iRacing formula) |
| `Field.AvgIRating` | int | Mean iRating of the field |
| `Field.CarCount` | int | Cars in session (excl. pace car) |

## Pit predictor (`ProDash.Pit.*`) — estimates

| Property | Type | Meaning |
|----------|------|---------|
| `FuelToAdd` | L | From the fuel calc |
| `FuelFillTime` | s | `FuelToAdd / RefuelLitresPerSecond` |
| `TyreChangeTime` | s | Configured tyre-change time |
| `StationaryTime` | s | Time stopped in the box (max of fuel/tyres — parallel) |
| `PitLaneLoss` | s | Per-track pit-lane time loss (configurable) |
| `TotalLoss` | s | `StationaryTime + PitLaneLoss` |
| `GapAhead` | s | Nearest car ahead on track |
| `GapBehind` | s | Nearest car behind on track |
| `ExitGapBehind` | s | `GapBehind − TotalLoss` (+ = rejoin ahead of them) |
| `Window` | string | `CLEAR` / `RISK` / `--` |

## Proximity / spotter (`ProDash.Prox.*`)

iRacing exposes no per-car lateral position, so side awareness uses iRacing's own
`CarLeftRight` spotter channel; a dot-radar isn't possible from iRacing telemetry.

| Property | Type | Meaning |
|----------|------|---------|
| `State` | string | `CLEAR` / `CAR LEFT` / `CAR RIGHT` / `3 WIDE` / `2 LEFT` / `2 RIGHT` |
| `CarLeft` / `CarRight` | bool | Car alongside on that side |
| `CarsLeft` / `CarsRight` | int | 0/1/2 on that side |
| `AheadGap` / `BehindGap` | s | Nearest car ahead/behind, seconds |
| `AheadDist` / `BehindDist` | m | Nearest car ahead/behind, metres (needs track length) |

## Settings (SimHub → Pro Dash, persisted)

`FuelMarginLaps`, `FuelBurnWindow`, `RelativeAhead`, `RelativeBehind`,
`RefuelLitresPerSecond`, `TyreChangeSeconds`, `PitLaneLossSeconds`.
Row counts (`RelativeAhead`/`RelativeBehind`) apply on next SimHub start.
