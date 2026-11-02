#!/usr/bin/env python3
"""Generate circuits.json by scraping the F1 calendar index and individual track stat pages, including normalized session structures."""
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

STREET_CIRCUITS = {
    'albert_park', 'miami', 'villeneuve', 'monaco', 'baku', 'singapore', 'vegas', 'madring'
}

def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()

def transform_track_image(url: str) -> str:
    """Replaces height specs with h_200 while preserving URL structure."""
    if not url:
        return ''
    return re.sub(r'h_\d+', 'h_200', url)

def to_bold_sans(text: str) -> str:
    """Converts normal text to Bold Sans-Serif Unicode characters."""
    out = []
    for c in text:
        code = ord(c)
        if ord('A') <= code <= ord('Z'):
            out.append(chr(0x1D5A0 + (code - ord('A'))))
        elif ord('a') <= code <= ord('z'):
            out.append(chr(0x1D5BA + (code - ord('a'))))
        elif ord('0') <= code <= ord('9'):
            out.append(chr(0x1D7EC + (code - ord('0'))))
        else:
            out.append(c)
    return ''.join(out)

def fmt_session(sess: dict) -> str:
    """Formats session date and time to MM-DD HH:MM (UTC)."""
    if not isinstance(sess, dict):
        return ''
    d = sess.get('date')
    t = sess.get('time')
    if not d or not t:
        return ''
    return f"{d[5:7]}-{d[8:10]} {t[:5]}"

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

def get_circuit_urls_from_calendar(session: requests.Session) -> dict:
    """Scrapes the main F1 calendar page to map race slugs to specific URLs."""
    mapping = {}
    try:
        r = session.get(F1_CALENDAR_URL, timeout=TIMEOUT)
        if r.status_code != 200:
            return mapping
        
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.select('a.group'):
            href = a.get('href', '')
            if f'/en/racing/{SEASON}/' in href and 'testing' not in href:
                parts = [p for p in href.split('/') if p]
                if parts:
                    slug = parts[-1].lower()
                    full_url = href if href.startswith('http') else f"{F1_BASE_URL}{href}"
                    mapping[slug] = full_url
    except Exception as e:
        print(f"Warning: Could not extract subpage links via a.group: {e}")
    return mapping

def parse_circuit_page(session: requests.Session, page_url: str) -> dict:
    """Scrapes track statistics, fastest lap info, and track image from the circuit page."""
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

        img_tag = soup.select_one('div img[src*="track"]')
        if img_tag and img_tag.get('src'):
            details['image_url'] = transform_track_image(img_tag['src'])

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
    print(f"Fetching {SEASON} calendar index and subpage links...")

    prev_winners = get_previous_year_winners()
    calendar_urls = get_circuit_urls_from_calendar(session)
    
    fallback_mapping = {
        'albert_park': 'australia', 'shanghai': 'china', 'suzuka': 'japan',
        'miami': 'miami', 'villeneuve': 'canada', 'monaco': 'monaco',
        'catalunya': 'barcelona-catalunya', 'red_bull_ring': 'austria',
        'silverstone': 'great-britain', 'spa': 'belgium', 'hungaroring': 'hungary',
        'zandvoort': 'netherlands', 'monza': 'italy', 'madring': 'spain',
        'baku': 'azerbaijan', 'sepang': 'bahrain', 'marina_bay': 'singapore',
        'americas': 'united-states', 'rodriguez': 'mexico', 'interlagos': 'brazil',
        'vegas': 'las-vegas', 'losail': 'qatar', 'yas_marina': 'united-arab-emirates'
    }

    jol_sched = session.get(f'{JOLPICA}/{SEASON}.json', timeout=TIMEOUT).json()
    races = jol_sched.get('MRData', {}).get('RaceTable', {}).get('Races', [])

    circuits_data = []

    for race in races:
        round_no = race.get('round')
        round_str = f"{int(round_no):02d}" if str(round_no).isdigit() else str(round_no)
        race_name = race.get('raceName')
        circuit = race.get('Circuit', {})
        c_id = circuit.get('circuitId', '').lower()
        loc = circuit.get('Location', {})
        
        country = loc.get('country', '')
        city = loc.get('locality', '')
        circuit_name = circuit.get('circuitName', '')
        
        norm_name = clean(race_name).lower()
        winner_code = None
        for k, v in prev_winners.items():
            if any(term in norm_name for term in k.split()):
                winner_code = v
                break

        sub_slug = calendar_urls.get(fallback_mapping.get(c_id, '')) or fallback_mapping.get(c_id)
        if sub_slug and not sub_slug.startswith('http'):
            circuit_page_url = f"{F1_BASE_URL}/en/racing/{SEASON}/{sub_slug}"
        elif sub_slug:
            circuit_page_url = sub_slug
        else:
            slug_fallback = norm_name.replace('grand prix', '').replace('gp', '').strip().replace(' ', '-')
            circuit_page_url = f"{F1_BASE_URL}/en/racing/{SEASON}/{slug_fallback}"
        
        print(f"Scraping Round {round_no}: {race_name} -> {circuit_page_url}")
        detailed_stats = parse_circuit_page(session, circuit_page_url)
        time.sleep(0.4)

        fallback_img = f"https://media.formula1.com/image/upload/c_fit,h_200/q_auto/v1740000001/common/f1/{SEASON}/track/{SEASON}track{c_id}detailed.webp"

        # Determine Sprint vs Normal structure
        is_sprint = bool(race.get('Sprint') or race.get('SprintQualifying'))
        event_type = "sprint" if is_sprint else "normal"

        if is_sprint:
            session_1 = fmt_session(race.get('FirstPractice', {}))
            session_1_type = "freePractice1"
            
            # Sprint Qualifying key varies slightly across Ergast/Jolpica endpoints
            sprint_qual = race.get('SprintQualifying') or race.get('SprintShootout') or race.get('SecondPractice', {})
            session_2 = fmt_session(sprint_qual)
            session_2_type = "sprintQualifying"
            
            session_3 = fmt_session(race.get('Sprint', {}))
            session_3_type = "sprint"
        else:
            session_1 = fmt_session(race.get('FirstPractice', {}))
            session_1_type = "freePractice1"
            
            session_2 = fmt_session(race.get('SecondPractice', {}))
            session_2_type = "freePractice2"
            
            session_3 = fmt_session(race.get('ThirdPractice', {}))
            session_3_type = "freePractice3"

        qualifying = fmt_session(race.get('Qualifying', {}))
        grandprix = fmt_session(race.get('Race', {}))
        
        track_type = "Street" if c_id in STREET_CIRCUITS else ""

        country_prefix = country
        if country == 'Australia': country_prefix = 'Australian'
        elif country == 'China': country_prefix = 'Chinese'
        elif country == 'Japan': country_prefix = 'Japanese'
        elif country == 'Canada': country_prefix = 'Canadian'
        elif country == 'Spain': country_prefix = 'Spanish' if 'Catalunya' in circuit_name else 'Spain'
        elif country == 'Austria': country_prefix = 'Austrian'
        elif country == 'United Kingdom': country_prefix = 'British'
        elif country == 'Belgium': country_prefix = 'Belgian'
        elif country == 'Hungary': country_prefix = 'Hungarian'
        elif country == 'Netherlands': country_prefix = 'Dutch'
        elif country == 'Italy': country_prefix = 'Italian'
        elif country == 'Mexico': country_prefix = 'Mexican'
        elif country == 'Brazil': country_prefix = 'Brazilian'
        elif country == 'United States': country_prefix = 'Miami' if 'Miami' in race_name else ('United States' if 'Americas' in circuit_name else 'Las Vegas')

        track_short_name = circuit_name
        if c_id == 'albert_park': track_short_name = 'Albert Park'
        elif c_id == 'suzuka': track_short_name = 'Suzuka'
        elif c_id == 'monaco': track_short_name = 'Monaco'
        elif c_id == 'hungaroring': track_short_name = 'Hungaroring'
        elif c_id == 'zandvoort': track_short_name = 'Zandvoort'
        elif c_id == 'monza': track_short_name = 'Monza'
        elif c_id == 'singapore': track_short_name = 'Singapore'
        elif c_id == 'interlagos': track_short_name = 'Interlagos'
        elif c_id == 'yas_marina': track_short_name = 'Yas Marina'

        plain_stylized = f"{country_prefix} - {track_short_name}"
        stylized_name = to_bold_sans(plain_stylized)
        circuit_title = f"{race_name} - {circuit_name}"

        circuits_data.append({
            'round': round_str,
            'race_name': race_name,
            'city': city,
            'country': country,
            'circuitId': c_id,
            'circuitName': circuit_name,
            'stylized_name': stylized_name,
            'eventType': event_type,
            'type': track_type,
            'session_1': session_1,
            'session_1_type': session_1_type,
            'session_2': session_2,
            'session_2_type': session_2_type,
            'session_3': session_3,
            'session_3_type': session_3_type,
            'qualifying': qualifying,
            'grandprix': grandprix,
            'circuit_title': circuit_title,
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
    print(f"Successfully generated {OUTPUT} with {len(circuits_data)} fully populated circuits matching the grid layout format.")

if __name__ == '__main__':
    main()
