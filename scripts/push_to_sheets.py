#!/usr/bin/env python3
"""Push current.json into the F1 Pool Google Sheet."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1qlgU6Nbcj9gSmnI8RgFmrm3lqtK03kanHaxwGJrcIII"
SHEET_TAB = "results"
JSON_FILE = Path(__file__).resolve().parent.parent / "current.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
MIN_RACE_ROWS = 10  # Avoid replacing results with a partial/pre-race response.


def numeric_value(value):
    """Return an integer for whole-number fields, or blank when unavailable."""
    if value is None or str(value).strip() == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return int(number) if number.is_integer() else number


def duration_value(value):
    """Convert HH:MM:SS.mmm (or M:SS.mmm) into a Sheets duration serial."""
    if value is None or str(value).strip() == "":
        return ""

    value = str(value).strip()
    match = re.fullmatch(r"(?:(\\d+):)?(\\d+):([0-5]\\d)(?:\\.(\\d+))?", value)
    if not match:
        logging.warning("Unrecognized fastest-lap duration %r; writing blank.", value)
        return ""

    hours, minutes, seconds, fraction = match.groups()
    hours = int(hours or 0)
    minutes = int(minutes)
    seconds = int(seconds)
    fractional_seconds = float(f"0.{fraction}") if fraction else 0.0
    total_seconds = hours * 3600 + minutes * 60 + seconds + fractional_seconds
    return total_seconds / 86400  # Google Sheets stores durations as fractions of a day.


def load_payload() -> dict:
    if not JSON_FILE.is_file():
        raise FileNotFoundError(f"Missing JSON file: {JSON_FILE}")

    with JSON_FILE.open(encoding="utf-8") as file:
        payload = json.load(file)

    drivers = payload.get("drivers")
    if not isinstance(drivers, list) or not drivers:
        raise ValueError("current.json has no driver records; refusing to update the sheet.")
    return payload


def get_worksheet():
    raw_credentials = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw_credentials:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON environment variable.")

    credentials_info = json.loads(raw_credentials)
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
    )
    client = gspread.authorize(credentials)
    return client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    payload = load_payload()
    drivers = payload["drivers"]
    worksheet = get_worksheet()

    # Starting grid: only replace the sheet range when grid data exists.
    grid_drivers = [d for d in drivers if str(d.get("grid", "")).strip().isdigit()]
    grid_drivers.sort(key=lambda d: int(d["grid"]))
    if grid_drivers:
        grid_rows = [
            [d.get("driverName", ""), d.get("constructorName", "")]
            for d in grid_drivers
        ]
        worksheet.batch_clear(["B2:C"])
        worksheet.update(
            values=grid_rows,
            range_name="B2",
            value_input_option="RAW",
        )
        logging.info("Updated starting grid (%d drivers).", len(grid_rows))
    else:
        logging.info("No starting-grid data found; B:C left unchanged.")

    # Race results: require a substantial classification before replacing old data.
    race_data_count = sum(
        1
        for d in drivers
        if any(str(d.get(field, "")).strip() for field in ("race", "laps", "status"))
    )
    if race_data_count >= MIN_RACE_ROWS:
        race_rows = [
            [
                d.get("driverName", ""),
                d.get("constructorName", ""),
                numeric_value(d.get("grid", "")),
                numeric_value(d.get("race", "")),
                d.get("status", ""),
                numeric_value(d.get("laps", "")),
                duration_value(d.get("fastestLap", "")),
            ]
            for d in drivers
        ]
        worksheet.batch_clear(["E2:K"])
        worksheet.update(
            values=race_rows,
            range_name="E2",
            value_input_option="RAW",
        )
        # Ensure numeric fields are stored/displayed as numbers, not text.
        worksheet.format("G2:H", {"numberFormat": {"type": "NUMBER", "pattern": "0"}})
        worksheet.format("J2:J", {"numberFormat": {"type": "NUMBER", "pattern": "0"}})
        worksheet.format("K2:K", {"numberFormat": {"type": "TIME", "pattern": "[hh]:mm:ss.000"}})

        logging.info(
            "Updated race results (%d drivers; %d with race data).",
            len(race_rows),
            race_data_count,
        )
    else:
        logging.info(
            "Race classification not ready (%d/%d records); E:K left unchanged.",
            race_data_count,
            MIN_RACE_ROWS,
        )


if __name__ == "__main__":
    main()
