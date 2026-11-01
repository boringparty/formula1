#!/usr/bin/env python3
"""Build operational current.json by fetching the starting grid from formula1.com."""
from __future__ import annotations
import json, os, re, logging
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURRENT_OUTPUT = Path(os.getenv('F1_CURRENT_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
JOLPICA = 'https://api.jolpi.ca/ergast/f1'

# Calendar source: a local file (checked out with the repo) is preferred, then the URL.
ICS_FILE = Path(os.getenv('F1_ICS_FILE', str(REPO_ROOT / 'data' / 'calendar.ics')))
ICS_URL = os.getenv('F1_ICS_URL', 'https://raw.githubusercontent.com/boringparty/formula1/refs/heads/main/data/calendar.ics')
# A weekend becomes "current" this many hours before its first session, and stays current
# (so race results remain visible) until the next weekend's lead window opens.
LEAD_HOURS = float(os.getenv('F1_LEAD_HOURS', '36'))
WEEKEND_GAP = timedelta(days=4)  # sessions further apart than this belong to different weekends

# formula1.com race IDs for 2026: round 1 -> 1279 ... round 24 -> 1302
F1_ID_BASE = 1278

DRIVER_NAME_MAP = {
    'hamilton': 'Lewis Hamilton', 'russell': 'George Russell', 'leclerc': 'Charles Leclerc',
    'max_verstappen': 'Max Verstappen', 'antonelli': 'Kimi Antonelli', 'norris': 'Lando Norris',
    'hadjar': 'Isack Hadjar', 'arvid_lindblad': 'Arvid Lindblad', 'lawson': 'Liam Lawson',
    'gasly': 'Pierre Gasly', 'hulkenberg': 'Nico Hulkenberg', 'colapinto': 'Franco Colapinto',
    'piastri': 'Oscar Piastri', 'bortoleto': 'Gabriel Bortoleto', 'bearman': 'Oliver Bearman',
    'sainz': 'Carlos Sainz', 'albon': 'Alexander Albon', 'alonso': 'Fernando Alonso',
    'ryo_hirakawa': 'Ryo Hirakawa', 'stroll': 'Lance Stroll', 'perez': 'Sergio Perez', 'bottas': 'Valtteri Bottas'
}

TEAM_NAME_MAP = {
    'alpine': 'Alpine', 'aston martin': 'Aston Martin', 'audi': 'Audi', 'cadillac': 'Cadillac',
    'ferrari': 'Ferrari', 'haas': 'Haas', 'mclaren': 'McLaren', 'mercedes': 'Mercedes',
    'rb': 'RB', 'red bull': 'Red Bull', 'williams': 'Williams', 'sauber': 'Sauber'
}

TIMEOUT = 30
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.formula1.com/',
}


def clean(s): return re.sub(r'\s+', ' ', str(s or '')).strip()
def norm(s): return re.sub(r'[^a-z0-9]+', '', clean(s).lower()).strip()
def get_std_name(d_id, raw): return DRIVER_NAME_MAP.get(d_id, clean(raw))
def get_std_team(raw):
    # TEAM_NAME_MAP keys contain spaces (e.g. 'aston martin'), so look up on the cleaned lowercase name
    key = clean(raw).lower()
    return TEAM_NAME_MAP.get(key, clean(raw))


def num_key(n) -> str:
    """Normalise a car number so '01', '1' and 1 all compare equal."""
    s = clean(n)
    return str(int(s)) if s.isdigit() else s


def fetch_json(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json().get('MRData', {})
        logging.warning("HTTP %s fetching %s", r.status_code, url)
    except Exception as e:
        logging.warning("Error fetching %s: %s", url, e)
    return {}


def race_keywords(meta: dict) -> list[str]:
    """Normalised name fragments that identify this race (country, city, GP name)."""
    loc = meta.get('Circuit', {}).get('Location', {})
    raw = [
        loc.get('country', ''),
        loc.get('locality', ''),
        meta.get('raceName', '').replace('Grand Prix', ''),
    ]
    out = []
    for k in raw:
        n = norm(k)
        if len(n) >= 4 and n not in out:
            out.append(n)
    return out


def discover_race_id_from_index(season: int, kws: list[str]) -> int | None:
    """Find the formula1.com race ID by matching link slugs on the season results page."""
    url = f"https://www.formula1.com/en/results/{season}/races"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        logging.warning("Index request failed: %s", e)
        return None
    logging.info("Index %s -> HTTP %s, %d bytes", url, r.status_code, len(r.text))
    if r.status_code != 200:
        return None

    pat = re.compile(rf'/results/{season}/races/(\d+)/([a-z0-9-]+)')
    seen = []
    for m in pat.finditer(r.text):
        item = (int(m.group(1)), m.group(2))
        if item not in seen:
            seen.append(item)
    logging.info("Index lists %d races: %s", len(seen), seen)

    for rid, slug in seen:
        ns = norm(slug)
        if len(ns) >= 4 and any(ns in k or k in ns for k in kws):
            logging.info("Matched race on index: id=%s slug=%s", rid, slug)
            return rid
    return None


def page_identifies_race(soup: BeautifulSoup, kws: list[str]) -> bool:
    """True if the page's title/headings (not the nav menus) mention this race."""
    parts = [soup.title.get_text(' ', strip=True) if soup.title else '']
    parts += [h.get_text(' ', strip=True) for h in soup.find_all(['h1', 'h2'])]
    blob = norm(' '.join(parts))
    return any(k in blob for k in kws)


def scrape_official_f1_grid(season: int, round_no: int, meta: dict) -> dict[str, str]:
    """Returns {car_number: grid_position} for the race described by `meta`.

    The formula1.com race ID is NOT derived from the Jolpica round alone (rounds shift if
    the calendar changes). Order of attempts:
      1. F1_RACE_ID env override
      2. ID discovered from the season results index page (slug matched to country/city)
      3. Offset guesses around 1278+round, accepted only if the page title names this race
    """
    kws = race_keywords(meta)
    logging.info("Race keywords: %s", kws)

    # (race_id, trusted) -- trusted IDs skip the title check
    candidates: list[tuple[int, bool]] = []
    override = os.getenv('F1_RACE_ID', '').strip()
    if override.isdigit():
        candidates.append((int(override), True))
    discovered = discover_race_id_from_index(season, kws)
    if discovered:
        candidates.append((discovered, True))
    guess = F1_ID_BASE + round_no
    for delta in (0, 1, -1, 2, -2):
        candidates.append((guess + delta, False))

    tried = set()
    for race_id, trusted in candidates:
        if race_id in tried:
            continue
        tried.add(race_id)
        grid = _scrape_grid_page(season, race_id, kws, trusted)
        if grid:
            return grid

    logging.error("No verified starting grid found for %s; refusing to use a different race's grid.",
                  meta.get('raceName'))
    return {}


def _scrape_grid_page(season: int, race_id: int, kws: list[str], trusted: bool) -> dict[str, str]:
    url = f"https://www.formula1.com/en/results/{season}/races/{race_id}/_/starting-grid"
    logging.info("Scraping official starting grid from: %s (trusted=%s)", url, trusted)

    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        logging.error("Request failed: %s", e)
        return {}

    logging.info("HTTP %s, %d bytes, final URL: %s", r.status_code, len(r.text), r.url)
    if r.status_code != 200:
        logging.error("Non-200 response. 403 usually means bot protection is blocking the runner IP.")
        return {}

    soup = BeautifulSoup(r.text, 'html.parser')
    title = clean(soup.title.get_text()) if soup.title else ''
    logging.info("Page title: %s", title)

    if not trusted and not page_identifies_race(soup, kws):
        logging.warning("ID %s title doesn't match %s; skipping (wrong race?)", race_id, kws)
        return {}

    table = soup.find('table')
    if not table:
        logging.error("No <table> found (page may be JS-rendered, a bot-block page, or the grid isn't published yet)")
        return {}

    # Detect column positions from the header row, falling back to [Pos, No, Driver, ...]
    pos_idx, no_idx = 0, 1
    header = table.find('tr')
    if header:
        heads = [norm(c.get_text(' ', strip=True)) for c in header.find_all(['th', 'td'])]
        for i, h in enumerate(heads):
            if h in ('pos', 'position'):
                pos_idx = i
            elif h in ('no', 'number', 'num'):
                no_idx = i
        logging.info("Header: %s (pos col=%d, no col=%d)", heads, pos_idx, no_idx)

    grid: dict[str, str] = {}
    for row in table.find_all('tr'):
        cells = [c.get_text(' ', strip=True) for c in row.find_all(['td', 'th'])]
        if len(cells) <= max(pos_idx, no_idx):
            continue
        pos, no = cells[pos_idx], cells[no_idx]
        if not pos.isdigit() or not no.isdigit():
            continue
        grid[num_key(no)] = pos

    logging.info("Extracted %d grid rows: %s", len(grid), grid)
    return grid


def _parse_ics_dt(value: str, params: str) -> datetime | None:
    """Parse a DTSTART/DTEND value to an aware UTC datetime."""
    value = value.strip()
    try:
        if re.fullmatch(r'\d{8}', value):  # all-day event
            return datetime.strptime(value, '%Y%m%d').replace(tzinfo=timezone.utc)
        if value.endswith('Z'):
            return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        dt = datetime.strptime(value, '%Y%m%dT%H%M%S')
        m = re.search(r'TZID=([^;:]+)', params, re.I)
        if m:
            try:
                from zoneinfo import ZoneInfo
                return dt.replace(tzinfo=ZoneInfo(m.group(1))).astimezone(timezone.utc)
            except Exception:
                logging.warning("Unknown TZID %s; treating as UTC", m.group(1))
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_ics_text() -> str:
    """Read the calendar from the local file if present, otherwise download it."""
    if ICS_FILE.is_file():
        logging.info("Reading calendar from local file %s", ICS_FILE)
        return ICS_FILE.read_text(encoding='utf-8', errors='replace')
    try:
        r = requests.get(ICS_URL, headers=HEADERS, timeout=TIMEOUT)
    except Exception as e:
        logging.warning("ICS request failed: %s", e)
        return ''
    logging.info("ICS %s -> HTTP %s, %d bytes", ICS_URL, r.status_code, len(r.text))
    return r.text if r.status_code == 200 else ''


def fetch_ics_events() -> list[dict]:
    """Return [{'start','end','summary','weekend','session'}] sorted by start (UTC).

    'weekend' and 'session' come from UIDs like '...#GP18_2026_qualifying' when present.
    """
    raw = load_ics_text()
    if not raw:
        return []

    # Unfold continuation lines (RFC 5545)
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
    logging.info("Parsed %d calendar events", len(events))
    return events


def group_weekends(events: list[dict]) -> list[dict]:
    """Group sessions into race weekends: by UID key (GPn) when present, else by time gap."""
    weekends: list[dict] = []
    by_key: dict[str, dict] = {}
    for e in events:
        key = e.get('weekend')
        if key:
            w = by_key.get(key)
            if w is None:
                w = {'start': e['start'], 'end': e['end'], 'events': []}
                by_key[key] = w
                weekends.append(w)
        elif weekends and e['start'] - weekends[-1]['end'] <= WEEKEND_GAP:
            w = weekends[-1]
        else:
            w = {'start': e['start'], 'end': e['end'], 'events': []}
            weekends.append(w)
        w['events'].append(e)
        w['start'] = min(w['start'], e['start'])
        w['end'] = max(w['end'], e['end'])
    weekends.sort(key=lambda w: w['start'])
    return weekends


def match_race_to_weekend(races: list[dict], weekend: dict) -> dict | None:
    lo = weekend['start'].date() - timedelta(days=1)
    hi = weekend['end'].date() + timedelta(days=1)
    for r in races:
        try:
            rd = datetime.strptime(r.get('date', ''), '%Y-%m-%d').date()
        except ValueError:
            continue
        if lo <= rd <= hi:
            return r
    return None


def pick_active_race(races: list[dict], now: datetime) -> tuple[dict, datetime | None]:
    """Return (race_meta, qualifying_end_utc_or_None) for the current race weekend.

    Uses the ICS calendar when available, falling back to the first race on/after today.
    """
    weekends = group_weekends(fetch_ics_events())
    # keep only weekends that correspond to a Jolpica race this season
    paired = [(w, match_race_to_weekend(races, w)) for w in weekends]
    paired = [(w, r) for w, r in paired if r]

    if paired:
        lead = timedelta(hours=LEAD_HOURS)
        chosen = paired[0]
        for w, r in paired:
            if w['start'] - lead <= now:
                chosen = (w, r)
            else:
                break
        w, r = chosen
        quali_end = None
        for e in w['events']:
            s = e['summary'].lower()
            is_quali = e.get('session') == 'qualifying' or (
                not e.get('session') and 'qualifying' in s and 'sprint' not in s)
            if is_quali:
                quali_end = e['end']
        logging.info("ICS says current weekend is round %s (%s): %s -> %s; qualifying ends %s",
                     r.get('round'), r.get('raceName'), w['start'].isoformat(),
                     w['end'].isoformat(), quali_end.isoformat() if quali_end else 'unknown')
        return r, quali_end

    logging.warning("ICS unavailable or unmatched; falling back to date-based race selection")
    today = now.date()
    for r in races:
        try:
            if datetime.strptime(r.get('date', ''), '%Y-%m-%d').date() >= today:
                return r, None
        except ValueError:
            continue
    return races[-1], None


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    now = datetime.now(timezone.utc)

    # 1. Fetch schedule to locate active/latest round
    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])

    if not races:
        logging.error("Could not fetch season schedule.")
        return

    active_meta, quali_end = pick_active_race(races, now)

    active_round = str(active_meta.get('round'))
    race_name = active_meta.get('raceName', '')
    logging.info("Targeting Round %s: %s", active_round, race_name)

    # 2. Scrape official F1 starting grid (keyed by car number)
    if quali_end and now < quali_end:
        logging.info("Qualifying hasn't finished yet (ends %s); skipping grid scrape.", quali_end.isoformat())
        official_grid_map = {}
    else:
        official_grid_map = scrape_official_f1_grid(SEASON, int(active_round), active_meta)
    if not official_grid_map:
        logging.warning("No grid data scraped; 'grid' fields will be empty.")

    # 3. Fetch qualifying session data (for Q1/Q2/Q3 times)
    qual_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/qualifying.json')
    qual_races = qual_data.get('RaceTable', {}).get('Races', [])
    qual_results = qual_races[0].get('QualifyingResults', []) if qual_races else []

    qual_map = {}
    for q in qual_results:
        d_id = q.get('Driver', {}).get('driverId', '')
        qual_map[d_id] = {
            'qualy': q.get('position', ''),
            'q1': q.get('Q1', ''),
            'q2': q.get('Q2', ''),
            'q3': q.get('Q3', ''),
            'number': q.get('number', '')
        }

    # 4. Fetch race classification (for completed race data)
    race_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_races = race_data.get('RaceTable', {}).get('Races', [])
    race_results = race_races[0].get('Results', []) if race_races else []

    race_map = {}
    for res in race_results:
        d_id = res.get('Driver', {}).get('driverId', '')
        race_map[d_id] = {
            'pos': res.get('position', ''),
            'race': res.get('position', ''),
            'laps': res.get('laps', ''),
            'pts': res.get('points', ''),
            'status': res.get('status', ''),
            'number': res.get('number', '')
        }

    # 5. Build unified driver list
    all_driver_ids = set(qual_map.keys()).union(set(race_map.keys()))
    driver_info_source = qual_results if qual_results else race_results
    driver_metadata = {}

    for item in driver_info_source:
        d = item.get('Driver', {})
        c = item.get('Constructor', {})
        d_id = d.get('driverId', '')
        driver_metadata[d_id] = {
            'driverName': get_std_name(d_id, f"{d.get('givenName', '')} {d.get('familyName', '')}"),
            'driverId': d_id,
            'code': d.get('code') or d_id[:3].upper(),
            'constructorId': c.get('constructorId', ''),
            'constructorName': get_std_team(c.get('name', ''))
        }

    combined_drivers = []
    for d_id in all_driver_ids:
        meta = driver_metadata.get(d_id, {
            'driverName': DRIVER_NAME_MAP.get(d_id, d_id),
            'driverId': d_id,
            'code': d_id[:3].upper(),
            'constructorId': '',
            'constructorName': ''
        })

        q_info = qual_map.get(d_id, {})
        r_info = race_map.get(d_id, {})

        # Grid comes strictly from the formula1.com grid map, looked up by car number
        no = r_info.get('number') or q_info.get('number', '')
        grid_pos = official_grid_map.get(num_key(no), '')

        combined_drivers.append({
            'driverName': meta['driverName'],
            'driverId': meta['driverId'],
            'code': meta['code'],
            'constructorId': meta['constructorId'],
            'constructorName': meta['constructorName'],
            'no': no,
            'grid': str(grid_pos),
            'qualy': q_info.get('qualy', ''),
            'q1': q_info.get('q1', ''),
            'q2': q_info.get('q2', ''),
            'q3': q_info.get('q3', ''),
            'pos': r_info.get('pos', ''),
            'race': r_info.get('race', ''),
            'laps': r_info.get('laps', ''),
            'pts': r_info.get('pts', ''),
            'status': r_info.get('status', '')
        })

    # Sort by official grid position, then race position, then unknowns last
    combined_drivers.sort(
        key=lambda x: int(x['grid']) if x['grid'].isdigit() else (int(x['pos']) if x['pos'].isdigit() else 99)
    )

    payload = {
        'season': SEASON,
        'round': active_round,
        'race_name': active_meta.get('raceName'),
        'date': active_meta.get('date'),
        'time': active_meta.get('time'),
        'updated_at_utc': now.isoformat(),
        'drivers': combined_drivers
    }

    CURRENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    logging.info('Generated %s (%d drivers, %d with grid positions).',
                 CURRENT_OUTPUT, len(combined_drivers),
                 sum(1 for d in combined_drivers if d['grid']))


if __name__ == '__main__':
    main()
