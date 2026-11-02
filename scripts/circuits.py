#!/usr/bin/env python3
"""Generate circuits.json by parsing calendar.ics and scraping individual track stat pages."""
from __future__ import annotations
import json, os, re, time
from datetime import datetime, timezone, timedelta
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

ICS_FILE = Path(os.getenv('F1_ICS_FILE', str(REPO_ROOT / 'data' / 'calendar.ics')))
ICS_URL = os.getenv('F1_ICS_URL', 'https://raw.githubusercontent.com/boringparty/formula1/refs/heads/main/data/calendar.ics')
WEEKEND_GAP = timedelta(days=4)

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
    if not url:
        return ''
    return re.sub(r'h_\d+', 'h_200', url)

def to_bold_sans(text: str) -> str:
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

def fmt_ics_dt(dt: datetime | None) -> str:
    if not dt:
        return ''
    return dt.strftime('%m-%d %H:%M')

def get_previous_year_winners() -> dict:
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

def load_ics_text() -> str:
    if ICS_FILE.is_file():
        return ICS_FILE.read_text(encoding='utf-8', errors='replace')
    try:
        r = requests.get(ICS_URL, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ''

def _parse_ics_dt(value: str, params: str) -> datetime | None:
    value = value.strip()
    try:
        if re.fullmatch(r'\d{8}', value):
            return datetime.strptime(value, '%Y%m%d').replace(tzinfo=timezone.utc)
        if value.endswith('Z'):
            return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        dt = datetime.strptime(value, '%Y%m%dT%H%M%S')
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def fetch_ics_weekends() -> list[dict]:
    raw = load_ics_text()
    if not raw:
        return []
    text = re.sub(r'\r?\n[ \t]', '', raw)
    events, cur = [], None
    for line in text.splitlines():
        if line.startswith('BEGIN:VEVENT'):
            cur = {}
        elif line.startswith('END:VEVENT'):
            if cur and cur.get('start'):
                cur.setdefault('end', cur['start'] + timedelta(hours=2))
                cur.setdefault('summary', '')
                events.append(cur)
            cur = None
        elif cur is not None and ':' in line:
            head, _, val = line.partition(':')
            name, _, params = head.partition(';')
            name = name.upper()
            if name == 'DTSTART':
                cur['start'] = _parse_ics_dt(val, params)
            elif name == 'DTEND':
                cur['end'] = _parse_ics_dt(val, params)
            elif name == 'SUMMARY':
                cur['summary'] = clean(val.replace('\\,', ',').replace('\\;', ';'))
            elif name == 'UID':
                m = re.search(r'#(GP\d+)_\d+_(\w+)', val)
                if m:
                    cur['weekend'], cur['session'] = m.group(1), m.group(2).lower()
    
    events = [e for e in events if e.get('start') and e.get('end')]
    events.sort(key=lambda e: e['start'])

    # Group by GPn key from UID
    weekends_map = {}
    for e in events:
        wk = e.get('weekend')
        if not wk:
            continue
        if wk not in weekends_map:
            weekends_map[wk] = {'start': e['start'], 'end': e['end'], 'events': []}
        weekends_map[wk]['events'].append(e)
        weekends_map[wk]['start'] = min(weekends_map[wk]['start'], e['start'])
        weekends_map[wk]['end'] = max(weekends_map[wk]['end'], e['end'])

    sorted_weekends = sorted(weekends_map.values(), key=lambda w: w['start'])
    return sorted_weekends

def get_circuit_urls_from_calendar(session: requests.Session) -> dict:
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
        print(f"Warning: Could not extract subpage links: {e}")
    return mapping

def parse_circuit_page(session: requests.Session, page_url: str) -> dict:
    details = {
        'circuit_length': '', 'first_grand_prix': '', 'number_of_laps': '',
        'fastest_lap': {'time': '', 'holder': ''}, 'race_distance': '', 'image_url': ''
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
            for container in dl.select('div'):
                dt, dd, span = container.select_one('dt'), container.select_one('dd'), container.select_one('span')
                if not dt or not dd:
                    continue
                label, val, extra = clean(dt.text).lower(), clean(dd.text), clean(span.text) if span else ''
                if 'length' in label: details['circuit_length'] = val
                elif 'first' in label: details['first_grand_prix'] = val
                elif 'laps' in label: details['number_of_laps'] = val
                elif 'fastest' in label: details['fastest_lap'] = {'time': val, 'holder': extra}
                elif 'distance' in label: details['race_distance'] = val
    except Exception as e:
        print(f"Warning: Failed to parse circuit page {page_url}: {e}")
    return details

def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    print(f"Parsing {SEASON} schedule from calendar.ics...")

    prev_winners = get_previous_year_winners()
    calendar_urls = get_circuit_urls_from_calendar(session)
    weekends = fetch_ics_weekends()

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

    circuits_data = []

    for idx, w in enumerate(weekends, start=1):
        round_str = f"{idx:02d}"
        
        # Categorize events from ICS
        sessions_dict = {}
        race_title_raw = ""
        for e in w['events']:
            s_type = e.get('session', '')
            summary = e.get('summary', '')
            if 'grand prix' in summary.lower() or 'gp' in summary.lower():
                race_title_raw = summary
            sessions_dict[s_type] = e

        is_sprint = 'sprint' in sessions_dict or 'sprintqualifying' in sessions_dict
        eventType = "sprint" if is_sprint else "normal"

        # Extract session timings
        fp1 = sessions_dict.get('practice_1') or sessions_dict.get('practice1') or sessions_dict.get('fp1')
        fp2 = sessions_dict.get('practice_2') or sessions_dict.get('practice2') or sessions_dict.get('fp2')
        fp3 = sessions_dict.get('practice_3') or sessions_dict.get('practice3') or sessions_dict.get('fp3')
        sprint_qual = sessions_dict.get('sprintqualifying') or sessions_dict.get('sprint_qualifying')
        sprint_race = sessions_dict.get('sprint')
        qualifying = sessions_dict.get('qualifying')
        race_sess = sessions_dict.get('race')

        session_1 = fmt_ics_dt(fp1.get('start') if fp1 else None)
        session_1_type = "freePractice1"

        if is_sprint:
            session_2 = fmt_ics_dt(sprint_qual.get('start') if sprint_qual else (fp2.get('start') if fp2 else None))
            session_2_type = "sprintQualifying"
            session_3 = fmt_ics_dt(sprint_race.get('start') if sprint_race else None)
            session_3_type = "sprint"
        else:
            session_2 = fmt_ics_dt(fp2.get('start') if fp2 else None)
            session_2_type = "freePractice2"
            session_3 = fmt_ics_dt(fp3.get('start') if fp3 else None)
            session_3_type = "freePractice3"

        qual_time = fmt_ics_dt(qualifying.get('start') if qualifying else None)
        grandprix_time = fmt_ics_dt(race_sess.get('start') if race_sess else None)

        # Derive race & circuit names from ICS summary or fallback
        race_name = race_title_raw if race_title_raw else f"Round {round_str} Grand Prix"
        # Guess circuit info based on summary or lookup
        circuit_name = race_name.split(' - ')[-1] if ' - ' in race_name else race_name
        
        # Infer circuit ID from race name or summary keywords
        c_id = 'albert_park'
        summary_blob = race_name.lower()
        for known_id in fallback_mapping.keys():
            if known_id in summary_blob or fallback_mapping[known_id] in summary_blob:
                c_id = known_id
                break

        sub_slug = calendar_urls.get(fallback_mapping.get(c_id, '')) or fallback_mapping.get(c_id)
        if sub_slug and not sub_slug.startswith('http'):
            circuit_page_url = f"{F1_BASE_URL}/en/racing/{SEASON}/{sub_slug}"
        elif sub_slug:
            circuit_page_url = sub_slug
        else:
            circuit_page_url = f"{F1_BASE_URL}/en/racing/{SEASON}"

        print(f"Parsing ICS Round {round_str}: {race_name}")
        detailed_stats = parse_circuit_page(session, circuit_page_url)
        time.sleep(0.3)

        fallback_img = f"https://media.formula1.com/image/upload/c_fit,h_200/q_auto/v1740000001/common/f1/{SEASON}/track/{SEASON}track{c_id}detailed.webp"

        country = "Unknown"
        city = "Unknown"
        for k, v in fallback_mapping.items():
            if k == c_id:
                country = v.replace('-', ' ').title()
                break

        winner_code = prev_winners.get(race_name.lower())

        circuits_data.append({
            'round': round_str,
            'race_name': race_name,
            'city': city,
            'country': country,
            'circuitId': c_id,
            'circuitName': circuit_name,
            'stylized_name': to_bold_sans(f"{country} - {circuit_name}"),
            'eventType': eventType,
            'type': "Street" if c_id in STREET_CIRCUITS else "",
            'session_1': session_1,
            'session_1_type': session_1_type,
            'session_2': session_2,
            'session_2_type': session_2_type,
            'session_3': session_3,
            'session_3_type': session_3_type,
            'qualifying': qual_time,
            'grandprix': grandprix_time,
            'circuit_title': race_name,
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
    print(f"Successfully generated {OUTPUT} directly from calendar.ics with {len(circuits_data)} circuits.")

if __name__ == '__main__':
    main()
