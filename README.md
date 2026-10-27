# Consolidated F1 season JSON

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

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
F1_FORCE_SCRAPE=1 python scrape_f1_season.py
```

Set `F1_SEASON` to change the year. Do not put credentials directly in the repository. FastF1 data is optional; the consolidated Jolpica and Formula1.com data is still written if FastF1 fails.
