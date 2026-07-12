using System;
using System.Linq;
using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// Pit-stop predictor. Estimates service time (fuel fill + tyre change, done in
    /// parallel), the total pit time loss (service + pit-lane loss), and whether you would
    /// rejoin clear of the car currently behind.
    ///
    /// Note: iRacing telemetry has no pit-lane length, so the pit-lane loss is a
    /// per-track configurable estimate (PluginSettings.PitLaneLossSeconds). Everything here
    /// is an ESTIMATE and is labelled as such — it is refined live against real stops.
    ///
    /// Properties (ProDash.Pit.*):
    ///   FuelToAdd / FuelFillTime / TyreChangeTime / StationaryTime
    ///   PitLaneLoss / TotalLoss / GapAhead / GapBehind / ExitGapBehind / Window
    /// </summary>
    public class PitModule : IProModule
    {
        public string Name => "Pit";

        public void Init(ProDashPlugin p)
        {
            p.AddProp("ProDash.Pit.FuelToAdd", 0.0);
            p.AddProp("ProDash.Pit.FuelFillTime", 0.0);
            p.AddProp("ProDash.Pit.TyreChangeTime", 0.0);
            p.AddProp("ProDash.Pit.StationaryTime", 0.0);
            p.AddProp("ProDash.Pit.PitLaneLoss", 0.0);
            p.AddProp("ProDash.Pit.TotalLoss", 0.0);
            p.AddProp("ProDash.Pit.GapAhead", 0.0);
            p.AddProp("ProDash.Pit.GapBehind", 0.0);
            p.AddProp("ProDash.Pit.ExitGapBehind", 0.0);
            p.AddProp("ProDash.Pit.Window", "--");
        }

        public void Reset() { }

        public void Update(ProDashPlugin p, GameData data, IRacingRaw raw, int frame)
        {
            // Fuel-to-add is computed by FuelModule (runs before us this frame).
            double fuelToAdd = ToDouble(p.PluginManager.GetPropertyValue("ProDash.Fuel.ToAdd"));

            double fillRate = Math.Max(0.1, p.Settings.RefuelLitresPerSecond);
            double fillTime = fuelToAdd / fillRate;
            double tyreTime = p.Settings.TyreChangeSeconds;
            // Fuel and tyres are serviced in parallel; stationary time is the longer of the two.
            double stationary = Math.Max(fillTime, tyreTime);
            double laneLoss = p.Settings.PitLaneLossSeconds;
            double totalLoss = stationary + laneLoss;

            // Nearest cars on track (seconds), used for the rejoin projection.
            double gapAhead, gapBehind;
            NearestGaps(data, raw, out gapAhead, out gapBehind);

            // If you stop and the car behind doesn't, you lose totalLoss vs them.
            double exitGapBehind = gapBehind - totalLoss; // + = still ahead of them
            string window = "--";
            if (gapBehind > 0.1)
                window = exitGapBehind >= 0 ? "CLEAR" : "RISK";

            p.SetProp("ProDash.Pit.FuelToAdd", Round(fuelToAdd, 2));
            p.SetProp("ProDash.Pit.FuelFillTime", Round(fillTime, 1));
            p.SetProp("ProDash.Pit.TyreChangeTime", Round(tyreTime, 1));
            p.SetProp("ProDash.Pit.StationaryTime", Round(stationary, 1));
            p.SetProp("ProDash.Pit.PitLaneLoss", Round(laneLoss, 1));
            p.SetProp("ProDash.Pit.TotalLoss", Round(totalLoss, 1));
            p.SetProp("ProDash.Pit.GapAhead", Round(gapAhead, 1));
            p.SetProp("ProDash.Pit.GapBehind", Round(gapBehind, 1));
            p.SetProp("ProDash.Pit.ExitGapBehind", Round(exitGapBehind, 1));
            p.SetProp("ProDash.Pit.Window", window);
        }

        /// <summary>Nearest car ahead/behind on track in seconds (both classes).</summary>
        private static void NearestGaps(GameData data, IRacingRaw raw, out double ahead, out double behind)
        {
            ahead = 0; behind = 0;
            int player = raw.PlayerCarIdx();
            float[] pct = raw.TelFloatArray("CarIdxLapDistPct");
            int[] surf = raw.TelIntArray("CarIdxTrackSurface");
            if (player < 0 || player >= pct.Length) return;

            double refLap = data.NewData != null && data.NewData.BestLapTime.TotalSeconds > 1
                ? data.NewData.BestLapTime.TotalSeconds
                : (data.NewData != null && data.NewData.LastLapTime.TotalSeconds > 1
                    ? data.NewData.LastLapTime.TotalSeconds : 90.0);

            double best = double.MaxValue, worst = double.MinValue;
            double pp = pct[player];
            for (int i = 0; i < pct.Length; i++)
            {
                if (i == player) continue;
                if (i < surf.Length && surf[i] < 0) continue;
                double d = pct[i] - pp;
                if (d > 0.5) d -= 1.0; else if (d < -0.5) d += 1.0;
                double gap = d * refLap;
                if (gap >= 0 && gap < best) best = gap;
                if (gap < 0 && gap > worst) worst = gap;
            }
            if (best != double.MaxValue) ahead = best;
            if (worst != double.MinValue) behind = -worst; // report behind as positive magnitude
        }

        private static double ToDouble(object o)
        {
            try { return o == null ? 0.0 : Convert.ToDouble(o); } catch { return 0.0; }
        }

        private static double Round(double v, int d) => Math.Round(v, d, MidpointRounding.AwayFromZero);
    }
}
