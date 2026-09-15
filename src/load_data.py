"""Helpers for loading the raw WPRDC bike datasets into tidy DataFrames/GeoDataFrames."""
import glob
import json
import os
import re

import geopandas as gpd
import pandas as pd

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed")

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}


def _parse_month_year(filename: str):
    m = re.search(r"([a-z]+)-(\d{4})", filename.lower())
    if not m or m.group(1) not in _MONTHS:
        return None
    return pd.Timestamp(year=int(m.group(2)), month=_MONTHS[m.group(1)], day=1)


def load_trips(pattern: str = "trips/*.xlsx", use_cache: bool = True) -> pd.DataFrame:
    """Load and concatenate all POGOH trip-data files matching `pattern`.

    Parsing 52 months of .xlsx is slow (~1-2 min), so the tidied result is cached to
    data/processed/trips_cache.parquet. Pass use_cache=False to force a re-read of the raw files
    (e.g. after running fetch_data.py again).
    """
    cache_path = os.path.join(PROCESSED_DIR, "trips_cache.parquet")
    if use_cache and pattern == "trips/*.xlsx" and os.path.exists(cache_path):
        return pd.read_parquet(cache_path)

    paths = sorted(glob.glob(os.path.join(RAW_DIR, pattern)))
    if not paths:
        raise FileNotFoundError(f"No trip files found matching {pattern}. Run src/fetch_data.py first.")
    frames = [pd.read_excel(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    bad_rows = df["start_date"].isna().sum()
    if bad_rows:
        print(f"load_trips: dropping {bad_rows} row(s) with unparseable start_date")
        df = df.dropna(subset=["start_date"])
    df["duration_min"] = df["duration"] / 60

    if pattern == "trips/*.xlsx":
        os.makedirs(PROCESSED_DIR, exist_ok=True)
        df.to_parquet(cache_path)
    return df


def load_stations() -> pd.DataFrame:
    """Load the latest POGOH station-locations file."""
    paths = sorted(glob.glob(os.path.join(RAW_DIR, "stations", "*.xlsx")))
    if not paths:
        raise FileNotFoundError("No station files found. Run src/fetch_data.py first.")
    df = pd.read_excel(paths[-1])
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    return df


def load_station_history() -> pd.DataFrame:
    """Load every historical station-locations snapshot into one long DataFrame with a
    `snapshot_date` column, for tracking network growth (station count, dock count) over time."""
    paths = sorted(glob.glob(os.path.join(RAW_DIR, "stations", "history", "*.xlsx")))
    if not paths:
        raise FileNotFoundError("No station history found. Run: python src/fetch_data.py")
    frames = []
    for p in paths:
        snap_date = _parse_month_year(os.path.basename(p))
        if snap_date is None:
            continue
        df = pd.read_excel(p)
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
        df["snapshot_date"] = snap_date
        frames.append(df)
    return pd.concat(frames, ignore_index=True).sort_values("snapshot_date")


def load_bike_lanes() -> gpd.GeoDataFrame:
    """Load the BikePGH bike-lanes GeoJSON as a GeoDataFrame in WGS84 (EPSG:4326)."""
    path = os.path.join(RAW_DIR, "infrastructure", "bike-lanes.geojson")
    if not os.path.exists(path):
        raise FileNotFoundError("No bike-lanes.geojson found. Run src/fetch_data.py first.")
    return gpd.read_file(path).to_crs(epsg=4326)


# slug -> (subfolder or filename, display label, map color)
BIKE_INFRASTRUCTURE_LAYERS = {
    "bike_lanes": ("bike-lanes.geojson", "Bike Lanes", "#1f77b4"),
    "protected_bike_lanes": ("protected-bike-lanes/*.shp", "Protected Bike Lanes", "#2ca02c"),
    "trails": ("trails/*.shp", "Trails", "#9467bd"),
    "sharrows": ("sharrows/*.shp", "Sharrows (Shared Lane Markings)", "#ff7f0e"),
    "on_street_bike_routes": ("on-street-bike-routes/*.shp", "On-Street Bike Routes", "#17becf"),
    "cautionary_bike_routes": ("cautionary-bike-routes/*.shp", "Cautionary Bike Routes", "#d62728"),
    "bikeable_sidewalks": ("bikeable-sidewalks/*.shp", "Bikeable Sidewalks", "#7f7f7f"),
}


def load_bike_infrastructure() -> dict:
    """Load every BikePGH bike-map GIS layer (lanes, protected lanes, trails, sharrows, on-street
    routes, cautionary routes, bikeable sidewalks) as a dict of {slug: (label, color, GeoDataFrame)},
    all reprojected to WGS84 (EPSG:4326) for web mapping."""
    result = {}
    for slug, (rel_pattern, label, color) in BIKE_INFRASTRUCTURE_LAYERS.items():
        matches = glob.glob(os.path.join(RAW_DIR, "infrastructure", rel_pattern))
        if not matches:
            print(f"load_bike_infrastructure: skipping {slug}, no file matching {rel_pattern}")
            continue
        gdf = gpd.read_file(matches[0]).to_crs(epsg=4326)
        result[slug] = (label, color, gdf)
    return result


def load_pavement_markings() -> pd.DataFrame:
    matches = glob.glob(os.path.join(RAW_DIR, "infrastructure", "*avement*.csv"))
    if not matches:
        raise FileNotFoundError("No pavement-markings CSV found. Run src/fetch_data.py first.")
    return pd.read_csv(matches[0])


def _parse_openmeteo_daily(path: str) -> pd.DataFrame:
    with open(path) as f:
        raw = json.load(f)["daily"]
    df = pd.DataFrame({
        "date": pd.to_datetime(raw["time"]),
        "temp_max_c": raw["temperature_2m_max"],
        "temp_min_c": raw["temperature_2m_min"],
        "precip_mm": raw["precipitation_sum"],
        "snow_cm": raw["snowfall_sum"],
    })
    df["temp_avg_c"] = (df["temp_max_c"] + df["temp_min_c"]) / 2
    return df


def load_weather() -> pd.DataFrame:
    """Load daily Pittsburgh weather (Open-Meteo archive, historical only) as a tidy DataFrame."""
    path = os.path.join(RAW_DIR, "weather", "pittsburgh_daily_weather.json")
    if not os.path.exists(path):
        raise FileNotFoundError("No weather data found. Run: python src/fetch_data.py")
    return _parse_openmeteo_daily(path)


def load_weather_forecast() -> pd.DataFrame:
    """Load the current 16-day weather FORECAST (Open-Meteo forecast API) - refreshed each time
    src/fetch_data.py runs, since forecasts change day to day. Use for genuinely future dates;
    load_weather() (the historical archive) has no data past today."""
    path = os.path.join(RAW_DIR, "weather", "pittsburgh_forecast.json")
    if not os.path.exists(path):
        raise FileNotFoundError("No weather forecast found. Run: python src/fetch_data.py")
    return _parse_openmeteo_daily(path)


def load_weather_with_forecast() -> pd.DataFrame:
    """Historical weather concatenated with the current forecast, for whatever dates a model
    needs - past dates come from the archive (ERA5 reanalysis, precise), any date from today
    onward comes from the forecast (updated daily, so re-run fetch_data.py close to when you need
    a forecast for the freshest numbers)."""
    hist = load_weather()
    fcst = load_weather_forecast()
    combined = pd.concat([hist[hist["date"] < fcst["date"].min()], fcst], ignore_index=True)
    return combined.sort_values("date").reset_index(drop=True)


def fetch_live_station_status() -> pd.DataFrame:
    """Pull POGOH's CURRENT real-time station status (bikes/docks available right now) directly
    from the operator's public GBFS feed. Live data, not cached to disk - call fresh each time."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from fetch_data import fetch_live_station_status as _fetch
    return pd.DataFrame(_fetch())
