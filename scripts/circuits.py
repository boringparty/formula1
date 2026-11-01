#!/usr/bin/env python3
"""Generate circuits.json by scraping F1 calendar and individual track stats."""
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
F1_BASE_URL = 'https://www.formula1.com'
F1_CALENDAR_URL = f'{F1_BASE_URL}/en/racing/{SEASON}'

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

DRIVER_CODE_MAP = {
    'max_verstappen': 'VER', 'hamilton': 'HAM', 'russell': 'RUS', 'leclerc': 'LEC',
    'norris': 'NOR', 'piastri': 'PIA', 'sainz': 'SAI', 'alonso': 'ALO', 'perez': 'PER',
    'stroll': 'STR', 'gasly': 'GAS', 'ocon': 'OCO', 'albon': 'ALB', 'hulkenberg': 'HUL',
    'bottas': 'BOT', 'tsunoda': 'TSU', 'ricciardo': 'RIC', 'bearman': 'BEA', 'colapinto': 'COL',
    'antonelli': 'ANT', 'bortoleto': 'BOR', 'hadjar': 'HAD', 'lawson': 'LAW'
}

def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()

def transform_track_image(url: str) -> str:
    """Replaces h_704 with h_200 while preserving the rest of the Cloudinary URL."""
    if not url:
        return ''
    return re.sub(r'h_\d+', 'h_200', url)

def get_previous_year_winners() -> dict:
    """Fetch previous season race winners mapped by normalized race name."""
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

def parse_circuit_page(session: requests.Session, page_url: str) -> dict:
    """Scrapes track statistics and track image directly from circuit page."""
    details = {
        'circuit_length': '',
        'first_grand_prix': '',
        'number_of_laps': '',
        'fastest_lap': {'time': '', 'holder': ''},
        'race_distance': '',
        'image_url': ''
    }
    try:
        r = session.get(page_url, timeout=TIMEOUT)
        if r.status_code != 200:
            return details

        soup = BeautifulSoup(r.text, 'html.parser')

        # 1. Scrape Track Image URL
        img_tag = soup.select_one('div img[src*="track"]')
        if img_tag and img_tag.get('src'):
            details['image_url'] = transform_track_image(img_tag['src'])

        # 2. Parse dl/dt/dd Track Details Grid
        dl = soup.select_one('dl')
        if dl:
            containers = dl.select('div')
            for container in containers:
                dt = container.select_one('dt')
                dd = container.select_one('dd')
                span = container.select_one('span')

                if not dt or not dd:
                    continue

                label = clean(dt.text).lower()
                val = clean(dd.text)
                extra = clean(span.text) if span else ''

                if 'length' in label:
                    details['circuit_length'] = val
                elif 'first' in label:
                    details['first_grand_prix'] = val
                elif 'laps' in label:
                    details['number_of_laps'] = val
                elif 'fastest' in label:
                    details['fastest_lap'] = {'time': val, 'holder': extra}
                elif 'distance' in label:
                    details['race_distance'] = val

    except Exception as e:
        print(f"Warning: Failed to parse circuit page {page_url}: {e}")

    return details

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    print(f"Fetching {SEASON} calendar baseline...")

    prev_winners = get_previous_year_winners()
    
    # Fetch schedule baseline from Jolpica API
    jol_sched = session.get(f'{JOLPICA}/{SEASON}.json', timeout=TIMEOUT).json()
    races = jol_sched.get('MRData', {}).get('RaceTable', {}).get('Races', [])

    circuits_data = []

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

        # Generate standard F1 circuit slug (e.g., /en/racing/2026/mexico)
        slug = norm_name.replace('grand prix', '').strip().replace(' ', '-')
        circuit_page_url = f"{F1_BASE_URL}/en/racing/{SEASON}/{slug}"
        
        print(f"Scraping Round {round_no}: {race_name} ({circuit_page_url})...")
        detailed_stats = parse_circuit_page(session, circuit_page_url)
        time.sleep(0.5)

        # Fallback image URL construction
        fallback_img = f"https://media.formula1.com/image/upload/c_fit,h_200/q_auto/v1740000001/common/f1/{SEASON}/track/{SEASON}track{c_id}detailed.webp"

        circuits_data.append({
            'round': round_no,
            'race_name': race_name,
            'circuitId': c_id,
            'circuitName': circuit.get('circuitName'),
            'locality': loc.get('locality'),
            'country': loc.get('country'),
            'previous_year_winner_code': winner_code,
            'circuit_length': detailed_stats['circuit_length'],
            'first_grand_prix': detailed_stats['first_grand_prix'],
            'number_of_laps': detailed_stats['number_of_laps'],
            'fastest_lap': detailed_stats['fastest_lap'],
            'race_distance': detailed_stats['race_distance'],
            'image_url': detailed_stats['image_url'] or transform_track_image(fallback_img)
        })

    payload = {
        'season': SEASON,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'circuits': circuits_data
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Successfully generated {OUTPUT} with {len(circuits_data)} circuits.")

if __name__ == '__main__':
    main()
