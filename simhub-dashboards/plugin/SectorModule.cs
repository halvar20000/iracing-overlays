using System;
using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// Live sector timing (S1/S2/S3) computed from the player's track position — iRacing
    /// doesn't expose sector splits directly, so we derive them (same approach as the Python
    /// qualifying-delta overlay). Self-contained; no third-party plugin required.
    ///
    /// Properties (ProDash.Sector.*):
    ///   CurrentLap        running current-lap time "m:ss.mmm"
    ///   Current           active sector number (1..3)
    ///   S1 / S2 / S3      sector time strings (this lap once done, else last completed lap)
    ///   S1Delta/2/3       delta vs session-best sector "+0.000" / "-0.000" ("" if none)
    /// </summary>
    public class SectorModule : IProModule
    {
        public string Name => "Sector";

        private double[] _bounds = { 0.0, 1.0 / 3.0, 2.0 / 3.0 };
        private readonly double[] _cur = { -1, -1, -1 };   // this lap's sector times
        private readonly double[] _last = { -1, -1, -1 };  // last completed lap
        private readonly double[] _best = { -1, -1, -1 };  // session best per sector
        private int _lastLap = int.MinValue;
        private int _secIdx = 0;
        private double _secEntry = -1;
        private double _lapStart = -1;

        public void Init(ProDashPlugin p)
        {
            p.AddProp("ProDash.Sector.CurrentLap", "-:--.---");
            p.AddProp("ProDash.Sector.Current", 1);
            for (int i = 1; i <= 3; i++)
            {
                p.AddProp("ProDash.Sector.S" + i, "");
                p.AddProp("ProDash.Sector.S" + i + "Delta", "");
            }
        }

        public void Reset()
        {
            for (int i = 0; i < 3; i++) { _cur[i] = _last[i] = _best[i] = -1; }
            _lastLap = int.MinValue; _secIdx = 0; _secEntry = -1; _lapStart = -1;
        }

        public void Update(ProDashPlugin p, GameData data, IRacingRaw raw, int frame)
        {
            // Refresh sector boundaries occasionally (from SplitTimeInfo, fallback = thirds).
            if (frame % 60 == 0)
            {
                var b = raw.SectorStartPcts();
                if (b != null && b.Length >= 3) _bounds = new[] { b[0], b[1], b[2] };
            }

            int player = raw.PlayerCarIdx();
            float[] pct = raw.TelFloatArray("CarIdxLapDistPct");
            int[] lapArr = raw.TelIntArray("CarIdxLap");
            double t = raw.TelDouble("SessionTime", -1);
            if (player < 0 || player >= pct.Length || t < 0) return;
            double p0 = pct[player];
            int lap = player < lapArr.Length ? lapArr[player] : 0;

            if (lap != _lastLap)
            {
                // Lap boundary: close final sector of the lap just finished.
                if (_secEntry >= 0 && _lapStart >= 0)
                {
                    _cur[Math.Min(_secIdx, 2)] = t - _secEntry;
                    for (int i = 0; i < 3; i++)
                    {
                        if (_cur[i] > 0) { _last[i] = _cur[i]; if (_best[i] < 0 || _cur[i] < _best[i]) _best[i] = _cur[i]; }
                    }
                }
                for (int i = 0; i < 3; i++) _cur[i] = -1;
                _lapStart = t; _secEntry = t; _secIdx = 0; _lastLap = lap;
            }
            else
            {
                int sec = SectorOf(p0);
                if (sec == _secIdx + 1 && sec <= 2) // moved forward one sector
                {
                    if (_secEntry >= 0)
                    {
                        _cur[_secIdx] = t - _secEntry;
                        if (_cur[_secIdx] > 0 && (_best[_secIdx] < 0 || _cur[_secIdx] < _best[_secIdx]))
                            _best[_secIdx] = _cur[_secIdx];
                    }
                    _secEntry = t; _secIdx = sec;
                }
            }

            p.SetProp("ProDash.Sector.Current", _secIdx + 1);
            p.SetProp("ProDash.Sector.CurrentLap", _lapStart >= 0 ? Fmt(t - _lapStart) : "-:--.---");
            for (int i = 0; i < 3; i++)
            {
                double show = _cur[i] > 0 ? _cur[i] : _last[i];
                p.SetProp("ProDash.Sector.S" + (i + 1), show > 0 ? Fmt(show) : "");
                string dl = "";
                double basis = _cur[i] > 0 ? _cur[i] : -1;
                if (basis > 0 && _best[i] > 0)
                {
                    double d = basis - _best[i];
                    dl = (d >= 0 ? "+" : "-") + Math.Abs(d).ToString("0.000");
                }
                p.SetProp("ProDash.Sector.S" + (i + 1) + "Delta", dl);
            }
        }

        private int SectorOf(double pct)
        {
            int s = 0;
            for (int i = 0; i < _bounds.Length && i < 3; i++)
                if (pct >= _bounds[i]) s = i;
            return s;
        }

        private static string Fmt(double secs)
        {
            if (secs <= 0 || secs >= 3600) return "";
            int m = (int)(secs / 60);
            double s = secs - m * 60;
            return m + ":" + (s < 10 ? "0" : "") + s.ToString("0.000");
        }
    }
}
