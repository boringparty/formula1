#!/usr/bin/env python3
"""Build operational current.json for active race sessions."""
from __future__ import annotations
import json, logging, os, re
from datetime import datetime, timezone
from pathlib import Path
import requests

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_OUTPUT = Path(os.getenv('F1_CURRENT_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'

DRIVER_NAME_MAP = {
    'hamilton': 'Lewis Hamilton', 'russell': 'George Russell', 'leclerc': 'Charles Leclerc',
    'max_verstappen': 'Max Verstappen', 'antonelli': 'Kimi Antonelli', 'norris': 'Lando Norris',
    'hadjar': 'Isack Hadjar', 'arvid_lindblad': 'Arvid Lindblad', 'lawson': 'Liam Lawson',
    'gasly': 'Pierre Gasly', 'hulkenberg': 'Nico Hulkenberg', 'colapinto': 'Franco Colapinto',
    'piastri': 'Oscar Piastri', 'bortoleto': 'Gabriel Bortoleto', 'bearman': 'Oliver Bearman',
    'sainz': 'Carlos Sainz', 'albon': 'Alexander Albon', 'alonso': 'Fernando Alonso',
    'ryo_hirakawa': 'Ryo Hirakawa', 'stroll': 'Lance Stroll', 'perez': 'Sergio Perez', 'bottas': 'Valtteri Bottas'
}

TEAM_NAME_MAP = {
    'alpine': 'Alpine', 'aston martin': 'Aston Martin', 'audi': 'Sauber', 'cadillac': 'Cadillac',
    'ferrari': 'Ferrari', 'haas': 'Haas', 'mclaren': 'McLaren', 'mercedes': 'Mercedes',
    'rb': 'RB', 'red bull': 'Red Bull', 'williams': 'Williams', 'sauber': 'Sauber'
}

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0'}

def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()
def norm(s): return re.sub(r'[^a-z0-9 ]+', ' ', clean(s).lower()).strip()
def get_std_name(d_id, raw): return DRIVER_NAME_MAP.get(d_id, clean(raw))
def get_std_team(raw): return TEAM_NAME_MAP.get(norm(raw), clean(raw))

def fetch_json(url):
    try:
        r = requests.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get('MRData', {})
    except Exception as e:
        logging.warning('API fetch failed for %s: %s', url, e)
        return {}

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    session = requests.Session()
    session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    
    # 1. Fetch schedule to find active/latest round
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])
    
    active_round = '1'
    active_meta = races[0] if races else {}
    for r in races:
        active_round = str(r.get('round'))
        active_meta = r
        race_date_str = r.get('date', '2026-01-01')
        # Standard active window targeting logic
        if datetime.strptime(race_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc) >= now.replace(hour=0, minute=0, second=0):
            break

    logging.info('Targeting Round %s: %s', active_round, active_meta.get('raceName'))

    # 2. Check for post-race results
    race_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_results = race_data.get('RaceTable', {}).get('Races', [{}])[0].get('Results', [])
    
    # 3. Check for qualifying results
    qual_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/qualifying.json')
    qual_results = qual_data.get('RaceTable', {}).get('Races', [{}])[0].get('QualifyingResults', [])

    current_rows = []

    # Priority 1: Post-Race -> Populate pos and race
    if race_results:
        logging.info('Race results found. Populating final positions.')
        for res in race_results:
            d = res.get('Driver', {})
            c = res.get('Constructor', {})
            d_id = d.get('driverId', '')
            final_pos = res.get('position')
            
            q_match = next((q for q in qual_results if q.get('Driver', {}).get('driverId') == d_id), {})
            
            current_rows.append({
                'driver': get_std_name(d_id, f"{d.get('givenName', '')} {d.get('familyName', '')}"),
                'team': get_std_team(c.get('name', '')),
                'grid': res.get('grid'),
                'pos': final_pos,  # Classified position after race completion
                'race': final_pos,
                'no': res.get('number'),
                'qualy': q_match.get('position', ''),
                'laps': res.get('laps'),
                'pts': res.get('points'),
                'fastest_lap': res.get('FastestLap', {}).get('Time', {}).get('time'),
                'status': res.get('status'),
                'q1': q_match.get('Q1'),
                'q2': q_match.get('Q2'),
                'q3': q_match.get('Q3')
            })

    # Priority 2: Pre-Race / Post-Qualifying -> Keep pos blank ("")
    elif qual_results:
        logging.info('Qualifying results found. Setting pos to blank until race finishes.')
        for q in qual_results:
            d = q.get('Driver', {})
            c = q.get('Constructor', {})
            d_id = d.get('driverId', '')
            q_pos = q.get('position')
            
            current_rows.append({
                'driver': get_std_name(d_id, f"{d.get('givenName', '')} {d.get('familyName', '')}"),
                'team': get_std_team(c.get('name', '')),
                'grid': q_pos,
                'pos': "",  # Left blank until race completes
                'no': q.get('number'),
                'qualy': q_pos,
                'q1': q.get('Q1'),
                'q2': q.get('Q2'),
                'q3': q.get('Q3')
            })

    # Build payload intentionally omitting circuit details
    payload = {
        'generated_at_utc': now.isoformat(),
        'round': active_round,
        'race_name': active_meta.get('raceName'),
        'drivers': current_rows
    }

    CURRENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info('Successfully updated %s with %d drivers.', CURRENT_OUTPUT, len(current_rows))

if __name__ == '__main__':
    main()
