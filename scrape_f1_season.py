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
OUTPUT = Path(os.getenv('F1_OUTPUT_FILE', f'f1-{SEASON}.json'))
CALENDAR_URL = os.getenv('F1_CALENDAR_URL', 'https://files-f1.motorsportcalendars.com/f1-calendar_p1_p2_p3_qualifying_sprint_gp.ics')
JOLPICA = 'https://api.jolpi.ca/ergast/f1'
F1_BASE = f'https://www.formula1.com/en/results/{SEASON}/races'
START_AFTER = timedelta(minutes=90)
CUTOFF_AFTER = timedelta(minutes=150)
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
    # RaceTable endpoints have nested Race objects, with results in the race.
    return table.get('Races',[]) if key=='RaceTable' else []

def merge_page_records(existing, incoming):
    """Merge paginated Ergast responses, whose rows are nested inside race objects."""
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
    # De-duplicate rows in case an API page boundary repeats a record.
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
        # Ergast-style endpoints paginate the nested rows (e.g. Laps), not the
        # outer RaceTable wrapper. Therefore compare the offset with MRData.total.
        if not records or offset + limit >= total: break
        offset += limit
        time.sleep(DELAY)
    return {'source':'Jolpica API','urls':source_urls,'count':sum(len(r.get('Laps',[])) + len(r.get('PitStops',[])) for r in all_records) if path.endswith(('/laps','/pitstops')) else len(all_records),'data':all_records}

def fetch_jolpica_season(session):
    # Core reference tables and all season-level event/session tables.
    specs=[('seasons','seasons','SeasonTable'),('circuits','circuits','CircuitTable'),(f'{SEASON}/races',f'{SEASON}/races','RaceTable'),(f'{SEASON}/constructors',f'{SEASON}/constructors','ConstructorTable'),(f'{SEASON}/drivers',f'{SEASON}/drivers','DriverTable'),(f'{SEASON}/results',f'{SEASON}/results','RaceTable'),(f'{SEASON}/sprint',f'{SEASON}/sprint','RaceTable'),(f'{SEASON}/qualifying',f'{SEASON}/qualifying','RaceTable'),(f'{SEASON}/driverstandings',f'{SEASON}/driverstandings','StandingsTable'),(f'{SEASON}/constructorstandings',f'{SEASON}/constructorstandings','StandingsTable'),('status','status','StatusTable')]
    data={}
    for name,path,key in specs:
        LOG.info('Jolpica: %s',name)
        try: data[name]=fetch_paginated(session,path,key)
        except Exception as e:
            LOG.warning('Jolpica %s failed: %s',name,e); data[name]={'source':'Jolpica API','urls':[api_url(path)],'count':0,'data':[],'error':f'{type(e).__name__}: {e}'}
        time.sleep(DELAY)
    races=data.get('races',{}).get('data',[])
    # Laps and pit stops are indexed by round. Collect every round unless
    # --skip-round-details is used for a faster local smoke test.
    data['pitstops_by_round']={}; data['laps_by_round']={}
    if SKIP_ROUND_DETAILS:
        data['round_details_skipped'] = True
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
    return data

def _ics_unescape(value):
    return value.replace(r'\n', '\n').replace(r'\N', '\n').replace(r'\,', ',').replace(r'\;', ';').replace(r'\\', '\\')

def _parse_ics_datetime(value, tzid=None):
    value = value.strip()
    if len(value) == 8:  # Date-only entries are not race start times.
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
    """Read the small subset of iCalendar needed for race-event selection."""
    r=session.get(CALENDAR_URL,timeout=TIMEOUT); r.raise_for_status()
    # Unfold RFC 5545 continuation lines before splitting event properties.
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
    completed=[e for e in events if e['start_utc']+START_AFTER<=now]
    if RACE_FILTER:
        matches=[e for e in completed if norm(RACE_FILTER) in norm(e['summary']) or norm(RACE_FILTER) in norm(race_name(e['summary']))]
        return max(matches,key=lambda x:x['start_utc']) if matches else None
    if FORCE:
        candidates=[e for e in events if e['start_utc']+START_AFTER<=now]
        return max(candidates,key=lambda x:x['start_utc']) if candidates else None
    candidates=[e for e in events if e['start_utc']+START_AFTER<=now<=e['start_utc']+CUTOFF_AFTER]
    return max(candidates,key=lambda x:x['start_utc']) if candidates else None

def parse_f1_table(html):
    soup=BeautifulSoup(html,'html.parser'); table=soup.select_one('#results-table table') or soup.find('table')
    if not table:return {'ok':False,'headers':[],'rows':[],'error':'No results table found.'}
    headers=[clean(th.get_text(' ',strip=True)).rstrip('.').lower() for th in table.select('thead th')]; rows=[]
    for tr in table.select('tbody tr'):
        cells=tr.find_all('td',recursive=False); vals=[]
        for i,c in enumerate(cells):
            if i<len(headers) and headers[i]=='driver':
                a=c.select_one('span.max-lg\\:hidden'); b=c.select_one('span.max-md\\:hidden')
                vals.append(clean((a.get_text(' ',strip=True) if a else '')+' '+(b.get_text(' ',strip=True) if b else '')) or clean(c.get_text(' ',strip=True)))
            else: vals.append(clean(c.get_text(' ',strip=True)))
        if not any(vals):continue
        row={ (headers[i].replace(' / ','_').replace(' ','_').replace('-','_') if i<len(headers) and headers[i] else f'column_{i+1}'):v for i,v in enumerate(vals)}
        row['position']=row.get('pos'); row['driver_number']=row.get('no'); rows.append(row)
    return {'ok':bool(rows),'headers':headers,'rows':rows,'error':None if rows else 'No rows found.'}
def scrape_f1_page(session,url):
    try:
        r=session.get(url,timeout=TIMEOUT); r.raise_for_status(); out=parse_f1_table(r.text); out.update({'url':url,'status_code':r.status_code,'source':'Formula1.com'}); return out
    except Exception as e:return {'url':url,'source':'Formula1.com','status_code':None,'ok':False,'headers':[],'rows':[],'error':f'{type(e).__name__}: {e}'}
def fetch_formula1_current(session,event):
    summary=event['summary'].lower(); match=next(((k,v) for k,v in sorted(RACE_URLS.items(),key=lambda p:len(p[0]),reverse=True) if k in summary),None)
    if not match:return {'ok':False,'error':f'No Formula1.com race mapping for {event["summary"]}'}
    race_id,slug=match[1]; base=f'{F1_BASE}/{race_id}/{slug}'
    result=scrape_f1_page(session,base+'/race-result'); time.sleep(DELAY)
    grid=scrape_f1_page(session,base+'/starting-grid')
    if not grid['ok']:
        time.sleep(DELAY); qualifying=scrape_f1_page(session,base+'/qualifying')
        grid={'source':'Formula1.com qualifying fallback','url':qualifying['url'],'ok':qualifying['ok'],'headers':qualifying['headers'],'rows':qualifying['rows'],'error':qualifying['error']} if qualifying['ok'] else {'source':'Formula1.com starting-grid','ok':False,'rows':[],'error':'Starting grid and qualifying unavailable.'}
    return {'race_id':race_id,'slug':slug,'race_url':base,'race_results':result,'starting_grid':grid}

def main(argv=None):
    global SEASON, OUTPUT, F1_BASE, FORCE, SKIP_ROUND_DETAILS, RACE_FILTER
    parser = argparse.ArgumentParser(description='Collect F1 season data into one JSON file.')
    parser.add_argument('--season', type=int, default=SEASON, help='season year (default: %(default)s)')
    parser.add_argument('--output', type=Path, default=OUTPUT, help='output JSON path (default: %(default)s)')
    parser.add_argument('--force', action='store_true', help='scrape the latest completed Grand Prix outside the automatic race window')
    parser.add_argument('--race', help='select a completed Grand Prix by name, e.g. United States Grand Prix')
    parser.add_argument('--skip-round-details', action='store_true', help='skip per-round lap and pit-stop endpoints (faster smoke test)')
    args = parser.parse_args(argv)
    SEASON = args.season
    OUTPUT = args.output
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
        LOG.info('Outside the post-race window; no data-source requests made. Use workflow_dispatch force=true to test.')
        return 0
    LOG.info('Selected race: %s at %s',event['summary'],event['start_utc'].isoformat())
    document={'schema_version':1,'season':SEASON,'generated_at_utc':now.isoformat(),'race_window':{'calendar_event':event['summary'],'scheduled_start_utc':event['start_utc'].isoformat(),'opens_utc':(event['start_utc']+START_AFTER).isoformat(),'closes_utc':(event['start_utc']+CUTOFF_AFTER).isoformat(),'forced':FORCE},'sources':{}}
    # Fetch all Jolpica resources listed by the user into a single, predictable object.
    try: document['sources']['jolpica']=fetch_jolpica_season(session)
    except Exception as e: document['sources']['jolpica']={'error':f'{type(e).__name__}: {e}'}; LOG.exception('Jolpica fetch failed')
    document['sources']['formula1_com']=fetch_formula1_current(session,event)
    # Convenient derived summary joins Jolpica classification, fastest laps and F1.com rows.
    jol=document['sources'].get('jolpica',{})
    rounds=jol.get('races',{}).get('data',[]) if isinstance(jol,dict) else []
    target=norm(race_name(event['summary']))
    matched=next((r for r in rounds if norm(r.get('raceName',''))==target),None)
    if matched:
        round_no=str(matched.get('round'))
        result_races=jol.get('results',{}).get('data',[])
        race_results=next((r for r in result_races if str(r.get('round'))==round_no),None)
        qual_races=jol.get('qualifying',{}).get('data',[])
        sprint_races=jol.get('sprint',{}).get('data',[])
        document['current_race']={'round':round_no,'race_name':matched.get('raceName'),'race_metadata':matched,'results':race_results,'qualifying':next((r for r in qual_races if str(r.get('round'))==round_no),None),'sprint':next((r for r in sprint_races if str(r.get('round'))==round_no),None),'pitstops':jol.get('pitstops_by_round',{}).get(round_no),'laps':jol.get('laps_by_round',{}).get(round_no)}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT,document)
    LOG.info('Wrote one consolidated JSON file: %s (%d bytes)',OUTPUT,OUTPUT.stat().st_size)

    # Do not make the whole workflow fail just because an optional source or a
    # summary count is missing. Jolpica can return useful data even when its
    # metadata total/count is absent or zero; check the actual saved records.
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
