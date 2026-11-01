#!/usr/bin/env python3
"""One-off script to backfill historical rounds 1-18 into season.json."""
from __future__ import annotations
import json, os, subprocess
from pathlib import Path
import requests

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
CURRENT_OUTPUT = REPO_ROOT / 'current.json'

def fetch_json(url):
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.json().get('MRData', {})
    except Exception:
        pass
    return {}

def main():
    # Fetch schedule to get race names for rounds 1-18
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])
    
    race_lookup = {str(r.get('round')): r for r in races}

    # Loop through completed rounds up to Round 18
    for round_no in range(1, 19):
        r_str = str(round_no)
        meta = race_lookup.get(r_str, {})
        race_name = meta.get('raceName', f'Round {round_no}')
        
        print(f"Backfilling Round {round_no}: {race_name}...")

        # Fetch qualifying and race results for this historical round
        qual_data = fetch_json(f'{JOLPICA}/{SEASON}/{r_str}/qualifying.json')
        qual_results = qual_data.get('RaceTable', {}).get('Races', [{}])[0].get('QualifyingResults', [])
        qual_map = {q.get('Driver', {}).get('driverId', ''): q for q in qual_results}

        race_data = fetch_json(f'{JOLPICA}/{SEASON}/{r_str}/results.json')
        race_results = race_data.get('RaceTable', {}).get('Races', [{}])[0].get('Results', [])

        drivers_data = []
        for res in race_results:
            d = res.get('Driver', {})
            c = res.get('Constructor', {})
            d_id = d.get('driverId', '')
            q_info = qual_map.get(d_id, {})

            drivers_data.append({
                'driverName': f"{d.get('givenName', '')} {d.get('familyName', '')}".strip(),
                'driverId': d_id,
                'code': d.get('code') or d_id[:3].upper(),
                'constructorId': c.get('constructorId', ''),
                'constructorName': c.get('name', ''),
                'no': res.get('number', ''),
                'grid': res.get('grid', ''),
                'qualy': q_info.get('position', ''),
                'q1': q_info.get('Q1', ''),
                'q2': q_info.get('Q2', ''),
                'q3': q_info.get('Q3', ''),
                'pos': res.get('position', ''),
                'race': res.get('position', ''),
                'laps': res.get('laps', ''),
                'pts': res.get('points', ''),
                'status': res.get('status', '')
            })

        payload = {
            'season': SEASON,
            'round': r_str,
            'race_name': race_name,
            'date': meta.get('date', ''),
            'time': meta.get('time', ''),
            'drivers': drivers_data
        }

        # Write to current.json temporarily
        CURRENT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

        # Run your season.py script to archive it into season.json
        subprocess.run(['python3', 'scripts/season.py'], check=True)

    print("Historical backfill complete! season.json now contains rounds 1-18.")

if __name__ == '__main__':
    main()
