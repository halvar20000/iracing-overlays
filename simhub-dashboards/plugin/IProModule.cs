using GameReaderCommon;

namespace SimHubProDash
{
    /// <summary>
    /// A single computed-feature module. Each module registers its properties in
    /// <see cref="Init"/> and refreshes their values every frame in <see cref="Update"/>.
    /// </summary>
    public interface IProModule
    {
        /// <summary>Short name, used for logging.</summary>
        string Name { get; }

        /// <summary>Register the module's properties (called once per game load).</summary>
        void Init(ProDashPlugin plugin);

        /// <summary>
        /// Recompute and publish values. Called only when a live iRacing session is running.
        /// <paramref name="frame"/> is a 0..59 counter for cheap rate-limiting.
        /// </summary>
        void Update(ProDashPlugin plugin, GameData data, IRacingRaw raw, int frame);

        /// <summary>Reset internal state (new session / game stopped).</summary>
        void Reset();
    }
}
