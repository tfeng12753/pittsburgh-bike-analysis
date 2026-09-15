#!/usr/bin/env bash
# Run monthly on Render (WPRDC only publishes a new month of trip data monthly, so retraining more
# often than that teaches the model nothing new): re-fetches everything, re-runs all three
# notebooks (retraining the demand model, regenerating every chart, re-exporting the comparison
# dataset), and pushes the result back to git so the Render static site auto-redeploys.
# Requires env vars GITHUB_TOKEN (a PAT with repo scope) and GITHUB_REPO ("owner/repo").
#
# NOTE: this commits data/processed/models/*.joblib (~70MB each) - track those with Git LFS (see
# README "Deploying to Render") or this will bloat the repo fast.
set -euo pipefail
cd "$(dirname "$0")/.."

python src/fetch_data.py --trip-months 6   # pick up newly published month(s) + refresh everything else
jupyter nbconvert --to notebook --execute --inplace notebooks/01_exploration.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/02_ridership_over_time.ipynb
jupyter nbconvert --to notebook --execute --inplace notebooks/03_station_demand_prediction.ipynb
python src/export_comparison_data.py

git config user.email "bot@render.com"
git config user.name "render-cron-bot"
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"

git add docs/ data/processed/models/ data/processed/future_predictions_cache.parquet notebooks/
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "chore: monthly full retrain + chart refresh [skip ci]"
  git push origin HEAD:main
fi
