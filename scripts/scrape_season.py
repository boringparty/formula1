#!/usr/init/env python3
"""Build comprehensive season.json with API sessions and F1.com circuit metrics."""
from __future__ import annotations
import argparse, json, logging, os, re, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.getenv('F1_OUTPUT_FILE', str(REPO_ROOT / 'season.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
F1_TRACK_BASE = 'https://www.formula1.com/en/racing'

TIMEOUT = 30
HEADERS = {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

TRACK_SLUGS = {
    'australia': 'australia', 'china': 'china', 'japan': 'japan', 'miami': 'miami',
    'canada': 'canada', 'monaco': 'monaco', 'barcelona-catalunya': 'barcelona-catalunya',
    'austria': 'austria', 'great-britain': 'great-britain', 'belgium': 'belgium',
    'hungary': 'hungary', 'netherlands': 'netherlands', 'italy': 'italy', 'spain': 'spain',
    'azerbaijan': 'azerbaijan', 'singapore': 'singapore', 'united-states': 'united-states',
    'mexico': 'mexico', 'brazil': 'sao-paulo', 'las-vegas': 'las-vegas', 'qatar': 'qatar', 'abu-dhabi': 'abu-dhabi'
}

def clean(s): return re.sub(r'\s+',' ', str(s or '')).strip()

def scrape_f1_circuit_stats(session, slug):
    """Scrapes circuit length, laps, fastest lap, and race distance from F1.com circuit page."""
    url = f'{F1_TRACK_BASE}/{SEASON}/{slug}'
    stats = {'circuit_length': None, 'first_gp': None, 'number_of_laps': None, 'fastest_lap_time': None, 'fastest_lap_driver': None, 'race_distance': None}
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return stats
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Locate all definition list items (dl/div structure provided)
        for div in soup.select('dl div'):
            dt = div.find('dt')
            dd = div.find('dd')
            span = div.find('span')
            if not dt or not dd:
                continue
            label = clean(dt.get_text())
            val = clean(dd.get_text())
            
            if label == 'Circuit Length':
                stats['circuit_length'] = val
            elif label == 'First Grand Prix':
                stats['first_gp'] = val
            elif label == 'Number of Laps':
                stats['number_of_laps'] = val
            elif label == 'Fastest lap time':
                stats['fastest_lap_time'] = val
                if span:
                    stats['fastest_lap_driver'] = clean(span.get_text())
            elif label == 'Race Distance':
                stats['race_distance'] = val
    except Exception as e:
        logging.warning('Could not scrape F1 stats for %s: %s', slug, e)
    return stats

def main():
    session = requests.Session(); session.headers.update(HEADERS)
    now_iso = datetime.now(timezone.utc).isoformat()
    
    # 1. Gather all session dates and times from Jolpica API
    print(f"Fetching season schedule from Jolpica for {SEASON}...")
    r = session.get(f'{JOLPICA}/{SEASON}.json', timeout=TIMEOUT)
    races = r.json()['MRData']['RaceTable']['Races']
    
    rounds_data = []
    for race in races:
        round_no = race.get('round')
        race_name = race.get('raceName')
        circuit = race.get('Circuit', {})
        circuit_id = circuit.get('circuitId', '')
        
        # Map circuit to F1.com slug format
        f1_slug = TRACK_SLUGS.get(circuit_id, circuit.get('circuitName', '').lower().replace(' ', '-'))
        
        print(f"Scraping detailed track metrics for Round {round_no}: {race_name}...")
        track_metrics = scrape_f1_circuit_stats(session, f1_slug)
        time.sleep(0.5) # Be polite to F1.com servers
        
        rounds_data.append({
            'round': round_no,
            'race_name': race_name,
            'date': race.get('date'),
            'time': race.get('time'),
            'sessions': {
                'first_practice': race.get('FirstPractice'),
                'second_practice': race.get('SecondPractice'),
                'third_practice': race.get('ThirdPractice'),
                'qualifying': race.get('Qualifying'),
                'sprint': race.get('Sprint'),
                'sprint_qualifying': race.get('SprintQualifying'),
            },
            'circuit': {
                'circuitId': circuit_id,
                'circuitName': circuit.get('circuitName'),
                'locality': circuit.get('Location', {}).get('locality'),
                'country': circuit.get('Location', {}).get('country'),
                **track_metrics
            }
        })

    season_payload = {
        'season': SEASON,
        'generated_at_utc': now_iso,
        'rounds': rounds_data
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(season_payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Successfully generated full season archive: {OUTPUT}")

if __name__ == '__main__':
    main()
