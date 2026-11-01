#!/usr/bin/env python3
"""Maintain cumulative drivers.json directory across race weekends."""
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

def format_driver_image(url: str) -> str:
    """Applies required crop and quality transformations to F1 driver image URLs."""
    if not url:
        return ''
    # Transform c_lfill,w_64 -> c_lfill,w_64,h_64,g_north and q_auto -> q_100
    url = re.sub(r'c_lfill,w_\d+', 'c_lfill,w_64,h_64,g_north', url)
    url = re.sub(r'q_auto', 'q_100', url)
    return url

def load_existing_drivers() -> dict:
    """Loads existing drivers from drivers.json to ensure cumulative retention."""
    if OUTPUT.exists():
        try:
            data = json.loads(OUTPUT.read_text(encoding='utf-8'))
            return {d.get('code') or d.get('name'): d for d in data.get('drivers', [])}
        except Exception as e:
            print(f"Warning: Could not parse existing drivers.json: {e}")
    return {}

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    
    # Load cumulative directory
    existing_drivers = load_existing_drivers()
    
    # 1. Fetch active driver roster from Jolpica API
    try:
        r = session.get(f'{JOLPICA}/{SEASON}/drivers.json?limit=100', timeout=TIMEOUT)
        drivers_list = r.json().get('MRData', {}).get('DriverTable', {}).get('Drivers', [])
        
        for d in drivers_list:
            d_id = d.get('driverId', '')
            code = d.get('code') or d.get('familyName', '???')[:3].upper()
            full_name = f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()
            
            # Construct standard driver portrait URL pattern
            raw_img = f"https://media.formula1.com/image/upload/c_lfill,w_64/q_auto/d_common:f1:{SEASON}:fallback:driver:2026fallbackdriverright.webp/v1740000001/common/f1/{SEASON}/drivers/{code.lower()}.webp"
            formatted_img = format_driver_image(raw_img)
            
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
                # Update metadata if needed while preserving existing record
                existing_drivers[entry_key]['driverId'] = d_id
                existing_drivers[entry_key]['permanentNumber'] = d.get('permanentNumber')
                if not existing_drivers[entry_key].get('image_url'):
                    existing_drivers[entry_key]['image_url'] = formatted_img

    except Exception as e:
        print(f"Jolpica drivers directory sync warning: {e}")

    # Build output document
    payload = {
        'season': SEASON,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'drivers': list(existing_drivers.values())
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Successfully updated cumulative driver directory: {OUTPUT} ({len(existing_drivers)} drivers retained)")

if __name__ == '__main__':
    main()
