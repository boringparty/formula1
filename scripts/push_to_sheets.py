#!/usr/bin/env python3
"""Push current.json into the F1 Pool Google Sheet."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import gspread
from google.oauth2.service_account import Credentials


SHEET_ID = "1qlgU6Nbcj9gSmnI8RgFmrm3lqtK03kanHaxwGJrcIII"
SHEET_TAB = "results"

JSON_FILE = Path(__file__).resolve().parent.parent / "current.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

MIN_RACE_ROWS = 10
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


def notify(message, title="F1 Pool"):
    """Send a Pushover notification. Missing credentials are non-fatal."""

    user_key = os.environ.get("PUSHOVER_USER_KEY", "").strip()
    api_token = os.environ.get("PUSHOVER_API_TOKEN", "").strip()

    if not user_key or not api_token:
        logging.warning("Pushover credentials missing; notification skipped.")
        return

    data = urlencode({
        "token": api_token,
        "user": user_key,
        "title": title,
        "message": message,
    }).encode("utf-8")

    request = Request(PUSHOVER_URL, data=data)

    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

        if result.get("status") != 1:
            logging.warning("Pushover rejected notification: %s", result)

    except Exception as error:
        logging.warning("Pushover notification failed: %s", error)


def numeric_value(value):
    """Convert numeric fields to actual numbers."""

    if value is None or str(value).strip() == "":
        return ""

    try:
        number = float(value)
        return int(number) if number.is_integer() else number

    except (TypeError, ValueError):
        return ""


def valid_position(value):
    """Return a positive integer position, or None."""

    number = numeric_value(value)

    if isinstance(number, int) and number > 0:
        return number

    return None


def race_position(value):
    """Preserve numeric positions and non-numeric classifications such as NC."""

    position = valid_position(value)

    if position is not None:
        return position

    return str(value or "").strip()


def duration_value(value):
    """Convert F1 lap times into Google Sheets duration values.

    Supports 1:19.328 and 00:01:19.328.
    Google Sheets stores durations as fractions of a day.
    """

    if value is None or str(value).strip() == "":
        return ""

    value = str(value).strip()
    parts = value.split(":")

    try:
        if len(parts) == 2:
            minutes, seconds = parts
            hours = 0
        elif len(parts) == 3:
            hours, minutes, seconds = parts
        else:
            raise ValueError("Unexpected time format")

        if "." in seconds:
            whole_seconds, fraction = seconds.split(".", 1)
            fractional_seconds = float(f"0.{fraction}")
        else:
            whole_seconds = seconds
            fractional_seconds = 0

        hours = int(hours)
        minutes = int(minutes)
        whole_seconds = int(whole_seconds)

        if (
            hours < 0
            or not 0 <= minutes < 60
            or not 0 <= whole_seconds < 60
        ):
            raise ValueError("Time component out of range")

        total_seconds = (
            hours * 3600
            + minutes * 60
            + whole_seconds
            + fractional_seconds
        )

        return total_seconds / 86400

    except (ValueError, TypeError):
        logging.warning(
            "Unrecognized fastest-lap duration %r; writing blank.",
            value,
        )
        return ""


def load_payload():
    """Load and validate current.json."""

    if not JSON_FILE.is_file():
        raise FileNotFoundError(f"Missing JSON file: {JSON_FILE}")

    with JSON_FILE.open(encoding="utf-8") as file:
        payload = json.load(file)

    drivers = payload.get("drivers")

    if not isinstance(drivers, list) or not drivers:
        raise ValueError(
            "current.json has no driver records; refusing to update the sheet."
        )

    if not all(isinstance(driver, dict) for driver in drivers):
        raise ValueError("current.json contains invalid driver records.")

    return payload


def get_worksheet():
    """Authenticate and open the results worksheet."""

    raw_credentials = os.environ.get(
        "GOOGLE_SERVICE_ACCOUNT_JSON", ""
    ).strip()

    if not raw_credentials:
        raise RuntimeError(
            "Missing GOOGLE_SERVICE_ACCOUNT_JSON environment variable."
        )

    credentials_info = json.loads(raw_credentials)

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)

    return client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)


def normalize_rows(rows):
    """Normalize values for comparison and remove trailing empty rows."""

    normalized = [
        [
            str(value).strip() if value is not None else ""
            for value in row
        ]
        for row in rows
    ]

    while normalized and not any(normalized[-1]):
        normalized.pop()

    return normalized


def update_if_changed(worksheet, range_name, rows):
    """Only clear and rewrite a range when its contents actually change."""

    existing = worksheet.get_values(
        range_name,
        value_render_option="UNFORMATTED_VALUE",
    )

    if normalize_rows(existing) == normalize_rows(rows):
        return False

    worksheet.batch_clear([range_name])

    if rows:
        start_cell = range_name.split(":")[0]

        worksheet.update(
            values=rows,
            range_name=start_cell,
            value_input_option="RAW",
        )

    return True


def race_sort_key(driver):
    """Classified positions first; unclassified drivers in qualifying order."""

    position = valid_position(driver.get("pos"))
    qualifying = valid_position(driver.get("qualy"))

    return (
        0 if position is not None else 1,
        position if position is not None else 999,
        qualifying if qualifying is not None else 999,
    )


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    payload = load_payload()
    drivers = payload["drivers"]
    worksheet = get_worksheet()

    logging.info(
        "Processing %s, round %s (%d drivers).",
        payload.get("race_name", "Unknown race"),
        payload.get("round", "?"),
        len(drivers),
    )

    # GRID: official starting grid takes priority over qualifying order.
    official_grid = [
        (driver, valid_position(driver.get("grid")))
        for driver in drivers
    ]
    official_grid = [
        (driver, position)
        for driver, position in official_grid
        if position is not None
    ]

    qualifying_grid = [
        (driver, valid_position(driver.get("qualy")))
        for driver in drivers
    ]
    qualifying_grid = [
        (driver, position)
        for driver, position in qualifying_grid
        if position is not None
    ]

    if official_grid:
        selected_grid = official_grid
        grid_source = "Official starting grid"

    elif qualifying_grid:
        selected_grid = qualifying_grid
        grid_source = "Qualifying results"

    else:
        selected_grid = []
        grid_source = None

    if selected_grid:
        selected_grid.sort(key=lambda item: item[1])

        grid_rows = [
            [
                driver.get("driverName", ""),
                driver.get("constructorName", ""),
            ]
            for driver, _ in selected_grid
        ]

        grid_changed = update_if_changed(
            worksheet,
            "B2:C",
            grid_rows,
        )

        if grid_changed:
            logging.info(
                "Updated grid from %s (%d drivers).",
                grid_source,
                len(grid_rows),
            )

            notify(
                f"{grid_source} pushed to the spreadsheet.\n"
                f"{len(grid_rows)} drivers updated.",
                "F1 Pool — Grid Updated",
            )
        else:
            logging.info("Grid unchanged; no update needed.")

    else:
        logging.info(
            "No official grid or qualifying positions found; "
            "B:C left unchanged."
        )

    # RACE RESULTS: do not overwrite existing results until the official
    # classification is published and enough records are available.
    race_data_count = sum(
        1
        for driver in drivers
        if any(
            driver.get(field) is not None
            and str(driver.get(field)).strip()
            for field in ("race", "laps", "status")
        )
    )

    results_ready = payload.get("race_results_published") is True

    if not results_ready or race_data_count < MIN_RACE_ROWS:
        logging.info(
            "Race classification not ready "
            "(published=%s, %d/%d records); E:K left unchanged.",
            results_ready,
            race_data_count,
            MIN_RACE_ROWS,
        )
        return

    sorted_drivers = sorted(drivers, key=race_sort_key)

    # E:K — DRIVER, CONSTRUCTOR, GRID, POSITION, STATUS, LAPS, FASTEST LAP
    race_rows = [
        [
            driver.get("driverName", ""),
            driver.get("constructorName", ""),
            numeric_value(driver.get("grid")),
            race_position(driver.get("race")),
            driver.get("status", "") or "",
            numeric_value(driver.get("laps")),
            duration_value(driver.get("fastestLap")),
        ]
        for driver in sorted_drivers
    ]

    race_changed = update_if_changed(
        worksheet,
        "E2:K",
        race_rows,
    )

    if race_changed:
        worksheet.format(
            "G2:H",
            {"numberFormat": {"type": "NUMBER", "pattern": "0"}},
        )

        worksheet.format(
            "J2:J",
            {"numberFormat": {"type": "NUMBER", "pattern": "0"}},
        )

        worksheet.format(
            "K2:K",
            {"numberFormat": {"type": "TIME", "pattern": "[h]:mm:ss.000"}},
        )

        logging.info(
            "Updated race results (%d drivers; %d with race data).",
            len(race_rows),
            race_data_count,
        )

        notify(
            f"Race results pushed to the spreadsheet.\n"
            f"{race_data_count} drivers with race data.",
            "F1 Pool — Race Results Updated",
        )

    else:
        logging.info("Race results unchanged; no update needed.")


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        logging.exception("Google Sheets push failed.")

        notify(
            f"Google Sheets push failed:\n{str(error)[:900]}",
            "F1 Pool — ERROR",
        )

        raise
