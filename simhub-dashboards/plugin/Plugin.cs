using System;
using System.Collections.Generic;
using System.Windows.Controls;
using System.Windows.Media;
using GameReaderCommon;
using SimHub.Plugins;

namespace SimHubProDash
{
    /// <summary>
    /// SimHub Pro Dash — a free, open-source plugin that computes the "pro tier" iRacing
    /// telemetry SimHub doesn't expose natively (fuel-to-finish, relative + iRating/SoF,
    /// pit-stop predictor, proximity/spotter) and publishes them as SimHub properties for
    /// Dash Studio dashboards, LEDs and shakers to bind to.
    ///
    /// Property namespace: everything is published under "ProDash.*".
    /// (SimHub prefixes plugin properties, so in Dash Studio they appear as
    ///  [SimHubProDash.ProDash.Fuel.LapsToFinish] etc.)
    /// </summary>
    [PluginDescription("Free, open-source computed iRacing telemetry (fuel-to-finish, relative + iRating/SoF, pit predictor, proximity) for pro-grade dashboards.")]
    [PluginAuthor("Thomas Herbrig")]
    [PluginName("SimHub Pro Dash")]
    public class ProDashPlugin : IPlugin, IDataPlugin, IWPFSettingsV2
    {
        public PluginManager PluginManager { get; set; }
        public string LeftMenuTitle => "Pro Dash";
        public ImageSource PictureIcon => null;

        public PluginSettings Settings;

        private readonly List<IProModule> _modules = new List<IProModule>();
        private readonly IRacingRaw _raw = new IRacingRaw();

        private int _frame;
        private double _lastSessionUid = double.NaN;
        private int _lastSessionNum = int.MinValue;
        private bool _wasRunning;

        // ---- lifecycle ---------------------------------------------------------

        public void Init(PluginManager pluginManager)
        {
            System.Diagnostics.Trace.WriteLine("[ProDash] starting");

            Settings = this.ReadCommonSettings("GeneralSettings", () => new PluginSettings());

            _modules.Clear();
            _modules.Add(new FuelModule());
            _modules.Add(new RelativeModule());
            _modules.Add(new PitModule());
            _modules.Add(new ProximityModule());
            _modules.Add(new LeaderboardModule());
            _modules.Add(new SectorModule());

            AddProp("ProDash.Active", false);
            AddProp("ProDash.Version", PluginSettings.Version);

            foreach (var m in _modules)
            {
                try { m.Init(this); }
                catch (Exception e) { System.Diagnostics.Trace.WriteLine("[ProDash] init " + m.Name + ": " + e.Message); }
            }
        }

        public void DataUpdate(PluginManager pluginManager, ref GameData data)
        {
            _frame = (_frame + 1) % 60;

            bool active = data != null && data.GameRunning && data.NewData != null
                          && string.Equals(data.GameName, "IRacing", StringComparison.OrdinalIgnoreCase);

            SetProp("ProDash.Active", active);

            if (!active)
            {
                if (_wasRunning) ResetAll();
                _wasRunning = false;
                return;
            }
            _wasRunning = true;

            // Feed the raw iRacing sample to the reflection accessor.
            _raw.Update(data.NewData.GetRawDataObject());
            if (!_raw.Valid) return;

            // Session-change reset (same lesson as the Python overlays): clear all per-car
            // trackers when the session identity changes, else stale state poisons deltas.
            double uid = _raw.TelDouble("SessionUniqueID", double.NaN);
            int snum = _raw.TelInt("SessionNum", int.MinValue);
            if (uid != _lastSessionUid || snum != _lastSessionNum)
            {
                ResetAll();
                _lastSessionUid = uid;
                _lastSessionNum = snum;
            }

            foreach (var m in _modules)
            {
                try { m.Update(this, data, _raw, _frame); }
                catch (Exception e)
                {
                    if (_frame == 0) System.Diagnostics.Trace.WriteLine("[ProDash] update " + m.Name + ": " + e.Message);
                }
            }
        }

        public void End(PluginManager pluginManager)
        {
            this.SaveCommonSettings("GeneralSettings", Settings);
        }

        public Control GetWPFSettingsControl(PluginManager pluginManager)
        {
            // Minimal, code-only settings panel (no XAML) so the project builds without a
            // compiled resource. Replace with a proper UserControl later if needed.
            var panel = new StackPanel { Margin = new System.Windows.Thickness(16) };
            panel.Children.Add(new TextBlock
            {
                Text = "SimHub Pro Dash",
                FontSize = 20,
                FontWeight = System.Windows.FontWeights.Bold,
                Margin = new System.Windows.Thickness(0, 0, 0, 8)
            });
            panel.Children.Add(new TextBlock
            {
                Text = "Free, open-source computed iRacing telemetry. Bind the ProDash.* "
                     + "properties in Dash Studio. See PROPERTIES.md in the repo.",
                TextWrapping = System.Windows.TextWrapping.Wrap,
                Opacity = 0.8
            });
            // IWPFSettingsV2 requires a Control; StackPanel is not one, so wrap it.
            return new UserControl { Content = panel };
        }

        private void ResetAll()
        {
            foreach (var m in _modules)
            {
                try { m.Reset(); } catch { /* ignore */ }
            }
        }

        // ---- property helpers (mirror the community convention) ----------------

        public void AddProp(string name, object defaultValue)
            => PluginManager.AddProperty(name, GetType(), defaultValue);

        public void SetProp(string name, object value)
            => PluginManager.SetPropertyValue(name, GetType(), value);
    }
}
