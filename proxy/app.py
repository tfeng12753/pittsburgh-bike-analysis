"""
A tiny CORS proxy for POGOH's live GBFS feed.

Why this needs to exist at all: POGOH's GBFS endpoint doesn't send an
Access-Control-Allow-Origin header (confirmed by hand - even requests that get a 200 have no ACAO
header), so a browser blocks JavaScript on our static site from reading it directly. This service
does the same fetch server-to-server (no CORS involved there) and re-serves it with CORS headers
attached, so docs/station_demand_prediction_map.html can poll it directly from the browser every
few minutes for genuinely live data, instead of the once-a-day snapshot baked in at build time.

Run locally:  FLASK_RUN_PORT=5055 flask --app proxy.app run
Production:   gunicorn proxy.app:app --bind 0.0.0.0:$PORT   (see render.yaml)
"""
import os
import sys
import time

from flask import Flask, jsonify
from flask_cors import CORS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from fetch_data import fetch_live_station_status  # noqa: E402

app = Flask(__name__)
CORS(app, resources={r"/live-status": {"origins": "*"}})

_cache = {"data": None, "fetched_at": 0}
CACHE_TTL_SECONDS = 25  # POGOH's own feed says ttl=30s; no point polling upstream more than that


@app.route("/live-status")
def live_status():
    now = time.time()
    if _cache["data"] is None or now - _cache["fetched_at"] > CACHE_TTL_SECONDS:
        try:
            _cache["data"] = fetch_live_station_status()
            _cache["fetched_at"] = now
        except Exception as e:
            if _cache["data"] is not None:
                # Serve the last good snapshot rather than a hard failure if POGOH's feed hiccups.
                return jsonify({"stations": _cache["data"], "fetched_at": _cache["fetched_at"],
                                 "stale": True, "error": str(e)})
            return jsonify({"error": str(e)}), 502
    return jsonify({"stations": _cache["data"], "fetched_at": _cache["fetched_at"], "stale": False})


@app.route("/")
def health():
    return jsonify({"status": "ok", "see": "/live-status"})


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5055)))
