#!/usr/bin/env python3
"""Build one season JSON document from Jolpica and Formula1.com."""
from __future__ import annotations
import argparse, json, logging, math, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SEASON = int(os.getenv('F1_SEASON', '2026'))
REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.getenv('F1_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
CURRENT_OUTPUT = Path(os.getenv('F1_CURRENT_OUTPUT_FILE', str(REPO_ROOT / 'current.json')))
CALENDAR_URL = os.getenv('F1_CALENDAR_URL', 'https://files-f1.motorsportcalendars.com/f1-calendar_p1_p2_p3_qualifying_sprint_gp.ics')
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
F1_BASE = f'https://www.formula1.com/en/results/{SEASON}/races'

# ==========================================
# SEASON MAPPING CONFIGURATION
# Update these arrays as driver lineups or team names change across seasons.
# ==========================================
DRIVER_NAME_MAP = {
    'hamilton': 'Lewis Hamilton',
    'russell': 'George Russell',
    'leclerc': 'Charles Leclerc',
    'max_verstappen': 'Max Verstappen',
    'antonelli': 'Kimi Antonelli',
    'norris': 'Lando Norris',
    'hadjar': 'Isack Hadjar',
    'arvid_lindblad': 'Arvid Lindblad',
    'lawson': 'Liam Lawson',
    'gasly': 'Pierre Gasly',
    'hulkenberg': 'Nico Hulkenberg',
    'colapinto': 'Franco Colapinto',
    'piastri': 'Oscar Piastri',
    'bortoleto': 'Gabriel Bortoleto',
    'bearman': 'Oliver Bearman',
    'sainz': 'Carlos Sainz',
    'albon': 'Alexander Albon',
    'alonso': 'Fernando Alonso',
    'ryo_hirakawa': 'Ryo Hirakawa',
    'stroll': 'Lance Stroll',
    'perez': 'Sergio Perez',
    'bottas': 'Valtteri Bottas'
}

TEAM_NAME_MAP = {
    'alpine': 'Alpine',
    'alpine f1 team': 'Alpine',
    'aston_martin': 'Aston Martin',
    'aston martin': 'Aston Martin',
    'audi': 'Sauber',
    'cadillac': 'Cadillac',
    'cadillac f1 team': 'Cadillac',
    'ferrari': 'Ferrari',
    'haas': 'Haas',
    'haas f1 team': 'Haas',
    'mclaren': 'McLaren',
    'mercedes': 'Mercedes',
    'rb': 'RB',
    'rb f1 team': 'RB',
    'alphatauri': 'RB',
    'visa cash app rb': 'RB',
    'red_bull': 'Red Bull',
    'red bull': 'Red Bull',
    'red bull racing': 'Red Bull',
    'williams': 'Williams',
    'sauber': 'Sauber'
}
# ==========================================

# Time windows for weekend-aware tracking
START_AFTER = timedelta(minutes=90)
CUTOFF_AFTER = timedelta(hours=6)
WEEKEND_START_BEFORE = timedelta(days=3)  # Active from Friday onwards

TIMEOUT = 30
DELAY = float(os.getenv('F1_REQUEST_DELAY', '0.15'))
FORCE = os.getenv('F1_FORCE_SCRAPE', '').lower() in {'1','true','yes','on'}
SKIP_ROUND_DETAILS = False
RACE_FILTER = ''
LOG = logging.getLogger('f1-json')
HEADERS = {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36','Accept-Language':'en-US,en;q=0.9','Referer':'https://www.formula1.com/'}
RACE_URLS = {
 'australia':(1279,'australia'),'china':(1280,'china'),'japan':(1281,'japan'),'miami':(1284,'miami'),
 'canada':(1285,'canada'),'monaco':(1286,'monaco'),'barcelona':(1287,'barcelona-catalunya'),'catalunya':(1287,'barcelona-catalunya'),
 'austria':(1288,'austria'),'great britain':(1289,'great-britain'),'british':(1289,'great-britain'),'belgium':(1290,'belgium'),
 'hungary':(1291,'hungary'),'netherlands':(1292,'netherlands'),'italy':(1293,'italy'),'spain':(1294,'spain'),
 'azerbaijan':(1295,'azerbaijan'),'singapore':(1296,'singapore'),'united states':(1297,'united-states'),'usa':(1297,'united-states'),
 'mexico':(1298,'mexico'),'brazil':(1299,'brazil'),'sao paulo':(1299,'brazil'),'las vegas':(1300,'las-vegas'),'qatar':(1301,'qatar'),'abu dhabi':(1302,'abu-dhabi')}

def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
def norm(s): return re.sub(r'[^a-z0-9 ]+',' ',clean(s).lower()).strip()

def get_standardized_name(driver_id: str, raw_name: str) -> str:
    if driver_id and driver_id in DRIVER_NAME_MAP:
        return DRIVER_NAME_MAP[driver_id]
    norm_raw = norm(raw_name)
    for key, pref_name in DRIVER_NAME_MAP.items():
        if key.replace('_', ' ') in norm_raw or norm(pref_name) == norm_raw:
            return pref_name
    cleaned = clean(raw_name)
    if cleaned == 'Andrea Kimi Antonelli': return 'Kimi Antonelli'
    if cleaned == 'Nico Hülkenberg': return 'Nico Hulkenberg'
    if cleaned == 'Sergio Pérez': return 'Sergio Perez'
    return cleaned

def get_standardized_team(raw_team: str) -> str:
    cleaned = clean(raw_team)
    norm_team = norm(cleaned)
    if norm_team in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[norm_team]
    for key, pref_team in TEAM_NAME_MAP.items():
        if key in norm_team:
            return pref_team
    return cleaned

def plain(v):
    if v is None: return None
    if isinstance(v, dict): return {str(k):plain(x) for k,x in v.items()}
    if isinstance(v,(list,tuple)): return [plain(x) for x in v]
    if isinstance(v,datetime): return v.isoformat()
    if isinstance(v,timedelta): return v.total_seconds()
    try:
        import pandas as pd
        if v is pd.NaT or pd.isna(v): return None
        if isinstance(v,pd.Timestamp): return v.isoformat()
        if isinstance(v,pd.Timedelta): return v.total_seconds()
    except (ImportError,TypeError,ValueError): pass
    if hasattr(v,'item'):
        try: v=v.item()
        except (ValueError,TypeError): pass
    if isinstance(v,float) and (math.isnan(v) or math.isinf(v)): return None
    if isinstance(v,(str,int,float,bool)): return v
    return str(v)

def write_json(path,payload): path.write_text(json.dumps(plain(payload),ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def request_json(session,url):
    r=session.get(url,headers={'Accept':'application/json'},timeout=TIMEOUT); r.raise_for_status(); data=r.json()
    if not isinstance(data,dict) or 'MRData' not in data: raise ValueError(f'Unexpected Jolpica response: {url}')
    return data['MRData']
def api_url(path,offset=0,limit=1000):
    suffix='.json'
    return f'{JOLPICA}/{path}{suffix}?limit={limit}&offset={offset}'
def table_key(path):
    last=path.split('/')[-1]
    return {'races':'RaceTable','circuits':'CircuitTable','constructors':'ConstructorTable','drivers':'DriverTable','results':'RaceTable','sprint':'RaceTable','qualifying':'RaceTable','pitstops':'RaceTable','laps':'RaceTable','driverstandings':'StandingsTable','constructorstandings':'StandingsTable','seasons':'SeasonTable','status':'StatusTable'}.get(last,'MRData')
def extract_records(mr,key):
    table=mr.get(key,{}) if key else mr
    if key in ('RaceTable','CircuitTable','ConstructorTable','DriverTable','SeasonTable','StatusTable'):
        plural={'RaceTable':'Races','CircuitTable':'Circuits','ConstructorTable':'Constructors','DriverTable':'Drivers','SeasonTable':'Seasons','StatusTable':'Status'}.get(key)
        return table.get(plural,[]) if plural else []
    if key=='StandingsTable':
        out=[]
        for race in table.get('StandingsLists',[]):
            entry={'season':race.get('season'),'round':race.get('round'),'date':race.get('date'),'standings':race.get('DriverStandings') or race.get('ConstructorStandings') or []}
            out.append(entry)
        return out
    return table.get('Races',[]) if key=='RaceTable' else []

def merge_page_records(existing, incoming):
    by_key = {}
    ordered = []
    nested_keys = ('Results', 'SprintResults', 'QualifyingResults', 'PitStops', 'Laps',
                   'DriverStandings', 'ConstructorStandings')
    for record in list(existing) + list(incoming):
        identity = (str(record.get('season', '')), str(record.get('round', '')),
                    str(record.get('raceName', '')), str(record.get('date', '')))
        if identity not in by_key:
            clone = dict(record)
            for nested in nested_keys:
                if isinstance(clone.get(nested), list):
                    clone[nested] = list(clone[nested])
            by_key[identity] = clone
            ordered.append(identity)
            continue
        target = by_key[identity]
        for nested in nested_keys:
            if isinstance(record.get(nested), list):
                target.setdefault(nested, []).extend(record[nested])
        for field, value in record.items():
            if field not in nested_keys and field not in target:
                target[field] = value
    for record in by_key.values():
        for nested in nested_keys:
            values = record.get(nested)
            if not isinstance(values, list):
                continue
            seen = set(); unique = []
            for item in values:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker not in seen:
                    seen.add(marker); unique.append(item)
            record[nested] = unique
    return [by_key[k] for k in ordered]

def fetch_paginated(session,path,key=None):
    key=key or table_key(path); offset=0; all_records=[]; source_urls=[]; total=None
    while True:
        url=api_url(path,offset=offset)
        mr=request_json(session,url); source_urls.append(url)
        records=extract_records(mr,key)
        all_records=merge_page_records(all_records,records)
        try: total=int(mr.get('total',offset+len(records)))
        except (ValueError,TypeError): total=offset+len(records)
        limit=int(mr.get('limit',1000) or 1000)
        if not records or offset + limit >= total: break
        offset += limit
        time.sleep(DELAY)
    return {'source':'Jolpica API','urls':source_urls,'count':sum(len(r.get('Laps',[])) + len(r.get('PitStops',[])) for r in all_records) if path.endswith(('/laps','/pitstops')) else len(all_records),'data':all_records}

def extract_fastest_laps(race_record):
    if not isinstance(race_record, dict):
        return []
    rows = []
    for result in race_record.get('Results', []) or []:
        if not isinstance(result, dict):
            continue
        fastest = result.get('FastestLap')
        if not isinstance(fastest, dict):
            continue
        lap_time = fastest.get('Time') or {}
        lap_time = lap_time.get('time') if isinstance(lap_time, dict) else lap_time
        if not lap_time:
            continue
        driver = result.get('Driver') or {}
        d_id = driver.get('driverId', '')
        raw_name = clean(f"{driver.get('givenName', '')} {driver.get('familyName', '')}")
        name = get_standardized_name(d_id, raw_name)
        if name:
            rows.append({'driver': name, 'fastest_lap': lap_time})
    return rows

def add_fastest_laps_by_round(jolpica_data):
    results = jolpica_data.get('results', {}).get('data', [])
    jolpica_data['fastest_laps_by_round'] = {
        str(race.get('round')): extract_fastest_laps(race)
        for race in results
        if race.get('round') is not None
    }

def fetch_jolpica_season(session):
    specs=[(f'{SEASON}/races',f'{SEASON}/races','RaceTable'),(f'{SEASON}/constructors',f'{SEASON}/constructors','ConstructorTable'),(f'{SEASON}/drivers',f'{SEASON}/drivers','DriverTable'),(f'{SEASON}/results',f'{SEASON}/results','RaceTable'),(f'{SEASON}/sprint',f'{SEASON}/sprint','RaceTable'),(f'{SEASON}/qualifying',f'{SEASON}/qualifying','RaceTable'),(f'{SEASON}/driverstandings',f'{SEASON}/driverstandings','StandingsTable'),(f'{SEASON}/constructorstandings',f'{SEASON}/constructorstandings','StandingsTable')]
    data={}
    for name,path,key in specs:
        LOG.info('Jolpica: %s',name)
        try: data[name]=fetch_paginated(session,path,key)
        except Exception as e:
            LOG.warning('Jolpica %s failed: %s',name,e); data[name]={'source':'Jolpica API','urls':[api_url(path)],'count':0,'data':[],'error':f'{type(e).__name__}: {e}'}
        time.sleep(DELAY)
    races=data.get('races',{}).get('data',[])
    circuits={}
    for race in races:
        circuit=race.get('Circuit')
        if isinstance(circuit,dict) and circuit.get('circuitId'):
            circuits[circuit['circuitId']]=circuit
    data['circuits']={'source':'Jolpica API (derived from this season race calendar)','urls':data.get('races',{}).get('urls',[]),'count':len(circuits),'data':list(circuits.values())}
    data['pitstops_by_round']={}; data['laps_by_round']={}
    if SKIP_ROUND_DETAILS:
        data['round_details_skipped'] = True
        add_fastest_laps_by_round(data)
        return data
    for race in races:
        round_no=str(race.get('round','')).strip()
        if not round_no: continue
        for name,endpoint in [('pitstops_by_round',f'{SEASON}/{round_no}/pitstops'),('laps_by_round',f'{SEASON}/{round_no}/laps')]:
            LOG.info('Jolpica: %s round %s (%s)',name,round_no,race.get('raceName',''))
            try: data[name][round_no]=fetch_paginated(session,endpoint,'RaceTable')
            except Exception as e:
                LOG.warning('Jolpica %s round %s failed: %s',name,round_no,e)
                data[name][round_no]={'source':'Jolpica API','urls':[api_url(endpoint)],'count':0,'data':[],'error':f'{type(e).__name__}: {e}'}
            time.sleep(DELAY)
    add_fastest_laps_by_round(data)
    return data

def _ics_unescape(value):
    return value.replace(r'\n', '\n').replace(r'\N', '\n').replace(r'\,', ',').replace(r'\;', ';').replace(r'\\', '\\')

def _parse_ics_datetime(value, tzid=None):
    value = value.strip()
    if len(value) == 8:
        return None
    fmt = '%Y%m%dT%H%M%SZ' if value.endswith('Z') else '%Y%m%dT%H%M%S'
    try:
        dt = datetime.strptime(value, fmt)
    except ValueError:
        return None
    if value.endswith('Z'):
        return dt.replace(tzinfo=timezone.utc)
    if tzid:
        try:
            return dt.replace(tzinfo=ZoneInfo(tzid))
        except ZoneInfoNotFoundError:
            LOG.warning('Unknown calendar timezone %s; treating event as UTC', tzid)
    return dt.replace(tzinfo=timezone.utc)

def get_calendar(session):
    r=session.get(CALENDAR_URL,timeout=TIMEOUT); r.raise_for_status()
    lines=[]
    for line in r.text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if line.startswith((' ', '\t')) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    events=[]; current=None
    for line in lines:
        if line == 'BEGIN:VEVENT':
            current={}; continue
        if line == 'END:VEVENT' and current is not None:
            summary=_ics_unescape(current.get('SUMMARY',''))
            low=summary.lower()
            if 'grand prix' in low and not any(x in low for x in ('practice','qualifying','sprint','warm-up','warm up')):
                dt=_parse_ics_datetime(current.get('DTSTART',''),current.get('DTSTART_TZID'))
                if dt is not None:
                    events.append({'summary':clean(summary),'start_utc':dt.astimezone(timezone.utc),'uid':clean(current.get('UID',''))})
            current=None; continue
        if current is None or ':' not in line:
            continue
        left,value=line.split(':',1)
        bits=left.split(';'); prop=bits[0].upper()
        params={}
        for bit in bits[1:]:
            if '=' in bit:
                k,v=bit.split('=',1); params[k.upper()]=v.strip('"')
        if prop in ('SUMMARY','UID'):
            current[prop]=value
        elif prop=='DTSTART':
            current['DTSTART']=value
            current['DTSTART_TZID']=params.get('TZID')
    return sorted(events,key=lambda x:x['start_utc'])

def race_name(summary):
    m=re.search(r'\(([^()]*Grand Prix)\)',summary,re.I)
    if m:return clean(m.group(1))
    m=re.search(r'((?:[A-Za-zÀ-ÿ0-9\'’.-]+\s+){0,5}Grand Prix)',summary,re.I)
    return clean(m.group(1)) if m else clean(summary)

def select_event(events,now):
    if RACE_FILTER:
        matches=[e for e in events if norm(RACE_FILTER) in norm(e['summary']) or norm(RACE_FILTER) in norm(race_name(e['summary']))]
        return max(matches,key=lambda x:x['start_utc']) if matches else None
    if FORCE:
        candidates=[e for e in events if e['start_utc'] - WEEKEND_START_BEFORE <= now <= e['start_utc'] + CUTOFF_AFTER]
        return max(candidates,key=lambda x:x['start_utc']) if candidates else max(events,key=lambda x:x['start_utc'])
    
    active = [e for e in events if e['start_utc'] - WEEKEND_START_BEFORE <= now <= e['start_utc'] + CUTOFF_AFTER]
    if active:
        return max(active, key=lambda x: x['start_utc'])
    
    past = [e for e in events if e['start_utc'] + CUTOFF_AFTER < now]
    return max(past, key=lambda x:x['start_utc']) if past else events[0]

def parse_f1_table(html):
    soup=BeautifulSoup(html,'html.parser'); table=soup.select_one('#results-table table') or soup.find('table')
    if not table:return {'ok':False,'headers':[],'rows':[],'error':'No results table found.'}
    headers=[clean(th.get_text(' ',strip=True)).rstrip('.').lower() for th in table.select('thead th')]; rows=[]
    for tr in table.select('tbody tr'):
        cells=tr.find_all('td',recursive=False); vals=[]
        for i,c in enumerate(cells):
            if i<len(headers) and headers[i]=='driver':
                a=c.select_one('span.max-lg\\:hidden'); b=c.select_one('span.max-md\\:hidden')
                raw_d = clean((a.get_text(' ',strip=True) if a else '')+' '+(b.get_text(' ',strip=True) if b else '')) or clean(c.get_text(' ',strip=True))
                vals.append(get_standardized_name('', raw_d))
            elif i<len(headers) and headers[i]=='team':
                raw_t = clean(c.get_text(' ',strip=True))
                vals.append(get_standardized_team(raw_t))
            else: vals.append(clean(c.get_text(' ',strip=True)))
        if not any(vals):continue
        row={ (headers[i].replace(' / ','_').replace(' ','_').replace('-','_') if i<len(headers) and headers[i] else f'column_{i+1}'):v for i,v in enumerate(vals)}
        row['driver_number']=row.get('no'); rows.append(row)
    return {'ok':bool(rows),'headers':headers,'rows':rows,'error':None if rows else 'No rows found.'}

def scrape_f1_page(session,url):
    try:
        r=session.get(url,timeout=TIMEOUT); r.raise_for_status(); out=parse_f1_table(r.text); out.update({'url':url,'status_code':r.status_code,'source':'Formula1.com'}); return out
    except Exception as e:return {'url':url,'source':'Formula1.com','status_code':None,'ok':False,'headers':[],'rows':[],'error':f'{type(e).__name__}: {e}'}

def attach_fastest_laps_to_formula1_rows(formula1_source, jolpica_race):
    if not isinstance(formula1_source, dict):
        return
    if not isinstance(jolpica_race, dict):
        jolpica_race = {}
    result_table = formula1_source.get('race_results')
    if not isinstance(result_table, dict) or not isinstance(result_table.get('rows'), list):
        return
    by_number = {}
    by_name = {}
    for result in jolpica_race.get('Results', []) or []:
        if not isinstance(result, dict):
            continue
        driver = result.get('Driver') or {}
        d_id = driver.get('driverId', '')
        raw_name = clean(f"{driver.get('givenName', '')} {driver.get('familyName', '')}")
        std_name = get_standardized_name(d_id, raw_name)
        
        fastest = result.get('FastestLap') or {}
        lap_time = fastest.get('Time') or {}
        lap_time = lap_time.get('time') if isinstance(lap_time, dict) else lap_time
        if not lap_time:
            continue
        number = clean(driver.get('permanentNumber') or driver.get('number'))
        if number:
            by_number[number] = lap_time or None
        if std_name:
            by_name[norm(std_name)] = lap_time or None
    for row in result_table['rows']:
        number = clean(row.get('driver_number') or row.get('no'))
        driver_name = norm(row.get('driver') or row.get('name'))
        if number and number in by_number:
            row['fastest_lap'] = by_number[number]
        else:
            row['fastest_lap'] = by_name.get(driver_name) if driver_name else None
    result_table['headers'] = list(dict.fromkeys(result_table.get('headers', []) + ['grid', 'fastest_lap']))

def fetch_formula1_current(session,event):
    summary=event['summary'].lower(); match=next(((k,v) for k,v in sorted(RACE_URLS.items(),key=lambda p:len(p[0]),reverse=True) if k in summary),None)
    if not match:return {'ok':False,'error':f'No Formula1.com race mapping for {event["summary"]}'}
    race_id,slug=match[1]; base=f'{F1_BASE}/{race_id}/{slug}'
    
    result=scrape_f1_page(session,base+'/race-result'); time.sleep(DELAY)
    grid=scrape_f1_page(session,base+'/starting-grid')
    if not grid['ok']:
        time.sleep(DELAY); qualifying=scrape_f1_page(session,base+'/qualifying')
        grid={'source':'Formula1.com qualifying fallback','url':qualifying['url'],'ok':qualifying['ok'],'headers':qualifying['headers'],'rows':qualifying['rows'],'error':qualifying['error']} if qualifying['ok'] else {'source':'Formula1.com starting-grid','url':base+'/starting-grid','ok':False,'rows':[],'error':'Starting grid and qualifying unavailable.'}

    if not result['ok'] and grid['ok']:
        result = {
            'ok': True,
            'source': 'Formula1.com qualifying/grid (pre-race fallback)',
            'url': grid.get('url', base + '/qualifying'),
            'headers': grid.get('headers', []),
            'rows': grid.get('rows', []),
            'error': None
        }

    grid_by_number = {
        str(row.get('driver_number') or row.get('no')): row
        for row in grid.get('rows', [])
        if row.get('driver_number') or row.get('no')
    }
    for row in result.get('rows', []):
        number = str(row.get('driver_number') or row.get('no') or '')
        start_row = grid_by_number.get(number)
        if start_row:
            row['grid'] = start_row.get('pos') or start_row.get('position')
        else:
            row['grid'] = None
    result['headers'] = list(dict.fromkeys(result.get('headers', []) + ['grid']))

    grid_metadata = {key: grid.get(key) for key in ('source', 'url', 'ok', 'error') if key in grid}
    return {'race_id':race_id,'slug':slug,'race_url':base,'race_results':result,'starting_grid_source':grid_metadata, 'starting_grid': grid}

def main(argv=None):
    global SEASON, OUTPUT, CURRENT_OUTPUT, F1_BASE, FORCE, SKIP_ROUND_DETAILS, RACE_FILTER
    parser = argparse.ArgumentParser(description='Collect F1 season data into one JSON file.')
    parser.add_argument('--season', type=int, default=SEASON, help='season year (default: %(default)s)')
    parser.add_argument('--output', type=Path, default=OUTPUT, help='full current-season JSON path (default: %(default)s)')
    parser.add_argument('--current-output', type=Path, default=CURRENT_OUTPUT, help='latest selected Grand Prix JSON path (default: %(default)s)')
    parser.add_argument('--force', action='store_true', help='scrape the latest Grand Prix outside the automatic window')
    parser.add_argument('--race', help='select a Grand Prix by name, e.g. Mexico Grand Prix')
    parser.add_argument('--skip-round-details', action='store_true', help='skip per-round lap and pit-stop endpoints (faster smoke test)')
    args = parser.parse_args(argv)
    SEASON = args.season
    OUTPUT = args.output
    CURRENT_OUTPUT = args.current_output
    F1_BASE = f'https://www.formula1.com/en/results/{SEASON}/races'
    FORCE = FORCE or args.force or bool(args.race)
    SKIP_ROUND_DETAILS = args.skip_round_details
    RACE_FILTER = args.race or ''
    logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(message)s')
    session=requests.Session(); session.headers.update(HEADERS); now=datetime.now(timezone.utc)
    try: events=get_calendar(session)
    except Exception as e: LOG.error('Calendar fetch failed: %s',e); return 1
    event=select_event(events,now)
    if not event:
        LOG.info('Outside the active weekend window; no data-source requests made.')
        return 0
    LOG.info('Selected race: %s at %s',event['summary'],event['start_utc'].isoformat())
    document={'schema_version':1,'season':SEASON,'generated_at_utc':now.isoformat(),'race_window':{'calendar_event':event['summary'],'scheduled_start_utc':event['start_utc'].isoformat(),'opens_utc':(event['start_utc'] - WEEKEND_START_BEFORE).isoformat(),'closes_utc':(event['start_utc']+CUTOFF_AFTER).isoformat(),'forced':FORCE},'sources':{}}
    try: document['sources']['jolpica']=fetch_jolpica_season(session)
    except Exception as e: document['sources']['jolpica']={'error':f'{type(e).__name__}: {e}'}; LOG.exception('Jolpica fetch failed')
    document['sources']['formula1_com']=fetch_formula1_current(session,event)
    
    jol=document['sources'].get('jolpica',{})
    rounds=jol.get('races',{}).get('data',[]) if isinstance(jol,dict) else []
    result_races=jol.get('results',{}).get('data',[]) if isinstance(jol,dict) else []
    qual_races=jol.get('qualifying',{}).get('data',[]) if isinstance(jol,dict) else []
    sprint_races=jol.get('sprint',{}).get('data',[]) if isinstance(jol,dict) else []
    target=norm(race_name(event['summary']))
    
    def find_race(records):
        exact=next((r for r in records if norm(r.get('raceName',''))==target),None)
        if exact is not None:
            return exact
        candidates=[r for r in records if norm(r.get('raceName','')) and
                    (norm(r.get('raceName','')) in target or target in norm(r.get('raceName','')))]
        return candidates[0] if len(candidates)==1 else None
        
    matched=find_race(rounds)
    race_results=find_race(result_races)
    if race_results is None and matched and matched.get('round'):
        round_no=str(matched['round'])
        try:
            round_payload=fetch_paginated(session,f'{SEASON}/{round_no}/results','RaceTable')
            race_results=find_race(round_payload.get('data',[]))
            if race_results is None:
                race_results=next((r for r in round_payload.get('data',[]) if str(r.get('round'))==round_no and r.get('Results')),None)
        except Exception as exc:
            LOG.warning('Could not fetch selected-round results from Jolpica: %s',exc)
            
    if matched is None and race_results is None:
        try:
            schedule_payload=fetch_paginated(session,f'{SEASON}/races','RaceTable')
            matched=find_race(schedule_payload.get('data',[]))
            if matched and matched.get('round'):
                round_no=str(matched['round'])
                round_payload=fetch_paginated(session,f'{SEASON}/{round_no}/results','RaceTable')
                race_results=next((r for r in round_payload.get('data',[]) if str(r.get('round'))==round_no and r.get('Results')),None)
        except Exception as exc:
            LOG.warning('Selected-race Jolpica fallback failed: %s',exc)
            
    if matched is None and race_results is not None:
        matched = {
            'season': race_results.get('season', str(SEASON)),
            'round': race_results.get('round'),
            'raceName': race_results.get('raceName'),
            'date': race_results.get('date'),
            'Circuit': race_results.get('Circuit'),
            'url': race_results.get('url'),
            '_derived_from': 'Jolpica results fallback (race calendar metadata unavailable)',
        }
        
    if matched or race_results:
        round_no=str((matched or race_results).get('round'))
        if race_results is None:
            race_results=next((r for r in result_races if str(r.get('round'))==round_no and r.get('Results')),None)
        if race_results is None:
            try:
                round_payload=fetch_paginated(session,f'{SEASON}/{round_no}/results','RaceTable')
                race_results=next((r for r in round_payload.get('data',[]) if str(r.get('round'))==round_no and r.get('Results')),None)
            except Exception as exc:
                LOG.warning('Could not backfill selected-round results for round %s: %s',round_no,exc)
        
        qual_record = next((r for r in qual_races if str(r.get('round'))==round_no),None)
        if not qual_record or not qual_record.get('QualifyingResults'):
            try:
                qual_payload = fetch_paginated(session, f'{SEASON}/{round_no}/qualifying', 'RaceTable')
                qual_record = next((r for r in qual_payload.get('data', []) if str(r.get('round')) == round_no and r.get('QualifyingResults')), None)
                if qual_record:
                    LOG.info('Loaded round %s qualifying directly from Jolpica round endpoint.', round_no)
            except Exception as exc:
                LOG.warning('Could not fetch round-specific qualifying from Jolpica: %s', exc)

        document['current_race']={
            'round':round_no,
            'race_name':(matched or race_results).get('raceName'),
            'race_metadata':matched,
            'results':race_results,
            'qualifying':qual_record,
            'sprint':next((r for r in sprint_races if str(r.get('round'))==round_no),None),
            'pitstops':jol.get('pitstops_by_round',{}).get(round_no),
            'laps':jol.get('laps_by_round',{}).get(round_no)
        }
    else:
        document['current_race']={
            'round':None,
            'race_name':race_name(event['summary']),
            'race_metadata':None,
            'results':None,
            'qualifying':None,
            'sprint':None,
            'pitstops':None,
            'laps':None,
            'note':'Jolpica race results were unavailable for this run.'
        }
        
    attach_fastest_laps_to_formula1_rows(document['sources'].get('formula1_com', {}), race_results)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT,document)
    LOG.info('Wrote full current-season JSON: %s (%d bytes)',OUTPUT,OUTPUT.stat().st_size)

    f1_source = document.get('sources', {}).get('formula1_com', {})
    f1_result = f1_source.get('race_results', {}) if isinstance(f1_source, dict) else {}
    f1_starting_grid = f1_source.get('starting_grid', {}) if isinstance(f1_source, dict) else {}
    jol_race = document.get('current_race', {})
    jol_qual = jol_race.get('qualifying', {}) if isinstance(jol_race, dict) else {}
    
    # Map scraped starting grid by driver number and normalized name
    grid_map_num = {}
    grid_map_name = {}
    if isinstance(f1_starting_grid, dict) and f1_starting_grid.get('rows'):
        for row in f1_starting_grid.get('rows', []):
            pos = row.get('pos') or row.get('position')
            num = str(row.get('driver_number') or row.get('no') or '').strip()
            dname = norm(row.get('driver') or row.get('name') or '')
            if num:
                grid_map_num[num] = pos
            if dname:
                grid_map_name[dname] = pos

    current_rows = []

    # Priority 1: Jolpica QualifyingResults (standardized driver and constructor names)
    if jol_qual and isinstance(jol_qual.get('QualifyingResults'), list) and jol_qual['QualifyingResults']:
        for q in jol_qual['QualifyingResults']:
            driver_info = q.get('Driver', {})
            constructor = q.get('Constructor', {})
            
            d_id = driver_info.get('driverId', '')
            raw_name = f"{driver_info.get('givenName', '')} {driver_info.get('familyName', '')}".strip()
            driver_name = get_standardized_name(d_id, raw_name)
            
            raw_team = constructor.get('name', '')
            team_name = get_standardized_team(raw_team)
            
            num = str(q.get('number', '')).strip()
            dname_norm = norm(driver_name)
            
            scraped_grid = grid_map_num.get(num) or grid_map_name.get(dname_norm) or q.get('position')
            qual_position = q.get('position')
            
            current_rows.append({
                'driver': driver_name,
                'team': team_name,
                'grid': scraped_grid,
                'pos': qual_position,
                'no': q.get('number'),
                'qualy': qual_position,
                'q1': q.get('Q1'),
                'q2': q.get('Q2'),
                'q3': q.get('Q3'),
            })

    # Priority 2: Formula1.com race or starting grid fallback
    elif isinstance(f1_result, dict) and f1_result.get('rows'):
        for row in f1_result.get('rows', []):
            raw_dname = row.get('driver') or row.get('name') or ''
            driver_name = get_standardized_name('', raw_dname)
            
            raw_team = row.get('team') or row.get('car') or ''
            team_name = get_standardized_team(raw_team)
            
            num = str(row.get('no') or row.get('driver_number') or '').strip()
            dname = norm(driver_name)
            scraped_grid = grid_map_num.get(num) or grid_map_name.get(dname) or row.get('grid') or row.get('pos')
            qual_position = row.get('pos') or row.get('position')
            
            current_rows.append({
                'driver': driver_name,
                'team': team_name,
                'grid': scraped_grid,
                'pos': qual_position,
                'no': row.get('no') or row.get('driver_number'),
                'qualy': qual_position,
                'laps': row.get('laps'),
                'time_retired': row.get('time_retired'),
                'pts': row.get('pts'),
                'fastest_lap': row.get('fastest_lap'),
                'q1': row.get('q1'),
                'q2': row.get('q2'),
                'q3': row.get('q3'),
            })

    CURRENT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_json(CURRENT_OUTPUT, current_rows)
    LOG.info('Wrote compact latest-race driver rows: %s (%d bytes)', CURRENT_OUTPUT, CURRENT_OUTPUT.stat().st_size)

    jolpica = document.get('sources', {}).get('jolpica', {})
    race_records = jolpica.get('races', {}).get('data', []) if isinstance(jolpica, dict) else []
    result_records = jolpica.get('results', {}).get('data', []) if isinstance(jolpica, dict) else []
    f1 = document.get('sources', {}).get('formula1_com', {})
    f1_results_ok = bool(f1.get('race_results', {}).get('ok')) if isinstance(f1, dict) else False
    core_data_saved = bool(race_records or result_records or f1_results_ok)
    if not core_data_saved:
        LOG.error('JSON was written, but no race calendar/results data was found in the core sources.')
        return 1
    if not race_records:
        LOG.warning('Jolpica race metadata is empty; workflow succeeds because other core race data was saved.')
    LOG.info('Core race data saved successfully.')
    return 0

if __name__=='__main__': raise SystemExit(main())
