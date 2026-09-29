#!/usr/bin/env bash
# Run monthly on Render (WPRDC only publishes a new month of trip data monthly, so retraining more
# often than that teaches the model nothing new): re-fetches everything, re-runs all three
# notebooks (retraining the demand model, regenerating every chart, re-exporting the comparison
# dataset), and pushes the result back to git so the Render static site auto-redeploys.
# Requires env vars GITHUB_TOKEN (a PAT with repo scope) and GITHUB_REPO ("owner/repo").
#
# NOTE: this commits data/processed/models/*.joblib - track those with Git LFS (see README
# "Deploying to Render"). They're small now (~15MB each after a memory-driven hyperparameter fix,
# see git history), but LFS is harmless to keep and saves re-doing this if they grow again.
set -euo pipefail
cd "$(dirname "$0")/.."

python src/fetch_data.py --trip-months 6   # pick up newly published month(s) + refresh everything else
jupyter nbconvert --to notebook --execute --inplace notebooks/01_exploration.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/02_ridership_over_time.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/03_station_demand_prediction.ipynb
python src/export_comparison_data.py

bash scripts/push_to_main.sh "chore: monthly full retrain + chart refresh [skip ci]" docs/ data/processed/models/ data/processed/future_predictions_cache.parquet notebooks/
