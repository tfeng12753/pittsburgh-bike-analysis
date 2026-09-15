"""
Export a daily time-series dataset combining ridership, weather, and rider-mix metrics as JSON,
for docs/compare.html's client-side interactive chart builder ("compare any two factors").

Usage: python src/export_comparison_data.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from load_data import load_trips, load_weather, load_station_history

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "comparison_dataset.json")


def main():
    trips = load_trips()
    weather = load_weather()
    stations_hist = load_station_history()

    trips = trips.copy()
    trips["date"] = trips["start_date"].dt.normalize()

    daily = trips.groupby("date").agg(
        trips=("date", "size"),
        avg_duration_min=("duration_min", lambda s: s[(s >= 0) & (s <= 180)].mean()),
    ).reset_index()

    mix = trips.groupby(["date", "rider_type"]).size().unstack("rider_type").fillna(0)
    mix_total = mix.sum(axis=1)
    mix_df = pd.DataFrame({
        "casual_share_pct": (mix.get("CASUAL", 0) / mix_total.replace(0, np.nan) * 100).round(1),
        "member_share_pct": (mix.get("MEMBER", 0) / mix_total.replace(0, np.nan) * 100).round(1),
    }).reset_index().rename(columns={"date": "date"})
    daily = daily.merge(mix_df, on="date", how="left")

    daily["day_of_week"] = daily["date"].dt.dayofweek
    daily["is_weekend"] = (daily["day_of_week"] >= 5).astype(int)
    daily["month"] = daily["date"].dt.month

    daily = daily.merge(weather, on="date", how="left")

    # Station network size on each date (forward-filled from the historical snapshots) - lets you
    # compare "ridership vs. network size" the same way notebook 02 does, but interactively.
    network = stations_hist.groupby("snapshot_date").size().rename("n_stations").reset_index()
    network = network.rename(columns={"snapshot_date": "date"})
    network["date"] = pd.to_datetime(network["date"]).astype("datetime64[ns]")
    daily["date"] = pd.to_datetime(daily["date"]).astype("datetime64[ns]")
    daily = daily.sort_values("date")
    daily = pd.merge_asof(daily, network.sort_values("date"), on="date", direction="backward")

    daily = daily.round(2)
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")

    METRICS = {
        "trips": "Daily trips",
        "avg_duration_min": "Avg trip duration (min)",
        "casual_share_pct": "Casual rider share (%)",
        "member_share_pct": "Member rider share (%)",
        "temp_avg_c": "Avg temperature (C)",
        "temp_max_c": "Max temperature (C)",
        "temp_min_c": "Min temperature (C)",
        "precip_mm": "Precipitation (mm)",
        "snow_cm": "Snowfall (cm)",
        "n_stations": "Station network size",
        "is_weekend": "Is weekend (0/1)",
    }

    payload = {
        "dates": daily["date"].tolist(),
        "metrics": METRICS,
        "series": {col: daily[col].where(daily[col].notna(), None).tolist() for col in METRICS},
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_PATH) / 1024
    print(f"Exported {len(daily)} days x {len(METRICS)} metrics to {OUTPUT_PATH} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
