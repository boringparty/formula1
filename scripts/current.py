#!/usr/bin/env python3
"""Build operational current.json by strictly fetching the starting grid from formula1.com."""
from __future__ import annotations
import json, os, re, logging
from datetime import datetime, timezone, date
from pathlib import Path
import requests
from bs4 import BeautifulSoup

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
    'ferrari': 'Ferrari', 'haas': 'Haas', 'mclaren': 'McLaren', 'mercedes': 'Mercedes',
    'rb': 'RB', 'red bull': 'Red Bull', 'williams': 'Williams', 'sauber': 'Sauber'
}

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()
def norm(s): return re.sub(r'[^a-z0-9]+', '', clean(s).lower()).strip()
def get_std_name(d_id, raw): return DRIVER_NAME_MAP.get(d_id, clean(raw))
def get_std_team(raw): return TEAM_NAME_MAP.get(norm(raw), clean(raw))

def fetch_json(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json().get('MRData', {})
    except Exception as e:
        logging.warning("Error fetching %s: %s", url, e)
    return {}

def scrape_official_f1_grid(season: int, race_slug: str) -> dict[str, str]:
    """Strictly scrapes starting grid positions directly from formula1.com results table."""
    grid_map = {}
    
    slugs_to_try = [
        race_slug,
        race_slug.replace('-grand-prix', ''),
        race_slug.replace('grand-prix', '').strip('-')
    ]
    
    for slug in set(slugs_to_try):
        url = f"https://www.formula1.com/en/results/{season}/races/{slug}/starting-grid"
        logging.info("Scraping official starting grid from: %s", url)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, 'html.parser')
            table = soup.find('table')
            if not table:
                continue

            rows = table.find_all('tr')
            for row in rows:
                cols = [clean(c.text) for c in row.find_all(['td', 'th'])]
                if len(cols) < 3:
                    continue

                # F1 results table format: [Position, Car No, Driver Name/Code, Constructor, Time, ...]
                pos_candidate = cols[0]
                if not pos_candidate.isdigit():
                    continue

                grid_pos = pos_candidate
                row_text = norm(' '.join(cols))

                # Match row against known drivers using normalized IDs, numbers, or last names
                matched = False
                for d_id, std_name in DRIVER_NAME_MAP.items():
                    last_name = norm(std_name.split()[-1])
                    if last_name in row_text or norm(d_id) in row_text:
                        grid_map[d_id] = grid_pos
                        matched = True
                        break
                
                # Fallback: if name didn't match cleanly, check standard F1 three-letter code
                if not matched:
                    for d_id, std_name in DRIVER_NAME_MAP.items():
                        code = d_id[:3].lower()
                        if code in row_text:
                            grid_map[d_id] = grid_pos
                            break

            if len(grid_map) >= 15:
                logging.info("Successfully extracted %d driver grid positions from formula1.com", len(grid_map))
                break

        except Exception as e:
            logging.warning("Failed scraping %s: %s", url, e)

    return grid_map

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    session = requests.Session()
    session.headers.update(HEADERS)
    now = datetime.now(timezone.utc)
    
    # 1. Fetch schedule to locate active/latest round
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])
    
    if not races:
        logging.error("Could not fetch season schedule.")
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
    race_name = active_meta.get('raceName', '')
    logging.info(f"Targeting Round {active_round}: {race_name}")

    # 2. Strictly Scrape Official F1 Starting Grid
    slug_part = norm(race_name.replace('Grand Prix', '')).replace(' ', '-')
    official_grid_map = scrape_official_f1_grid(SEASON, slug_part)

    # 3. Fetch Qualifying Session Data (for Q1/Q2/Q3 times)
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

    # 4. Fetch Race Classification (for completed race data)
    race_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_races = race_data.get('RaceTable', {}).get('Races', [])
    race_results = race_races[0].get('Results', []) if race_races else []

    race_map = {}
    for res in race_results:
        d_id = res.get('Driver', {}).get('driverId', '')
        race_map[d_id] = {
            'pos': res.get('position', ''),
            'race': res.get('position', ''),
            'laps': res.get('laps', ''),
            'pts': res.get('points', ''),
            'status': res.get('status', ''),
            'number': res.get('number', '')
        }

    # 5. Build Unified Driver List
    all_driver_ids = set(qual_map.keys()).union(set(race_map.keys())).union(set(official_grid_map.keys()))
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
            'driverName': DRIVER_NAME_MAP.get(d_id, d_id),
            'driverId': d_id,
            'code': d_id[:3].upper(),
            'constructorId': '',
            'constructorName': ''
        })
        
        q_info = qual_map.get(d_id, {})
        r_info = race_map.get(d_id, {})

        # Grid MUST come strictly from formula1.com official grid map
        grid_pos = official_grid_map.get(d_id, '')

        combined_drivers.append({
            'driverName': meta['driverName'],
            'driverId': meta['driverId'],
            'code': meta['code'],
            'constructorId': meta['constructorId'],
            'constructorName': meta['constructorName'],
            'no': r_info.get('number') or q_info.get('number', ''),
            'grid': str(grid_pos),
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

    # Sort drivers strictly by official starting grid position (P1 to P22)
    combined_drivers.sort(
        key=lambda x: int(x['grid']) if x['grid'].isdigit() else (int(x['pos']) if x['pos'].isdigit() else 99)
    )

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
    logging.info('Successfully generated %s strictly from formula1.com grid data.', CURRENT_OUTPUT)

if __name__ == '__main__':
    main()
