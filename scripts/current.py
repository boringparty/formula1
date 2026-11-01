#!/usr/bin/env python3
"""Fetch current F1 session details, results, grid positions, qualifying, and standings from Jolpica."""
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

def safe_get(session: requests.Session, url: str) -> dict:
    try:
        r = session.get(url, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"Warning: Failed to fetch {url}: {e}")
    return {}

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    
    print(f"Fetching {SEASON} schedule from Jolpica...")
    sched_data = safe_get(session, f'{JOLPICA}/{SEASON}.json')
    races = sched_data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
    
    if not races:
        print("Error: Could not retrieve schedule.")
        return

    today = date.today()
    target_race = None

    # Locate current or upcoming race round
    for race in races:
        race_date_str = race.get('date')
        if race_date_str:
            r_date = datetime.strptime(race_date_str, '%Y-%m-%d').date()
            if r_date >= today:
                target_race = race
                break
    
    if not target_race:
        target_race = races[-1]

    round_no = target_race.get('round')
    race_name = target_race.get('raceName')
    circuit = target_race.get('Circuit', {})
    
    print(f"Targeting Round {round_no}: {race_name}")

    # 1. Fetch Official Race Results & Grid Positions
    res_data = safe_get(session, f'{JOLPICA}/{SEASON}/{round_no}/results.json')
    res_races = res_data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
    results_list = res_races[0].get('Results', []) if res_races else []

    formatted_results = []
    starting_grid = []

    for res in results_list:
        driver = res.get('Driver', {})
        constructor = res.get('Constructor', {})
        
        position = res.get('position')
        grid_pos = res.get('grid')
        
        formatted_results.append({
            'position': position,
            'grid': grid_pos,
            'driverId': driver.get('driverId'),
            'code': driver.get('code'),
            'driverName': f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
            'constructorId': constructor.get('constructorId'),
            'constructorName': constructor.get('name'),
            'laps': res.get('laps'),
            'status': res.get('status'),
            'time': res.get('Time', {}).get('time', ''),
            'points': res.get('points', '0')
        })

    # 2. Fetch Qualifying Classification (Fallback for starting grid if race is pending)
    qual_data = safe_get(session, f'{JOLPICA}/{SEASON}/{round_no}/qualifying.json')
    qual_races = qual_data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
    qual_list = qual_races[0].get('QualifyingResults', []) if qual_races else []

    formatted_qualifying = []
    for q in qual_list:
        driver = q.get('Driver', {})
        constructor = q.get('Constructor', {})
        q_pos = q.get('position')

        formatted_qualifying.append({
            'position': q_pos,
            'driverId': driver.get('driverId'),
            'code': driver.get('code'),
            'driverName': f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
            'constructorId': constructor.get('constructorId'),
            'constructorName': constructor.get('name'),
            'q1': q.get('Q1', ''),
            'q2': q.get('Q2', ''),
            'q3': q.get('Q3', '')
        })

    # Construct explicit starting grid list
    if formatted_results:
        # Sort by starting grid position
        grid_sorted = sorted([r for r in formatted_results if r.get('grid')], key=lambda x: int(x['grid']))
        starting_grid = [
            {
                'grid_position': item['grid'],
                'driverId': item['driverId'],
                'code': item['code'],
                'driverName': item['driverName'],
                'constructorId': item['constructorId'],
                'constructorName': item['constructorName']
            } for item in grid_sorted
        ]
    elif formatted_qualifying:
        # Fallback to qualifying results if race grid hasn't set officially
        starting_grid = [
            {
                'grid_position': item['position'],
                'driverId': item['driverId'],
                'code': item['code'],
                'driverName': item['driverName'],
                'constructorId': item['constructorId'],
                'constructorName': item['constructorName']
            } for item in formatted_qualifying
        ]

    # 3. Fetch Driver Standings
    d_stand_data = safe_get(session, f'{JOLPICA}/{SEASON}/driverstandings.json')
    d_lists = d_stand_data.get('MRData', {}).get('StandingsTable', {}).get('StandingsLists', [])
    driver_standings = []
    if d_lists:
        for ds in d_lists[0].get('DriverStandings', []):
            driver = ds.get('Driver', {})
            constructors = ds.get('Constructors', [{}])
            driver_standings.append({
                'position': ds.get('position'),
                'driverId': driver.get('driverId'),
                'code': driver.get('code'),
                'driverName': f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                'points': ds.get('points'),
                'wins': ds.get('wins'),
                'constructorId': constructors[0].get('constructorId') if constructors else '',
                'constructorName': constructors[0].get('name') if constructors else ''
            })

    # 4. Fetch Constructor Standings
    c_stand_data = safe_get(session, f'{JOLPICA}/{SEASON}/constructorstandings.json')
    c_lists = c_stand_data.get('MRData', {}).get('StandingsTable', {}).get('StandingsLists', [])
    constructor_standings = []
    if c_lists:
        for cs in c_lists[0].get('ConstructorStandings', []):
            constructor = cs.get('Constructor', {})
            constructor_standings.append({
                'position': cs.get('position'),
                'constructorId': constructor.get('constructorId'),
                'constructorName': constructor.get('name'),
                'points': cs.get('points'),
                'wins': cs.get('wins')
            })

    payload = {
        'season': SEASON,
        'round': round_no,
        'race_name': race_name,
        'circuit_id': circuit.get('circuitId'),
        'circuit_name': circuit.get('circuitName'),
        'date': target_race.get('date'),
        'time': target_race.get('time'),
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'starting_grid': starting_grid,
        'race_results': formatted_results,
        'qualifying_results': formatted_qualifying,
        'driver_standings': driver_standings,
        'constructor_standings': constructor_standings
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Successfully generated {OUTPUT} for Round {round_no} with starting grid data.")

if __name__ == '__main__':
    main()
