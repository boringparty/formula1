#!/usr/bin/env python3
"""Generate markdown.md race report from current.json, enriching it from F1.com."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
CURRENT_FILE = Path(os.getenv("F1_CURRENT_JSON", ROOT / "current.json"))
SEASON_FILE = Path(os.getenv("F1_SEASON_JSON", ROOT / "season.json"))
OUTPUT_FILE = Path(os.getenv("F1_MARKDOWN_OUTPUT", ROOT / "markdown.md"))
SEASON = int(os.getenv("F1_SEASON", "2026"))
JOLPICA = "https://api.jolpi.ca/ergast/f1"
F1_RESULTS = f"https://www.formula1.com/en/results/{SEASON}"
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.formula1.com/",
}
TEAM_DISPLAY = {
    "red bull": "Red Bull Racing",
    "rb": "Racing Bulls",
    "rb f1 team": "Racing Bulls",
    "alpine f1 team": "Alpine",
    "haas": "Haas F1 Team",
    "haas f1 team": "Haas F1 Team",
    "cadillac f1 team": "Cadillac",
    "aston martin": "Aston Martin",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def log(msg: str, *args: Any) -> None:
    logging.info(msg, *args)


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean(value).lower())


def fetch_text(url: str) -> str:
    try:
        response = SESSION.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        logging.warning("Could not fetch %s: %s", url, exc)
        return ""


def fetch_json(url: str) -> dict:
    try:
        response = SESSION.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data.get("MRData", data) if isinstance(data, dict) else {}
    except (requests.RequestException, ValueError) as exc:
        logging.warning("Could not fetch JSON %s: %s", url, exc)
        return {}


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def table_records(url: str) -> tuple[list[str], list[list[str]]]:
    html = fetch_text(url)
    if not html:
        return [], []
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="results-table") or soup.find("table")
    if not table:
        logging.warning("No HTML results table found at %s", url)
        return [], []
    rows = table.find_all("tr")
    if not rows:
        return [], []
    headers = [norm(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
    records = []
    for row in rows[1:]:
        cells = [clean(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"])]
        if cells:
            records.append(cells)
    return headers, records


def col(headers: list[str], *names: str) -> int | None:
    wanted = {norm(n) for n in names}
    return next((i for i, h in enumerate(headers) if h in wanted), None)


def cell(row: list[str], index: int | None) -> str:
    return row[index] if index is not None and index < len(row) else ""


def get_race_meta(current: dict, season_doc: Any) -> dict:
    """Use the season JSON first, then Jolpica for schedule/circuit metadata."""
    round_no = str(current.get("round", ""))
    candidate = {}
    if isinstance(season_doc, dict):
        # Support the current season document's common layouts.
        for path in (
            ("current_race", "race_metadata"),
            ("current_race",),
        ):
            value: Any = season_doc
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, dict) and str(value.get("round", "")) == round_no:
                candidate = value
                break
        if not candidate:
            sources = season_doc.get("sources", {})
            jol = sources.get("jolpica", {}) if isinstance(sources, dict) else {}
            races = jol.get("races", {}).get("data", []) if isinstance(jol, dict) else []
            candidate = next((r for r in races if str(r.get("round", "")) == round_no), {})
    if candidate:
        return candidate

    data = fetch_json(f"{JOLPICA}/{SEASON}/races.json")
    races = data.get("RaceTable", {}).get("Races", [])
    return next((r for r in races if str(r.get("round", "")) == round_no), {})


def discover_f1_race_name(race_name: str, round_no: str = "") -> tuple[int, str] | None:
    html = fetch_text(f"{F1_RESULTS}/races")
    if not html:
        return None
    wanted = norm(race_name.replace("Grand Prix", ""))
    pattern = re.compile(rf"/en/results/{SEASON}/races/(\d+)/([a-z0-9-]+)")
    found: list[tuple[int, str]] = []
    for match in pattern.finditer(html):
        item = (int(match.group(1)), match.group(2))
        if item not in found:
            found.append(item)
    for race_id, slug in found:
        slug_key = norm(slug)
        if slug_key == wanted or slug_key in wanted or wanted in slug_key:
            return race_id, slug
    if round_no.isdigit() and 1 <= int(round_no) <= len(found):
        return found[int(round_no) - 1]
    return None


def discover_f1_race_name(race_name: str, round_no: str = "") -> tuple[int, str] | None:
    html = fetch_text(f"{F1_RESULTS}/races")
    if not html:
        return None
    wanted = norm(race_name.replace("Grand Prix", ""))
    pattern = re.compile(rf"/en/results/{SEASON}/races/(\d+)/([a-z0-9-]+)")
    found: list[tuple[int, str]] = []
    for match in pattern.finditer(html):
        item = (int(match.group(1)), match.group(2))
        if item not in found:
            found.append(item)
    for race_id, slug in found:
        slug_key = norm(slug)
        if slug_key == wanted or slug_key in wanted or wanted in slug_key:
            return race_id, slug
    if round_no.isdigit() and 1 <= int(round_no) <= len(found):
        return found[int(round_no) - 1]
    return None


def discover_f1_race(current: dict) -> tuple[int, str] | None:
    return discover_f1_race_name(str(current.get("race_name", "")), str(current.get("round", "")))


def official_event_details(url: str) -> tuple[str, str]:
    """Extract official weekend date range and circuit name from an F1 results page."""
    html = fetch_text(url)
    if not html:
        return "", ""
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" ", strip=True))
    date_label = ""
    match = re.search(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", text)
    if match:
        start, end, month, year = match.groups()
        try:
            month_num = datetime.strptime(month[:3], "%b").month
            month_name = datetime(month_num, 1, 1).strftime("%B")
            date_label = f"{month_name} {int(start)}-{int(end)}, {year}"
        except ValueError:
            date_label = match.group(0)
    circuit = ""
    circuit_match = re.search(
        r"\d{1,2}\s*[-–]\s*\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\s+([^,]{4,100}Circuit)",
        text,
    )
    if circuit_match:
        circuit = clean(circuit_match.group(1))
    return date_label, circuit


def scrape_dotd(race_name: str) -> str:
    """Read the official F1 season Driver of the Day results table."""
    url = f"https://www.formula1.com/en/results/{SEASON}/awards/driver-of-the-day"
    html = fetch_text(url)
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    wanted = norm(race_name.replace("Grand Prix", ""))
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [norm(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
        gp_idx = col(headers, "grand prix", "grandprix")
        winner_idx = col(headers, "winner")
        if gp_idx is None or winner_idx is None:
            continue
        for row in rows[1:]:
            cells = [clean(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"])]
            gp = cell(cells, gp_idx)
            winner = cell(cells, winner_idx)
            if gp and winner and (norm(gp) in wanted or wanted in norm(gp)):
                return re.sub(r"\s+[A-Z]{3}$", "", winner).strip()
    return ""


def parse_official_table(url: str, kind: str) -> list[dict]:
    headers, rows = table_records(url)
    if not headers:
        return []
    indexes = {
        "pos": col(headers, "pos", "position"),
        "no": col(headers, "no", "number", "num"),
        "driver": col(headers, "driver"),
        "team": col(headers, "team"),
        "laps": col(headers, "laps"),
        "time": col(headers, "time", "time/retired"),
        "pts": col(headers, "pts", "points"),
        "q1": col(headers, "q1"),
        "q2": col(headers, "q2"),
        "q3": col(headers, "q3"),
        "lap": col(headers, "lap"),
    }
    output = []
    for row in rows:
        number = cell(row, indexes["no"])
        if not number.isdigit():
            continue
        record = {"no": number}
        for key in ("pos", "driver", "team", "laps", "time", "pts", "q1", "q2", "q3", "lap"):
            record[key] = cell(row, indexes[key])
        output.append(record)
    return output


def display_team(team: str) -> str:
    return TEAM_DISPLAY.get(clean(team).lower(), clean(team))


def md_escape(value: Any) -> str:
    return clean(value).replace("|", r"\|")


def pad_table(headers: list[str], rows: list[list[str]], aligns: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    head = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    sep = "| " + " | ".join(
        (":" + "-" * max(1, widths[i] - 1) if aligns[i] == "left"
         else "-" * max(1, widths[i] - 1) + ":" if aligns[i] == "right"
         else ":" + "-" * max(1, widths[i] - 2) + ":")
        for i in range(len(headers))
    ) + " |"
    body = ["| " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(row)) + " |" for row in rows]
    return "\n".join([head, sep, *body])


def date_range_from_season(current: dict, meta: dict) -> str:
    # Prefer weekend date metadata if the season JSON contains it.
    for key in ("weekend", "weekend_dates", "date_range"):
        value = meta.get(key)
        if isinstance(value, str) and value:
            return value
    # Otherwise use official F1 race date; don't invent a weekend span.
    date = clean(meta.get("date") or current.get("date"))
    try:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return dt.strftime("%B %-d, %Y")
    except ValueError:
        return date


def find_next_race(current: dict) -> dict:
    data = fetch_json(f"{JOLPICA}/{SEASON}/races.json")
    races = data.get("RaceTable", {}).get("Races", [])
    try:
        this_round = int(current.get("round", 0))
    except (TypeError, ValueError):
        this_round = 0
    return next((r for r in races if int(r.get("round", 0)) > this_round), {})


def build_report(current: dict, season_doc: Any) -> str:
    drivers = current.get("drivers", [])
    if not isinstance(drivers, list) or not drivers:
        raise ValueError("current.json has no drivers array.")

    meta = get_race_meta(current, season_doc)
    race_page = discover_f1_race(current)
    if race_page:
        race_id, slug = race_page
        base = f"{F1_RESULTS}/races/{race_id}/{slug}"
        race_url = f"https://www.formula1.com/en/racing/{SEASON}/{slug}"
    else:
        race_id, slug = None, norm(current.get("race_name", "")).lower()
        base = ""
        race_url = f"https://www.formula1.com/en/racing/{SEASON}/{slug}"

    official_qualy = parse_official_table(f"{base}/qualifying", "qualifying") if base else []
    official_race = parse_official_table(f"{base}/race-result", "race") if base else []
    official_fastest = parse_official_table(f"{base}/fastest-laps", "fastest") if base else []

    by_no = {str(d.get("no", "")): d for d in drivers}
    qualy_by_no = {r["no"]: r for r in official_qualy}
    race_by_no = {r["no"]: r for r in official_race}
    fastest_by_no = {r["no"]: r for r in official_fastest}

    # Official result tables enrich fields absent from current.json.
    qualy_rows = []
    for d in sorted(drivers, key=lambda x: (int(x.get("qualy") or 999) if str(x.get("qualy", "")).isdigit() else 999,
                                           int(x.get("no") or 999))):
        no = str(d.get("no", ""))
        q = qualy_by_no.get(no, {})
        qualy_rows.append([
            md_escape(d.get("qualy") or q.get("pos") or "NC"),
            md_escape(no),
            md_escape(d.get("driverName", "")),
            md_escape(display_team(d.get("constructorName", ""))),
            md_escape(d.get("q1") or q.get("q1", "")),
            md_escape(d.get("q2") or q.get("q2", "")),
            md_escape(d.get("q3") or q.get("q3", "")),
            md_escape(q.get("laps", "")),
        ])

    race_rows = []
    for d in sorted(drivers, key=lambda x: (int(x.get("pos") or 999) if str(x.get("pos", "")).isdigit() else 999,
                                           int(x.get("no") or 999))):
        no = str(d.get("no", ""))
        r = race_by_no.get(no, {})
        # Use the JSON for classification, falling back to the official table.
        pos = d.get("pos") or r.get("pos") or "NC"
        laps = d.get("laps") or r.get("laps", "")
        time_retired = r.get("time") or d.get("status") or ""
        pts = d.get("pts") or r.get("pts", "")
        race_rows.append([
            md_escape(pos), md_escape(no), md_escape(d.get("driverName", "")),
            md_escape(display_team(d.get("constructorName", ""))),
            md_escape(laps), md_escape(time_retired), md_escape(pts),
        ])

    # Winner's total elapsed time is the winner's Time/Retired cell.
    winner_time = ""
    if official_race:
        winner = next((r for r in official_race if r.get("pos") == "1"), None)
        if winner:
            winner_time = winner.get("time", "")

    # Fastest lap: use the fastest-laps table ordering (P1), then current.json.
    fastest_driver = ""
    fastest_time = ""
    fastest_lap_no = ""
    if official_fastest:
        fastest = next((r for r in official_fastest if r.get("pos") == "1"), official_fastest[0])
        fastest_driver = fastest.get("driver", "")
        fastest_time = fastest.get("time", "")
        fastest_lap_no = fastest.get("lap", "")
    if fastest_driver:
        fastest_driver = re.sub(r"\s+[A-Z]{3}$", "", fastest_driver).strip()
    if not fastest_driver or not fastest_time:
        candidates = [d for d in drivers if d.get("fastestLap")]
        if candidates:
            best = min(candidates, key=lambda d: str(d["fastestLap"]))
            fastest_driver = best.get("driverName", "")
            raw_time = str(best.get("fastestLap", ""))
            duration = re.fullmatch(r"00:(\d{2}):(\d{2}\.\d+)", raw_time)
            fastest_time = f"{int(duration.group(1))}:{duration.group(2)}" if duration else raw_time

    # Current event metadata and circuit.
    circuit = meta.get("Circuit", {}) if isinstance(meta, dict) else {}
    circuit_name = clean(circuit.get("circuitName", "")) or clean(meta.get("circuitName", ""))
    event_date = date_range_from_season(current, meta)
    if base:
        scraped_date, scraped_circuit = official_event_details(f"{base}/race-result")
        event_date = scraped_date or event_date
        circuit_name = scraped_circuit or circuit_name
    race_name = clean(current.get("race_name", "Grand Prix"))
    source_suffix = f"{race_id}/{slug}" if race_id else ""
    q_source = f"https://www.formula1.com/en/results/{SEASON}/races/{source_suffix}/qualifying" if source_suffix else ""
    r_source = f"https://www.formula1.com/en/results/{SEASON}/races/{source_suffix}/race-result" if source_suffix else ""

    lines = [
        f"**[{race_name}]({race_url})**",
        circuit_name,
        event_date,
        "",
        "------",
        "",
        "<details>",
        "<summary>Qualifying Results -- SPOILER</summary>",
        "",
        pad_table(["Pos.", "No.", "Driver", "Team", "Q1", "Q2", "Q3", "Laps"], qualy_rows,
                  ["center"] * 8),
        "",
    ]
    if q_source:
        lines.append(f"Source: [F1.com]({q_source})")
    lines += [
        "</details>",
        "",
        "<details>",
        "<summary>Grand Prix Results -- SPOILER</summary>",
        "",
        pad_table(["Pos.", "No.", "Driver", "Team", "Laps", "Time / Retired", "Pts."], race_rows,
                  ["center"] * 7),
        "",
    ]
    if fastest_driver and fastest_time:
        lap_text = f" on lap {fastest_lap_no}" if fastest_lap_no else ""
        lines.append(f"Fastest Lap:  {fastest_driver} // {fastest_time}{lap_text}")
    # DOTD is not part of current.json or the standard results tables.
    # Leave it out unless a source is explicitly available in the season data.
    dotd = scrape_dotd(race_name)
    if not dotd and isinstance(season_doc, dict):
        current_race = season_doc.get("current_race", {})
        if isinstance(current_race, dict):
            dotd = clean(current_race.get("driver_of_the_day", ""))
    if dotd:
        lines.append(f"DOTD: {dotd}")
    if r_source:
        lines += ["", f"Source: [F1.com]({r_source})"]
    lines += ["</details>", ""]

    next_race = find_next_race(current)
    if next_race:
        circuit_next = next_race.get("Circuit", {})
        location = circuit_next.get("Location", {})
        next_page = discover_f1_race_name(str(next_race.get("raceName", "")), str(next_race.get("round", "")))
        next_slug = next_page[1] if next_page else re.sub(r"[^a-z0-9-]+", "-", norm(next_race.get("raceName", "")).lower()).strip("-")
        next_url = f"https://www.formula1.com/en/racing/{SEASON}/{next_slug}"
        next_name = clean(next_race.get("raceName", ""))
        next_circuit = clean(circuit_next.get("circuitName", ""))
        next_date = clean(next_race.get("date", ""))
        if next_page:
            next_base = f"{F1_RESULTS}/races/{next_page[0]}/{next_page[1]}/race-result"
            scraped_next_date, scraped_next_circuit = official_event_details(next_base)
            next_date = scraped_next_date or next_date
            next_circuit = scraped_next_circuit or next_circuit
        lines += [
            "**Next race:**",
            f"**[{next_name}]({next_url})**",
            next_circuit,
            next_date,
        ]

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        current = read_json(CURRENT_FILE)
        season_doc = read_json(SEASON_FILE) if SEASON_FILE.exists() else {}
        markdown = build_report(current, season_doc)
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(markdown, encoding="utf-8")
        log("Wrote %s (%d characters).", OUTPUT_FILE, len(markdown))
        return 0
    except Exception as exc:
        logging.exception("Could not generate Markdown report: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
