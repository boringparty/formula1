#!/usr/bin/env python3
"""Push current.json data into the F1 Pool Google Sheet."""

import json
import logging
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1qlgU6Nbcj9gSmnI8RgFmrm3lqtK03kanHaxwGJrcIII"
SHEET_TAB = "results"
JSON_FILE = Path(__file__).resolve().parent.parent / "current.json"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Load and validate the source data before touching the spreadsheet.
    if not JSON_FILE.exists():
        raise FileNotFoundError(f"Missing data file: {JSON_FILE}")

    with JSON_FILE.open(encoding="utf-8") as file:
        payload = json.load(file)

    drivers = payload.get("drivers", [])

    if not drivers:
        raise ValueError("current.json contains no drivers; refusing to update.")

    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        raise RuntimeError("Missing GOOGLE_SERVICE_ACCOUNT_JSON GitHub secret.")

    credentials = Credentials.from_service_account_info(
        json.loads(service_account_json),
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)
    worksheet = client.open_by_key(SHEET_ID).worksheet(SHEET_TAB)

    # Starting grid: sort by grid position and write only the two requested fields.
    grid_drivers = sorted(
        [d for d in drivers if str(d.get("grid", "")).isdigit()],
        key=lambda d: int(d["grid"]),
    )

    grid_rows = [
        [d.get("driverName", ""), d.get("constructorName", "")]
        for d in grid_drivers
    ]

    if grid_rows:
        worksheet.batch_clear(["B2:C"])
        worksheet.update(
            values=grid_rows,
            range_name="B2",
            value_input_option="RAW",
        )
        logging.info("Updated starting grid: %d drivers.", len(grid_rows))
    else:
        logging.info("No starting-grid data yet; left B:C unchanged.")

    # Race classification: don't clear existing results until race data exists.
    has_race_data = any(
        d.get("race") or d.get("laps") or d.get("status")
        for d in drivers
    )

    if has_race_data:
        race_rows = [
            [
                d.get("driverName", ""),
                d.get("constructorName", ""),
                d.get("grid", ""),
                d.get("race", ""),
                d.get("status", ""),
                d.get("laps", ""),
                d.get("fastestLap", ""),
            ]
            for d in drivers
        ]

        worksheet.batch_clear(["E2:K"])
        worksheet.update(
            values=race_rows,
            range_name="E2",
            value_input_option="RAW",
        )
        logging.info("Updated race results: %d drivers.", len(race_rows))
    else:
        logging.info("No race classification yet; left E:K unchanged.")


if __name__ == "__main__":
    main()
