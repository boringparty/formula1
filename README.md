# F1 current-race scraper

This GitHub Actions setup uses the MotorsportCalendars ICS feed to determine race timing.
It downloads the calendar on each hourly workflow run, but does **not** request Formula1.com
unless the current time is inside the post-race window.

## Files

- `scrape_current_race.py` — calendar-aware scraper
- `requirements.txt` — Python dependencies
- `.github/workflows/f1-current-race.yml` — hourly workflow and manual trigger
- `current-race.json` — generated after confirmed race results
- `.f1-scraper-state.json` — remembers races with confirmed results so they are not scraped again

## Install

Copy these files into the root of your GitHub repository, preserving the `.github/workflows/` path.
In GitHub, open **Settings → Actions → General → Workflow permissions** and enable read/write
permissions if your organization/repository policy requires it. The workflow also declares
`contents: write`.

The workflow runs hourly at minute 17 UTC. Each run fetches the ICS calendar, then:
- exits without contacting Formula1.com outside the post-race window;
- begins checks about two hours after the scheduled Grand Prix start;
- retries hourly for up to four additional hours if the official race-results table is not available;
- saves JSON and records the race as complete once race-result rows are found;
- uses `starting-grid`, falling back to `qualifying` when the grid page has no rows.

The output JSON is committed to the repository root. To fetch it from another script, use the
raw GitHub URL for `current-race.json`.

## Manual test

From a Python environment:

```sh
python -m pip install -r requirements.txt
python scrape_current_race.py
```

A manual run outside a post-race window should log that it is waiting and make no Formula1.com
requests. `workflow_dispatch` lets you run it from the Actions tab.

## Important

The script currently expects the ICS feed to label the Grand Prix event with “Grand Prix”.
It safely skips unknown event names rather than guessing a Formula1.com URL. If Formula1.com
changes race IDs/slugs, update `RACE_URLS` in `scrape_current_race.py`.

The state file marks a race complete when race-result rows are found, even if grid/qualifying
is still unavailable. If you want grid data to be refreshed separately after results publish,
that should be added as a second completion state.
