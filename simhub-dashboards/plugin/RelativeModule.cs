using System;
using System.Collections.Generic;
using System.Linq;
using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// Relative box + Strength of Field. Publishes the cars physically nearest to the
    /// player on track (ahead and behind, regardless of lap), each with iRating, licence,
    /// live time gap, class colour and pit state — plus the field's SoF and average iRating.
    ///
    /// Row properties (n = 1..Rows, top = furthest ahead, middle = player, bottom = furthest behind):
    ///   ProDash.Rel.{n}.CarIdx / .Position / .CarNumber / .Name / .IRating / .License
    ///   ProDash.Rel.{n}.ClassColor / .Gap (s, +ahead/-behind) / .LapsDiff / .InPit / .IsPlayer / .Valid
    /// Field properties:
    ///   ProDash.Field.SoF / .AvgIRating / .CarCount
    /// </summary>
    public class RelativeModule : IProModule
    {
        public string Name => "Relative";

        private int _rows = 7;
        private int _ahead = 3;
        private int _behind = 3;

        // Driver info is parsed from the session YAML; refresh occasionally, not every frame.
        private readonly Dictionary<int, DriverInfoRow> _drivers = new Dictionary<int, DriverInfoRow>();
        private int _sof;
        private int _avgIr;

        private class DriverInfoRow
        {
            public int CarIdx;
            public string Name = "";
            public int IRating;
            public string License = "";
            public string CarNumber = "";
            public string ClassColor = "";
            public bool IsPace;
        }

        private struct RelCar
        {
            public int CarIdx;
            public double Gap;      // seconds, + = ahead of player
            public int LapsDiff;    // whole laps vs player
            public bool InPit;
        }

        public void Init(ProDashPlugin p)
        {
            // Fixed 4 ahead / 4 behind (9 rows, player centred) — the ProDash Relative
            // DDU dashboard binds Rel.1..9. Decoupled from persisted settings so a rebuild
            // always publishes all 9 rows regardless of any previously saved value.
            _ahead = 4;
            _behind = 4;
            _rows = _ahead + _behind + 1;

            for (int n = 1; n <= _rows; n++)
            {
                string b = "ProDash.Rel." + n + ".";
                p.AddProp(b + "CarIdx", -1);
                p.AddProp(b + "Position", 0);
                p.AddProp(b + "CarNumber", "");
                p.AddProp(b + "Name", "");
                p.AddProp(b + "IRating", 0);
                p.AddProp(b + "License", "");
                p.AddProp(b + "ClassColor", "");
                p.AddProp(b + "Gap", 0.0);
                p.AddProp(b + "LapsDiff", 0);
                p.AddProp(b + "InPit", false);
                p.AddProp(b + "IsPlayer", false);
                p.AddProp(b + "Valid", false);
            }
            p.AddProp("ProDash.Field.SoF", 0);
            p.AddProp("ProDash.Field.AvgIRating", 0);
            p.AddProp("ProDash.Field.CarCount", 0);
        }

        public void Reset()
        {
            _drivers.Clear();
            _sof = 0;
            _avgIr = 0;
        }

        public void Update(ProDashPlugin p, GameData data, IRacingRaw raw, int frame)
        {
            // Refresh driver roster + SoF ~once a second (session YAML changes rarely).
            if (frame % 30 == 0 || _drivers.Count == 0)
                RefreshDrivers(raw);

            int player = raw.PlayerCarIdx();
            float[] pct = raw.TelFloatArray("CarIdxLapDistPct");
            int[] lap = raw.TelIntArray("CarIdxLap");
            int[] surf = raw.TelIntArray("CarIdxTrackSurface");
            int[] classPos = raw.TelIntArray("CarIdxClassPosition");
            int[] pos = raw.TelIntArray("CarIdxPosition");
            bool[] onPit = raw.TelBoolArray("CarIdxOnPitRoad");

            double refLap = RefLapSeconds(data);
            var cars = new List<RelCar>();

            if (player >= 0 && player < pct.Length && refLap > 1)
            {
                double playerPct = pct[player];
                int playerLap = player < lap.Length ? lap[player] : 0;

                for (int i = 0; i < pct.Length; i++)
                {
                    if (i == player) continue;
                    if (!_drivers.TryGetValue(i, out var di) || di.IsPace) continue;
                    if (i < surf.Length && surf[i] < 0) continue; // not in world

                    double delta = pct[i] - playerPct;
                    if (delta > 0.5) delta -= 1.0;
                    else if (delta < -0.5) delta += 1.0;

                    cars.Add(new RelCar
                    {
                        CarIdx = i,
                        Gap = delta * refLap,
                        LapsDiff = (i < lap.Length ? lap[i] : 0) - playerLap,
                        InPit = i < onPit.Length && onPit[i]
                    });
                }
            }

            // Ahead = positive gaps (nearest first), behind = negative gaps (nearest first).
            var ahead = cars.Where(c => c.Gap >= 0).OrderBy(c => c.Gap).Take(_ahead).Reverse().ToList();
            var behind = cars.Where(c => c.Gap < 0).OrderByDescending(c => c.Gap).Take(_behind).ToList();

            var ordered = new List<RelCar?>();
            foreach (var c in ahead) ordered.Add(c);
            ordered.Add(null); // player row
            foreach (var c in behind) ordered.Add(c);

            // Pad to exactly _rows, keeping the player centred.
            while (ahead.Count < _ahead) { ordered.Insert(0, null); ahead.Add(default); }
            while (behind.Count < _behind) { ordered.Add(null); behind.Add(default); }

            for (int n = 1; n <= _rows; n++)
            {
                string b = "ProDash.Rel." + n + ".";
                RelCar? slot = (n - 1) < ordered.Count ? ordered[n - 1] : null;
                bool isPlayerRow = (n - 1) == _ahead;

                if (isPlayerRow)
                {
                    PublishRow(p, b, player, 0.0, 0, false, true, raw, classPos, pos);
                }
                else if (slot.HasValue)
                {
                    var c = slot.Value;
                    PublishRow(p, b, c.CarIdx, c.Gap, c.LapsDiff, c.InPit, false, raw, classPos, pos);
                }
                else
                {
                    ClearRow(p, b);
                }
            }

            p.SetProp("ProDash.Field.SoF", _sof);
            p.SetProp("ProDash.Field.AvgIRating", _avgIr);
            p.SetProp("ProDash.Field.CarCount", _drivers.Values.Count(d => !d.IsPace));
        }

        private void PublishRow(ProDashPlugin p, string b, int carIdx, double gap, int lapsDiff,
            bool inPit, bool isPlayer, IRacingRaw raw, int[] classPos, int[] pos)
        {
            _drivers.TryGetValue(carIdx, out var di);
            int position = 0;
            if (carIdx >= 0)
            {
                if (carIdx < classPos.Length && classPos[carIdx] > 0) position = classPos[carIdx];
                else if (carIdx < pos.Length) position = pos[carIdx];
            }

            p.SetProp(b + "CarIdx", carIdx);
            p.SetProp(b + "Position", position);
            p.SetProp(b + "CarNumber", di?.CarNumber ?? "");
            p.SetProp(b + "Name", di?.Name ?? "");
            p.SetProp(b + "IRating", di?.IRating ?? 0);
            p.SetProp(b + "License", di?.License ?? "");
            p.SetProp(b + "ClassColor", di?.ClassColor ?? "");
            p.SetProp(b + "Gap", Math.Round(gap, 1, MidpointRounding.AwayFromZero));
            p.SetProp(b + "LapsDiff", lapsDiff);
            p.SetProp(b + "InPit", inPit);
            p.SetProp(b + "IsPlayer", isPlayer);
            p.SetProp(b + "Valid", carIdx >= 0);
        }

        private void ClearRow(ProDashPlugin p, string b)
        {
            p.SetProp(b + "CarIdx", -1);
            p.SetProp(b + "Position", 0);
            p.SetProp(b + "CarNumber", "");
            p.SetProp(b + "Name", "");
            p.SetProp(b + "IRating", 0);
            p.SetProp(b + "License", "");
            p.SetProp(b + "ClassColor", "");
            p.SetProp(b + "Gap", 0.0);
            p.SetProp(b + "LapsDiff", 0);
            p.SetProp(b + "InPit", false);
            p.SetProp(b + "IsPlayer", false);
            p.SetProp(b + "Valid", false);
        }

        private void RefreshDrivers(IRacingRaw raw)
        {
            _drivers.Clear();
            var irs = new List<int>();
            foreach (var d in raw.Drivers())
            {
                int idx = Convert.ToInt32(SafeStr(raw.Driver(d, "CarIdx"), "-1"));
                if (idx < 0) continue;
                bool pace = SafeStr(raw.Driver(d, "CarIsPaceCar"), "0") != "0";
                int ir = ParseInt(raw.Driver(d, "IRating"));
                var row = new DriverInfoRow
                {
                    CarIdx = idx,
                    Name = SafeStr(raw.Driver(d, "UserName"), ""),
                    IRating = ir,
                    License = SafeStr(raw.Driver(d, "LicString"), ""),
                    CarNumber = SafeStr(raw.Driver(d, "CarNumber"), "").Trim('"'),
                    ClassColor = SafeStr(raw.Driver(d, "CarClassColor"), ""),
                    IsPace = pace
                };
                _drivers[idx] = row;
                if (!pace && ir > 0) irs.Add(ir);
            }

            _avgIr = irs.Count > 0 ? (int)Math.Round(irs.Average()) : 0;
            _sof = StrengthOfField(irs);
        }

        /// <summary>
        /// iRacing Strength of Field: SoF = (1/k) * ln( n / Σ e^(-k*ir) ), k = ln(2)/1600.
        /// Reduces to the common iRating when the field is uniform.
        /// </summary>
        private static int StrengthOfField(List<int> irs)
        {
            if (irs.Count == 0) return 0;
            const double k = 0.69314718056 / 1600.0;
            double sum = irs.Sum(ir => Math.Exp(-k * ir));
            if (sum <= 0) return 0;
            return (int)Math.Round((1.0 / k) * Math.Log(irs.Count / sum));
        }

        private static double RefLapSeconds(GameData data)
        {
            var nd = data.NewData;
            if (nd == null) return 0;
            if (nd.BestLapTime.TotalSeconds > 1) return nd.BestLapTime.TotalSeconds;
            if (nd.LastLapTime.TotalSeconds > 1) return nd.LastLapTime.TotalSeconds;
            return 90.0; // last-resort reference so the box still populates
        }

        private static string SafeStr(object o, string def) => o == null ? def : o.ToString();
        private static int ParseInt(object o)
        {
            if (o == null) return 0;
            int.TryParse(o.ToString(), out int v);
            return v;
        }
    }
}
