# Pittsburgh Bike Routes & Bike Share Analysis

An exploratory data-analysis project on cycling infrastructure and bike-share
usage in Pittsburgh, PA — built entirely on public open data.

## Project plan

1. **Ingest** — pull bike-share trips, station locations, and bike-lane/trail
   GIS data from the WPRDC open data portal (`src/fetch_data.py`).
2. **Clean & load** — tidy column names, parse dates, handle bad rows
   (`src/load_data.py`).
3. **Explore** — ridership trends, rider-type/duration patterns, busiest
   stations, a station + bike-lane map, and infrastructure growth over time
   (`notebooks/01_exploration.ipynb`).
4. **Trend over time** — full ridership history since program launch (May
   2022), seasonality by year, rider-mix shift, and ridership vs. station
   network growth (`notebooks/02_ridership_over_time.ipynb`).
5. **Predict & map** — Random Forest models predicting daily arrivals/
   departures per station, plus an interactive day-by-day demand map
   (`notebooks/03_station_demand_prediction.ipynb`).
6. **Extend further** (not yet built) — join ridership to bike-lane
   proximity, bring in Census commute-mode-share, crash data, weather, or
   campus/event calendars for stronger predictive features.

## Data sources

All datasets are public and require no API key. Fetched via the
[WPRDC CKAN API](https://data.wprdc.org) (Western PA Regional Data Center —
run by the City of Pittsburgh, Allegheny County, and Pitt's UCSUR).

| Dataset | Publisher | Contents | Update cadence |
|---|---|---|---|
| [POGOH Trip Data](https://data.wprdc.org/dataset/pogoh-trip-data) | Bike Share Pittsburgh | Every bike-share trip: start/end station, timestamps, duration, rider type | Monthly |
| [POGOH Station Locations](https://data.wprdc.org/dataset/station-locations) | Bike Share Pittsburgh | Station name, lat/lon, dock count | As stations change |
| [BikePGH's Pittsburgh Bike Map Geographic Data](https://data.wprdc.org/dataset/shape-files-for-bikepgh-s-pittsburgh-bike-map) | BikePGH | Shapefiles for bike lanes, protected lanes, trails, routes, bridges | As needed |
| [On-road Bicycle Pavement Markings](https://data.wprdc.org/dataset/on-road-bicycle-pavement-markings) | BikePGH | Mile-by-mile bike lane/sharrow installations, 1980–2016, with year added | As needed |
| [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) | Open-Meteo (ERA5 reanalysis) | Daily temperature, precipitation, snowfall for Pittsburgh, 1940–present | Daily, no key needed |
| [POGOH GBFS feed](https://pittsburgh.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json) | Bike Share Pittsburgh (PBSC) | Live, real-time bikes/docks available per station (industry-standard [GBFS](https://gbfs.org) format) | Live, ~30s, no key needed |

Note: **Healthy Ride** (the predecessor bike-share system, nextbike hardware)
ran until May 2022; its [archived trip data](https://data.wprdc.org/dataset/healthyride-trip-data)
is also on WPRDC if you want a longer time series by combining both systems
(column schemas differ slightly — would need a small mapping layer).

### Possible future additions
- **Census ACS** commute-mode-share (% biking to work) by tract, for demand-side context.
- **PennDOT crash data** for a safety analysis join against lane locations.
- **NOAA weather data** to explain seasonal ridership swings.
- A specific research study's dataset, if/when you have one to point at (private data was intentionally left out of this pass — see conversation).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Fetching data

```bash
python src/fetch_data.py                    # full trip history (52 months) + all station snapshots + infrastructure
python src/fetch_data.py --trip-months 12    # only last 12 months of trip data (faster)
python src/fetch_data.py --skip-trips        # just stations + infrastructure (fast)
```

Downloaded files land in `data/raw/` (git-ignored — re-fetchable any time, ~95MB for full trip history).

## Running the analysis

```bash
jupyter notebook notebooks/01_exploration.ipynb
jupyter notebook notebooks/02_ridership_over_time.ipynb
jupyter notebook notebooks/03_station_demand_prediction.ipynb
```

or regenerate headlessly, e.g.:

```bash
jupyter nbconvert --to notebook --execute --inplace notebooks/03_station_demand_prediction.ipynb
```

Charts and the interactive maps are written to `docs/`. `src/load_data.load_trips()`
caches the parsed trip data to `data/processed/trips_cache.parquet` (loading
52 months of .xlsx takes ~1 min the first time, <2s after); pass
`use_cache=False` to force a re-read after running `fetch_data.py` again.

## Station demand prediction & map

`notebooks/03_station_demand_prediction.ipynb` trains two Random Forest
regressors (arrivals, departures) on ~3 years of per-station **bi-hourly**
trip counts (2023-07 onward, once the 60-station network stabilized, binned
into 2-hour windows), using station identity, hour-of-day, day-of-week,
month, cyclical day-of-year, and daily weather (temperature, precipitation,
snow) as features. On a held-out final 2 months it beats a naive "station's
historical hour-of-day average" baseline by ~10-11% (MAE ~1.6 trips/
station/bin, R² ≈ 0.55) — a harder problem than daily totals since per-bin
counts are small and noisier.

Weather's contribution is mixed depending on the holdout window - a
summer-only test period has little freezing/snow variance, so temperature
and precipitation add real signal but less than you'd see with a
winter-inclusive holdout; see the feature-importance chart in `docs/`.

It then predicts every 2-hour window across the **next ~7 days from
whenever the notebook runs** (not a fixed date — re-run it next week and the
window rolls forward automatically), using Open-Meteo's real weather
**forecast** (`load_weather_with_forecast()`, up to 16 days out, refreshed
by `fetch_data.py`) for those upcoming days. There's a real gap between the
latest available trip data (WPRDC publishes monthly, with lag) and today,
but since the model predicts from station identity + calendar/weather
features rather than a running time series, that gap doesn't hurt
prediction quality. It renders `docs/station_demand_prediction_map.html`, a
lightweight, dependency-minimal Leaflet map (just Leaflet itself, no plugin
stack) with:

- **Live now / Predicted mode toggle.** Live pulls real bike/dock
  availability straight from [POGOH's public GBFS feed](https://pittsburgh.publicbikesystem.net/customer/gbfs/v3.0/gbfs.json)
  (no API key), broken out **by e-bike vs. classic bike** — a sub-toggle
  lets you view total, e-bike-only, or classic-only availability per
  station. This split is live-only: the historical trip-data export has no
  vehicle-type column, so past e-bike usage can't be reconstructed, only
  observed in real time (see notebook section 5 for the vehicle-type
  classification: FIT/ICONIC/METRO/METRO_CONNECTED are classic bikes,
  BOOST/EFIT/COSMO/ASTRO are e-bikes, and CHLOE is actually an e-scooter).
- **A time slider** (84 steps = 7 days × 12 bi-hourly windows) in Predicted
  mode to scrub through the demand forecast at 2-hour resolution, with a
  sub-toggle for what the color represents: **net flow** (the rate a
  station is gaining/losing bikes) or **predicted bikes available** (a
  simulated stock — starting from the live count now, walking forward
  bin-by-bin adding predicted net flow, clipped to [0, capacity]). The two
  answer different questions: net flow shows *where demand is right now*,
  predicted-bikes shows *whether a station will actually be empty/full* —
  but since it's a cumulative simulation on top of the per-bin model, its
  errors compound further into the week, unlike net flow which is a fresh
  prediction each bin. "Predicted bikes available" has its own Total /
  E-bikes / Classic toggle (mirroring Live), splitting the simulated stock
  by each station's *current* live e-bike fraction, held constant going
  forward - an approximation on top of an approximation, since there's no
  historical vehicle-type data to learn how e-bike vs. classic demand
  actually differs.
- **Toggleable bike-infrastructure overlays** (top-left): bike lanes,
  protected bike lanes, trails, sharrows, on-street bike routes, cautionary
  bike routes, and bikeable sidewalks — each its own color, independently
  switchable, sourced from BikePGH's Aug 2019 GIS layers.

Circle size scales with predicted/actual volume; color runs red
(draining/empty) to green (accumulating/full). Click any station for exact
numbers.

**Keeping it current**: since both the forecast weather and the "next 7
days" window are relative to today, re-run these two commands whenever you
want a fresh forecast (the RF retrains on ~830K rows each time, ~20 min):

```bash
python src/fetch_data.py --skip-trips --skip-stations --skip-station-history --skip-infrastructure
jupyter nbconvert --to notebook --execute --inplace notebooks/03_station_demand_prediction.ipynb
```

If you've only changed `src/build_map.py` (styling, a new toggle, etc.) and
don't need fresh predictions, skip the retrain with the cached predictions
the notebook leaves behind (`data/processed/future_predictions_cache.parquet`):

```bash
python src/rebuild_map.py   # rebuilds the map in under a second, using the last training run
```

## Homepage

`docs/index.html` ties everything together into one entry point — open it
directly in a browser (`open docs/index.html`, no server needed):

- **Explore the map**: three cards to pick a view — **current** (live
  bike/dock availability), **forecasted** (next-7-days demand), or a static
  **stations & bike lanes** overview. The first two link into
  `station_demand_prediction_map.html?mode=live` / `?mode=predicted`, which
  reads that query parameter and opens straight into the right mode instead
  of defaulting to Live and making you click through.
- **Explore the data**: a click-through chart browser (grouped by topic —
  ridership growth, seasonality, riders & trips, stations, when-is-it-busy,
  infrastructure, the demand model, fleet mix) showing one of the 13 charts
  generated across the three notebooks at a time, each with a plain-language
  caption of what it shows. Pure vanilla HTML/CSS/JS, no build step or
  dependency — editing the `CHARTS` array at the bottom of the file is
  enough to add a new chart.
- **Compare any two factors** (`docs/compare.html`, linked from the chart
  browser): pick any two of 11 daily metrics (trips, duration, rider mix,
  temperature, precipitation, station count, ...) and see them either as an
  overlaid time series (dual y-axis) or a scatter plot, with a live Pearson
  correlation coefficient. Built on `docs/data/comparison_dataset.json`
  (regenerate with `python src/export_comparison_data.py` after new trip
  data lands) and [Chart.js](https://www.chartjs.org/) from cdnjs — the only
  external dependency anywhere in this project's pages besides Leaflet and
  map tiles.

## Keeping everything up to date automatically

Two scripts, meant to be run on a schedule (locally via cron, or on a host
like Render — see below):

- **`scripts/daily_cron.sh`** — calls `src/daily_refresh.py` (a few
  seconds: re-predicts the rolling 7-day window using the already-trained
  model + today's live status + weather forecast) and commits just the
  updated map file.
- **`scripts/monthly_cron.sh`** — re-fetches everything and re-runs all
  three notebooks (~20-25 min: retrains the demand model on whatever new
  trip data WPRDC has published, regenerates every chart, re-exports the
  comparison dataset) and commits the lot. Monthly, not daily or weekly,
  because WPRDC only publishes a new month of trip data monthly — retraining
  more often teaches the model nothing new, and full retrains produce large
  files (below) you don't want committing to git more often than necessary.

Both scripts assume they're running inside a git checkout with push access
(they `git commit` + `git push` at the end) — see "Deploying to Render"
for how that's wired up in practice.

## Deploying to Render

The site (`docs/`) is a static site; the two scripts above are what keep it
current. The architecture: Render's **Static Site** auto-deploys from
GitHub on every push, and two Render **Cron Jobs** produce those pushes on
schedule (`render.yaml` defines all three as one Blueprint).

**One-time setup, before connecting to Render:**

1. **Git LFS for the model files.** The trained Random Forests serialize to
   ~70MB each even compressed — committing them as plain git blobs every
   month will bloat the repo fast. This repo's `.gitattributes` already
   marks `data/processed/models/*.joblib` for LFS; you just need LFS
   installed once:
   ```bash
   git lfs install
   git add .gitattributes
   ```
   Be aware GitHub's free LFS tier is 1GB storage + 1GB bandwidth/month —
   at ~140MB/month (monthly retrain only) that's comfortable, but watch it
   if you retrain more often than the schedule above.
2. **Commit and push to GitHub** (this project wasn't pushed automatically
   — you asked to handle that step yourself):
   ```bash
   git add -A
   git commit -m "Initial commit"
   gh repo create pittsburgh-bike-analysis --private --source=. --push
   # or: create the repo on github.com, then `git remote add origin <url> && git push -u origin main`
   ```
3. **Create a GitHub Personal Access Token** (Settings → Developer settings
   → Personal access tokens → generate one with `repo` scope) — the cron
   jobs need this to push their updates back.

**On Render:**

4. New → Blueprint → connect the GitHub repo you just created. Render reads
   `render.yaml` and proposes all three services (the static site + two
   cron jobs) — approve them.
5. For **both** cron job services, add two environment variables in the
   Render dashboard (they're declared as `sync: false` in `render.yaml`,
   meaning Render won't ask for them until you set them manually):
   - `GITHUB_TOKEN` — the personal access token from step 3.
   - `GITHUB_REPO` — `yourusername/pittsburgh-bike-analysis` (no URL, no
     `.git` suffix).
6. Trigger each cron job manually once (Render dashboard → the job → "Run
   Job") to confirm they run cleanly before waiting for their schedule.

After that: the daily job keeps the forecast rolling forward every morning,
the monthly job keeps the model itself current, and every push automatically
redeploys the live static site — nothing else to do.

**If you'd rather avoid Git LFS entirely**: Render's Private Services /
Background Workers support persistent disks (Cron Jobs may or may not,
depending on Render's current product — check their docs, since this
changes over time). An alternative architecture is one long-running Render
service with a disk that runs both schedules internally and writes
`docs/station_demand_prediction_map.html` on the same disk the static site
serves from — but that requires the static site and the worker to share a
disk, which Render's static sites don't support directly, so this repo uses
the simpler git-push approach instead.

## Project structure

```
data/raw/            # downloaded source files (git-ignored, re-fetchable)
  trips/              POGOH monthly trip data, full history (xlsx)
  stations/           latest POGOH station locations (xlsx) + history/ snapshots
  infrastructure/      7 BikePGH GIS layers (lanes, protected lanes, trails, sharrows,
                        on-street routes, cautionary routes, sidewalks) + pavement markings CSV
  weather/             daily Pittsburgh weather (Open-Meteo archive) + 16-day forecast
data/processed/
  trips_cache.parquet         git-ignored, rebuilt from raw
  models/                     TRACKED (Git LFS) - persisted RF models + station-hour baseline
  future_predictions_cache.parquet   TRACKED - lets src/rebuild_map.py skip retraining
src/
  fetch_data.py        downloads everything from WPRDC + weather/forecast + live GBFS status
  load_data.py          loading/cleaning helpers, incl. parquet caching + infra layer loader
  predict.py             shared feature-engineering + model persistence (notebook 03 AND
                          daily_refresh.py both import this, so they can never drift apart)
  build_map.py           hand-built minimal Leaflet map (no fragile plugin stack)
  rebuild_map.py          fast (<1s) map-only rebuild from cached predictions, skips retraining
  daily_refresh.py        fast (~seconds) daily prediction refresh using persisted models
  export_comparison_data.py   exports docs/data/comparison_dataset.json for compare.html
scripts/
  daily_cron.sh           calls daily_refresh.py, commits + pushes the updated map
  monthly_cron.sh         re-fetches + retrains + regenerates everything, commits + pushes
notebooks/
  01_exploration.ipynb            ridership snapshot, station rankings, infra map
  02_ridership_over_time.ipynb    full history trends, seasonality, network growth, dow x hour heatmap
  03_station_demand_prediction.ipynb   bi-hourly RF demand model + live/predicted map w/ infra layers
docs/
  index.html            homepage: map picker + chart browser
  compare.html            interactive "compare any two factors" chart builder
  data/comparison_dataset.json   daily metrics feeding compare.html
  *.png, *.html          generated charts + interactive maps (regenerated by the notebooks)
render.yaml           Render Blueprint: static site + 2 cron jobs (see "Deploying to Render")
.gitattributes        marks data/processed/models/*.joblib for Git LFS
```
