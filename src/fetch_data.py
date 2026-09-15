"""
Download Pittsburgh bike datasets from the WPRDC (Western PA Regional Data
Center) open data portal, which runs on CKAN.

Usage:
    python src/fetch_data.py                  # fetch full trip history + all station snapshots + infrastructure
    python src/fetch_data.py --trip-months 12  # only last 12 months of trip data
    python src/fetch_data.py --skip-trips      # only fetch stations + infrastructure

All datasets are public, no API key required.
"""
import argparse
import json
import os
import zipfile

import requests

CKAN_BASE = "https://data.wprdc.org/api/3/action"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
GBFS_DISCOVERY = "https://pittsburgh.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json"

TRIP_DATA_PACKAGE = "pogoh-trip-data"
STATIONS_PACKAGE = "station-locations"
BIKE_MAP_PACKAGE = "shape-files-for-bikepgh-s-pittsburgh-bike-map"
PAVEMENT_MARKINGS_PACKAGE = "on-road-bicycle-pavement-markings"

PITTSBURGH_LAT, PITTSBURGH_LON = 40.4406, -79.9959

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def package_show(package_id: str) -> dict:
    resp = requests.get(f"{CKAN_BASE}/package_show", params={"id": package_id}, timeout=30)
    resp.raise_for_status()
    return resp.json()["result"]


def download(url: str, dest_path: str) -> None:
    if os.path.exists(dest_path):
        print(f"  skip (exists): {os.path.basename(dest_path)}")
        return
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    print(f"  downloaded: {os.path.basename(dest_path)} ({len(resp.content):,} bytes)")


def fetch_trip_data(n_months: int) -> None:
    print(f"Fetching latest {n_months} month(s) of POGOH trip data...")
    pkg = package_show(TRIP_DATA_PACKAGE)
    resources = [r for r in pkg["resources"] if r["format"].upper() in ("XLSX", "CSV")]
    for r in resources[:n_months]:
        ext = r["format"].lower()
        safe_name = r["name"].lower().replace(" ", "-").replace("---", "-")
        dest = os.path.join(RAW_DIR, "trips", f"{safe_name}.{ext}")
        download(r["url"], dest)


def fetch_stations() -> None:
    print("Fetching latest POGOH station locations...")
    pkg = package_show(STATIONS_PACKAGE)
    latest = pkg["resources"][0]
    dest = os.path.join(RAW_DIR, "stations", f"stations-latest.{latest['format'].lower()}")
    download(latest["url"], dest)


def fetch_stations_history() -> None:
    """Download every historical station-locations snapshot (network grew from ~50 to 60+ stations
    over 2022-2026), so growth of the station network over time can be tracked."""
    print("Fetching all historical POGOH station-location snapshots...")
    pkg = package_show(STATIONS_PACKAGE)
    for r in pkg["resources"]:
        safe_name = r["name"].lower().replace(" ", "-").replace("--", "-")
        dest = os.path.join(RAW_DIR, "stations", "history", f"{safe_name}.{r['format'].lower()}")
        download(r["url"], dest)


BIKE_MAP_CATEGORIES = {
    "bike-lanes": "August 2019 Bike Lanes",
    "protected-bike-lanes": "August 2019 Protected Bike Lanes",
    "sharrows": "August 2019 Sharrows",
    "trails": "August 2019 Trails",
    "cautionary-bike-routes": "August 2019 Cautionary Bike Routes",
    "on-street-bike-routes": "August 2019 On Street Bike Routes",
    "bikeable-sidewalks": "August 2019 Bikeable Sidewalks",
}


def fetch_infrastructure() -> None:
    print("Fetching bike lane / trail / sidewalk GIS layers (BikePGH bike map, Aug 2019)...")
    pkg = package_show(BIKE_MAP_PACKAGE)
    by_name = {r["name"]: r for r in pkg["resources"]}
    for slug, resource_name in BIKE_MAP_CATEGORIES.items():
        resource = by_name.get(resource_name)
        if resource is None:
            print(f"  ! resource not found: {resource_name}")
            continue
        ext = resource["format"].lower()
        zip_dest = os.path.join(RAW_DIR, "infrastructure", f"{slug}.{ext}")
        download(resource["url"], zip_dest)
        if ext == "zip":
            extract_dir = os.path.join(RAW_DIR, "infrastructure", slug)
            if not os.path.isdir(extract_dir):
                with zipfile.ZipFile(zip_dest) as zf:
                    zf.extractall(extract_dir)
                print(f"  extracted to {extract_dir}")

    print("Fetching on-road bicycle pavement markings...")
    pkg = package_show(PAVEMENT_MARKINGS_PACKAGE)
    csv_resource = next(r for r in pkg["resources"] if r["format"].upper() == "CSV" and "dictionary" not in r["name"].lower())
    csv_dest = os.path.join(RAW_DIR, "infrastructure", "pavement-markings.csv")
    download(csv_resource["url"], csv_dest)


def fetch_weather(start_date: str = "2022-05-01", end_date: str = None) -> None:
    """Download daily historical weather for Pittsburgh from Open-Meteo's free archive API
    (ERA5 reanalysis, no API key needed). Covers the full trip-data history plus a few days
    beyond, so it lines up with any near-term demand predictions."""
    import datetime
    if end_date is None:
        end_date = datetime.date.today().isoformat()  # archive API is historical only, no future dates
    print(f"Fetching daily weather for Pittsburgh, {start_date} to {end_date}...")
    dest = os.path.join(RAW_DIR, "weather", "pittsburgh_daily_weather.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    resp = requests.get(OPEN_METEO_ARCHIVE, params={
        "latitude": PITTSBURGH_LAT,
        "longitude": PITTSBURGH_LON,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum",
        "timezone": "America/New_York",
    }, timeout=60)
    resp.raise_for_status()
    with open(dest, "w") as f:
        json.dump(resp.json(), f)
    n_days = len(resp.json()["daily"]["time"])
    print(f"  saved {n_days} days of weather to {dest}")


def fetch_weather_forecast() -> None:
    """Download the current 16-day daily weather FORECAST for Pittsburgh from Open-Meteo's free
    forecast API (no API key). Unlike fetch_weather (historical archive, no future dates), this is
    what lets demand predictions for upcoming days use real forecast weather instead of a guess.
    Always re-fetches (forecasts change day to day) - not meant to be cached long-term."""
    print("Fetching 16-day weather forecast for Pittsburgh...")
    dest = os.path.join(RAW_DIR, "weather", "pittsburgh_forecast.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    resp = requests.get(OPEN_METEO_FORECAST, params={
        "latitude": PITTSBURGH_LAT,
        "longitude": PITTSBURGH_LON,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum",
        "timezone": "America/New_York",
        "forecast_days": 16,
    }, timeout=60)
    resp.raise_for_status()
    with open(dest, "w") as f:
        json.dump(resp.json(), f)
    n_days = len(resp.json()["daily"]["time"])
    print(f"  saved {n_days}-day forecast to {dest}")


# PBSC/POGOH vehicle_type_id -> category, from the GBFS vehicle_types feed (propulsion_type):
# human-powered bicycles vs. electric_assist bicycles vs. CHLOE, which is actually an e-scooter
# (form_factor scooter_standing), not a bike, despite being in the same fleet.
VEHICLE_TYPE_CATEGORY = {
    "FIT": "classic", "ICONIC": "classic", "METRO": "classic", "METRO_CONNECTED": "classic",
    "BOOST": "ebike", "EFIT": "ebike", "COSMO": "ebike", "ASTRO": "ebike",
    "CHLOE": "scooter",
}


def fetch_live_station_status() -> dict:
    """Pull the CURRENT (real-time) GBFS station_status + station_information feeds directly
    from POGOH's operator (PBSC), no API key needed. This is live data, not historical — call it
    right before you need it rather than caching, since it's only valid for the next few minutes.

    Includes an ebike/classic-bike breakdown per station (from vehicle_types_available) - this is
    the ONLY place that split is available; the historical trip-data export has no vehicle-type
    column, so ebike-vs-classic usage cannot be reconstructed for the trip history, only observed live.
    """
    feeds = {f["name"]: f["url"] for f in requests.get(GBFS_DISCOVERY, timeout=30).json()["data"]["feeds"]}
    info = requests.get(feeds["station_information"], timeout=30).json()["data"]["stations"]
    status = requests.get(feeds["station_status"], timeout=30).json()["data"]["stations"]
    info_df_rows = {s["station_id"]: s for s in info}
    merged = []
    for s in status:
        sid = s["station_id"]
        meta = info_df_rows.get(sid, {})
        name = next((n["text"] for n in meta.get("name", []) if n["language"] == "en"), None)
        by_type = {"classic": 0, "ebike": 0, "scooter": 0}
        for vt in s.get("vehicle_types_available", []):
            cat = VEHICLE_TYPE_CATEGORY.get(vt["vehicle_type_id"], "classic")
            by_type[cat] += vt["count"]
        merged.append({
            "station_id": int(sid),
            "num_classic_available": by_type["classic"],
            "num_ebike_available": by_type["ebike"],
            "num_scooter_available": by_type["scooter"],
            "name": name,
            "capacity": meta.get("capacity"),
            "num_bikes_available": s["num_vehicles_available"],
            "num_docks_available": s["num_docks_available"],
            "is_renting": s["is_renting"],
            "last_reported": s["last_reported"],
        })
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trip-months", type=int, default=100, help="Number of recent trip-data months to download (default: all)")
    parser.add_argument("--skip-trips", action="store_true", help="Skip trip data (large files)")
    parser.add_argument("--skip-stations", action="store_true")
    parser.add_argument("--skip-station-history", action="store_true", help="Skip historical station snapshots")
    parser.add_argument("--skip-infrastructure", action="store_true")
    parser.add_argument("--skip-weather", action="store_true")
    args = parser.parse_args()

    if not args.skip_trips:
        fetch_trip_data(args.trip_months)
    if not args.skip_stations:
        fetch_stations()
    if not args.skip_station_history:
        fetch_stations_history()
    if not args.skip_infrastructure:
        fetch_infrastructure()
    if not args.skip_weather:
        fetch_weather()
        fetch_weather_forecast()

    print("Done.")


if __name__ == "__main__":
    main()
