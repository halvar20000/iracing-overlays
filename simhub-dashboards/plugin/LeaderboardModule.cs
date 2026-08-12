using System;
using System.Collections.Generic;
using System.Linq;
using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// Full-field leaderboard. Publishes the whole classified field (sorted by position,
    /// or by best lap in practice) with per-row pos/#/name/iRating/licence/gap/interval/
    /// best/last/pit — everything a pit-wall board needs, from iRacing's own data, so no
    /// third-party plugin is required.
    ///
    /// Row properties (n = 1..Rows, PluginSettings.BoardRows):
    ///   ProDash.Board.{n}.Valid / .Pos / .CarNumber / .Name / .IRating / .License
    ///   ProDash.Board.{n}.Gap (s to leader) / .Interval (s to car ahead)
    ///   ProDash.Board.{n}.BestLap / .LastLap (strings m:ss.mmm) / .InPit / .IsPlayer / .ClassColor
    ///   ProDash.Board.Count
    /// </summary>
    public class LeaderboardModule : IProModule
    {
        public string Name => "Leaderboard";

        private int _rows = 30;
        private readonly Dictionary<int, Drv> _drivers = new Dictionary<int, Drv>();

        private class Drv
        {
            public int CarIdx;
            public string Name = "", License = "", CarNumber = "", ClassColor = "";
            public int IRating;
            public bool IsPace;
        }

        private struct Row
        {
            public int CarIdx, Pos;
            public double Gap, Interval, Best, Last;
            public bool InPit;
        }

        public void Init(ProDashPlugin p)
        {
            _rows = Math.Max(1, p.Settings.BoardRows);
            p.AddProp("ProDash.Board.Count", 0);
            for (int n = 1; n <= _rows; n++)
            {
                string b = "ProDash.Board." + n + ".";
                p.AddProp(b + "Valid", false);
                p.AddProp(b + "Pos", 0);
                p.AddProp(b + "CarNumber", "");
                p.AddProp(b + "Name", "");
                p.AddProp(b + "IRating", 0);
                p.AddProp(b + "License", "");
                p.AddProp(b + "Gap", "");
                p.AddProp(b + "Interval", "");
                p.AddProp(b + "BestLap", "");
                p.AddProp(b + "LastLap", "");
                p.AddProp(b + "InPit", false);
                p.AddProp(b + "IsPlayer", false);
                p.AddProp(b + "ClassColor", "");
            }
        }

        public void Reset() => _drivers.Clear();

        public void Update(ProDashPlugin p, GameData data, IRacingRaw raw, int frame)
        {
            if (frame % 15 != 0) return; // board updates a few times/sec — plenty

            if (frame % 30 == 0 || _drivers.Count == 0) RefreshDrivers(raw);

            int player = raw.PlayerCarIdx();
            int[] pos = raw.TelIntArray("CarIdxPosition");
            float[] f2 = raw.TelFloatArray("CarIdxF2Time");
            float[] best = raw.TelFloatArray("CarIdxBestLapTime");
            float[] last = raw.TelFloatArray("CarIdxLastLapTime");
            int[] surf = raw.TelIntArray("CarIdxTrackSurface");
            bool[] onPit = raw.TelBoolArray("CarIdxOnPitRoad");

            var entries = new List<Row>();
            bool anyPos = false;
            foreach (var kv in _drivers)
            {
                int i = kv.Key;
                if (kv.Value.IsPace) continue;
                int pp = i < pos.Length ? pos[i] : 0;
                if (pp > 0) anyPos = true;
                double bl = i < best.Length ? best[i] : 0;
                entries.Add(new Row
                {
                    CarIdx = i,
                    Pos = pp,
                    Gap = i < f2.Length ? f2[i] : 0,
                    Best = bl > 0 ? bl : double.MaxValue,
                    Last = i < last.Length ? last[i] : 0,
                    InPit = i < onPit.Length && onPit[i]
                });
            }

            // Race: sort by position. Practice/quali (no positions): sort by best lap.
            if (anyPos)
                entries = entries.Where(e => e.Pos > 0).OrderBy(e => e.Pos).ToList();
            else
                entries = entries.OrderBy(e => e.Best).ToList();

            // Interval = gap difference to the row above (F2Time is time behind leader).
            double prevGap = entries.Count > 0 ? entries[0].Gap : 0;
            for (int k = 0; k < entries.Count; k++)
            {
                var e = entries[k];
                e.Interval = k == 0 ? 0 : e.Gap - prevGap;
                prevGap = e.Gap;
                entries[k] = e;
            }

            p.SetProp("ProDash.Board.Count", entries.Count);
            for (int n = 1; n <= _rows; n++)
            {
                string b = "ProDash.Board." + n + ".";
                if (n <= entries.Count)
                {
                    var e = entries[n - 1];
                    _drivers.TryGetValue(e.CarIdx, out var di);
                    p.SetProp(b + "Valid", true);
                    p.SetProp(b + "Pos", e.Pos > 0 ? e.Pos : n);
                    p.SetProp(b + "CarNumber", di?.CarNumber ?? "");
                    p.SetProp(b + "Name", di?.Name ?? "");
                    p.SetProp(b + "IRating", di?.IRating ?? 0);
                    p.SetProp(b + "License", di?.License ?? "");
                    p.SetProp(b + "Gap", n == 1 ? "LEADER" : "+" + e.Gap.ToString("0.0"));
                    p.SetProp(b + "Interval", n == 1 ? "" : "+" + e.Interval.ToString("0.0"));
                    p.SetProp(b + "BestLap", LapStr(e.Best));
                    p.SetProp(b + "LastLap", LapStr(e.Last));
                    p.SetProp(b + "InPit", e.InPit);
                    p.SetProp(b + "IsPlayer", e.CarIdx == player);
                    p.SetProp(b + "ClassColor", di?.ClassColor ?? "");
                }
                else
                {
                    p.SetProp(b + "Valid", false);
                    p.SetProp(b + "Pos", 0);
                    p.SetProp(b + "CarNumber", "");
                    p.SetProp(b + "Name", "");
                    p.SetProp(b + "IRating", 0);
                    p.SetProp(b + "License", "");
                    p.SetProp(b + "Gap", "");
                    p.SetProp(b + "Interval", "");
                    p.SetProp(b + "BestLap", "");
                    p.SetProp(b + "LastLap", "");
                    p.SetProp(b + "InPit", false);
                    p.SetProp(b + "IsPlayer", false);
                    p.SetProp(b + "ClassColor", "");
                }
            }
        }

        private void RefreshDrivers(IRacingRaw raw)
        {
            _drivers.Clear();
            foreach (var d in raw.Drivers())
            {
                int idx;
                if (!int.TryParse(SafeStr(raw.Driver(d, "CarIdx"), "-1"), out idx) || idx < 0) continue;
                _drivers[idx] = new Drv
                {
                    CarIdx = idx,
                    Name = SafeStr(raw.Driver(d, "UserName"), ""),
                    IRating = ParseInt(raw.Driver(d, "IRating")),
                    License = SafeStr(raw.Driver(d, "LicString"), ""),
                    CarNumber = SafeStr(raw.Driver(d, "CarNumber"), "").Trim('"'),
                    ClassColor = SafeStr(raw.Driver(d, "CarClassColor"), ""),
                    IsPace = SafeStr(raw.Driver(d, "CarIsPaceCar"), "0") != "0"
                };
            }
        }

        private static string LapStr(double secs)
        {
            if (secs <= 0 || secs >= 3600 || secs == double.MaxValue) return "";
            int m = (int)(secs / 60);
            double s = secs - m * 60;
            return m + ":" + (s < 10 ? "0" : "") + s.ToString("0.000");
        }

        private static string SafeStr(object o, string def) => o == null ? def : o.ToString();
        private static int ParseInt(object o)
        {
            if (o == null) return 0;
            int v; int.TryParse(o.ToString(), out v); return v;
        }
    }
}
