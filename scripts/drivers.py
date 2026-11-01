#!/usr/bin/env python3
"""Maintain cumulative drivers.json directory with full F1 CDN team-nested fallback paths."""
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

# Complete 2026 map linking Jolpica driverId to the exact team directory and asset code slug
DRIVER_ASSET_MAP = {
    'leclerc': {'team': 'ferrari', 'slug': 'chalec01'},
    'hamilton': {'team': 'ferrari', 'slug': 'lewham01'},
    'max_verstappen': {'team': 'redbullracing', 'slug': 'maxver01'},
    'hadjar': {'team': 'redbullracing', 'slug': 'isahad01'},
    'arvid_lindblad': {'team': 'racingbulls', 'slug': 'arvlin01'},
    'piastri': {'team': 'mclaren', 'slug': 'oscpia01'},
    'norris': {'team': 'mclaren', 'slug': 'lannor01'},
    'russell': {'team': 'mercedes', 'slug': 'georus01'},
    'antonelli': {'team': 'mercedes', 'slug': 'andant01'},
    'bortoleto': {'team': 'audi', 'slug': 'gabbor01'},
    'hulkenberg': {'team': 'audi', 'slug': 'nichul01'},
    'ocon': {'team': 'haasf1team', 'slug': 'estoco01'},
    'bearman': {'team': 'haasf1team', 'slug': 'olibea01'},
    'sainz': {'team': 'williams', 'slug': 'carsai01'},
    'albon': {'team': 'williams', 'slug': 'alealb01'},
    'lawson': {'team': 'racingbulls', 'slug': 'lialaw01'},
    'colapinto': {'team': 'alpine', 'slug': 'fracol01'},
    'gasly': {'team': 'alpine', 'slug': 'piegas01'},
    'bottas': {'team': 'cadillac', 'slug': 'valbot01'},
    'perez': {'team': 'cadillac', 'slug': 'serper01'},
    'stroll': {'team': 'astonmartin', 'slug': 'lanstr01'},
    'alonso': {'team': 'astonmartin', 'slug': 'fenalo01'}
}

def generate_f1_image_url(d_id: str) -> str:
    """Constructs the full nested F1 CDN image URL with fallback parameters."""
    info = DRIVER_ASSET_MAP.get(d_id, {'team': 'ferrari', 'slug': f"{d_id[:7]}01"})
    team = info['team']
    slug = info['slug']

    transforms = "c_lfill,w_64,h_64,g_north/q_100"
    fallback = "d_common:f1:2026:fallback:driver:2026fallbackdriverright.webp"
    asset_path = f"common/f1/2026/{team}/{slug}/2026{team}{slug}right.webp"

    return f"https://media.formula1.com/image/upload/{transforms}/{fallback}/v1740000001/{asset_path}"

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
        r = session.get(f'{JOLPICA}/{SEASON}/drivers.json?limit=100', timeout=TIMEOUT)
        drivers_list = r.json().get('MRData', {}).get('DriverTable', {}).get('Drivers', [])
        
        for d in drivers_list:
            d_id = d.get('driverId', '')
            code = d.get('code') or d.get('familyName', '???')[:3].upper()
            full_name = f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()
            
            formatted_img = generate_f1_image_url(d_id)
            
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
    print(f"Successfully updated drivers.json with team-nested image paths.")

if __name__ == '__main__':
    main()
