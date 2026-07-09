using System;
using System.Linq;
using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// Proximity / spotter. iRacing does NOT expose per-car lateral coordinates, so a true
    /// dot-radar is impossible from its telemetry. What iRacing DOES give is its own spotter
    /// channel <c>CarLeftRight</c> (clear / car left / car right / both / 2 left / 2 right),
    /// which is the honest, sim-supported basis for side awareness. We combine that with the
    /// nearest car ahead/behind on track for a spotter-grade proximity readout.
    ///
    /// Properties (ProDash.Prox.*):
    ///   State      text: CLEAR / CAR LEFT / CAR RIGHT / 3 WIDE / 2 LEFT / 2 RIGHT
    ///   CarLeft / CarRight  bool
    ///   CarsLeft / CarsRight  int (0/1/2)
    ///   AheadGap / BehindGap  seconds to nearest car
    ///   AheadDist / BehindDist  metres to nearest car
    /// </summary>
    public class ProximityModule : IProModule
    {
        public string Name => "Proximity";

        private double _trackLengthM;

        public void Init(ProDashPlugin p)
        {
            p.AddProp("ProDash.Prox.State", "CLEAR");
            p.AddProp("ProDash.Prox.CarLeft", false);
            p.AddProp("ProDash.Prox.CarRight", false);
            p.AddProp("ProDash.Prox.CarsLeft", 0);
            p.AddProp("ProDash.Prox.CarsRight", 0);
            p.AddProp("ProDash.Prox.AheadGap", 0.0);
            p.AddProp("ProDash.Prox.BehindGap", 0.0);
            p.AddProp("ProDash.Prox.AheadDist", 0.0);
            p.AddProp("ProDash.Prox.BehindDist", 0.0);
        }

        public void Reset() { _trackLengthM = 0; }

        public void Update(ProDashPlugin p, GameData data, IRacingRaw raw, int frame)
        {
            if (_trackLengthM <= 0 || frame % 60 == 0)
                _trackLengthM = ParseTrackLength(raw);

            // iRacing irsdk_CarLeftRight enum.
            int lr = raw.TelInt("CarLeftRight", 1);
            bool left = lr == 2 || lr == 4 || lr == 5;
            bool right = lr == 3 || lr == 4 || lr == 6;
            int carsLeft = lr == 5 ? 2 : (left ? 1 : 0);
            int carsRight = lr == 6 ? 2 : (right ? 1 : 0);
            string state = StateText(lr);

            // Nearest car ahead/behind on track.
            double aheadS = 0, behindS = 0, aheadM = 0, behindM = 0;
            int player = raw.PlayerCarIdx();
            float[] pct = raw.TelFloatArray("CarIdxLapDistPct");
            int[] surf = raw.TelIntArray("CarIdxTrackSurface");
            if (player >= 0 && player < pct.Length)
            {
                double refLap = data.NewData != null && data.NewData.BestLapTime.TotalSeconds > 1
                    ? data.NewData.BestLapTime.TotalSeconds
                    : (data.NewData != null && data.NewData.LastLapTime.TotalSeconds > 1
                        ? data.NewData.LastLapTime.TotalSeconds : 90.0);
                double pp = pct[player];
                double bestAhead = double.MaxValue, bestBehind = double.MaxValue;
                for (int i = 0; i < pct.Length; i++)
                {
                    if (i == player) continue;
                    if (i < surf.Length && surf[i] < 0) continue;
                    double d = pct[i] - pp;
                    if (d > 0.5) d -= 1.0; else if (d < -0.5) d += 1.0;
                    if (d >= 0 && d < bestAhead) bestAhead = d;
                    if (d < 0 && -d < bestBehind) bestBehind = -d;
                }
                if (bestAhead != double.MaxValue) { aheadS = bestAhead * refLap; aheadM = bestAhead * _trackLengthM; }
                if (bestBehind != double.MaxValue) { behindS = bestBehind * refLap; behindM = bestBehind * _trackLengthM; }
            }

            p.SetProp("ProDash.Prox.State", state);
            p.SetProp("ProDash.Prox.CarLeft", left);
            p.SetProp("ProDash.Prox.CarRight", right);
            p.SetProp("ProDash.Prox.CarsLeft", carsLeft);
            p.SetProp("ProDash.Prox.CarsRight", carsRight);
            p.SetProp("ProDash.Prox.AheadGap", Math.Round(aheadS, 1, MidpointRounding.AwayFromZero));
            p.SetProp("ProDash.Prox.BehindGap", Math.Round(behindS, 1, MidpointRounding.AwayFromZero));
            p.SetProp("ProDash.Prox.AheadDist", Math.Round(aheadM, 0));
            p.SetProp("ProDash.Prox.BehindDist", Math.Round(behindM, 0));
        }

        private static string StateText(int lr)
        {
            switch (lr)
            {
                case 2: return "CAR LEFT";
                case 3: return "CAR RIGHT";
                case 4: return "3 WIDE";
                case 5: return "2 LEFT";
                case 6: return "2 RIGHT";
                default: return "CLEAR";
            }
        }

        /// <summary>
        /// Parse SessionData.WeekendInfo.TrackLength (e.g. "3.70 km") to metres. TrackLength
        /// is in the session YAML, not a telemetry channel, so it's read via the session
        /// graph. Returns 0 if unavailable — distances then read 0, but second-gaps still work.
        /// </summary>
        private static double ParseTrackLength(IRacingRaw raw)
        {
            try
            {
                var s = raw.WeekendString("TrackLength");
                if (string.IsNullOrEmpty(s)) return 0;
                s = s.Trim();
                bool km = s.IndexOf("km", StringComparison.OrdinalIgnoreCase) >= 0;
                bool mi = s.IndexOf("mi", StringComparison.OrdinalIgnoreCase) >= 0;
                var num = new string(s.TakeWhile(c => char.IsDigit(c) || c == '.' || c == ',').ToArray())
                    .Replace(',', '.');
                if (!double.TryParse(num, System.Globalization.NumberStyles.Any,
                    System.Globalization.CultureInfo.InvariantCulture, out double v)) return 0;
                if (mi) return v * 1609.344;
                return km ? v * 1000.0 : v; // default assume km
            }
            catch { return 0; }
        }
    }
}
