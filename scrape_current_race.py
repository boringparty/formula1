#!/usr/bin/env python3
"""Fetch the latest Grand Prix during its post-race window.

Formula1.com supplies the official classification/grid. FastF1 supplies detailed
lap timing, fastest laps, pit stops, weather, race-control messages, and
telemetry for each driver's personal fastest lap.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar

LOG = logging.getLogger("f1-current-race")

CALENDAR_URL = os.getenv(
    "F1_CALENDAR_URL",
    "https://files-f1.motorsportcalendars.com/f1-calendar_p1_p2_p3_qualifying_sprint_gp.ics",
)
OUTPUT_FILE = Path(os.getenv("F1_OUTPUT_FILE", "current-race.json"))
STATE_FILE = Path(os.getenv("F1_STATE_FILE", ".f1-scraper-state.json"))
SEASON = int(os.getenv("F1_SEASON", "2026"))
SCRAPE_START_AFTER = timedelta(minutes=90)
SCRAPE_CUTOFF_AFTER = timedelta(minutes=150)
REQUEST_TIMEOUT = 25
REQUEST_DELAY_SECONDS = 1.0
BASE_URL = f"https://www.formula1.com/en/results/{SEASON}/races"
FASTF1_CACHE_DIR = Path(os.getenv("FASTF1_CACHE_DIR", ".fastf1-cache"))
FORCE_SCRAPE = os.getenv("F1_FORCE_SCRAPE", "").strip().lower() in {"1", "true", "yes", "on"}

# Formula1.com race IDs/slugs supplied for this project.
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
        if start:
            events.append({
                "summary": event_text(component),
                "start_utc": start,
                "uid": clean(str(component.get("UID", ""))),
            })
    return sorted(events, key=lambda item: item["start_utc"])


def resolve_race(summary: str) -> tuple[int, str] | None:
    normalized = clean(summary).lower()
    for key in sorted(RACE_URLS, key=len, reverse=True):
        if key in normalized:
            return RACE_URLS[key]
    return None


def race_name_for_fastf1(summary: str) -> str:
    """Extract the actual GP name from summaries such as 'F1: Grand Prix (United States Grand Prix)'."""
    match = re.search(r"\(([^()]*Grand Prix)\)", summary, re.IGNORECASE)
    if match:
        return clean(match.group(1))
    # Fallback for calendar formats without parenthesized titles.
    match = re.search(r"((?:[A-Za-zÀ-ÿ0-9'’.-]+\s+){0,5}Grand Prix)", summary, re.IGNORECASE)
    if match:
        return clean(match.group(1))
    return clean(summary)


def select_event(events: list[dict], now: datetime, force: bool = False) -> tuple[dict | None, str]:
    """Select the GP whose +90m to +150m window is active, or latest past GP when forced."""
    if force:
        candidates = [e for e in events if e["start_utc"] + SCRAPE_START_AFTER <= now]
        return (max(candidates, key=lambda e: e["start_utc"]), "forced") if candidates else (None, "no_completed_event")

    candidates = [
        e for e in events
        if e["start_utc"] + SCRAPE_START_AFTER <= now <= e["start_utc"] + SCRAPE_CUTOFF_AFTER
    ]
    if not candidates:
        return None, "outside_window"
    return max(candidates, key=lambda e: e["start_utc"]), "scheduled_window"


def next_window(events: list[dict], now: datetime) -> dict | None:
    future = [e for e in events if e["start_utc"] + SCRAPE_CUTOFF_AFTER > now]
    return min(future, key=lambda e: e["start_utc"]) if future else None


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"official_completed": {}, "fastf1_completed": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data.setdefault("official_completed", data.pop("completed", {}))
        data.setdefault("fastf1_completed", {})
        return data
    except (OSError, json.JSONDecodeError):
        LOG.warning("State file unreadable; starting with empty state.")
        return {"official_completed": {}, "fastf1_completed": {}}


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
    headers = [clean(th.get_text(" ", strip=True)).rstrip(".").lower() for th in table.select("thead th")]
    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        values = []
        for i, cell in enumerate(cells):
            values.append(driver_name(cell) if i < len(headers) and headers[i] == "driver"
                          else clean(cell.get_text(" ", strip=True)))
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


def json_value(value):
    """Convert pandas/numpy timing values into plain JSON-safe values."""
    if value is None:
        return None
    # pandas timestamps and timedeltas are handled before generic scalar checks.
    try:
        import pandas as pd
        if value is pd.NaT:
            return None
        if isinstance(value, pd.Timedelta):
            return value.total_seconds()
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    # Handles NaT-like and NaN-like values without assuming pandas is present.
    try:
        import pandas as pd
        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def records(frame) -> list[dict]:
    if frame is None:
        return []
    if hasattr(frame, "empty") and frame.empty:
        return []
    return [json_value(row) for row in frame.to_dict(orient="records")]


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(json_value(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def authenticate_fastf1() -> None:
    username = os.getenv("F1_USERNAME", "").strip()
    password = os.getenv("F1_PASSWORD", "")
    if not username or not password:
        raise RuntimeError(
            "F1TV credentials are missing. Add repository Actions secrets F1_USERNAME and F1_PASSWORD."
        )

    env = os.environ.copy()
    env["FASTF1_AUTH_METHOD"] = "password"
    LOG.info("Authenticating with F1TV via FastF1 (credentials are kept in GitHub Secrets).")
    result = subprocess.run(
        [sys.executable, "-m", "fastf1", "auth", "f1tv", "--authenticate"],
        env=env, text=True, capture_output=True, timeout=90,
    )
    if result.stdout.strip():
        LOG.info("%s", result.stdout.strip())
    if result.returncode != 0:
        # Do not log the environment or credentials.
        safe_error = (result.stderr or result.stdout or "FastF1 authentication failed").strip()
        LOG.error("FastF1 authentication failed: %s", safe_error[-1800:])
        raise RuntimeError("FastF1 F1TV authentication failed; check the secrets and F1TV account.")


def df_records(frame) -> list[dict]:
    return records(frame)


def export_fastf1(event: dict, race_name: str, race_key: str) -> dict:
    """Load the Race session and write detailed timing datasets."""
    import fastf1

    FASTF1_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.configure(cache_dir=str(FASTF1_CACHE_DIR))

    authenticate_fastf1()

    LOG.info("Loading FastF1 Race session for %s, season %s.", race_name, SEASON)
    session = fastf1.get_session(SEASON, race_name, "R")
    session.load(laps=True, telemetry=True, weather=True, messages=True)

    laps = session.laps.copy()
    if laps is None or laps.empty:
        raise RuntimeError("FastF1 loaded the session but returned no lap data.")

    lap_records = df_records(laps)
    write_json(Path("lap-times.json"), {
        "source": "FastF1",
        "season": SEASON,
        "race": race_name,
        "race_key": race_key,
        "session": "Race",
        "columns_note": "Timedelta fields are represented as seconds; timestamps are ISO strings.",
        "laps": lap_records,
    })

    fastest = []
    for driver in sorted(str(d) for d in laps["Driver"].dropna().unique()):
        driver_laps = laps.pick_drivers(driver)
        valid = driver_laps[driver_laps["LapTime"].notna()]
        if valid.empty:
            continue
        lap = valid.pick_fastest()
        fastest.append({
            "driver": driver,
            "driver_number": json_value(lap.get("DriverNumber")),
            "team": json_value(lap.get("Team")),
            "lap_number": json_value(lap.get("LapNumber")),
            "lap_time_seconds": json_value(lap.get("LapTime")),
            "sector_1_seconds": json_value(lap.get("Sector1Time")),
            "sector_2_seconds": json_value(lap.get("Sector2Time")),
            "sector_3_seconds": json_value(lap.get("Sector3Time")),
            "speed_i1_kph": json_value(lap.get("SpeedI1")),
            "speed_i2_kph": json_value(lap.get("SpeedI2")),
            "speed_finish_line_kph": json_value(lap.get("SpeedFL")),
            "speed_trap_kph": json_value(lap.get("SpeedST")),
            "compound": json_value(lap.get("Compound")),
            "tyre_life": json_value(lap.get("TyreLife")),
            "stint": json_value(lap.get("Stint")),
            "is_personal_best": json_value(lap.get("IsPersonalBest")),
        })
    fastest.sort(key=lambda row: (row["lap_time_seconds"] is None, row["lap_time_seconds"] or float("inf")))
    if fastest:
        overall_time = fastest[0]["lap_time_seconds"]
        for row in fastest:
            row["is_overall_fastest"] = row["lap_time_seconds"] == overall_time
    write_json(Path("fastest-laps.json"), {
        "source": "FastF1",
        "season": SEASON,
        "race": race_name,
        "race_key": race_key,
        "overall_fastest": fastest[0] if fastest else None,
        "drivers": fastest,
    })

    pit_stops = []
    for driver in sorted(str(d) for d in laps["Driver"].dropna().unique()):
        dlaps = laps.pick_drivers(driver).sort_values("LapNumber")
        pending_in = None
        for _, lap in dlaps.iterrows():
            pit_in = lap.get("PitInTime")
            pit_out = lap.get("PitOutTime")
            if pit_in is not None and str(pit_in) != "NaT":
                pending_in = {
                    "driver": driver,
                    "driver_number": json_value(lap.get("DriverNumber")),
                    "in_lap": json_value(lap.get("LapNumber")),
                    "pit_in_time_seconds": json_value(pit_in),
                    "compound_before_stop": json_value(lap.get("Compound")),
                    "stint_before_stop": json_value(lap.get("Stint")),
                }
            if pit_out is not None and str(pit_out) != "NaT":
                entry = {
                    "driver": driver,
                    "driver_number": json_value(lap.get("DriverNumber")),
                    "out_lap": json_value(lap.get("LapNumber")),
                    "pit_out_time_seconds": json_value(pit_out),
                    "compound_after_stop": json_value(lap.get("Compound")),
                    "stint_after_stop": json_value(lap.get("Stint")),
                }
                if pending_in:
                    entry.update(pending_in)
                    pending_in = None
                pit_stops.append(entry)
    write_json(Path("pit-stops.json"), {
        "source": "FastF1", "season": SEASON, "race": race_name, "race_key": race_key,
        "pit_stops": pit_stops,
    })

    weather = getattr(session, "weather_data", None)
    write_json(Path("weather.json"), {
        "source": "FastF1", "season": SEASON, "race": race_name, "race_key": race_key,
        "weather": df_records(weather),
    })

    messages = getattr(session, "race_control_messages", None)
    write_json(Path("race-control.json"), {
        "source": "FastF1", "season": SEASON, "race": race_name, "race_key": race_key,
        "messages": df_records(messages),
    })

    telemetry_rows = []
    for item in fastest:
        driver = item["driver"]
        try:
            valid = laps.pick_drivers(driver)
            valid = valid[valid["LapTime"].notna()]
            if valid.empty:
                continue
            fastest_lap = valid.pick_fastest()
            telemetry = fastest_lap.get_telemetry()
            for sample in df_records(telemetry):
                sample["driver"] = driver
                sample["driver_number"] = item["driver_number"]
                sample["lap_number"] = item["lap_number"]
                telemetry_rows.append(sample)
        except Exception as exc:
            LOG.warning("Could not export fastest-lap telemetry for %s: %s", driver, exc)
    write_json(Path("fastest-lap-telemetry.json"), {
        "source": "FastF1",
        "season": SEASON,
        "race": race_name,
        "race_key": race_key,
        "note": "Telemetry samples for each driver's personal fastest valid race lap.",
        "samples": telemetry_rows,
    })

    return {
        "ok": True,
        "session_name": str(getattr(session, "name", "Race")),
        "lap_rows": len(lap_records),
        "drivers_with_fastest_laps": len(fastest),
        "pit_stops": len(pit_stops),
        "weather_rows": len(df_records(weather)),
        "race_control_messages": len(df_records(messages)),
        "telemetry_samples": len(telemetry_rows),
        "files": [
            "lap-times.json", "fastest-laps.json", "pit-stops.json", "weather.json",
            "race-control.json", "fastest-lap-telemetry.json",
        ],
    }


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    session = requests.Session()
    session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)

    # Calendar request is the only network request outside the scrape window.
    try:
        events = fetch_calendar(session)
    except Exception as exc:
        LOG.error("Could not load/parse race calendar: %s", exc)
        return 1

    event, mode = select_event(events, now, FORCE_SCRAPE)
    if not event:
        upcoming = next_window(events, now)
        if upcoming:
            opens = upcoming["start_utc"] + SCRAPE_START_AFTER
            closes = upcoming["start_utc"] + SCRAPE_CUTOFF_AFTER
            LOG.info(
                "Outside scrape window. Next candidate: %s; window %s to %s UTC. "
                "No Formula1.com or FastF1 requests made.",
                upcoming["summary"], opens.isoformat(), closes.isoformat(),
            )
        else:
            LOG.info("No eligible Grand Prix window found. No Formula1.com or FastF1 requests made.")
        return 0

    race_start = event["start_utc"]
    race_id_slug = resolve_race(event["summary"])
    race_name = race_name_for_fastf1(event["summary"])
    LOG.info(
        "Selected %s (%s; scheduled start %s; mode=%s)",
        event["summary"], race_name, race_start.isoformat(), mode,
    )

    if not race_id_slug:
        LOG.error("No Formula1.com URL mapping for %r", event["summary"])
        return 2

    race_id, slug = race_id_slug
    race_key = f"{race_id}:{slug}:{race_start.date().isoformat()}"
    state = load_state()
    official_done = race_key in state["official_completed"] and OUTPUT_FILE.exists()
    fastf1_outputs = [
        Path("lap-times.json"), Path("fastest-laps.json"), Path("pit-stops.json"),
        Path("weather.json"), Path("race-control.json"), Path("fastest-lap-telemetry.json"),
    ]
    fastf1_done = (
        race_key in state["fastf1_completed"]
        and all(path.exists() for path in fastf1_outputs)
    )

    race_base = f"{BASE_URL}/{race_id}/{slug}"
    document = None

    if not official_done or FORCE_SCRAPE:
        LOG.info("Fetching Formula1.com classification and grid.")
        results = scrape_page(session, f"{race_base}/race-result")
        time.sleep(REQUEST_DELAY_SECONDS)
        grid = scrape_page(session, f"{race_base}/starting-grid")
        grid_source = "starting-grid" if grid["ok"] else None
        if not grid["ok"]:
            time.sleep(REQUEST_DELAY_SECONDS)
            qualifying = scrape_page(session, f"{race_base}/qualifying")
            if qualifying["ok"]:
                grid, grid_source = qualifying, "qualifying"
            else:
                grid["fallback_qualifying"] = qualifying
        document = {
            "source": "Formula1.com",
            "season": SEASON,
            "race_id": race_id,
            "race": event["summary"],
            "fastf1_event_name": race_name,
            "calendar_event_start_utc": race_start.isoformat(),
            "scraped_at_utc": now.isoformat(),
            "race_url": race_base,
            "race_results": results,
            "starting_grid": {"source": grid_source, **grid},
        }
        if results["ok"]:
            # Preserve FastF1 metadata already present if rerunning.
            try:
                existing = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
                if existing.get("race_id") == race_id and existing.get("fastf1"):
                    document["fastf1"] = existing["fastf1"]
            except (OSError, json.JSONDecodeError):
                pass
            write_json(OUTPUT_FILE, document)
            state["official_completed"][race_key] = {
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "output": str(OUTPUT_FILE),
            }
            save_state(state)
            official_done = True
            LOG.info("Saved official results to %s.", OUTPUT_FILE)
        else:
            LOG.warning("Formula1.com results not confirmed; will retry if still inside the window.")
    else:
        LOG.info("Official results already saved for this race; continuing with any missing FastF1 data.")

    if fastf1_done and not FORCE_SCRAPE:
        LOG.info("FastF1 datasets already saved for this race; nothing more to do.")
        return 0

    try:
        fastf1_status = export_fastf1(event, race_name, race_key)
        fastf1_status["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(Path("fastf1-status.json"), fastf1_status)
        state["fastf1_completed"][race_key] = {
            "completed_at_utc": fastf1_status["completed_at_utc"],
            "files": fastf1_status["files"],
        }
        save_state(state)
        try:
            current = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {
                "source": "Formula1.com", "season": SEASON, "race_id": race_id,
                "race": event["summary"], "fastf1_event_name": race_name,
                "calendar_event_start_utc": race_start.isoformat(), "race_url": race_base,
            }
        current["fastf1"] = fastf1_status
        write_json(OUTPUT_FILE, current)
        LOG.info("FastF1 datasets saved successfully.")
        return 0
    except Exception as exc:
        LOG.exception("FastF1 export failed; next scheduled attempt will retry inside the window.")
        status = {
            "ok": False,
            "season": SEASON,
            "race": race_name,
            "race_key": race_key,
            "attempted_at_utc": datetime.now(timezone.utc).isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
            "note": "Check the Actions log. Credentials are never written to this file.",
        }
        write_json(Path("fastf1-status.json"), status)
        return 0


if __name__ == "__main__":
    sys.exit(main())
