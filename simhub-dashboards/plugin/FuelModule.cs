using System;
using System.Collections.Generic;
using System.Linq;
using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// Fuel-to-finish. Learns per-lap burn from clean (non-pit) laps and projects whether
    /// the current fuel load reaches the flag, how much to add, and a fuel-save target.
    ///
    /// Properties (ProDash.Fuel.*):
    ///   Remaining      litres in the tank now
    ///   PerLap         average litres burned per clean lap (learned)
    ///   LapsLeftOnFuel laps the current fuel will last at PerLap
    ///   LapsToFinish   laps remaining in the race (lap- or time-limited)
    ///   ToFinish       litres needed to reach the flag (LapsToFinish * PerLap)
    ///   ToAdd          litres to add now (incl. safety margin), 0 if already enough
    ///   SaveTarget     litres/lap you must average to finish on current fuel
    ///   MarginLaps     LapsLeftOnFuel - LapsToFinish (positive = spare, negative = short)
    ///   Status         OK / TIGHT / SAVE / PIT / --
    /// </summary>
    public class FuelModule : IProModule
    {
        public string Name => "Fuel";

        private readonly Queue<double> _burns = new Queue<double>();
        private int _lastLap = -1;
        private double _fuelAtLapStart = double.NaN;
        private bool _sawPitThisLap;

        public void Init(ProDashPlugin p)
        {
            p.AddProp("ProDash.Fuel.Remaining", 0.0);
            p.AddProp("ProDash.Fuel.PerLap", 0.0);
            p.AddProp("ProDash.Fuel.LapsLeftOnFuel", 0.0);
            p.AddProp("ProDash.Fuel.LapsToFinish", 0.0);
            p.AddProp("ProDash.Fuel.ToFinish", 0.0);
            p.AddProp("ProDash.Fuel.ToAdd", 0.0);
            p.AddProp("ProDash.Fuel.SaveTarget", 0.0);
            p.AddProp("ProDash.Fuel.MarginLaps", 0.0);
            p.AddProp("ProDash.Fuel.Status", "--");
        }

        public void Reset()
        {
            _burns.Clear();
            _lastLap = -1;
            _fuelAtLapStart = double.NaN;
            _sawPitThisLap = false;
        }

        public void Update(ProDashPlugin p, GameData data, IRacingRaw raw, int frame)
        {
            double fuel = raw.TelFloat("FuelLevel", 0f);
            int lap = raw.TelInt("Lap", -1);
            bool onPit = raw.TelBool("OnPitRoad", false);
            if (onPit) _sawPitThisLap = true;

            // Lap boundary: learn the burn of the lap we just finished.
            if (lap != _lastLap)
            {
                if (_lastLap >= 0 && !double.IsNaN(_fuelAtLapStart) && !_sawPitThisLap)
                {
                    double burn = _fuelAtLapStart - fuel;
                    if (burn > 0.02 && burn < 100) // ignore refuels & noise
                    {
                        _burns.Enqueue(burn);
                        while (_burns.Count > Math.Max(1, p.Settings.FuelBurnWindow)) _burns.Dequeue();
                    }
                }
                _fuelAtLapStart = fuel;
                _lastLap = lap;
                _sawPitThisLap = onPit;
            }

            double perLap = _burns.Count > 0 ? _burns.Average() : 0.0;

            double lapsToFinish = LapsRemaining(data, raw);
            double lapsLeftOnFuel = perLap > 0 ? fuel / perLap : 0.0;
            double toFinish = lapsToFinish * perLap;
            double margin = perLap > 0 ? lapsLeftOnFuel - lapsToFinish : 0.0;
            double toAdd = 0.0;
            double saveTarget = 0.0;

            if (perLap > 0 && lapsToFinish > 0)
            {
                double needed = (lapsToFinish + p.Settings.FuelMarginLaps) * perLap;
                toAdd = Math.Max(0.0, needed - fuel);
                saveTarget = fuel / lapsToFinish;
            }

            string status = "--";
            if (perLap > 0 && lapsToFinish > 0)
            {
                if (margin >= p.Settings.FuelMarginLaps) status = "OK";
                else if (margin >= 0) status = "TIGHT";
                else if (margin >= -1.0) status = "SAVE";
                else status = "PIT";
            }

            p.SetProp("ProDash.Fuel.Remaining", Round(fuel, 2));
            p.SetProp("ProDash.Fuel.PerLap", Round(perLap, 2));
            p.SetProp("ProDash.Fuel.LapsLeftOnFuel", Round(lapsLeftOnFuel, 1));
            p.SetProp("ProDash.Fuel.LapsToFinish", Round(lapsToFinish, 1));
            p.SetProp("ProDash.Fuel.ToFinish", Round(toFinish, 2));
            p.SetProp("ProDash.Fuel.ToAdd", Round(toAdd, 2));
            p.SetProp("ProDash.Fuel.SaveTarget", Round(saveTarget, 2));
            p.SetProp("ProDash.Fuel.MarginLaps", Round(margin, 1));
            p.SetProp("ProDash.Fuel.Status", status);
        }

        /// <summary>
        /// Laps left in the race. Lap-limited sessions use SessionLapsRemainEx directly;
        /// timed sessions estimate from time remaining / lap time, +1 for the leader's
        /// last-lap crossing rule (same convention as the flag/session Python overlays).
        /// </summary>
        private static double LapsRemaining(GameData data, IRacingRaw raw)
        {
            int lapsRemain = raw.TelInt("SessionLapsRemainEx", 32767);
            if (lapsRemain > 0 && lapsRemain < 32767)
                return lapsRemain;

            double timeRemain = raw.TelDouble("SessionTimeRemain", -1);
            double lapTime = LapTimeSeconds(data);
            if (timeRemain > 0 && lapTime > 1)
                return Math.Ceiling(timeRemain / lapTime) + 1;

            return 0.0;
        }

        private static double LapTimeSeconds(GameData data)
        {
            var nd = data.NewData;
            if (nd == null) return 0;
            if (nd.LastLapTime.TotalSeconds > 1) return nd.LastLapTime.TotalSeconds;
            if (nd.BestLapTime.TotalSeconds > 1) return nd.BestLapTime.TotalSeconds;
            return 0;
        }

        private static double Round(double v, int d) => Math.Round(v, d, MidpointRounding.AwayFromZero);
    }
}
