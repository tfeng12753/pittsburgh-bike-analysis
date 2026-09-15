"""
Build a minimal, self-contained interactive Leaflet map combining:
  - live station status (bikes/docks available now, broken out by e-bike vs classic bike)
  - a bi-hourly demand-prediction timeline (scrub via a slider) for the 7 days after the trip data ends
  - toggleable bike-infrastructure overlays (lanes, protected lanes, trails, sharrows, routes, sidewalks)

Deliberately avoids folium's default template (which pulls in Bootstrap, FontAwesome,
Leaflet.awesome-markers, D3, and a layer-control plugin from half a dozen CDNs, including one
dead one) - here it's just Leaflet (1 CSS + 1 JS from a single CDN) plus plain HTML/CSS/JS.
"""
import json

import pandas as pd


def build_demand_map(timeline_df, live_df, infra_layers, output_path, center=(40.4406, -79.9959)):
    """
    timeline_df: one row per (bin, station) - bi-hourly. Columns: station_id, name, latitude,
        longitude, total_docks, bin (Timestamp), pred_arrivals, pred_departures, pred_net_flow,
        pred_bikes_available (simulated stock, seeded from the live count - see notebook section 4),
        ebike_frac (each station's live e-bike fraction, held constant - used client-side to derive
        an e-bike/classic split for every metric, not just stock).
    live_df: one row per station (right now). Columns: station_id, name, capacity,
        num_bikes_available, num_docks_available, num_classic_available, num_ebike_available.
    infra_layers: dict {slug: (label, color, GeoDataFrame)} - e.g. from load_bike_infrastructure().
    """
    bins = sorted(timeline_df["bin"].unique())

    stations = {}
    for _, row in timeline_df.drop_duplicates("station_id").iterrows():
        stations[int(row["station_id"])] = {
            "name": row["name"],
            "lat": float(row["latitude"]),
            "lon": float(row["longitude"]),
            "docks": int(row["total_docks"]),
            # One value per station (not per bin) - cheap way to let the client derive e-bike/classic
            # splits for ANY metric (arrivals, departures, net flow, stock) from the aggregate value,
            # instead of doubling every per-bin field. See notebook section 4 for how it's computed
            # (each station's current live e-bike fraction, held constant going forward).
            "ebikeFrac": round(float(row["ebike_frac"]), 3) if "ebike_frac" in row else 0.5,
        }

    # The timeline repeats this per-station record 84 times (7 days x 12 bins), so short keys
    # (a/d/n/b instead of arrivals/departures/net_flow/bikes) meaningfully shrink the file -
    # verbose keys here previously pushed the map over this tool's local-file preview size limit.
    timeline = []
    for b in bins:
        bin_df = timeline_df[timeline_df["bin"] == b]
        b_ts = pd.Timestamp(b)
        date_label = b_ts.strftime("%a %b %d")
        hour = b_ts.hour
        hour_label = f"{_fmt_hour(hour)}–{_fmt_hour((hour + 2) % 24)}"
        timeline.append({
            "dl": date_label,
            "hl": hour_label,
            "v": {
                int(r["station_id"]): {
                    "a": round(float(r["pred_arrivals"]), 1),
                    "d": round(float(r["pred_departures"]), 1),
                    "n": round(float(r["pred_net_flow"]), 1),
                    "b": round(float(r["pred_bikes_available"])),
                }
                for _, r in bin_df.iterrows()
            },
        })

    live_values = {}
    for _, r in live_df.iterrows():
        sid = int(r["station_id"])
        cap = r.get("capacity") or stations.get(sid, {}).get("docks") or 1
        bikes = int(r["num_bikes_available"])
        live_values[sid] = {
            "bikes_available": bikes,
            "docks_available": int(r["num_docks_available"]),
            "capacity": int(cap),
            "pct_full": round(100 * bikes / max(int(cap), 1), 1),
            "classic": int(r.get("num_classic_available", 0)),
            "ebike": int(r.get("num_ebike_available", 0)),
        }
    live_reported = live_df["last_reported"].max() if "last_reported" in live_df.columns and len(live_df) else None

    infra_geojson = {}
    infra_meta = {}
    for slug, (label, color, gdf) in infra_layers.items():
        # Drop all attribute columns (only geometry is rendered; label comes from infra_meta),
        # simplify geometry (~11m tolerance), and round coordinates to 5 decimals (~1m precision)
        # - the shapefiles reproject from a state-plane CRS with far more vertices/precision than
        # a city-scale web map needs, and untrimmed it bloats file size a lot (was ~560KB just for
        # these 7 layers; this gets it under 150KB with no visible difference at city zoom levels).
        slim = gdf[["geometry"]].copy()
        slim["geometry"] = slim.geometry.simplify(0.0001)
        gj = json.loads(slim.to_json())
        for feat in gj["features"]:
            feat["geometry"]["coordinates"] = _round_coords(feat["geometry"]["coordinates"])
        infra_geojson[slug] = gj
        infra_meta[slug] = {"label": label, "color": color}

    payload = {
        "stations": stations,
        "live": {
            "label": "Live now" + (f" ({live_reported[:16].replace('T', ' ')} UTC)" if live_reported else ""),
            "values": live_values,
        },
        "timeline": timeline,
        "infraMeta": infra_meta,
    }

    # Compact JSON (no whitespace) keeps size down, but as ONE giant line it broke this tool's
    # local-file preview (probably a data-URL length cap) - _chunk_for_js splits it into a JS
    # string-concatenation + JSON.parse so every line stays short without bloating the payload.
    html = _TEMPLATE.replace("__DATA__", _chunk_for_js(payload)).replace(
        "__INFRA__", _chunk_for_js(infra_geojson)
    ).replace("__CENTER__", json.dumps(list(center)))
    with open(output_path, "w") as f:
        f.write(html)
    return output_path


def _round_coords(coords, ndigits=5):
    if isinstance(coords, (int, float)):
        return round(coords, ndigits)
    return [_round_coords(c, ndigits) for c in coords]


def _chunk_for_js(obj, chunk_size=2000):
    """Serialize obj to compact JSON, then emit it as 'JSON.parse("chunk1"+"chunk2"+...)' so no
    single line is too long, without the size overhead of pretty-printing."""
    raw = json.dumps(obj, separators=(",", ":"))
    chunks = [raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size)]
    joined = " +\n".join(json.dumps(c) for c in chunks)
    return f"JSON.parse(\n{joined}\n)"


def _fmt_hour(h):
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}{suffix}"


_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>POGOH Station Demand</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  html, body, #map { height: 100%; margin: 0; font-family: -apple-system, Helvetica, Arial, sans-serif; }
  .panel {
    position: absolute; z-index: 1000; background: white; border-radius: 8px;
    box-shadow: 0 1px 6px rgba(0,0,0,0.3); padding: 10px 14px; font-size: 13px;
  }
  #controls { top: 12px; right: 12px; width: 230px; max-height: 45vh; overflow-y: auto; }
  #infra-panel { top: 12px; left: 12px; max-height: 90vh; overflow-y: auto; }
  #legend { bottom: 20px; left: 12px; }
  .panel h4 { margin: 0 0 6px 0; font-size: 13px; }
  .panel label { display: block; padding: 2px 0; cursor: pointer; white-space: nowrap; }
  .swatch { display: inline-block; width: 14px; height: 3px; margin-right: 6px; vertical-align: middle; }
  #time-readout { font-weight: bold; margin: 6px 0 2px 0; text-align: center; }
  #time-slider { width: 100%; }
  #legend-bar { width: 180px; height: 10px; border-radius: 5px; margin: 4px 0; }
  #legend-labels { display: flex; justify-content: space-between; width: 180px; }
  .popup-table td { padding: 1px 6px 1px 0; }
  hr { border: none; border-top: 1px solid #ddd; margin: 8px 0; }
</style>
</head>
<body>
<div id="map"></div>

<div id="controls" class="panel">
  <h4>Station data</h4>
  <label><input type="radio" name="mode" id="mode_live" checked> <span id="live-label"></span></label>
  <label><input type="radio" name="mode" id="mode_predicted"> Predicted (bi-hourly)</label>
  <div id="live-sub" style="margin-left:18px;">
    <label><input type="radio" name="livesub" value="total" checked> Total bikes available</label>
    <label><input type="radio" name="livesub" value="ebike"> E-bikes only</label>
    <label><input type="radio" name="livesub" value="classic"> Classic bikes only</label>
  </div>
  <div id="predicted-sub" style="display:none;">
    <label><input type="radio" name="predsub" value="net_flow" checked> Net flow (arrivals - departures)</label>
    <label><input type="radio" name="predsub" value="bikes"> Predicted bikes available</label>
    <div id="predicted-bike-type-sub" style="margin-left:18px;">
      <label><input type="radio" name="predbiketype" value="total" checked> Total</label>
      <label><input type="radio" name="predbiketype" value="ebike"> E-bikes only</label>
      <label><input type="radio" name="predbiketype" value="classic"> Classic bikes only</label>
    </div>
    <div id="time-readout"></div>
    <input type="range" id="time-slider" min="0" max="0" value="0" step="1">
  </div>
</div>

<div id="infra-panel" class="panel">
  <h4>Bike infrastructure</h4>
  <div id="infra-checks"></div>
</div>

<div id="legend" class="panel">
  <div id="legend-title"></div>
  <div id="legend-bar"></div>
  <div id="legend-labels"><span id="legend-min"></span><span id="legend-max"></span></div>
</div>

<script>
const DATA = __DATA__;
const INFRA = __INFRA__;
const map = L.map('map').setView(__CENTER__, 13);
// Esri's free basemap CDN, not OSM's volunteer-run tile.openstreetmap.org - that server's usage
// policy (operations.osmfoundation.org/policies/tiles) reserves it for light evaluation use, not
// embedding in apps/tools. Esri's World Street Map tiles are commercial CDN infra meant for this.
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
  attribution: 'Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ', maxZoom: 19
}).addTo(map);

document.getElementById('live-label').innerText = DATA.live.label;

// "Live now" is otherwise just a snapshot baked in whenever this file was last generated (could
// be up to a day stale). POGOH's own GBFS feed doesn't send Access-Control-Allow-Origin, so a
// browser can't fetch it directly (confirmed by hand) - this tiny proxy (see proxy/app.py) does
// the same fetch server-to-server and re-serves it with CORS headers, so we can poll it here for
// genuinely live data. If you redeployed the proxy under a different URL, update this constant.
const LIVE_PROXY_URL = 'https://pogoh-live-proxy.onrender.com/live-status';
const LIVE_POLL_MS = 5 * 60 * 1000;

function lerpColor(t, cLow, cMid, cHigh) {
  function mix(a, b, f) { return a.map((v, i) => Math.round(v + (b[i]-v)*f)); }
  const c = t < 0 ? mix(cMid, cLow, -t) : mix(cMid, cHigh, t);
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
const RED = [214, 39, 40], WHITE = [247, 247, 247], GREEN = [44, 160, 44];

// --- infrastructure overlays (off by default except bike lanes, to avoid clutter) ---
const infraLayers = {};
const infraChecksDiv = document.getElementById('infra-checks');
const defaultOn = new Set(['bike_lanes', 'protected_bike_lanes']);
for (const [slug, geojson] of Object.entries(INFRA)) {
  const meta = DATA.infraMeta[slug];
  const layer = L.geoJSON(geojson, {
    style: { color: meta.color, weight: 3, opacity: 0.8 },
    onEachFeature: (f, l) => l.bindPopup(`<b>${meta.label}</b>`)
  });
  infraLayers[slug] = layer;
  if (defaultOn.has(slug)) layer.addTo(map);

  const label = document.createElement('label');
  label.innerHTML = `<input type="checkbox" ${defaultOn.has(slug) ? 'checked' : ''}> ` +
    `<span class="swatch" style="background:${meta.color}"></span>${meta.label}`;
  infraChecksDiv.appendChild(label);
  label.querySelector('input').addEventListener('change', (e) => {
    if (e.target.checked) layer.addTo(map); else map.removeLayer(layer);
  });
}

// --- station markers ---
const markers = {};
for (const [sid, s] of Object.entries(DATA.stations)) {
  const m = L.circleMarker([s.lat, s.lon], {radius: 6, weight: 1, fillOpacity: 0.85}).addTo(map);
  markers[sid] = m;
}

let mode = 'live';
let liveSub = 'total';
let predSub = 'net_flow';
let predBikeType = 'total';

function renderLive() {
  document.getElementById('legend-title').innerText =
    liveSub === 'total' ? '% of docks with a bike available now'
    : liveSub === 'ebike' ? 'E-bikes available now' : 'Classic bikes available now';
  document.getElementById('legend-bar').style.background = 'linear-gradient(to right, rgb(214,39,40), rgb(247,247,247), rgb(44,160,44))';
  document.getElementById('legend-min').innerText = liveSub === 'total' ? '0% (empty)' : 'none available';
  document.getElementById('legend-max').innerText = liveSub === 'total' ? '100% (full)' : 'plenty available';

  for (const [sid, s] of Object.entries(DATA.stations)) {
    const v = DATA.live.values[sid];
    const m = markers[sid];
    if (!v) { m.setStyle({opacity: 0, fillOpacity: 0}); continue; }
    let value, maxVal, color, radius;
    if (liveSub === 'total') {
      value = v.bikes_available; maxVal = v.capacity;
      const t = (v.pct_full - 50) / 50;
      color = lerpColor(t, RED, WHITE, GREEN);
      radius = 5 + Math.sqrt(value) * 2;
    } else if (liveSub === 'ebike') {
      value = v.ebike; maxVal = Math.max(v.capacity, 1);
      const t = Math.min(1, value / 6) * 2 - 1;
      color = lerpColor(Math.max(-1,t), RED, WHITE, GREEN);
      radius = 5 + Math.sqrt(value) * 2.5;
    } else {
      value = v.classic; maxVal = Math.max(v.capacity, 1);
      const t = Math.min(1, value / 6) * 2 - 1;
      color = lerpColor(Math.max(-1,t), RED, WHITE, GREEN);
      radius = 5 + Math.sqrt(value) * 2.5;
    }
    m.setStyle({opacity: 1, fillOpacity: 0.85, radius, color, fillColor: color});
    m.bindPopup(`<b>${s.name}</b><table class="popup-table">
      <tr><td>Bikes available:</td><td><b>${v.bikes_available}</b></td></tr>
      <tr><td>&nbsp;&nbsp;- classic:</td><td>${v.classic}</td></tr>
      <tr><td>&nbsp;&nbsp;- e-bike:</td><td>${v.ebike}</td></tr>
      <tr><td>Docks available:</td><td>${v.docks_available}</td></tr>
      <tr><td>Capacity:</td><td>${v.capacity}</td></tr>
      <tr><td>% full:</td><td>${v.pct_full}%</td></tr></table>`);
  }
}

function renderPredicted(idx) {
  // Timeline records use short keys (dl/hl/v/a/d/n/b) to keep the payload small - see build_map.py.
  // E-bike/classic breakdowns for EVERY metric (arrivals, departures, net flow, stock) are derived
  // client-side from each station's ebikeFrac rather than sent per-bin, to keep the payload small -
  // it's the same live-fraction-held-constant approximation either way (see notebook section 4).
  const slot = DATA.timeline[idx];
  document.getElementById('time-readout').innerText = `${slot.dl}, ${slot.hl}`;
  const typeLabel = predBikeType === 'ebike' ? 'e-bike' : predBikeType === 'classic' ? 'classic' : null;

  if (predSub === 'net_flow') {
    document.getElementById('legend-title').innerText =
      'Predicted net flow' + (typeLabel ? ` - ${typeLabel}` : '') + ' (arrivals - departures)';
    document.getElementById('legend-bar').style.background = 'linear-gradient(to right, rgb(214,39,40), rgb(247,247,247), rgb(44,160,44))';
    document.getElementById('legend-min').innerText = 'losing bikes';
    document.getElementById('legend-max').innerText = 'gaining bikes';
  } else {
    document.getElementById('legend-title').innerText =
      'Predicted' + (typeLabel ? ` ${typeLabel}` : '') + ' bikes available (simulated stock)';
    document.getElementById('legend-bar').style.background = 'linear-gradient(to right, rgb(214,39,40), rgb(247,247,247), rgb(44,160,44))';
    document.getElementById('legend-min').innerText = predBikeType === 'total' ? '0% (empty)' : 'none available';
    document.getElementById('legend-max').innerText = predBikeType === 'total' ? '100% (full)' : 'plenty available';
  }

  for (const [sid, s] of Object.entries(DATA.stations)) {
    const v = slot.v[sid];
    const m = markers[sid];
    if (!v) { m.setStyle({opacity: 0, fillOpacity: 0}); continue; }
    const frac = s.ebikeFrac;
    const a_e = v.a * frac, a_c = v.a - a_e;
    const d_e = v.d * frac, d_c = v.d - d_e;
    const n_e = a_e - d_e, n_c = a_c - d_c;
    const b_e = Math.round(v.b * frac), b_c = v.b - b_e;

    let color, radius;
    if (predSub === 'net_flow') {
      const n = predBikeType === 'ebike' ? n_e : predBikeType === 'classic' ? n_c : v.n;
      const a = predBikeType === 'ebike' ? a_e : predBikeType === 'classic' ? a_c : v.a;
      const vmax = predBikeType === 'total' ? 4 : 2.5;
      const t = Math.max(-1, Math.min(1, n / vmax));
      color = lerpColor(t, RED, WHITE, GREEN);
      radius = 4 + Math.sqrt(Math.max(a, 0)) * 2.2;
    } else {
      const value = predBikeType === 'ebike' ? b_e : predBikeType === 'classic' ? b_c : v.b;
      const t = predBikeType === 'total'
        ? (100 * value / Math.max(s.docks, 1) - 50) / 50
        : Math.min(1, value / 6) * 2 - 1;
      color = lerpColor(Math.max(-1, Math.min(1, t)), RED, WHITE, GREEN);
      radius = 5 + Math.sqrt(Math.max(value, 0)) * 2;
    }
    m.setStyle({opacity: 1, fillOpacity: 0.85, radius, color, fillColor: color});
    m.bindPopup(`<b>${s.name}</b> - ${slot.dl} ${slot.hl}<table class="popup-table">
      <tr><td>Predicted arrivals:</td><td><b>${v.a.toFixed(1)}</b> (classic ${a_c.toFixed(1)}, e-bike ${a_e.toFixed(1)})</td></tr>
      <tr><td>Predicted departures:</td><td>${v.d.toFixed(1)} (classic ${d_c.toFixed(1)}, e-bike ${d_e.toFixed(1)})</td></tr>
      <tr><td>Predicted net flow:</td><td>${v.n > 0 ? '+' : ''}${v.n.toFixed(1)} (classic ${n_c > 0 ? '+' : ''}${n_c.toFixed(1)}, e-bike ${n_e > 0 ? '+' : ''}${n_e.toFixed(1)})</td></tr>
      <tr><td>Predicted bikes available:</td><td>${v.b} (classic ${b_c}, e-bike ${b_e})</td></tr>
      <tr><td>Docks:</td><td>${s.docks}</td></tr></table>`);
  }
}

const slider = document.getElementById('time-slider');
slider.max = DATA.timeline.length - 1;
slider.addEventListener('input', () => renderPredicted(parseInt(slider.value)));

document.getElementById('mode_live').addEventListener('change', () => {
  mode = 'live';
  document.getElementById('live-sub').style.display = 'block';
  document.getElementById('predicted-sub').style.display = 'none';
  renderLive();
});
document.getElementById('mode_predicted').addEventListener('change', () => {
  mode = 'predicted';
  document.getElementById('live-sub').style.display = 'none';
  document.getElementById('predicted-sub').style.display = 'block';
  renderPredicted(parseInt(slider.value));
});
document.querySelectorAll('input[name=livesub]').forEach(el => el.addEventListener('change', (e) => {
  liveSub = e.target.value;
  renderLive();
}));
document.querySelectorAll('input[name=predsub]').forEach(el => el.addEventListener('change', (e) => {
  predSub = e.target.value;
  renderPredicted(parseInt(slider.value));
}));
document.querySelectorAll('input[name=predbiketype]').forEach(el => el.addEventListener('change', (e) => {
  predBikeType = e.target.value;
  renderPredicted(parseInt(slider.value));
}));

// Allow deep-linking straight into a mode, e.g. from a homepage: ?mode=predicted or ?mode=live
const urlMode = new URLSearchParams(window.location.search).get('mode');
if (urlMode === 'predicted') {
  document.getElementById('mode_predicted').checked = true;
  document.getElementById('mode_predicted').dispatchEvent(new Event('change'));
} else {
  renderLive();
}

async function refreshLiveData() {
  try {
    const resp = await fetch(LIVE_PROXY_URL, { cache: 'no-store' });
    if (!resp.ok) throw new Error(`proxy returned ${resp.status}`);
    const payload = await resp.json();
    const values = {};
    let maxReported = null;
    for (const s of payload.stations) {
      const sid = s.station_id;
      const cap = s.capacity || (DATA.stations[sid] && DATA.stations[sid].docks) || 1;
      const bikes = s.num_bikes_available;
      values[sid] = {
        bikes_available: bikes,
        docks_available: s.num_docks_available,
        capacity: cap,
        pct_full: Math.round(1000 * bikes / Math.max(cap, 1)) / 10,
        classic: s.num_classic_available,
        ebike: s.num_ebike_available,
      };
      if (s.last_reported && (!maxReported || s.last_reported > maxReported)) maxReported = s.last_reported;
    }
    DATA.live.values = values;
    let label = maxReported ? `Live now (${maxReported.slice(0, 16).replace('T', ' ')} UTC)` : 'Live now';
    if (payload.stale) label += ' - proxy could not reach POGOH, showing last known data';
    DATA.live.label = label;
    document.getElementById('live-label').innerText = label;
    if (mode === 'live') renderLive();
  } catch (e) {
    // Leave whatever was baked in at build time on screen - better than a broken page.
    console.warn('Live refresh failed, keeping last known data:', e);
  }
}

refreshLiveData();
setInterval(refreshLiveData, LIVE_POLL_MS);
</script>
</body>
</html>
"""
