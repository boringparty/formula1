#!/usr/bin/env python3
"""Build operational current.json by fetching the starting grid from formula1.com."""
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

# formula1.com race IDs for 2026: round 1 -> 1279 ... round 24 -> 1302
F1_ID_BASE = 1278

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
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.formula1.com/',
}


def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()
def norm(s): return re.sub(r'[^a-z0-9]+', '', clean(s).lower()).strip()
def get_std_name(d_id, raw): return DRIVER_NAME_MAP.get(d_id, clean(raw))
def get_std_team(raw):
    # TEAM_NAME_MAP keys contain spaces (e.g. 'aston martin'), so look up on the cleaned lowercase name
    key = clean(raw).lower()
    return TEAM_NAME_MAP.get(key, clean(raw))


def num_key(n) -> str:
    """Normalise a car number so '01', '1' and 1 all compare equal."""
    s = clean(n)
    return str(int(s)) if s.isdigit() else s


def fetch_json(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json().get('MRData', {})
        logging.warning("HTTP %s fetching %s", r.status_code, url)
    except Exception as e:
        logging.warning("Error fetching %s: %s", url, e)
    return {}


def scrape_official_f1_grid(season: int, round_no: int) -> dict[str, str]:
    """Returns {car_number: grid_position} scraped from the formula1.com starting grid page."""
    race_id = F1_ID_BASE + round_no
    url = f"https://www.formula1.com/en/results/{season}/races/{race_id}/_/starting-grid"
    logging.info("Scraping official starting grid from: %s", url)

    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        logging.error("Request failed: %s", e)
        return {}

    logging.info("HTTP %s, %d bytes, final URL: %s", r.status_code, len(r.text), r.url)
    if r.status_code != 200:
        logging.error("Non-200 response. 403 usually means bot protection is blocking the runner IP.")
        return {}

    soup = BeautifulSoup(r.text, 'html.parser')
    title = clean(soup.title.get_text()) if soup.title else ''
    logging.info("Page title: %s", title)

    table = soup.find('table')
    if not table:
        logging.error("No <table> found (page may be JS-rendered, a bot-block page, or the grid isn't published yet)")
        return {}

    # Detect column positions from the header row, falling back to [Pos, No, Driver, ...]
    pos_idx, no_idx = 0, 1
    header = table.find('tr')
    if header:
        heads = [norm(c.get_text(' ', strip=True)) for c in header.find_all(['th', 'td'])]
        for i, h in enumerate(heads):
            if h in ('pos', 'position'):
                pos_idx = i
            elif h in ('no', 'number', 'num'):
                no_idx = i
        logging.info("Header: %s (pos col=%d, no col=%d)", heads, pos_idx, no_idx)

    grid: dict[str, str] = {}
    for row in table.find_all('tr'):
        cells = [c.get_text(' ', strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) <= max(pos_idx, no_idx):
            continue
        pos, no = cells[pos_idx], cells[no_idx]
        if not pos.isdigit() or not no.isdigit():
            continue
        grid[num_key(no)] = pos

    logging.info("Extracted %d grid rows: %s", len(grid), grid)
    return grid


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    now = datetime.now(timezone.utc)

    # 1. Fetch schedule to locate active/latest round
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])

    if not races:
        logging.error("Could not fetch season schedule.")
        return

    today = date.today()
    active_meta = races[-1]
    for r in races:
        r_date_str = r.get('date')
        if r_date_str:
            r_date = datetime.strptime(r_date_str, '%Y-%m-%d').date()
            if r_date >= today:
                active_meta = r
                break

    active_round = str(active_meta.get('round'))
    race_name = active_meta.get('raceName', '')
    logging.info("Targeting Round %s: %s", active_round, race_name)

    # 2. Scrape official F1 starting grid (keyed by car number)
    official_grid_map = scrape_official_f1_grid(SEASON, int(active_round))
    if not official_grid_map:
        logging.warning("No grid data scraped; 'grid' fields will be empty.")

    # 3. Fetch qualifying session data (for Q1/Q2/Q3 times)
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

    # 4. Fetch race classification (for completed race data)
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

    # 5. Build unified driver list
    all_driver_ids = set(qual_map.keys()).union(set(race_map.keys()))
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

        # Grid comes strictly from the formula1.com grid map, looked up by car number
        no = r_info.get('number') or q_info.get('number', '')
        grid_pos = official_grid_map.get(num_key(no), '')

        combined_drivers.append({
            'driverName': meta['driverName'],
            'driverId': meta['driverId'],
            'code': meta['code'],
            'constructorId': meta['constructorId'],
            'constructorName': meta['constructorName'],
            'no': no,
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

    # Sort by official grid position, then race position, then unknowns last
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
    logging.info('Generated %s (%d drivers, %d with grid positions).',
                 CURRENT_OUTPUT, len(combined_drivers),
                 sum(1 for d in combined_drivers if d['grid']))


if __name__ == '__main__':
    main()
