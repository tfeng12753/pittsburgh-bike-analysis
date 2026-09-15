"""
Fast (~seconds) daily refresh of the demand-prediction map: loads the persisted models (trained
by notebooks/03_station_demand_prediction.ipynb), re-predicts the rolling next-7-days window using
today's live station status and weather forecast, and rewrites docs/station_demand_prediction_map.html.

Does NOT retrain - the model itself only needs retraining occasionally (new trip data arrives
monthly from WPRDC), but the prediction WINDOW and the WEATHER FORECAST it uses are both relative
to today, so this needs to run daily to stay current. Meant to be the thing a cron job calls.

Usage: python src/daily_refresh.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from build_map import build_demand_map
from load_data import load_stations, load_bike_infrastructure, load_weather_with_forecast, fetch_live_station_status
from predict import FEATURES, build_future_frame, simulate_bike_stock, load_models

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "station_demand_prediction_map.html")


def main():
    rf_arrivals, rf_departures, station_hour_avg = load_models()
    stations = load_stations()
    station_ids = sorted(stations["id"])
    docks_by_station = stations.set_index("id")["total_docks"].to_dict()

    weather_fc = load_weather_with_forecast()
    future = build_future_frame(station_ids, weather_fc, station_hour_avg)

    future["pred_arrivals"] = rf_arrivals.predict(future[FEATURES]).clip(min=0).round(2)
    future["pred_departures"] = rf_departures.predict(future[FEATURES]).clip(min=0).round(2)
    future["pred_net_flow"] = future["pred_arrivals"] - future["pred_departures"]
    future = future.merge(stations[["id", "name", "total_docks", "latitude", "longitude"]],
                           left_on="station_id", right_on="id")

    live = fetch_live_station_status()
    future, system_ebike_frac = simulate_bike_stock(future, live, docks_by_station)

    infra = load_bike_infrastructure()
    out_path = build_demand_map(future, live, infra, OUTPUT_PATH)

    print(f"Refreshed {out_path}")
    print(f"Window: {future['bin'].min()} to {future['bin'].max()}")
    print(f"System-wide live e-bike fraction: {system_ebike_frac:.0%}")


if __name__ == "__main__":
    main()
