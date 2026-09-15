"""
Shared feature-engineering, model persistence, and stock-simulation logic for the bi-hourly
station-demand model. Used by BOTH:
  - notebooks/03_station_demand_prediction.ipynb (trains on ~3 years of history, ~20 min, and
    should be re-run periodically - e.g. weekly, or whenever a new month of trip data lands)
  - src/daily_refresh.py (loads the persisted models and just re-predicts the rolling 7-day
    window with today's live status + weather forecast, in a couple seconds)

Keeping this in one module (rather than duplicating the feature logic in both places) guarantees
the daily refresh computes features identically to how the model was trained.
"""
import os

import joblib
import numpy as np
import pandas as pd

FEATURES = [
    "station_id", "hour_sin", "hour_cos", "day_of_week", "is_weekend", "month",
    "doy_sin", "doy_cos", "temp_avg_c", "precip_mm", "is_freezing", "is_rainy",
    "station_hour_avg_arrivals", "station_hour_avg_departures",
]

MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "models")


def add_calendar_features(df: pd.DataFrame, bin_col: str = "bin") -> pd.DataFrame:
    """Add hour/day-of-week/month/cyclical-day-of-year features from a datetime column."""
    df = df.copy()
    df["hour"] = df[bin_col].dt.hour
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["day_of_week"] = df[bin_col].dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month"] = df[bin_col].dt.month
    doy = df[bin_col].dt.dayofyear
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    return df


def add_weather_features(df: pd.DataFrame, weather_df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    df = df.merge(weather_df, on=date_col, how="left")
    df["is_freezing"] = (df["temp_min_c"] <= 0).astype(int)
    df["is_rainy"] = (df["precip_mm"] > 2).astype(int)
    return df


def build_future_frame(station_ids, weather_fc: pd.DataFrame, station_hour_avg: pd.DataFrame,
                        periods: int = 7 * 12, freq: str = "2h", start: pd.Timestamp = None) -> pd.DataFrame:
    """Build the feature frame for the next `periods` bi-hourly bins (default: 7 days), anchored to
    `start` (default: right now, floored to a 2h boundary). Does NOT predict - the caller applies
    the persisted models to the FEATURES columns of the result."""
    start = start or pd.Timestamp.now().floor("2h")
    bins = pd.date_range(start, periods=periods, freq=freq)
    future = pd.DataFrame([(b, s) for b in bins for s in station_ids], columns=["bin", "station_id"])
    future["date"] = future["bin"].dt.normalize()
    future = add_calendar_features(future)
    future = add_weather_features(future, weather_fc)
    future = future.merge(station_hour_avg, on=["station_id", "hour"], how="left")
    return future


def simulate_bike_stock(future: pd.DataFrame, live_status: pd.DataFrame, docks_by_station: dict):
    """Add `pred_bikes_available` (simulated stock, starting from each station's live bike count
    and walking forward bin-by-bin adding predicted net flow, clipped to [0, capacity]) and
    `ebike_frac` (each station's current live e-bike fraction - lets a map client derive an
    e-bike/classic split of ANY metric without a separate per-type model; see notebook section 4
    for why: the historical trip data has no vehicle-type column, so this is the best available
    proxy, not a trained prediction). Requires `future` to already have pred_arrivals/pred_departures/
    pred_net_flow columns. Returns (future_with_stock, system_wide_ebike_fraction)."""
    start_bikes = live_status.set_index("station_id")["num_bikes_available"].to_dict()
    total_ebike = live_status["num_ebike_available"].sum()
    total_classic = live_status["num_classic_available"].sum()
    system_ebike_frac = total_ebike / max(total_ebike + total_classic, 1)

    ebike_frac_by_station = {}
    for _, r in live_status.iterrows():
        total = r["num_ebike_available"] + r["num_classic_available"]
        ebike_frac_by_station[int(r["station_id"])] = (r["num_ebike_available"] / total) if total > 0 else system_ebike_frac

    future = future.sort_values(["station_id", "bin"]).reset_index(drop=True)
    pred_bikes_col = []
    for sid, grp in future.groupby("station_id", sort=False):
        cap = docks_by_station.get(sid, 20)
        running = min(max(start_bikes.get(sid, cap / 2), 0), cap)
        for nf in grp["pred_net_flow"]:
            running = min(max(running + nf, 0), cap)
            pred_bikes_col.append(round(running, 1))
    future["pred_bikes_available"] = pred_bikes_col
    future["ebike_frac"] = future["station_id"].map(ebike_frac_by_station).fillna(system_ebike_frac)
    return future, system_ebike_frac


def save_models(rf_arrivals, rf_departures, station_hour_avg: pd.DataFrame) -> None:
    """Persist the trained models + the per-station-hour baseline they depend on, so
    src/daily_refresh.py can generate fresh predictions without re-running the ~20min training."""
    # compress=6: these RandomForests serialize to ~250MB uncompressed each (200 trees, depth 14,
    # 790K training rows) - compression brings that to ~70MB each, which matters a lot for keeping
    # them in git and for how long a deploy/cron job takes to fetch them.
    os.makedirs(MODELS_DIR, exist_ok=True)
    joblib.dump(rf_arrivals, os.path.join(MODELS_DIR, "rf_arrivals.joblib"), compress=6)
    joblib.dump(rf_departures, os.path.join(MODELS_DIR, "rf_departures.joblib"), compress=6)
    station_hour_avg.to_parquet(os.path.join(MODELS_DIR, "station_hour_avg.parquet"))


def load_models():
    """Returns (rf_arrivals, rf_departures, station_hour_avg). Raises FileNotFoundError with a
    clear message if the notebook hasn't been run yet to produce them."""
    paths = {
        "rf_arrivals": os.path.join(MODELS_DIR, "rf_arrivals.joblib"),
        "rf_departures": os.path.join(MODELS_DIR, "rf_departures.joblib"),
        "station_hour_avg": os.path.join(MODELS_DIR, "station_hour_avg.parquet"),
    }
    missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            f"Missing persisted model artifact(s): {missing}. Run "
            "notebooks/03_station_demand_prediction.ipynb at least once first (it saves these "
            "after training)."
        )
    rf_arrivals = joblib.load(paths["rf_arrivals"])
    rf_departures = joblib.load(paths["rf_departures"])
    station_hour_avg = pd.read_parquet(paths["station_hour_avg"])
    return rf_arrivals, rf_departures, station_hour_avg
