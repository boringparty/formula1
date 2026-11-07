#!/usr/bin/env python3
"""Build operational current.json with grid, race results, and fastest laps."""
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

ICS_FILE = Path(os.getenv('F1_ICS_FILE', str(REPO_ROOT / 'data' / 'calendar.ics')))
ICS_URL = os.getenv('F1_ICS_URL', 'https://raw.githubusercontent.com/boringparty/formula1/refs/heads/main/data/calendar.ics')
LEAD_HOURS = float(os.getenv('F1_LEAD_HOURS', '36'))
ROUND_OFFSET = int(os.getenv('F1_ROUND_OFFSET', '0'))
WEEKEND_GAP = timedelta(days=4)

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
    key = clean(raw).lower()
    return TEAM_NAME_MAP.get(key, clean(raw))

def num_key(n) -> str:
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

def discover_race_page_from_index(season: int, kws: list[str]) -> tuple[int, str] | None:
    """Find the official F1 race ID and URL slug for the requested event."""
    url = f"https://www.formula1.com/en/results/{season}/races"
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("F1 race index request failed: %s", exc)
        return None

    pattern = re.compile(rf'/results/{season}/races/(\d+)/([a-z0-9-]+)')
    seen = []
    for match in pattern.finditer(response.text):
        item = (int(match.group(1)), match.group(2))
        if item not in seen:
            seen.append(item)

    for race_id, slug in seen:
        normalized_slug = norm(slug)
        if len(normalized_slug) >= 4 and any(
            normalized_slug in keyword or keyword in normalized_slug
            for keyword in kws
        ):
            return race_id, slug

    return None


def discover_race_id_from_index(season: int, kws: list[str]) -> int | None:
    """Backward-compatible race ID lookup."""
    race_page = discover_race_page_from_index(season, kws)
    return race_page[0] if race_page else None


def scrape_official_qualifying(season: int, meta: dict) -> dict[str, dict]:
    """Scrape official qualifying positions and Q1/Q2/Q3 times by car number."""
    race_page = discover_race_page_from_index(season, race_keywords(meta))
    if not race_page:
        logging.warning("Could not identify official F1 race page for qualifying.")
        return {}

    race_id, slug = race_page
    url = f"https://www.formula1.com/en/results/{season}/races/{race_id}/{slug}/qualifying"

    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("Could not load official qualifying page %s: %s", url, exc)
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", id="results-table") or soup.find("table")
    if not table:
        logging.warning("No qualifying results table found at %s", url)
        return {}

    rows = table.find_all("tr")
    if not rows:
        return {}

    headers = [norm(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]

    def column(*names):
        return next((i for i, header in enumerate(headers) if header in names), None)

    pos_idx = column("pos", "position")
    no_idx = column("no", "number", "num")
    driver_idx = column("driver")
    team_idx = column("team")
    q1_idx, q2_idx, q3_idx = column("q1"), column("q2"), column("q3")

    if None in (pos_idx, no_idx, driver_idx, team_idx):
        logging.warning("Official qualifying table is missing expected columns: %s", headers)
        return {}

    qualifying = {}
    for row in rows[1:]:
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        needed = [i for i in (pos_idx, no_idx, driver_idx, team_idx, q1_idx, q2_idx, q3_idx) if i is not None]
        if len(cells) <= max(needed):
            continue

        number = cells[no_idx].strip()
        if not number.isdigit():
            continue

        driver_text = clean(cells[driver_idx])
        driver_parts = driver_text.rsplit(" ", 1)
        code = driver_parts[-1].upper() if len(driver_parts) == 2 and len(driver_parts[-1]) == 3 else ""
        driver_name = driver_parts[0] if code else driver_text

        qualifying[num_key(number)] = {
            "qualy": cells[pos_idx],
            "q1": cells[q1_idx] if q1_idx is not None else "",
            "q2": cells[q2_idx] if q2_idx is not None else "",
            "q3": cells[q3_idx] if q3_idx is not None else "",
            "number": number,
            "driverName": driver_name,
            "code": code,
            "constructorName": get_std_team(cells[team_idx]),
        }

    logging.info(
        "Scraped %d qualifying records from official F1 page (%s).",
        len(qualifying),
        url,
    )
    return qualifying


def scrape_official_fastest_laps(
    season: int,
    meta: dict,
) -> dict[str, str]:
    """Return fastest-lap times keyed by car number from Formula1.com."""

    kws = race_keywords(meta)
    url = f"https://www.formula1.com/en/results/{season}/races"

    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("Could not load F1 race index: %s", exc)
        return {}

    pattern = re.compile(
        rf"/results/{season}/races/(\d+)/([a-z0-9-]+)"
    )

    race_page = None

    for match in pattern.finditer(response.text):
        race_id, slug = match.groups()
        normalized_slug = norm(slug)

        if any(
            normalized_slug in keyword or keyword in normalized_slug
            for keyword in kws
        ):
            race_page = (race_id, slug)
            break

    if not race_page:
        logging.warning("Could not identify the official F1 race page.")
        return {}

    race_id, slug = race_page
    fastest_url = (
        f"https://www.formula1.com/en/results/{season}/"
        f"races/{race_id}/{slug}/fastest-laps"
    )

    try:
        response = requests.get(
            fastest_url,
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.warning("Could not load fastest-laps page: %s", exc)
        return {}

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", id="results-table") or soup.find("table")

    if not table:
        logging.warning("No fastest-laps table found.")
        return {}

    header = table.find("tr")
    if not header:
        return {}

    headers = [
        norm(cell.get_text(" ", strip=True))
        for cell in header.find_all(["th", "td"])
    ]

    try:
        number_index = next(
            i for i, value in enumerate(headers)
            if value in ("no", "number", "num")
        )
        time_index = next(
            i for i, value in enumerate(headers)
            if value == "time"
        )
    except StopIteration:
        logging.warning("Fastest-laps table is missing expected columns.")
        return {}

    fastest_laps = {}

    for row in table.find_all("tr"):
        cells = [
            cell.get_text(" ", strip=True)
            for cell in row.find_all(["td", "th"])
        ]

        if len(cells) <= max(number_index, time_index):
            continue

        car_number = cells[number_index]
        lap_time = cells[time_index]

        if car_number.isdigit() and lap_time:
            # Convert F1's M:SS.mmm display into HH:MM:SS.mmm so Sheets
            # recognizes it as a valid duration (for example, 1:19.328
            # becomes 00:01:19.328). Leave other formats unchanged.
            duration_match = re.fullmatch(r"(\d+):([0-5]\d\.\d+)", lap_time)
            if duration_match:
                minutes, seconds = duration_match.groups()
                lap_time = f"00:{int(minutes):02d}:{seconds}"

            fastest_laps[num_key(car_number)] = lap_time

    logging.info(
        "Scraped %d fastest-lap times from the official F1 page.",
        len(fastest_laps),
    )

    return fastest_laps


def page_identifies_race(soup: BeautifulSoup, kws: list[str]) -> bool:
    parts = [soup.title.get_text(' ', strip=True) if soup.title else '']
    parts += [h.get_text(' ', strip=True) for h in soup.find_all(['h1', 'h2'])]
    blob = norm(' '.join(parts))
    return any(k in blob for k in kws)

def scrape_official_f1_table(season: int, round_no: int, meta: dict, subpage: str) -> dict[str, str]:
    """Scrape the official starting-grid or race-result table by car number."""
    kws = race_keywords(meta)
    candidates: list[tuple[int, str | None, bool]] = []

    override = os.getenv("F1_RACE_ID", "").strip()
    discovered_page = discover_race_page_from_index(season, kws)

    if override.isdigit():
        slug = discovered_page[1] if discovered_page and discovered_page[0] == int(override) else None
        candidates.append((int(override), slug, True))

    if discovered_page:
        candidates.append((discovered_page[0], discovered_page[1], True))

    guess = F1_ID_BASE + round_no
    for delta in (0, 1, -1, 2, -2):
        candidates.append((guess + delta, None, False))

    tried = set()
    for race_id, slug, trusted in candidates:
        if race_id in tried:
            continue
        tried.add(race_id)

        # F1 result URLs use the race slug, not an underscore placeholder.
        if not slug:
            continue

        url = f"https://www.formula1.com/en/results/{season}/races/{race_id}/{slug}/{subpage}"
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code != 200:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            if not trusted and not page_identifies_race(soup, kws):
                continue

            table = soup.find("table", id="results-table") or soup.find("table")
            if not table:
                continue

            rows = table.find_all("tr")
            if not rows:
                continue

            headers = [norm(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
            pos_idx = next((i for i, h in enumerate(headers) if h in ("pos", "position")), 0)
            no_idx = next((i for i, h in enumerate(headers) if h in ("no", "number", "num")), 1)

            data_map = {}
            for row in rows[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) <= max(pos_idx, no_idx):
                    continue

                pos, number = cells[pos_idx], cells[no_idx]
                if number.isdigit() and (
                    pos.isdigit() or pos.upper() in ("NC", "DNF", "DSQ", "EX")
                ):
                    data_map[num_key(number)] = pos

            if data_map:
                logging.info("Successfully scraped %s for race_id %s", subpage, race_id)
                return data_map

        except requests.RequestException as exc:
            logging.warning("Error scraping %s: %s", url, exc)

    logging.info("No official %s records available yet.", subpage)
    return {}


def _parse_ics_dt(value: str, params: str) -> datetime | None:
    value = value.strip()
    try:
        if re.fullmatch(r'\d{8}', value):
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
                pass
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

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

def fetch_ics_events() -> list[dict]:
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
    return events

def group_weekends(events: list[dict]) -> list[dict]:
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
    weekends = group_weekends(fetch_ics_events())
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
        return r, quali_end

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

    sched = fetch_json(f'{JOLPICA}/{SEASON}.json')
    races = sched.get('RaceTable', {}).get('Races', [])
    if not races:
        logging.error("Could not fetch season schedule.")
        return

    active_meta, quali_end = pick_active_race(races, now)

    # Optional test override: shift the selected race by a number of rounds.
    # 0 = normal selection, -1 = previous race, +1 = next race.
    if ROUND_OFFSET:
        selected_index = next(
            (i for i, race in enumerate(races)
             if str(race.get('round')) == str(active_meta.get('round'))),
            None,
        )
        if selected_index is None:
            raise RuntimeError("Could not locate the selected race in the season schedule.")

        target_index = selected_index + ROUND_OFFSET
        if not 0 <= target_index < len(races):
            raise ValueError(
                f"F1_ROUND_OFFSET={ROUND_OFFSET} moves outside the season schedule."
            )

        active_meta = races[target_index]
        quali_end = None  # The calendar qualifying time belongs to the unshifted race.
        logging.info("Applying round offset %s.", ROUND_OFFSET)

    active_round = str(active_meta.get('round'))
    race_name = active_meta.get('raceName', '')
    logging.info("Targeting Round %s: %s", active_round, race_name)

    # 1. Scrape official starting grid
    if quali_end and now < quali_end:
        official_grid_map = {}
    else:
        official_grid_map = scrape_official_f1_table(SEASON, int(active_round), active_meta, 'starting-grid')

    # 2. Scrape official race results as a robust fallback/supplement to Jolpica API results
    official_race_map = scrape_official_f1_table(SEASON, int(active_round), active_meta, 'race-result')

    # 2a. Scrape fastest-lap times from the official Formula 1 results page.
    # The scraper returns an empty mapping until the page/table is published.
    fastest_lap_map = scrape_official_fastest_laps(SEASON, active_meta)

    # 3. Fetch qualifying from Jolpica, with the official F1 page as fallback.
    qual_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/qualifying.json')
    qual_races = qual_data.get('RaceTable', {}).get('Races', [])
    qual_results = qual_races[0].get('QualifyingResults', []) if qual_races else []

    if qual_results:
        logging.info("Received %d qualifying records from Jolpica.", len(qual_results))
    else:
        logging.warning(
            "Jolpica has no qualifying records for round %s; using official F1 page.",
            active_round,
        )

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

    official_qualifying_map = scrape_official_qualifying(SEASON, active_meta)

    # Fill missing qualifying fields by car number when Jolpica is partial.
    api_numbers = {
        num_key(info.get('number', '')): driver_id
        for driver_id, info in qual_map.items()
        if info.get('number')
    }

    for number, official in official_qualifying_map.items():
        driver_id = api_numbers.get(number)
        if driver_id:
            api_info = qual_map[driver_id]
            for field in ('qualy', 'q1', 'q2', 'q3'):
                if not api_info.get(field):
                    api_info[field] = official.get(field, '')
        else:
            # Synthetic ID allows official F1 data to populate output when
            # Jolpica has not published qualifying data for this round.
            driver_id = f"official_{number}"
            qual_map[driver_id] = {
                field: official.get(field, '')
                for field in ('qualy', 'q1', 'q2', 'q3', 'number')
            }

    # 4. Fetch race classification. An empty response is normal before the race.
    race_data = fetch_json(f'{JOLPICA}/{SEASON}/{active_round}/results.json')
    race_races = race_data.get('RaceTable', {}).get('Races', [])
    race_results = race_races[0].get('Results', []) if race_races else []

    if race_results:
        logging.info("Received %d race-classification records from Jolpica.", len(race_results))
    else:
        logging.info("No Jolpica race classification published yet for round %s.", active_round)

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

    # Add driver/team details for qualifying records sourced only from F1.com.
    for d_id, q_info in qual_map.items():
        if d_id.startswith("official_") and d_id not in driver_metadata:
            official = official_qualifying_map.get(num_key(q_info.get("number", "")), {})
            driver_metadata[d_id] = {
                'driverName': official.get('driverName', d_id),
                'driverId': d_id,
                'code': official.get('code', ''),
                'constructorId': '',
                'constructorName': official.get('constructorName', ''),
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

        no = r_info.get('number') or q_info.get('number', '')
        grid_pos = official_grid_map.get(num_key(no), '')
        
        # Fallback to scraped race result position if API didn't return race pos yet
        pos_val = r_info.get('pos', '') or official_race_map.get(num_key(no), '')

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
            'pos': str(pos_val),
            'race': str(pos_val),
            'laps': r_info.get('laps', ''),
            'pts': r_info.get('pts', ''),
            'status': r_info.get('status', ''),
            'fastestLap': fastest_lap_map.get(num_key(no), '')
        })

    combined_drivers.sort(
        key=lambda x: int(x['pos']) if x['pos'].isdigit() else (int(x['grid']) if x['grid'].isdigit() else 99)
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
    logging.info('Generated %s (%d drivers, %d with race positions).',
                 CURRENT_OUTPUT, len(combined_drivers),
                 sum(1 for d in combined_drivers if d['pos']))

if __name__ == '__main__':
    main()
