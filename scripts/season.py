#!/usr/bin/env python3
"""Archive completed race weekend snapshots into cumulative season.json."""
from __future__ import annotations
import json, os, logging
from datetime import datetime, timezone
from pathlib import Path

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_INPUT = Path(os.getenv('F1_CURRENT_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
SEASON_OUTPUT = Path(os.getenv('F1_SEASON_OUTPUT_FILE', str(REPO_ROOT / 'season.json')))

def load_season_data() -> dict:
    if SEASON_OUTPUT.exists():
        try:
            return json.loads(SEASON_OUTPUT.read_text(encoding='utf-8'))
        except Exception as e:
            logging.warning("Could not parse existing season.json: %s", e)
    return {
        'season': SEASON,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'rounds': {}
    }

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    if not CURRENT_INPUT.exists():
        logging.error("Source file %s does not exist. Skipping archive step.", CURRENT_INPUT)
        return

    try:
        current_data = json.loads(CURRENT_INPUT.read_text(encoding='utf-8'))
    except Exception as e:
        logging.error("Failed to read %s: %s", CURRENT_INPUT, e)
        return

    round_no = str(current_data.get('round', ''))
    drivers = current_data.get('drivers', [])

    if not round_no:
        logging.error("No valid round found in %s", CURRENT_INPUT)
        return

    # Skip archiving if race results haven't been completed yet (e.g. pos is empty)
    has_finished_race = any(bool(d.get('pos') or d.get('race')) for d in drivers)
    if not has_finished_race:
        logging.info("Round %s does not contain finished race results yet. Skipping archive.", round_no)
        return

    season_payload = load_season_data()
    if 'rounds' not in season_payload or not isinstance(season_payload['rounds'], dict):
        season_payload['rounds'] = {}

    season_payload['rounds'][round_no] = {
        'round': round_no,
        'race_name': current_data.get('race_name', ''),
        'date': current_data.get('date', ''),
        'time': current_data.get('time', ''),
        'archived_at_utc': datetime.now(timezone.utc).isoformat(),
        'drivers': drivers
    }

    season_payload['updated_at_utc'] = datetime.now(timezone.utc).isoformat()

    SEASON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SEASON_OUTPUT.write_text(json.dumps(season_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info("Successfully archived Round %s into %s", round_no, SEASON_OUTPUT)

if __name__ == '__main__':
    main()
