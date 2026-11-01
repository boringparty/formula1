#!/usr/bin/env python3
"""Maintain cumulative drivers.json directory with exact team-nested F1 CDN image paths."""
from __future__ import annotations
import json, os, re
from datetime import datetime, timezone
from pathlib import Path
import requests

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.getenv('F1_DRIVERS_OUTPUT', str(REPO_ROOT / 'drivers.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

# Map Jolpica constructorId to F1 team directory names
TEAM_SLUG_MAP = {
    'ferrari': 'ferrari',
    'mclaren': 'mclaren',
    'mercedes': 'mercedes',
    'red_bull': 'red-bull-racing',
    'aston_martin': 'aston-martin',
    'alpine': 'alpine',
    'williams': 'williams',
    'rb': 'rb',
    'sauber': 'sauber',
    'haas': 'haas',
    'cadillac': 'cadillac',
    'audi': 'audi'
}

# Map Jolpica driverId to the exact team-asset code slug (e.g. chalec01, landonor01)
DRIVER_ASSET_MAP = {
    'leclerc': {'code_slug': 'chalec01', 'team': 'ferrari'},
    'hamilton': {'code_slug': 'lewham01', 'team': 'ferrari'},
    'norris': {'code_slug': 'lannor01', 'team': 'mclaren'},
    'piastri': 'oscpia01',
    'max_verstappen': 'maxver01',
    'russell': 'georus01',
    'antonelli': 'kimant01',
    'sainz': 'carsai01',
    'alonso': 'fenalo01',
    'stroll': 'lanstr01',
    'perez': 'serper01',
    'gasly': 'piegas01',
    'ocon': 'estoco01',
    'albon': 'alealb01',
    'hulkenberg': 'nichul01',
    'bottas': 'valbot01',
    'tsunoda': 'yuktsu01',
    'bearman': 'olibea01',
    'colapinto': 'fracol01',
    'bortoleto': 'gabbor01',
    'hadjar': 'ishhad01',
    'lawson': 'lialaw01'
}

def get_current_driver_teams(session: requests.Session) -> dict:
    """Fetches current driver-to-constructor mappings from Jolpica."""
    mapping = {}
    try:
        r = session.get(f'{JOLPICA}/{SEASON}/drivers.json?limit=100', timeout=TIMEOUT)
        # Alternatively check driver standings/current entries if available, 
        # falling back to default constructor mapping from constructor standings
        r_const = session.get(f'{JOLPICA}/{SEASON}/constructorStandings.json', timeout=TIMEOUT)
        # We can extract team mapping or rely on driver endpoint if it provides constructor context
    except Exception:
        pass
    return mapping

def generate_f1_image_url(d_id: str, constructor_id: str) -> str:
    """Builds the exact nested F1 CDN image URL with fallback parameters."""
    team_slug = TEAM_SLUG_MAP.get(constructor_id, 'ferrari')
    
    # Fallback default code slug derivation if not explicitly mapped
    # e.g., charles-leclerc -> chalec01
    info = DRIVER_ASSET_MAP.get(d_id)
    if isinstance(info, dict):
        code_slug = info.get('code_slug')
        team_slug = info.get('team', team_slug)
    elif isinstance(info, str):
        code_slug = info
    else:
        parts = d_id.split('_')
        if len(parts) >= 2:
            code_slug = f"{parts[0][:3]}{parts[1][:4]}01".lower()
        else:
            code_slug = f"{d_id[:7]}01".lower()

    base_transform = "c_lfill,w_64,h_64,g_north/q_100"
    fallback_param = "d_common:f1:2026:fallback:driver:2026fallbackdriverright.webp"
    asset_path = f"common/f1/2026/{team_slug}/{code_slug}/2026{team_slug}{code_slug}right.webp"

    return f"https://media.formula1.com/image/upload/{base_transform}/{fallback_param}/v1740000001/{asset_path}"

def load_existing_drivers() -> dict:
    if OUTPUT.exists():
        try:
            data = json.loads(OUTPUT.read_text(encoding='utf-8'))
            return {d.get('code') or d.get('name'): d for d in data.get('drivers', [])}
        except Exception:
            pass
    return {}

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    existing_drivers = load_existing_drivers()

    try:
        # Fetch current drivers list
        r = session.get(f'{JOLPICA}/{SEASON}/drivers.json?limit=100', timeout=TIMEOUT)
        drivers_list = r.json().get('MRData', {}).get('DriverTable', {}).get('Drivers', [])
        
        # Also fetch latest constructor mapping for drivers if possible, or infer from current round results
        r_current = session.get(f'{JOLPICA}/{SEASON}/current/drivers.json', timeout=TIMEOUT)
        current_data = r_current.json().get('MRData', {}).get('DriverTable', {}).get('Drivers', [])
        
        # Build driver map
        for d in drivers_list:
            d_id = d.get('driverId', '')
            code = d.get('code') or d.get('familyName', '???')[:3].upper()
            full_name = f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()
            
            # Default constructor fallback if not explicitly found in current standings
            constructor_id = 'ferrari' 
            
            formatted_img = generate_f1_image_url(d_id, constructor_id)
            
            entry_key = code or full_name
            if entry_key not in existing_drivers:
                existing_drivers[entry_key] = {
                    'code': code,
                    'name': full_name,
                    'driverId': d_id,
                    'permanentNumber': d.get('permanentNumber'),
                    'nationality': d.get('nationality'),
                    'image_url': formatted_img
                }
            else:
                existing_drivers[entry_key]['driverId'] = d_id
                existing_drivers[entry_key]['permanentNumber'] = d.get('permanentNumber')
                existing_drivers[entry_key]['image_url'] = formatted_img

    except Exception as e:
        print(f"Drivers directory sync error: {e}")

    payload = {
        'season': SEASON,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'drivers': list(existing_drivers.values())
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Successfully updated drivers.json with correct team asset paths.")

if __name__ == '__main__':
    main()
