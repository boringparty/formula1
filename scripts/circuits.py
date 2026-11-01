#!/usr/bin/env python3
"""Generate circuits.json once per season with calendar metadata and previous year winners."""
from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

SEASON = int(os.getenv('F1_SEASON', '2026'))
PREV_SEASON = SEASON - 1
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.getenv('F1_CIRCUITS_OUTPUT', str(REPO_ROOT / 'circuits.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
F1_CALENDAR_URL = f'https://www.formula1.com/en/racing/{SEASON}'

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

# Map F1 driver IDs or family names to 3-letter codes
DRIVER_CODE_MAP = {
    'max_verstappen': 'VER', 'hamilton': 'HAM', 'russell': 'RUS', 'leclerc': 'LEC',
    'norris': 'NOR', 'piastri': 'PIA', 'sainz': 'SAI', 'alonso': 'ALO', 'perez': 'PER',
    'stroll': 'STR', 'gasly': 'GAS', 'ocon': 'OCO', 'albon': 'ALB', 'hulkenberg': 'HUL',
    'bottas': 'BOT', 'tsunoda': 'TSU', 'ricciardo': 'RIC', 'bearman': 'BEA', 'colapinto': 'COL',
    'antonelli': 'ANT', 'bortoleto': 'BOR', 'hadjar': 'HAD', 'lawson': 'LAW'
}

def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()

def get_previous_year_winners():
    """Fetch previous season race winners mapped by normalized race name/circuit."""
    winners = {}
    try:
        url = f'{JOLPICA}/{PREV_SEASON}/results/1.json?limit=100'
        r = requests.get(url, timeout=TIMEOUT)
        data = r.json()
        races = data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
        for race in races:
            rname = clean(race.get('raceName', '')).lower()
            results = race.get('Results', [])
            if results:
                driver = results[0].get('Driver', {})
                d_id = driver.get('driverId', '')
                code = driver.get('code') or DRIVER_CODE_MAP.get(d_id, driver.get('familyName', '???')[:3].upper())
                winners[rname] = code
    except Exception as e:
        print(f"Warning: Could not fetch previous year winners: {e}")
    return winners

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    print(f"Fetching 2026 calendar from {F1_CALENDAR_URL}...")
    
    r = session.get(F1_CALENDAR_URL, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    
    prev_winners = get_previous_year_winners()
    circuits_data = []

    # Parse F1 calendar container cards
    # F1 calendar listing cards typically contain track images and metadata tags
    cards = soup.select('a[href*="/racing/"]') # Adjust selector safely based on structure
    
    # Fallback/Direct Jolpica schedule bootstrap if HTML structure fluctuates
    jol_sched = session.get(f'{JOLPICA}/{SEASON}.json', timeout=TIMEOUT).json()
    races = jol_sched.get('MRData', {}).get('RaceTable', {}).get('Races', [])

    for race in races:
        round_no = race.get('round')
        race_name = race.get('raceName')
        circuit = race.get('Circuit', {})
        c_id = circuit.get('circuitId')
        loc = circuit.get('Location', {})
        
        # Match previous year winner
        norm_name = clean(race_name).lower()
        winner_code = None
        for k, v in prev_winners.items():
            if any(term in norm_name for term in k.split()):
                winner_code = v
                break

        circuits_data.append({
            'round': round_no,
            'race_name': race_name,
            'circuitId': c_id,
            'circuitName': circuit.get('circuitName'),
            'locality': loc.get('locality'),
            'country': loc.get('country'),
            'previous_year_winner_code': winner_code,
            'image_url': f"https://media.formula1.com/image/upload/c_fit,h_200/q_auto/v1740000001/common/f1/{SEASON}/track/{SEASON}track{c_id}detailed.webp"
        })

    payload = {
        'season': SEASON,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'circuits': circuits_data
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Successfully generated {OUTPUT}")

if __name__ == '__main__':
    main()
