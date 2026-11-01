#!/usr/bin/env python3
"""Fetch current or next F1 session data accurately by scanning the 2026 calendar dates."""
from __future__ import annotations
import json, os
from datetime import datetime, timezone, date
from pathlib import Path
import requests

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.getenv('F1_CURRENT_OUTPUT', str(REPO_ROOT / 'current.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"Fetching {SEASON} full season calendar from Jolpica...")
    try:
        url = f'{JOLPICA}/{SEASON}.json'
        r = session.get(url, timeout=TIMEOUT)
        data = r.json()
        races = data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
        
        if not races:
            print("Error: Could not retrieve races for the season.")
            return

        today = date.today()
        target_race = None

        # Find the first race whose date is today or in the future
        for race in races:
            race_date_str = race.get('date')
            if race_date_str:
                r_date = datetime.strptime(race_date_str, '%Y-%m-%d').date()
                if r_date >= today:
                    target_race = race
                    break
        
        # If all races for the year have passed, default to the final round
        if not target_race and races:
            target_race = races[-1]

        round_no = target_race.get('round')
        race_name = target_race.get('raceName')
        print(f"Targeting Round {round_no}: {race_name} (Date: {target_race.get('date')})")

        # Try fetching results for this round safely
        results_url = f'{JOLPICA}/{SEASON}/{round_no}/results.json'
        res_r = session.get(results_url, timeout=TIMEOUT)
        res_data = res_r.json()
        
        race_list = res_data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
        results = []
        if race_list:
            results = race_list[0].get('Results', [])

        formatted_results = []
        for res in results:
            driver = res.get('Driver', {})
            constructor = res.get('Constructor', {})
            formatted_results.append({
                'position': res.get('position'),
                'pos': res.get('position') if res.get('position') else '',
                'driverId': driver.get('driverId'),
                'code': driver.get('code'),
                'constructorId': constructor.get('constructorId'),
                'laps': res.get('laps'),
                'status': res.get('status'),
                'time': res.get('Time', {}).get('time', '')
            })

        payload = {
            'season': SEASON,
            'round': round_no,
            'race_name': race_name,
            'date': target_race.get('date'),
            'time': target_race.get('time'),
            'updated_at_utc': datetime.now(timezone.utc).isoformat(),
            'results': formatted_results
        }

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"Successfully updated {OUTPUT} for Round {round_no} ({len(formatted_results)} results recorded).")

    except Exception as e:
        print(f"Error updating current session data: {e}")
        raise

if __name__ == '__main__':
    main()
