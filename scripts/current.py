#!/usr/init/env python3
"""Build operational current.json for active race sessions."""
from __future__ import annotations
import json, os, re, logging
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
        return r.json().get('MRData', {})
    except Exception:
        return {}

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    session = requests.Session()
    session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    
    # Fetch schedule to locate active/latest round
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])
    
    active_round = '1'
    active_meta = races[0] if races else {}
    for r in races:
        active_round = str(r.get('round'))
        active_meta = r
        # Basic date evaluation logic for active round can be fine-tuned here

    # Check for race results vs qualifying results
    race_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_results = race_data.get('RaceTable', {}).get('Races', [{}])[0].get('Results', [])
    
    qual_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/qualifying.json')
    qual_results = qual_data.get('RaceTable', {}).get('Races', [{}])[0].get('QualifyingResults', [])

    current_rows = []
    
    if race_results:
        for res in race_results:
            d = res.get('Driver', {})
            c = res.get('Constructor', {})
            d_id = d.get('driverId', '')
            final_pos = res.get('position')
            current_rows.append({
                'driver': get_std_name(d_id, f"{d.get('givenName')} {d.get('familyName')}"),
                'team': get_std_team(c.get('name', '')),
                'grid': res.get('grid'),
                'pos': final_pos,  # Populated post-race
                'race': final_pos,
                'no': res.get('number'),
                'laps': res.get('laps'),
                'pts': res.get('points'),
                'status': res.get('status')
            })
    elif qual_results:
        for q in qual_results:
            d = q.get('Driver', {})
            c = q.get('Constructor', {})
            d_id = d.get('driverId', '')
            q_pos = q.get('position')
            current_rows.append({
                'driver': get_std_name(d_id, f"{d.get('givenName')} {d.get('familyName')}"),
                'team': get_std_team(c.get('name', '')),
                'grid': q_pos,
                'pos': "",  # Left blank until after the race
                'no': q.get('number'),
                'qualy': q_pos,
                'q1': q.get('Q1'),
                'q2': q.get('Q2'),
                'q3': q.get('Q3')
            })

    payload = {
        'generated_at_utc': now.isoformat(),
        'round': active_round,
        'race_name': active_meta.get('raceName'),
        'drivers': current_rows
    }

    CURRENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info('Successfully updated %s', CURRENT_OUTPUT)

if __name__ == '__main__':
    main()
