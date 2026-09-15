"""
Rebuild docs/station_demand_prediction_map.html from CACHED predictions, without re-running the
~20min model training in notebooks/03_station_demand_prediction.ipynb.

Use this when you've only changed build_map.py (rendering/UI) - not when you need fresh
predictions (re-run the notebook for that; it retrains on whatever new trip/weather data has
shown up and re-caches). Fails loudly if the cache doesn't exist yet.

Usage: python src/rebuild_map.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from load_data import load_stations, load_bike_infrastructure, fetch_live_station_status
from build_map import build_demand_map

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "future_predictions_cache.parquet")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "station_demand_prediction_map.html")


def main():
    if not os.path.exists(CACHE_PATH):
        raise FileNotFoundError(
            f"No cached predictions at {CACHE_PATH}. Run notebooks/03_station_demand_prediction.ipynb "
            "at least once first (it writes this cache after training)."
        )
    future = pd.read_parquet(CACHE_PATH)
    cache_age = pd.Timestamp.now() - future["bin"].min()
    if cache_age > pd.Timedelta(days=1):
        print(f"WARNING: cached predictions start {cache_age} ago - stale. "
              f"Re-run the notebook for a genuinely up-to-date forecast.")

    stations = load_stations()
    infra = load_bike_infrastructure()
    live = fetch_live_station_status()  # always fetched fresh - it's cheap and meant to be live

    out_path = build_demand_map(future, live, infra, OUTPUT_PATH)
    print(f"Rebuilt {out_path} from cached predictions ({future['bin'].min()} to {future['bin'].max()})")


if __name__ == "__main__":
    main()
