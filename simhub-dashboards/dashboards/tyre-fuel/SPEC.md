# Dashboard spec — Tyre & Fuel

Build in Dash Studio against a live/AI iRacing session. Two variants share the same data
bindings; only the layout density differs.

- **Landscape** (tablet / 2nd monitor): 1024 × 600.
- **DDU** (USB display): 480 × 272 (also export an 800 × 480 variant).

Original design — dark carbon background `#0B0D10`, accent amber `#FFB020`, good/warn/bad
`#39D98A` / `#FFCC33` / `#FF4D4D`. No third-party UI copied.

## Zones (landscape)

1. **Header bar** (top, full width): session state + `ProDash.Fuel.Status` pill
   (colour by status: OK green, TIGHT/SAVE amber, PIT red), lap counter, `ProDash.Prox.State`
   mini-indicator on the right.
2. **Tyres block** (left ~45%): four tyre tiles (FL/FR/RL/RR) using SimHub's native
   `TyrePressure*`, `TyreTemp*` / `TyreWear*` (native — no plugin needed). Colour by
   temp/wear thresholds. Wear bar + numeric.
3. **Fuel block** (right ~55%), the pro core — all `ProDash.Fuel.*`:
   - Big centre number: `Remaining` (L).
   - Row: `PerLap` (L/lap) · `LapsLeftOnFuel` · `MarginLaps` (green if ≥ margin, red if < 0).
   - Row: `LapsToFinish` · `ToAdd` (L, highlight if > 0) · `SaveTarget` (L/lap).
   - Status strip driven by `ProDash.Fuel.Status`.
4. **Pit strip** (bottom): `ProDash.Pit.TotalLoss`, `FuelFillTime`, `StationaryTime`,
   `Window` pill (CLEAR green / RISK red).

## DDU variant (480 × 272)

Drop tyre temps to a compact 2×2, keep the fuel core (Remaining big, PerLap, ToAdd, Status),
one-line pit summary (`TotalLoss` + `Window`). Everything must be legible at arm's length.

## Bindings cheat-sheet

| Widget | Binding |
|--------|---------|
| Fuel remaining | `[SimHubProDash.ProDash.Fuel.Remaining]` |
| Per-lap burn | `[SimHubProDash.ProDash.Fuel.PerLap]` |
| Laps to finish | `[SimHubProDash.ProDash.Fuel.LapsToFinish]` |
| Fuel to add | `[SimHubProDash.ProDash.Fuel.ToAdd]` |
| Save target | `[SimHubProDash.ProDash.Fuel.SaveTarget]` |
| Margin laps | `[SimHubProDash.ProDash.Fuel.MarginLaps]` |
| Status pill text/colour | `[SimHubProDash.ProDash.Fuel.Status]` |
| Pit total loss | `[SimHubProDash.ProDash.Pit.TotalLoss]` |
| Pit window | `[SimHubProDash.ProDash.Pit.Window]` |
| Tyres | native `[DataCorePlugin.GameData.TyreTemperature*]` / `...Wear*` |

## Import test

Load in Dash Studio's live preview with iRacing running; do an out-lap + a couple of
green laps so `PerLap` learns, then confirm `LapsToFinish` / `ToAdd` / `Status` react. Save
as `tyre-fuel.simhubdash` (landscape) and `tyre-fuel-ddu.simhubdash`.
