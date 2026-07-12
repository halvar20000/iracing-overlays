namespace SimHubProDash
{
    /// <summary>
    /// Persisted, user-tunable settings. Saved by SimHub via SaveCommonSettings and
    /// re-read on startup. All values have sane defaults so the plugin works out of the box.
    /// </summary>
    public class PluginSettings
    {
        public const string Version = "0.1.0";

        // ---- fuel ----
        /// <summary>Extra laps of fuel to target beyond the exact finish (safety margin).</summary>
        public double FuelMarginLaps { get; set; } = 0.5;

        /// <summary>Number of recent clean laps averaged for per-lap burn.</summary>
        public int FuelBurnWindow { get; set; } = 5;

        // ---- relative ----
        /// <summary>Cars shown ahead of the player in the relative box.</summary>
        public int RelativeAhead { get; set; } = 3;

        /// <summary>Cars shown behind the player in the relative box.</summary>
        public int RelativeBehind { get; set; } = 3;

        // ---- leaderboard ----
        /// <summary>Rows exposed by the full-field leaderboard (ProDash.Board.*).</summary>
        public int BoardRows { get; set; } = 32;

        // ---- pit ----
        /// <summary>iRacing refuel rate in litres/second (approx; used for fuel fill time).</summary>
        public double RefuelLitresPerSecond { get; set; } = 2.6;

        /// <summary>Seconds to change all four tyres (parallel with fuelling in iRacing).</summary>
        public double TyreChangeSeconds { get; set; } = 12.0;

        /// <summary>
        /// Track-dependent pit lane time loss vs. staying on track, in seconds. A rough
        /// default; override per track. Used by the pit-stop predictor.
        /// </summary>
        public double PitLaneLossSeconds { get; set; } = 22.0;
    }
}
