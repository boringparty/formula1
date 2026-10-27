#!/usr/bin/env python3
"""Scrape the latest completed 2026 Grand Prix from Formula1.com."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar
from zoneinfo import ZoneInfo

LOG = logging.getLogger("f1-current-race")

CALENDAR_URL = os.getenv(
    "F1_CALENDAR_URL",
    "https://files-f1.motorsportcalendars.com/f1-calendar_p1_p2_p3_qualifying_sprint_gp.ics",
)
OUTPUT_FILE = Path(os.getenv("F1_OUTPUT_FILE", "current-race.json"))
STATE_FILE = Path(os.getenv("F1_STATE_FILE", ".f1-scraper-state.json"))
# A Grand Prix is treated as completed two hours after its scheduled start.
# The scraper always targets the latest such event, not the next event in the calendar.
EXPECTED_RACE_DURATION = timedelta(hours=2)
REQUEST_TIMEOUT = 25
REQUEST_DELAY_SECONDS = 1.0
BASE_URL = "https://www.formula1.com/en/results/2026/races"

# Map event names to the Formula1.com race IDs/slugs supplied for this project.
# Unmapped events are safely skipped and reported; add a mapping if F1 changes its URLs.
RACE_URLS = {
    "australia": (1279, "australia"),
    "china": (1280, "china"),
    "japan": (1281, "japan"),
    "miami": (1284, "miami"),
    "canada": (1285, "canada"),
    "monaco": (1286, "monaco"),
    "barcelona": (1287, "barcelona-catalunya"),
    "catalunya": (1287, "barcelona-catalunya"),
    "austria": (1288, "austria"),
    "great britain": (1289, "great-britain"),
    "british": (1289, "great-britain"),
    "belgium": (1290, "belgium"),
    "hungary": (1291, "hungary"),
    "netherlands": (1292, "netherlands"),
    "dutch": (1292, "netherlands"),
    "italy": (1293, "italy"),
    "italian": (1293, "italy"),
    "spain": (1294, "spain"),
    "azerbaijan": (1295, "azerbaijan"),
    "singapore": (1296, "singapore"),
    "united states": (1297, "united-states"),
    "usa": (1297, "united-states"),
    "mexico": (1298, "mexico"),
    "brazil": (1299, "brazil"),
    "são paulo": (1299, "brazil"),
    "sao paulo": (1299, "brazil"),
    "las vegas": (1300, "las-vegas"),
    "qatar": (1301, "qatar"),
    "abu dhabi": (1302, "abu-dhabi"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.formula1.com/",
}


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def event_text(event) -> str:
    return clean(str(event.get("SUMMARY", "")))


def is_grand_prix_event(event) -> bool:
    summary = event_text(event).lower()
    # The calendar contains practice, qualifying, sprint and race entries.
    # Accept the Grand Prix event itself; reject any session-specific title.
    if "grand prix" not in summary:
        return False
    excluded = ("practice", "qualifying", "sprint", "warm-up", "warm up")
    return not any(word in summary for word in excluded)


def event_start(event) -> datetime | None:
    value = event.get("DTSTART")
    if value is None:
        return None
    dt = value.dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            # ICS events should have a timezone. If one is absent, assume UTC
            # rather than silently using the runner's local timezone.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def fetch_calendar(session: requests.Session) -> list[dict]:
    response = session.get(CALENDAR_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    calendar = Calendar.from_ical(response.content)
    events = []
    for component in calendar.walk("VEVENT"):
        if not is_grand_prix_event(component):
            continue
        start = event_start(component)
        if not start:
            continue
        events.append({
            "summary": event_text(component),
            "start_utc": start,
            "uid": clean(str(component.get("UID", ""))),
        })
    return sorted(events, key=lambda item: item["start_utc"])


def resolve_race(summary: str) -> tuple[int, str] | None:
    normalized = clean(summary).lower()
    # Prefer the most specific known phrase first (e.g. "Las Vegas").
    for key in sorted(RACE_URLS, key=len, reverse=True):
        if key in normalized:
            return RACE_URLS[key]
    return None


def select_event(events: list[dict], now: datetime) -> tuple[dict | None, str]:
    """Return the most recent Grand Prix whose expected finish time has passed."""
    completed_candidates = [
        event for event in events
        if event["start_utc"] + EXPECTED_RACE_DURATION <= now
    ]
    if not completed_candidates:
        return None, "no_completed_event"
    return max(completed_candidates, key=lambda item: item["start_utc"]), "latest_completed"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"completed": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        LOG.warning("State file unreadable; starting with empty state.")
        return {"completed": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def driver_name(cell) -> str:
    first = cell.select_one("span.max-lg\\:hidden")
    last = cell.select_one("span.max-md\\:hidden")
    if first and last:
        value = clean(f"{first.get_text(' ', strip=True)} {last.get_text(' ', strip=True)}")
        if value:
            return value
    mobile = cell.select_one("span.md\\:hidden")
    if mobile:
        mobile.extract()
    return clean(cell.get_text(" ", strip=True))


def parse_table(html: str) -> tuple[list[dict], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#results-table table") or soup.find("table")
    if not table:
        return [], []
    headers = [
        clean(th.get_text(" ", strip=True)).rstrip(".").lower()
        for th in table.select("thead th")
    ]
    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        values = []
        for i, cell in enumerate(cells):
            if i < len(headers) and headers[i] == "driver":
                values.append(driver_name(cell))
            else:
                values.append(clean(cell.get_text(" ", strip=True)))
        if not any(values):
            continue
        record = {}
        for i, value in enumerate(values):
            key = headers[i] if i < len(headers) and headers[i] else f"column_{i + 1}"
            key = key.replace(" / ", "_").replace(" ", "_").replace("-", "_")
            record[key] = value
        record["position"] = record.get("pos") or record.get("position")
        record["driver_number"] = record.get("no") or record.get("number")
        rows.append(record)
    return rows, headers


def scrape_page(session: requests.Session, url: str) -> dict:
    result = {"url": url, "status_code": None, "ok": False, "headers": [], "rows": [], "error": None}
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        result["status_code"] = response.status_code
        response.raise_for_status()
        rows, headers = parse_table(response.text)
        result.update(ok=bool(rows), headers=headers, rows=rows)
        if not rows:
            result["error"] = "No table rows found; page may be unpublished or markup/challenge changed."
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    session = requests.Session()
    session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)

    # The calendar is the only request made outside the race window.
    try:
        events = fetch_calendar(session)
    except Exception as exc:
        LOG.error("Could not load/parse race calendar: %s", exc)
        return 1

    event, mode = select_event(events, now)
    if not event:
        LOG.info("No Grand Prix has passed its expected finish time yet; nothing to scrape.")
        return 0

    race_start = event["start_utc"]
    race_id_slug = resolve_race(event["summary"])
    LOG.info("Latest completed Grand Prix candidate: %s (scheduled start %s)", event["summary"], race_start.isoformat())

    if not race_id_slug:
        LOG.error("Inside race window but no Formula1.com URL mapping for %r", event["summary"])
        return 2

    race_id, slug = race_id_slug
    race_key = f"{race_id}:{slug}:{race_start.date().isoformat()}"
    state = load_state()
    if state.get("completed", {}).get(race_key) and OUTPUT_FILE.exists():
        try:
            existing = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            if existing.get("race_id") == race_id and existing.get("race_url", "").endswith(f"/{race_id}/{slug}"):
                LOG.info("Latest completed race %s is already saved in %s; no duplicate scrape needed.", race_key, OUTPUT_FILE)
                return 0
        except (OSError, json.JSONDecodeError):
            pass

    race_base = f"{BASE_URL}/{race_id}/{slug}"
    LOG.info("Inside post-race window; scraping %s.", race_base)

    results = scrape_page(session, f"{race_base}/race-result")
    import time
    time.sleep(REQUEST_DELAY_SECONDS)

    grid = scrape_page(session, f"{race_base}/starting-grid")
    grid_source = "starting-grid" if grid["ok"] else None
    if not grid["ok"]:
        time.sleep(REQUEST_DELAY_SECONDS)
        qualifying = scrape_page(session, f"{race_base}/qualifying")
        if qualifying["ok"]:
            grid = qualifying
            grid_source = "qualifying"
        else:
            grid["fallback_qualifying"] = qualifying

    document = {
        "source": "Formula1.com",
        "season": 2026,
        "race_id": race_id,
        "race": event["summary"],
        "calendar_event_start_utc": race_start.isoformat(),
        "scraped_at_utc": now.isoformat(),
        "race_url": race_base,
        "race_results": results,
        "starting_grid": {"source": grid_source, **grid},
    }

    # Only replace the published JSON when race results have actual rows.
    if results["ok"]:
        OUTPUT_FILE.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state.setdefault("completed", {})[race_key] = {
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "output": str(OUTPUT_FILE),
        }
        save_state(state)
        LOG.info("Saved %s; confirmed race results. Future runs will skip this race.", OUTPUT_FILE)
        return 0

    # Do not overwrite a previously good JSON with an empty/error result.
    LOG.warning("Race results not confirmed yet. Will retry on the next scheduled run if still in window.")
    LOG.warning("No confirmed results yet for the latest completed race. The next scheduled run will retry it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
