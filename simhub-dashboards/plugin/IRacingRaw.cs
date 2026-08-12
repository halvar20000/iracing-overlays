using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;

namespace SimHubProDash
{
    /// <summary>
    /// Reflection-based accessor for the raw iRacing SDK sample that SimHub hands us via
    /// <c>data.NewData.GetRawDataObject()</c> (an <c>IRacingReader.DataSampleEx</c>).
    ///
    /// Why reflection instead of a hard reference to IRacingReader / iRacingSDK:
    ///  - The plugin then compiles with only SimHub.Plugins + GameReaderCommon referenced,
    ///    so it can be authored and reviewed without those DLLs in hand.
    ///  - The iRacing SDK channel names (CarIdxLapDistPct, DriverInfo.Drivers[].IRating, ...)
    ///    are STABLE and are exactly the ones this project's Python overlays already use, so
    ///    binding by name is safe and future-proof across IRacingReader versions.
    ///
    /// All PropertyInfo lookups are cached, so per-frame cost is a dictionary hit + a call.
    /// Every getter is null-safe and returns a default on any miss.
    /// </summary>
    public class IRacingRaw
    {
        private object _raw;      // DataSampleEx
        private object _tel;      // .Telemetry
        private object _sess;     // .SessionData

        private static readonly Dictionary<string, PropertyInfo> _propCache =
            new Dictionary<string, PropertyInfo>();

        public bool Valid { get; private set; }

        /// <summary>Feed the object returned by GameData.NewData.GetRawDataObject().</summary>
        public void Update(object rawObject)
        {
            _raw = rawObject;
            _tel = GetMember(_raw, "Telemetry");
            _sess = GetMember(_raw, "SessionData");
            Valid = _raw != null && _tel != null;
        }

        // ---- telemetry scalars -------------------------------------------------

        public float TelFloat(string channel, float fallback = 0f)
            => ToFloat(GetTel(channel), fallback);

        public double TelDouble(string channel, double fallback = 0.0)
            => ToDouble(GetTel(channel), fallback);

        public int TelInt(string channel, int fallback = 0)
            => ToInt(GetTel(channel), fallback);

        public bool TelBool(string channel, bool fallback = false)
        {
            var v = GetTel(channel);
            if (v is bool b) return b;
            return ToInt(v, fallback ? 1 : 0) != 0;
        }

        // ---- telemetry arrays (per-car channels) -------------------------------

        public float[] TelFloatArray(string channel) => ToFloatArray(GetTel(channel));
        public int[] TelIntArray(string channel) => ToIntArray(GetTel(channel));
        public bool[] TelBoolArray(string channel) => ToBoolArray(GetTel(channel));

        // ---- session / driver info --------------------------------------------

        /// <summary>SessionData.DriverInfo.PaceCarIdx-safe enumeration of Driver objects.</summary>
        public IEnumerable<object> Drivers()
        {
            var driverInfo = GetMember(_sess, "DriverInfo");
            var drivers = GetMember(driverInfo, "Drivers");
            if (drivers is IEnumerable en)
                foreach (var d in en)
                    if (d != null) yield return d;
        }

        public int PlayerCarIdx()
        {
            // Prefer telemetry (updates live); fall back to DriverInfo.
            var v = GetTel("PlayerCarIdx");
            if (v != null) return ToInt(v, -1);
            var di = GetMember(_sess, "DriverInfo");
            return ToInt(GetMember(di, "DriverCarIdx"), -1);
        }

        /// <summary>Read a named member off a Driver object (from Drivers()).</summary>
        public object Driver(object driver, string member) => GetMember(driver, member);

        /// <summary>Read SessionData.WeekendInfo.&lt;member&gt; as a string (e.g. "TrackLength").</summary>
        public string WeekendString(string member)
        {
            var wi = GetMember(_sess, "WeekendInfo");
            var v = GetMember(wi, member);
            return v?.ToString();
        }

        /// <summary>SessionData.SplitTimeInfo.Sectors[].SectorStartPct (sector boundaries, 0..1).</summary>
        public double[] SectorStartPcts()
        {
            var sti = GetMember(_sess, "SplitTimeInfo");
            var sectors = GetMember(sti, "Sectors");
            var list = new List<double>();
            if (sectors is IEnumerable en)
                foreach (var s in en)
                {
                    var v = GetMember(s, "SectorStartPct");
                    if (v != null) { try { list.Add(Convert.ToDouble(v)); } catch { } }
                }
            return list.ToArray();
        }

        // ---- reflection plumbing ----------------------------------------------

        private object GetTel(string channel)
        {
            if (_tel == null) return null;
            var v = GetMember(_tel, channel);
            if (v != null) return v;
            // Some IRacingReader builds expose channels via a string indexer instead of
            // named properties. Try the indexer as a fallback.
            return GetIndexer(_tel, channel);
        }

        private static object GetMember(object obj, string name)
        {
            if (obj == null) return null;
            var t = obj.GetType();
            var key = t.FullName + "::" + name;
            PropertyInfo pi;
            if (!_propCache.TryGetValue(key, out pi))
            {
                pi = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
                _propCache[key] = pi; // cache even nulls to avoid repeated lookups
            }
            if (pi != null)
            {
                try { return pi.GetValue(obj, null); } catch { return null; }
            }
            var fi = t.GetField(name, BindingFlags.Public | BindingFlags.Instance);
            if (fi != null)
            {
                try { return fi.GetValue(obj); } catch { return null; }
            }
            return null;
        }

        private static object GetIndexer(object obj, string key)
        {
            if (obj == null) return null;
            try
            {
                var pi = obj.GetType().GetProperty("Item",
                    BindingFlags.Public | BindingFlags.Instance, null, typeof(object),
                    new[] { typeof(string) }, null);
                if (pi != null) return pi.GetValue(obj, new object[] { key });
            }
            catch { }
            return null;
        }

        // ---- conversions -------------------------------------------------------

        private static float ToFloat(object v, float f) { try { return v == null ? f : Convert.ToSingle(v); } catch { return f; } }
        private static double ToDouble(object v, double f) { try { return v == null ? f : Convert.ToDouble(v); } catch { return f; } }
        private static int ToInt(object v, int f) { try { return v == null ? f : Convert.ToInt32(v); } catch { return f; } }

        private static float[] ToFloatArray(object v)
        {
            if (v is float[] fa) return fa;
            if (v is double[] da) { var r = new float[da.Length]; for (int i = 0; i < da.Length; i++) r[i] = (float)da[i]; return r; }
            if (v is IEnumerable en && !(v is string))
            {
                var list = new List<float>();
                foreach (var o in en) list.Add(ToFloat(o, 0f));
                return list.ToArray();
            }
            return Array.Empty<float>();
        }

        private static int[] ToIntArray(object v)
        {
            if (v is int[] ia) return ia;
            if (v is IEnumerable en && !(v is string))
            {
                var list = new List<int>();
                foreach (var o in en) list.Add(ToInt(o, 0));
                return list.ToArray();
            }
            return Array.Empty<int>();
        }

        private static bool[] ToBoolArray(object v)
        {
            if (v is bool[] ba) return ba;
            if (v is IEnumerable en && !(v is string))
            {
                var list = new List<bool>();
                foreach (var o in en)
                {
                    if (o is bool b) list.Add(b);
                    else list.Add(ToInt(o, 0) != 0);
                }
                return list.ToArray();
            }
            return Array.Empty<bool>();
        }
    }
}
