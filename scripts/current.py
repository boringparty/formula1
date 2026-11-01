#!/usr/bin/env python3
"""Build operational current.json for active race sessions into unified driver records."""
from __future__ import annotations
import json, os, re, logging
from datetime import datetime, timezone, date
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
    'alpine': 'Alpine', 'aston martin': 'Aston Martin', 'audi': 'Audi', 'cadillac': 'Cadillac',
    'ferrari': 'Ferrari', 'haas': 'Haas', 'haasf1team': 'Haas', 'mclaren': 'McLaren', 
    'mercedes': 'Mercedes', 'rb': 'RB', 'racingbulls': 'RB', 'red bull': 'Red Bull', 
    'redbullracing': 'Red Bull', 'williams': 'Williams', 'sauber': 'Sauber'
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
        if r.status_code == 200:
            return r.json().get('MRData', {})
    except Exception:
        pass
    return {}

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    session = requests.Session()
    session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    
    # 1. Fetch full season schedule to locate current or upcoming active round
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])
    
    if not races:
        logging.error("Could not fetch season schedule from Jolpica.")
        return

    today = date.today()
    active_meta = races[0]
    
    for r in races:
        r_date_str = r.get('date')
        if r_date_str:
            r_date = datetime.strptime(r_date_str, '%Y-%m-%d').date()
            if r_date >= today:
                active_meta = r
                break
    else:
        active_meta = races[-1]

    active_round = str(active_meta.get('round'))
    logging.info(f"Active Round targeting: Round {active_round} - {active_meta.get('raceName')}")

    # 2. Fetch Qualifying Results
    qual_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/qualifying.json')
    qual_races = qual_data.get('RaceTable', {}).get('Races', [])
    qual_results = qual_races[0].get('QualifyingResults', []) if qual_races else []

    qual_map = {}
    for q in qual_results:
        d_id = q.get('Driver', {}).get('driverId', '')
        qual_map[d_id] = {
            'qualy': q.get('position', ''),
            'q1': q.get('Q1', ''),
            'q2': q.get('Q2', ''),
            'q3': q.get('Q3', ''),
            'number': q.get('number', '')
        }

    # 3. Fetch Race Results
    race_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_races = race_data.get('RaceTable', {}).get('Races', [])
    race_results = race_races[0].get('Results', []) if race_races else []

    race_map = {}
    for res in race_results:
        d_id = res.get('Driver', {}).get('driverId', '')
        race_map[d_id] = {
            'grid': res.get('grid', ''),
            'pos': res.get('position', ''),
            'race': res.get('position', ''),
            'laps': res.get('laps', ''),
            'pts': res.get('points', '0'),
            'status': res.get('status', ''),
            'number': res.get('number', '')
        }

    # 4. Consolidate driver IDs across both qualifying and race entries
    all_driver_ids = set(qual_map.keys()).union(set(race_map.keys()))
    
    # Fallback driver/constructor extraction when building combined list
    driver_info_source = qual_results if qual_results else race_results
    driver_metadata = {}
    for item in driver_info_source:
        d = item.get('Driver', {})
        c = item.get('Constructor', {})
        d_id = d.get('driverId', '')
        driver_metadata[d_id] = {
            'driverName': get_std_name(d_id, f"{d.get('givenName', '')} {d.get('familyName', '')}"),
            'driverId': d_id,
            'code': d.get('code') or d_id[:3].upper(),
            'constructorId': c.get('constructorId', ''),
            'constructorName': get_std_team(c.get('name', ''))
        }

    combined_drivers = []
    for d_id in all_driver_ids:
        meta = driver_metadata.get(d_id, {
            'driverName': d_id,
            'driverId': d_id,
            'code': d_id[:3].upper(),
            'constructorId': '',
            'constructorName': ''
        })
        
        q_info = qual_map.get(d_id, {})
        r_info = race_map.get(d_id, {})

        combined_drivers.append({
            'driverName': meta['driverName'],
            'driverId': meta['driverId'],
            'code': meta['code'],
            'constructorId': meta['constructorId'],
            'constructorName': meta['constructorName'],
            'no': r_info.get('number') or q_info.get('number', ''),
            'grid': r_info.get('grid') or q_info.get('qualy', ''),
            'qualy': q_info.get('qualy', ''),
            'q1': q_info.get('q1', ''),
            'q2': q_info.get('q2', ''),
            'q3': q_info.get('q3', ''),
            'pos': r_info.get('pos', ''),
            'race': r_info.get('race', ''),
            'laps': r_info.get('laps', ''),
            'pts': r_info.get('pts', ''),
            'status': r_info.get('status', '')
        })

    # Sort array by final race position first, or starting grid/qualifying rank
    combined_drivers.sort(key=lambda x: int(x['pos']) if x['pos'].isdigit() else (int(x['grid']) if x['grid'].isdigit() else 99))

    payload = {
        'season': SEASON,
        'round': active_round,
        'race_name': active_meta.get('raceName'),
        'date': active_meta.get('date'),
        'time': active_meta.get('time'),
        'updated_at_utc': now.isoformat(),
        'drivers': combined_drivers
    }

    CURRENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info('Successfully updated %s with %d unified driver records.', CURRENT_OUTPUT, len(combined_drivers))

if __name__ == '__main__':
    main()
