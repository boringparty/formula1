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

TIMEOUT = 30
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}

# All session times are written in UTC (the calendar's own times), as yyyy-mm-dd hh:mm
DATE_FMT = '%Y-%m-%d %H:%M'

DRIVER_CODE_MAP = {
    'max_verstappen': 'VER', 'hamilton': 'HAM', 'russell': 'RUS', 'leclerc': 'LEC',
    'norris': 'NOR', 'piastri': 'PIA', 'sainz': 'SAI', 'alonso': 'ALO', 'perez': 'PER',
    'stroll': 'STR', 'gasly': 'GAS', 'ocon': 'OCO', 'albon': 'ALB', 'hulkenberg': 'HUL',
    'bottas': 'BOT', 'tsunoda': 'TSU', 'ricciardo': 'RIC', 'bearman': 'BEA', 'colapinto': 'COL',
    'antonelli': 'ANT', 'bortoleto': 'BOR', 'hadjar': 'HAD', 'lawson': 'LAW'
}

STREET_CIRCUITS = {
    'albert_park', 'miami', 'villeneuve', 'monaco', 'baku', 'marina_bay', 'vegas', 'madring'
}

# Keyed by the race name exactly as it appears in the calendar (lower-case, inside the brackets of
# "F1: Grand Prix (Mexico City Grand Prix)").
# Value: (circuitId, circuit name, city, country, [formula1.com /en/racing/<season>/<slug> candidates])
CIRCUITS = {
    'australian grand prix':          ('albert_park',  'Albert Park Circuit',                'Melbourne',   'Australia',            ['australia']),
    'chinese grand prix':             ('shanghai',     'Shanghai International Circuit',     'Shanghai',    'China',                ['china']),
    'japanese grand prix':            ('suzuka',       'Suzuka International Racing Course', 'Suzuka',      'Japan',                ['japan']),
    'miami grand prix':               ('miami',        'Miami International Autodrome',      'Miami',       'United States',        ['miami']),
    'canadian grand prix':            ('villeneuve',   'Circuit Gilles Villeneuve',          'Montreal',    'Canada',               ['canada']),
    'monaco grand prix':              ('monaco',       'Circuit de Monaco',                  'Monte Carlo', 'Monaco',               ['monaco']),
    'barcelona-catalunya grand prix': ('catalunya',    'Circuit de Barcelona-Catalunya',     'Barcelona',   'Spain',                ['barcelona-catalunya']),
    'austrian grand prix':            ('red_bull_ring','Red Bull Ring',                      'Spielberg',   'Austria',              ['austria']),
    'british grand prix':             ('silverstone',  'Silverstone Circuit',                'Silverstone', 'United Kingdom',       ['great-britain']),
    'belgian grand prix':             ('spa',          'Circuit de Spa-Francorchamps',       'Spa',         'Belgium',              ['belgium']),
    'hungarian grand prix':           ('hungaroring',  'Hungaroring',                        'Budapest',    'Hungary',              ['hungary']),
    'dutch grand prix':               ('zandvoort',    'Circuit Zandvoort',                  'Zandvoort',   'Netherlands',          ['netherlands']),
    'italian grand prix':             ('monza',        'Autodromo Nazionale Monza',          'Monza',        'Italy',                ['italy']),
    'spanish grand prix':             ('madring',      'Madring',                            'Madrid',      'Spain',                ['spain']),
    'azerbaijan grand prix':          ('baku',         'Baku City Circuit',                  'Baku',        'Azerbaijan',           ['azerbaijan']),
    'bahrain grand prix (malaysia)':  ('sepang',       'Sepang International Circuit',       'Sepang',      'Malaysia',             ['bahrain']),
    'singapore grand prix':           ('marina_bay',   'Marina Bay Street Circuit',          'Singapore',   'Singapore',            ['singapore']),
    'united states grand prix':       ('americas',     'Circuit of the Americas',            'Austin',      'United States',        ['united-states']),
    'mexico city grand prix':         ('rodriguez',    'Autódromo Hermanos Rodríguez',       'Mexico City', 'Mexico',               ['mexico']),
    'brazilian grand prix':           ('interlagos',   'Autódromo José Carlos Pace',          'São Paulo',   'Brazil',               ['brazil']),
    'las vegas grand prix':           ('vegas',        'Las Vegas Strip Circuit',            'Las Vegas',   'United States',        ['las-vegas']),
    'qatar grand prix':               ('losail',       'Lusail International Circuit',       'Lusail',      'Qatar',                ['qatar']),
    'abu dhabi grand prix':           ('yas_marina',   'Yas Marina Circuit',                 'Abu Dhabi',   'United Arab Emirates', ['abu-dhabi', 'united-arab-emirates']),
}


def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()


def transform_track_image(url: str) -> str:
    if not url:
        return ''
    return re.sub(r'h_\d+', 'h_200', url)


def to_bold_sans(text):
    result = ''

    for char in text:
        if 'A' <= char <= 'Z':
            result += chr(ord(char) + 0x1DF1A)
        elif 'a' <= char <= 'z':
            result += chr(ord(char) + 0x1DF2A)
        elif '0' <= char <= '9':
            result += chr(ord(char) + 0x1D7EC)
        else:
            result += char

    return result

def fmt_ics_dt(dt: datetime | None) -> str:
    """yyyy-mm-dd hh:mm (UTC), or '' when the session isn't in the calendar."""
    if not dt:
        return ''
    return dt.astimezone(timezone.utc).strftime(DATE_FMT)


def race_name_from_summary(summary: str) -> str:
    """'F1: Grand Prix (Bahrain Grand Prix (Malaysia))' -> 'Bahrain Grand Prix (Malaysia)'."""
    m = re.search(r'\((.*)\)\s*$', summary or '')
    return clean(m.group(1)) if m else clean(summary)


def lookup_circuit(race_name: str) -> tuple[str, str, str, str, list[str]]:
    info = CIRCUITS.get(race_name.lower())
    if info:
        return info
    print(f"Warning: no CIRCUITS entry for '{race_name}'; add it to the table in circuits.py")
    slug = re.sub(r'[^a-z0-9]+', '-', race_name.lower().replace('grand prix', '')).strip('-')
    return ('', race_name, '', '', [slug] if slug else [])


def get_previous_year_winners() -> dict:
    """{circuitId: winner code} for last season. Keyed by circuit, not race name, because names change
    (e.g. 'Brazilian' vs 'São Paulo') and a venue that wasn't used last year (Madring) must stay empty."""
    winners = {}
    try:
        url = f'{JOLPICA}/{PREV_SEASON}/results/1.json?limit=100'
        r = requests.get(url, timeout=TIMEOUT)
        data = r.json()
        races = data.get('MRData', {}).get('RaceTable', {}).get('Races', [])
        for race in races:
            cid = race.get('Circuit', {}).get('circuitId', '')
            results = race.get('Results', [])
            if cid and results:
                driver = results[0].get('Driver', {})
                d_id = driver.get('driverId', '')
                code = driver.get('code') or DRIVER_CODE_MAP.get(d_id, driver.get('familyName', '???')[:3].upper())
                winners[cid] = code
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

    return sorted(weekends_map.values(), key=lambda w: w['start'])


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

        # Specifically select the detailed circuit-layout image, not a trackside photo.
        img_tag = soup.find(
            'img',
            src=re.compile(
                rf'/{SEASON}/track/[^/]*detailed\.(?:webp|png|jpe?g)',
                re.IGNORECASE
            )
        )
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

    circuits_data = []

    for idx, w in enumerate(weekends, start=1):
        round_str = f"{idx:02d}"

        # Index this weekend's sessions by the UID suffix (fp1, fp2, fp3, sprintqualifying, sprint, qualifying, gp)
        sessions_dict = {e.get('session', ''): e for e in w['events']}

        def start_of(*keys):
            for k in keys:
                if k in sessions_dict:
                    return sessions_dict[k].get('start')
            return None

        is_sprint = 'sprint' in sessions_dict or 'sprintqualifying' in sessions_dict
        eventType = "sprint" if is_sprint else "normal"

        # Race name comes from the bracketed part of the race session's title
        race_event = sessions_dict.get('gp') or sessions_dict.get('race') or w['events'][0]
        race_name = race_name_from_summary(race_event.get('summary', '')) or f"Round {round_str} Grand Prix"
        c_id, circuit_name, city, country, slugs = lookup_circuit(race_name)

        session_1 = fmt_ics_dt(start_of('fp1', 'practice1', 'practice_1'))
        session_1_type = "freePractice1"

        if is_sprint:
            session_2 = fmt_ics_dt(start_of('sprintqualifying', 'sprint_qualifying', 'fp2', 'practice2', 'practice_2'))
            session_2_type = "sprintQualifying"
            session_3 = fmt_ics_dt(start_of('sprint'))
            session_3_type = "sprint"
        else:
            session_2 = fmt_ics_dt(start_of('fp2', 'practice2', 'practice_2'))
            session_2_type = "freePractice2"
            session_3 = fmt_ics_dt(start_of('fp3', 'practice3', 'practice_3'))
            session_3_type = "freePractice3"

        qual_time = fmt_ics_dt(start_of('qualifying'))
        grandprix_time = fmt_ics_dt(start_of('gp', 'race'))

        # Circuit stats page: prefer the real link from the formula1.com calendar, else build it
        print(f"Parsing ICS Round {round_str}: {race_name} ({c_id or 'unknown circuit'})")
        detailed_stats = None
        for slug in slugs:
            page_url = calendar_urls.get(slug) or f"{F1_BASE_URL}/en/racing/{SEASON}/{slug}"
            detailed_stats = parse_circuit_page(session, page_url)
            time.sleep(0.3)
            if detailed_stats['circuit_length'] or detailed_stats['image_url']:
                break
        if detailed_stats is None:
            detailed_stats = parse_circuit_page(session, f"{F1_BASE_URL}/en/racing/{SEASON}")

        fallback_img = f"https://media.formula1.com/image/upload/c_fit,h_200/q_auto/v1740000001/common/f1/{SEASON}/track/{SEASON}track{c_id}detailed.webp"

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
            'previous_year_winner_code': prev_winners.get(c_id),
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
