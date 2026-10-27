# Consolidated F1 season JSON

This workflow writes two JSON files using only the selected/current season (2026 by default):

- **`f1-2026.json`**: the full season dataset, including all rounds in that season.
- **`current.json`**: only the latest selected/completed Grand Prix records, with no season-wide tables.

Both files combine data from two sources:

- **Jolpica**: current-season circuits (derived from that season's race calendar), races, constructors, drivers, race results, each driver's fastest-lap time/rank/lap where provided, sprint results, qualifying, pit stops by round, lap-by-lap records by round, driver standings, and constructor standings. The scraper does not request all-years season, circuit, or status catalogues.
- **Formula1.com**: the selected Grand Prix's official race classification and starting grid (qualifying page is used if the grid page is unavailable).

## When it runs

GitHub Actions checks the MotorsportCalendars calendar every 15 minutes. It only calls the data sources during the selected Grand Prix's scheduled start +90 to +150 minute window. Manually run the workflow with `force=true` to test against the latest completed Grand Prix outside that window.

GitHub Actions cron is best-effort and may run late.

## Output structure

### `f1-2026.json`

- `season`: year being collected.
- `sources.jolpica`: normalized resource collections and original API URLs. Laps and pit stops are grouped by round. Each resource includes `count`, `urls`, and `data`; failures include an `error` rather than aborting all other resources.
- `sources.formula1_com`: official race result and starting-grid scrape for the selected race.
- `current_race`: convenient selection of the chosen round's Jolpica race metadata, results, qualifying, sprint, laps, pit stops, and a flattened `fastest_laps` list with driver, team, fastest-lap rank, lap number, time, and average speed where available.
- `sources.jolpica.fastest_laps_by_round`: flattened per-driver fastest-lap records for each season round, derived from Jolpica's race results (no extra API request).

### `current.json`

Contains `season`, generation/race-window metadata, `current_race` (the selected latest Grand Prix's race metadata, results, qualifying, sprint, laps, pit stops, and `fastest_laps`), a top-level `fastest_laps` convenience array, and the Formula1.com results/grid for that race. It does not include season-wide drivers, constructors, standings, or other rounds. If Jolpica's race-calendar endpoint is empty, the scraper attempts to build the current-race view from the matching Jolpica results record instead.

Jolpica lap and pit-stop data can be sizeable. `f1-2026.json` stores all records for the selected season, not previous seasons. `current.json` is the compact latest-race view. This project intentionally does not fetch FastF1 telemetry, weather or race-control messages; it uses Jolpica and Formula1.com only.

## Run locally from the CLI (recommended for testing)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scrape_f1_season.py --help
python scrape_f1_season.py --season 2026 --force
```

Useful commands:

```bash
# Latest completed Grand Prix
python scrape_f1_season.py --force

# Explicitly select a completed race by name
python scrape_f1_season.py --race "United States Grand Prix"

# Quick smoke test without the per-round lap and pit-stop requests
python scrape_f1_season.py --force --skip-round-details --output /tmp/f1-smoke.json
```

`--race` only selects a race that has already started at least 90 minutes ago. The default full-season output is `f1-2026.json` and the latest-race output is `current.json`. Use `--output` and `--current-output` to change either path. `--season` changes the year. The install only needs Requests and Beautiful Soup. The calendar is parsed with Python's standard library, so `icalendar` is not needed.

## GitHub Actions

The scheduled workflow checks the calendar every 15 minutes and collects data during the post-race window. It commits both `f1-2026.json` and `current.json` when they change. Use **Actions → F1 season JSON → Run workflow** and set `force` to test outside that window. GitHub Actions cron is best-effort and may run late.

No Formula1.com account credentials are needed by this workflow. It uses public Jolpica API data and Formula1.com result pages.
