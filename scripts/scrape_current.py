#!/usr/bin/env python3
"""Build current.json for active race sessions with primary Jolpica API fallback."""
from __future__ import annotations
import argparse, json, logging, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_OUTPUT = Path(os.getenv('F1_CURRENT_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
F1_BASE = f'https://www.formula1.com/en/results/{SEASON}/races'

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
    'alpine': 'Alpine', 'alpine f1 team': 'Alpine', 'aston_martin': 'Aston Martin',
    'aston martin': 'Aston Martin', 'audi': 'Sauber', 'cadillac': 'Cadillac',
    'cadillac f1 team': 'Cadillac', 'ferrari': 'Ferrari', 'haas': 'Haas',
    'haas f1 team': 'Haas', 'mclaren': 'McLaren', 'mercedes': 'Mercedes',
    'rb': 'RB', 'rb f1 team': 'RB', 'alphatauri': 'RB', 'visa cash app rb': 'RB',
    'red_bull': 'Red Bull', 'red bull': 'Red Bull', 'red bull racing': 'Red Bull',
    'williams': 'Williams', 'sauber': 'Sauber'
}

RACE_URLS = {
 'australia':(1279,'australia'),'china':(1280,'china'),'japan':(1281,'japan'),'miami':(1284,'miami'),
 'canada':(1285,'canada'),'monaco':(1286,'monaco'),'barcelona':(1287,'barcelona-catalunya'),'catalunya':(1287,'barcelona-catalunya'),
 'austria':(1288,'austria'),'great britain':(1289,'great-britain'),'british':(1289,'great-britain'),'belgium':(1290,'belgium'),
 'hungary':(1291,'hungary'),'netherlands':(1292,'netherlands'),'italy':(1293,'italy'),'spain':(1294,'spain'),
 'azerbaijan':(1295,'azerbaijan'),'singapore':(1296,'singapore'),'united states':(1297,'united-states'),'usa':(1297,'united-states'),
 'mexico':(1298,'mexico'),'brazil':(1299,'brazil'),'sao paulo':(1299,'las-vegas'),'las vegas':(1300,'las-vegas'),'qatar':(1301,'qatar'),'abu dhabi':(1302,'abu-dhabi')}

TIMEOUT = 30
HEADERS = {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36'}

def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
def norm(s): return re.sub(r'[^a-z0-9 ]+',' ',clean(s).lower()).strip()
def get_standardized_name(d_id, raw): return DRIVER_NAME_MAP.get(d_id, clean(raw))
def get_standardized_team(raw): return TEAM_NAME_MAP.get(norm(raw), clean(raw))

def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def fetch_json_safely(session, url):
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data.get('MRData', {})
    except Exception as e:
        logging.warning('API request failed for %s: %s', url, e)
        return {}

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    session = requests.Session(); session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    
    # 1. Fetch Jolpica season schedule
    sched_data = fetch_json_safely(session, f'{JOLPICA}/{SEASON}.json')
    races_list = sched_data.get('RaceTable', {}).get('Races', [])
    
    active_round = None
    active_race_meta = {}
    for r in races_list:
        active_round = str(r.get('round'))
        active_race_meta = r
        race_date_str = r.get('date', '2026-01-01')
        if datetime.strptime(race_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc) >= now - timedelta(days=3):
            break

    if not active_round:
        active_round = '1'
        active_race_meta = races_list[0] if races_list else {}

    logging.info('Targeting Round %s: %s', active_round, active_race_meta.get('raceName'))

    circuit = active_race_meta.get('Circuit', {})
    circuit_info = {
        'circuitId': circuit.get('circuitId'),
        'circuitName': circuit.get('circuitName'),
        'locality': circuit.get('Location', {}).get('locality'),
        'country': circuit.get('Location', {}).get('country')
    }

    # Check for completed Grand Prix Race results
    race_data = fetch_json_safely(session, f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_results = []
    try:
        race_results = race_data.get('RaceTable', {}).get('Races', [])[0].get('Results', [])
    except (IndexError, AttributeError):
        pass

    # Check for Qualifying results
    qual_data = fetch_json_safely(session, f'{JOLPICA}/{SEASON}/{active_round}/qualifying.json')
    qual_results = []
    try:
        qual_results = qual_data.get('RaceTable', {}).get('Races', [])[0].get('QualifyingResults', [])
    except (IndexError, AttributeError):
        pass

    current_rows = []

    # Priority 1: Race Results exist (Post-Race) -> Populate pos, race, grid, and qualy
    if race_results:
        logging.info('Loaded race results from Jolpica API for round %s', active_round)
        for res in race_results:
            d_info = res.get('Driver', {})
            c_info = res.get('Constructor', {})
            d_id = d_info.get('driverId', '')
            raw_dname = f"{d_info.get('givenName', '')} {d_info.get('familyName', '')}".strip()
            
            driver_name = get_standardized_name(d_id, raw_dname)
            team_name = get_standardized_team(c_info.get('name', ''))
            final_pos = res.get('position')
            
            # Find matching qualifying time if available
            q_match = next((q for q in qual_results if q.get('Driver', {}).get('driverId') == d_id), {})
            
            current_rows.append({
                'driver': driver_name,
                'team': team_name,
                'grid': res.get('grid'),
                'pos': final_pos,  # Populated post-race
                'race': final_pos,
                'no': res.get('number'),
                'qualy': q_match.get('position', ''),
                'laps': res.get('laps'),
                'pts': res.get('points'),
                'fastest_lap': res.get('FastestLap', {}).get('Time', {}).get('time'),
                'q1': q_match.get('Q1'),
                'q2': q_match.get('Q2'),
                'q3': q_match.get('Q3')
            })

    # Priority 2: Pre-Race / Post-Qualifying -> Leave pos blank ("")
    elif qual_results:
        logging.info('Loaded qualifying data (Pre-Race); setting pos to blank for round %s', active_round)
        for q in qual_results:
            d_info = q.get('Driver', {})
            c_info = q.get('Constructor', {})
            d_id = d_info.get('driverId', '')
            raw_dname = f"{d_info.get('givenName', '')} {d_info.get('familyName', '')}".strip()
            
            driver_name = get_standardized_name(d_id, raw_dname)
            team_name = get_standardized_team(c_info.get('name', ''))
            qual_pos = q.get('position')
            
            current_rows.append({
                'driver': driver_name,
                'team': team_name,
                'grid': qual_pos,  # Default starting grid before penalties
                'pos': "",        # Left blank until after the race
                'no': q.get('number'),
                'qualy': qual_pos,
                'q1': q.get('Q1'),
                'q2': q.get('Q2'),
                'q3': q.get('Q3')
            })

    payload = {
        'generated_at_utc': now.isoformat(),
        'round': active_round,
        'race_name': active_race_meta.get('raceName'),
        'circuit': circuit_info,
        'drivers': current_rows
    }
    
    CURRENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_json(CURRENT_OUTPUT, payload)
    logging.info('Successfully updated %s with %d driver entries.', CURRENT_OUTPUT, len(current_rows))

if __name__ == '__main__':
    main()
