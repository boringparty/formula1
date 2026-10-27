## Consolidated F1 season JSON

This workflow writes **one JSON file** (`f1-2026.json` by default) combining three sources:

- **Jolpica**: seasons, circuits, races, constructors, drivers, race results, sprint results, qualifying, pit stops by round, lap-by-lap records by round, driver standings, constructor standings, and status codes.
- **Formula1.com**: the selected Grand Prix's official race classification and starting grid (qualifying page is used if the grid page is unavailable).
- **FastF1**: optional race lap records, each driver's fastest lap, weather, and race-control messages when the session archive can be loaded.

## When it runs

GitHub Actions checks the MotorsportCalendars calendar every 15 minutes. It only calls the data sources during the selected Grand Prix's scheduled start +90 to +150 minute window. Manually run the workflow with `force=true` to test against the latest completed Grand Prix outside that window.

GitHub Actions cron is best-effort and may run late.

## Output structure

- `season`: year being collected.
- `sources.jolpica`: normalized resource collections and original API URLs. Laps and pit stops are grouped by round. Each resource includes `count`, `urls`, and `data`; failures include an `error` rather than aborting all other resources.
- `sources.formula1_com`: official race result and starting-grid scrape for the selected race.
- `sources.fastf1`: optional detailed session data; contains an `ok` flag and an error note if unavailable.
- `current_race`: convenient selection of the chosen round's Jolpica race metadata, results, qualifying, sprint, laps and pit stops.

Jolpica lap and pit-stop data can be sizeable. The script paginates responses and stores everything in the same output file. The API does not provide full telemetry; FastF1 may provide additional timing data when its archive is accessible.

## Run locally from the CLI (recommended for testing)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Optional: install FastF1 support too
python -m pip install -r requirements-fastf1.txt
python scrape_f1_season.py --help
python scrape_f1_season.py --season 2026 --force
```

Useful commands:

```bash
# Latest completed Grand Prix; skip FastF1 while testing
python scrape_f1_season.py --force --skip-fastf1

# Explicitly select a completed race by name
python scrape_f1_season.py --race "United States Grand Prix" --skip-fastf1

# Quick smoke test without the per-round lap and pit-stop requests
python scrape_f1_season.py --force --skip-fastf1 --skip-round-details --output /tmp/f1-smoke.json
```

`--race` only selects a race that has already started at least 90 minutes ago. The default output is `f1-2026.json`; `--output` can put it elsewhere. `--season` changes the year. The base install only needs Requests and Beautiful Soup; FastF1 is deliberately optional because its timing archive can be unavailable for a session. The calendar is parsed with Python's standard library, so `icalendar` is not needed.

## GitHub Actions

The scheduled workflow checks the calendar every 15 minutes and collects data during the post-race window. Use **Actions → F1 season JSON → Run workflow** and set `force` to test outside that window. GitHub Actions cron is best-effort and may run late.

Do not put credentials directly in the repository. FastF1 data is optional; the consolidated Jolpica and Formula1.com data is still written if FastF1 fails.
