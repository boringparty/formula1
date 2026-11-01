#!/usr/bin/env python3
"""Archive historical snapshots into season.json organized by round number."""
from __future__ import annotations
import json, logging, os
from datetime import datetime, timezone
from pathlib import Path

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_OUTPUT = Path(os.getenv('F1_CURRENT_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
SEASON_OUTPUT = Path(os.getenv('F1_SEASON_OUTPUT_FILE', str(REPO_ROOT / 'season.json')))

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    if not CURRENT_OUTPUT.exists():
        logging.error("Source file %s does not exist. Skipping archive step.", CURRENT_OUTPUT)
        return

    try:
        current_data = json.loads(CURRENT_OUTPUT.read_text(encoding='utf-8'))
    except Exception as e:
        logging.error("Failed to read or parse %s: %s", CURRENT_OUTPUT, e)
        return

    round_no = str(current_data.get('round', '1'))
    
    # Load or initialize the season archive
    season_payload = {
        'season': SEASON,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'rounds': {}
    }

    if SEASON_OUTPUT.exists():
        try:
            season_payload = json.loads(SEASON_OUTPUT.read_text(encoding='utf-8'))
            if 'rounds' not in season_payload or not isinstance(season_payload['rounds'], dict):
                season_payload['rounds'] = {}
        except Exception as e:
            logging.warning("Could not parse existing %s, starting fresh: %s", SEASON_OUTPUT, e)

    # Archive/update the snapshot for the active round without wiping other rounds
    season_payload['rounds'][round_no] = current_data
    season_payload['updated_at_utc'] = datetime.now(timezone.utc).isoformat()

    SEASON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SEASON_OUTPUT.write_text(json.dumps(season_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info("Successfully archived Round %s snapshot into %s", round_no, SEASON_OUTPUT)

if __name__ == '__main__':
    main()
