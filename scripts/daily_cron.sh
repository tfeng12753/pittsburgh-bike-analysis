#!/usr/bin/env bash
# Run daily on Render: re-predicts the rolling 7-day demand map using the already-trained models
# (fast, ~seconds) and pushes the change back to git so the Render static site auto-redeploys.
# Requires env vars GITHUB_TOKEN (a PAT with repo scope) and GITHUB_REPO ("owner/repo").
set -euo pipefail
cd "$(dirname "$0")/.."

# data/raw/ is gitignored, so each Render run starts from a fresh clone with no raw data. Fetch the
# small inputs daily_refresh.py reads (current stations, bike-infrastructure layers, weather archive
# + today's 16-day forecast) - everything except the large trip files and station-history snapshots.
python src/fetch_data.py --skip-trips --skip-station-history

python src/daily_refresh.py

git config user.email "bot@render.com"
git config user.name "render-cron-bot"
git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"

git add docs/station_demand_prediction_map.html
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git commit -m "chore: daily forecast refresh [skip ci]"
  git push origin HEAD:main
fi
