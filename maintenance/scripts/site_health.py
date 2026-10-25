#!/usr/bin/env python3
"""Public, read-only site and release checks. No account, token or assistant needed."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

BASE = 'https://rbmglobal.co.uk'
STATES = set('Abia|Adamawa|Akwa Ibom|Anambra|Bauchi|Bayelsa|Benue|Borno|Cross River|Delta|Ebonyi|Edo|Ekiti|Enugu|FCT|Gombe|Imo|Jigawa|Kaduna|Kano|Katsina|Kebbi|Kogi|Kwara|Lagos|Nasarawa|Niger|Ogun|Ondo|Osun|Oyo|Plateau|Rivers|Sokoto|Taraba|Yobe|Zamfara'.split('|'))
ZONES = {'NC','NE','NW','SE','SS','SW'}
ROUTES = {
 'company':'/', 'privacy':'/privacy.html', 'ped':'/ped-tool/beta/',
 'ped-manual':'/ped-tool/beta/manual.html', 'lz':'/lz-tool/beta/',
 'sentiment':'/sentiment/', 'sentiment-data':'/sentiment/data.json',
 'sentiment-heat':'/sentiment/gdelt.json', 'sentiment-x':'/sentiment/x_sample.json',
 'events':'/nigeria-activity-map/', 'events-png':'/nigeria-activity-map/lastweek.png',
 'events-release':'/nigeria-activity-map/release.json',
 'weekly-brief':'/weekly-threat-brief/', 'weekly-pdf':'/weekly-threat-brief/latest.pdf',
 'ped-root':'/ped-tool/', 'lz-root':'/lz-tool/', 'retired-sentiment':'/nigeria-sentiment/',
 'retired-data':'/nigeria-sentiment/data.json',
}
REDIRECTS = {'ped-root':'ped','lz-root':'lz','retired-sentiment':'sentiment'}

@dataclass
class Response:
    url: str
    status: int
    body: bytes
    content_type: str = ''


def fetch(url, attempts=3, limit=25_000_000, timeout=40):
    error = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={'User-Agent':'RBM-site-health/1.0','Cache-Control':'no-cache'})
            try:
                response = urllib.request.urlopen(request, timeout=timeout)
            except urllib.error.HTTPError as e:
                response = e
            with response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise ValueError('Response exceeds size limit')
                result = Response(response.url, response.code, body, response.headers.get('Content-Type',''))
            if result.status >= 500 or result.status == 429:
                raise ValueError('Temporary HTTP ' + str(result.status))
            return result
        except (OSError, ValueError) as e:
            error = e
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f'{url}: {error}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def iso(value):
    require(isinstance(value,str), 'Date must be a YYYY-MM-DD string')
    require(bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}',value)), 'Invalid date: ' + str(value))
    return date.fromisoformat(value)


def json_body(response):
    require(response.status == 200, 'HTTP ' + str(response.status))
    return json.loads(response.body)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class HTML(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = set(); self.links = []; self.refresh = []; self.ids = set(); self.text = []
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        self.tags.add(tag); attrs = dict(attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        if tag == 'a' and 'href' in attrs: self.links.append(attrs['href'])
        if tag == 'meta' and attrs.get('http-equiv','').lower() == 'refresh':
            match = re.search(r'url\s*=\s*[\"\']?([^\"\']+)',attrs.get('content',''),re.I)
            if match: self.refresh.append(match.group(1).strip())
    def handle_data(self, data): self.text.append(data)


def check_html(response, minimum=800):
    require(response.status == 200, 'HTTP ' + str(response.status))
    require(len(response.body) >= minimum, 'Unexpectedly short page')
    text = response.body.decode('utf-8')
    parser = HTML(text)
    require({'html','head','body'} <= parser.tags, 'Incomplete HTML document')
    require(text.strip().upper() != 'PLACEHOLDER', 'Placeholder page')
    return parser


def check_manual(response):
    # This existing document legally omits optional html/head/body tags.
    require(response.status==200, 'Manual HTTP '+str(response.status))
    text=response.body.decode('utf-8');parsed=HTML(text)
    require(len(response.body)>10000 and 'PED Field Manual' in text and 'title' in parsed.tags and 'h1' in parsed.tags, 'Missing or incomplete field manual')


def check_redirect(response, source, target):
    require(response.status == 200, 'Redirect route HTTP ' + str(response.status))
    if response.url.split('?',1)[0].rstrip('/') == target.rstrip('/'):
        return
    text = response.body.decode('utf-8'); parsed = HTML(text)
    destinations = [urllib.parse.urljoin(source,u) for u in parsed.refresh]
    require(target in destinations, 'Missing or wrong automatic redirect target')
    require(not any(x in parsed.ids for x in ['map','state-jump','inp','addCoord']), 'Retired route still contains a working application')


def validate_sentiment(data):
    require(isinstance(data,dict), 'Assessment must be an object')
    states = data.get('states',[]); zones = data.get('zones',[])
    require(len(states)==37 and {s.get('name') for s in states}==STATES, 'Expected all 37 unique states/FCT')
    require(len(zones)==6 and {z.get('id') for z in zones}==ZONES, 'Expected six unique zones')
    for s in states:
        require(s.get('zone') in ZONES and s.get('president') in {'cold','cool','mixed','warm'} and s.get('nsa') in {'cold','cool','mixed','warm'}, 'Invalid state assessment: '+str(s.get('name')))
    iso(data.get('assessment_updated',data.get('updated')))
    return data


def validate_heat(data):
    require(isinstance(data,dict), 'Heat must be an object')
    first,last=iso(data.get('from')),iso(data.get('to'))
    points=data.get('points')
    require(isinstance(points,list) and bool(points) and data.get('count')==len(points), 'Empty/mismatched heat points')
    require(first<=last, 'Reversed heat window')
    for p in points:
        require(isinstance(p,list) and len(p)==3 and all(type(v) in (int,float) and math.isfinite(v) for v in p), 'Invalid heat point')
        require(-90<=p[0]<=90 and -180<=p[1]<=180 and p[2]>=0, 'Out-of-range heat point')
    return first,last


def validate_release(release, today):
    require(isinstance(release,dict), 'Release must be an object')
    require(release.get('schema_version')==1,'Unsupported events release schema')
    generated=datetime.fromisoformat(release['generated_at'].replace('Z','+00:00'))
    require(generated.tzinfo is not None, 'Release generation timestamp needs timezone')
    require(generated.astimezone(ZoneInfo('Europe/London')).date()<=today, 'Future release timestamp')
    coverage=release['coverage']
    through=iso(coverage['gdelt']['input_through'])
    first=iso(coverage['gdelt']['input_from'])
    event_max=iso(coverage['gdelt']['max_event_date'])
    curated=iso(coverage['ucdp']['max_event_date'])
    curated_first=iso(coverage['ucdp']['min_event_date'])
    require(curated<=today and through<=today, 'Future source coverage')
    require(first<=through and event_max<=through and curated_first<=curated, 'Invalid source coverage order')
    heat_first,heat_last=iso(coverage['heat']['from']),iso(coverage['heat']['to'])
    require(heat_first<=heat_last<=through, 'Invalid declared heat window')
    for name in ['index.html','lastweek.png','sentiment-gdelt.json']:
        entry=release['files'][name]
        require(type(entry['bytes']) is int and entry['bytes']>0,'Invalid byte count '+name)
        require(bool(re.fullmatch('[a-f0-9]{64}',entry['sha256'])),'Invalid SHA256 '+name)
    return through,curated


def verify_file(release,name,body):
    info=release['files'][name]
    require(len(body)==info['bytes'] and sha(body)==info['sha256'],'Live bytes/hash differ from release: '+name)


def parse_array(text,name):
    match=re.search(r'\bconst\s+'+re.escape(name)+r'\s*=\s*',text)
    require(match is not None,'Missing '+name)
    result,_=json.JSONDecoder().raw_decode(text[match.end():])
    require(isinstance(result,list) and result,'Empty '+name)
    return result


def validate_events(text):
    data={name:parse_array(text,name) for name in ['EVENTS','GDELT_EVENTS']}
    for name,events in data.items():
        for e in events:
            iso(e['d'])
            require(all(type(e.get(k)) in (int,float) and math.isfinite(e[k]) for k in ['la','lo']), 'Invalid coordinates in '+name)
            require(-90<=e['la']<=90 and -180<=e['lo']<=180,'Coordinate range in '+name)
    return data


def check_png(body):
    require(len(body)>100_000 and body.startswith(b'\x89PNG\r\n\x1a\n'),'Missing/incomplete PNG')
    require(body[12:16]==b'IHDR','PNG missing IHDR')
    width,height=struct.unpack('>II',body[16:24]);require(width>=800 and height>=400,'Unexpected PNG dimensions')
    require(body[-12:-8]==b'\x00\x00\x00\x00' and body[-8:-4]==b'IEND','Truncated PNG')


def check_pdf(body):
    require(len(body)>5000 and body.startswith(b'%PDF-') and b'%%EOF' in body[-1024:],'Invalid/incomplete PDF')


def brief_dates(html):
    text=' '.join(HTML(html).text)
    def find(label):
        m=re.search(label+r':\s*(\d{1,2} [A-Za-z]+ \d{4})',text)
        require(m is not None,'Missing explicit '+label.lower())
        return datetime.strptime(m.group(1),'%d %B %Y').date()
    return find('Issue date'),find('Published')


def due_brief(today, hour=24):
    # The new issue is editorial work; do not mark it late before Monday 13:00 London.
    return today-timedelta(days=today.weekday() + (7 if today.weekday()==0 and hour<13 else 0))


class Report:
    def __init__(self): self.items=[]
    def add(self,name,status,detail): self.items.append({'check':name,'status':status,'detail':str(detail)})
    def check(self,name,fn):
        try:
            detail=fn();self.add(name,'PASS','Validated' if isinstance(detail,(dict,list,tuple,HTML)) else detail if detail is not None else 'Verified')
            return detail
        except Exception as e: self.add(name,'FAIL',e);return None
    @property
    def failed(self): return any(i['status']=='FAIL' for i in self.items)
    def write(self,output):
        output.mkdir(parents=True,exist_ok=True)
        (output/'site-health.json').write_text(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'checks':self.items},indent=2)+'\n')
        lines=['## Published site health','', '| Check | Result | Evidence |','|---|---|---|']
        for i in self.items: lines.append('| '+ ' | '.join(str(i[k]).replace('|','\\|').replace('\n',' ') for k in ['check','status','detail'])+' |')
        summary='\n'.join(lines)+'\n';(output/'summary.md').write_text(summary)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(summary)
        for i in self.items:
            if i['status'] in {'FAIL','WARN'}:
                level='error' if i['status']=='FAIL' else 'warning'
                message=f"{i['check']}: {i['detail']}".replace('%','%25').replace('\r','%0D').replace('\n','%0A')
                print(f'::{level}::{message}')
        print(json.dumps({'failed':self.failed,'checks':len(self.items)}))


def run(base=BASE,today=None):
    now=datetime.now(ZoneInfo('Europe/London'));today=today or now.date()
    report=Report();responses={}
    def get(item):
        key,path=item
        try:return key,fetch(base+path)
        except Exception as e:return key,e
    with ThreadPoolExecutor(max_workers=5) as pool:
        for key,value in pool.map(get,ROUTES.items()):
            if isinstance(value,Exception):report.add(key,'FAIL',value)
            else:responses[key]=value
    for key,response in responses.items():
        if key=='retired-data':
            report.check(key,lambda r=response:require(r.status in {404,410},'Retired data must not be served: HTTP '+str(r.status)))
        elif key in REDIRECTS:
            report.check(key,lambda r=response,k=key:check_redirect(r,base+ROUTES[k],base+ROUTES[REDIRECTS[k]]))
        elif key not in {'sentiment-data','sentiment-heat','sentiment-x','events-release','events-png','weekly-pdf'}:
            report.check(key,lambda r=response,k=key:check_manual(r) if k=='ped-manual' else check_html(r) and 'HTTP 200; complete HTML structure')
            expected={'ped':{'map','addCoord','saveProjBtn'},'lz':{'inp','fixcard','build'},'sentiment':{'state-jump','panel','chk-gdelt'},'events':{'kw','fState','nEvents','rwToggle'}}.get(key)
            if expected:report.check(key+' controls',lambda r=response,e=expected:require(e<=HTML(r.body.decode()).ids,'Required application controls missing'))
            if key in {'company','privacy'}:
                report.check(key+' identity',lambda r=response,k=key:require(('privacy' if k=='privacy' else 'rbm') in r.body.decode().lower(),'Wrong page identity'))
    data=report.check('sentiment structure',lambda:validate_sentiment(json_body(responses['sentiment-data'])))
    if data:
        report.add('assessment date','INFO',str(data.get('assessment_updated',data.get('updated')))+'; retained evidence date, not automatically stale')
    heat=report.check('sentiment heat',lambda:validate_heat(json_body(responses['sentiment-heat'])))
    if heat:report.add('heat event window','INFO',f'{heat[0]} to {heat[1]}; event window is separate from daily input coverage')
    def historical():
        x=json_body(responses['sentiment-x']);require(bool(x.get('window')) and bool(x.get('caveat')),'Missing historical X date/caveat')
        require('historical' in responses['sentiment'].body.decode().lower() and 'unverified' in responses['sentiment'].body.decode().lower(),'Historical X caveat not visible')
        return x['window']+'; historical, not a fresh sample'
    report.check('historical X caveat',historical)
    report.check('events PNG',lambda:check_png(responses['events-png'].body))
    report.check('weekly PDF',lambda:check_pdf(responses['weekly-pdf'].body))
    release=report.check('events release metadata',lambda:json_body(responses['events-release']))
    coverage=report.check('release schema/dates',lambda:validate_release(release,today)) if release else None
    events=report.check('embedded event data',lambda:validate_events(responses['events'].body.decode()))
    if release and coverage:
        through,curated=coverage
        report.check('GDELT input coverage',lambda:require(through>=today-timedelta(days=1),f'Inputs through {through}; required through {today-timedelta(days=1)}. Retry publisher; do not replace with empty data.') or f"Inputs {release['coverage']['gdelt']['input_from']} through {through}; latest event {release['coverage']['gdelt']['max_event_date']}")
        report.add('UCDP source lag','WARN' if (today-curated).days>90 else 'INFO',f'Events through {curated}, {(today-curated).days} days old. Monthly release lag is expected; this is not GDELT ingestion coverage.')
        for warning in release.get('warnings',[]):
            report.add('publisher source warning','WARN',warning)
        if 'reliefweb' in release['coverage']:
            def reliefweb():
                meta=release['coverage']['reliefweb'];items=parse_array(responses['events'].body.decode(),'RELIEFWEB_REPORTS')
                require(len(items)==meta['count'],'ReliefWeb report count differs from release')
                require(max(iso(x['d']) for x in items)==iso(meta['latest_publication']),'ReliefWeb publication date differs from release')
                first,last=iso(meta['from']),iso(meta['to'])
                require(first<=last<=today,'Invalid ReliefWeb input window')
                if last<today-timedelta(days=2):report.add('ReliefWeb retained source','WARN',f'Last checked window ends {last}; review publisher warning/source availability. Latest publication date is a separate measure.')
                return f"Window {first} to {last}; latest publication {meta['latest_publication']}; {len(items)} reports; refresh {meta.get('refresh','unspecified')}"
            report.check('ReliefWeb source window',reliefweb)
        for key,name in [('events','index.html'),('events-png','lastweek.png')]:
            report.check('release hash '+name,lambda k=key,n=name:verify_file(release,n,responses[k].body))
        report.check('sentiment heat matches events release',lambda:verify_file(release,'sentiment-gdelt.json',responses['sentiment-heat'].body))
        if events:
            report.check('UCDP date agrees with metadata',lambda:require(max(iso(e['d']) for e in events['EVENTS'])==curated,'UCDP maximum date differs from release metadata'))
            report.check('event dates within input coverage',lambda:require(max(iso(e['d']) for e in events['GDELT_EVENTS'])<=through,'GDELT event dates exceed input coverage'))
            report.check('GDELT maximum date agrees with metadata',lambda:require(max(iso(e['d']) for e in events['GDELT_EVENTS'])==iso(release['coverage']['gdelt']['max_event_date']),'GDELT maximum date differs from release metadata'))
        if heat:
            report.check('heat within source coverage',lambda:require(heat[1]<=through,'Heat maximum event date exceeds ingested inputs'))
            if 'heat' in release['coverage']:
                report.check('heat window matches release',lambda:require((iso(release['coverage']['heat']['from']),iso(release['coverage']['heat']['to']))==heat,'Heat window differs from release metadata'))
        # Catch a deployment changing underneath these checks. Retry the entire check, not individual mismatched assets.
        report.check('release stable during check',lambda:require(json_body(fetch(base+ROUTES['events-release']))==release,'Events release changed during checks; rerun against one complete release'))
    def weekly():
        r=responses['weekly-brief'];issue,published=brief_dates(r.body.decode())
        require(issue<=today and published<=today,'Future issue/publication date')
        parser=HTML(r.body.decode());pdfs=[u for u in parser.links if re.search(r'\d{4}-\d{2}-\d{2}\.pdf$',u)]
        require(pdfs,'Missing dated PDF link');pdf_url=urllib.parse.urljoin(r.url,pdfs[0]);require(urllib.parse.urlparse(pdf_url).netloc==urllib.parse.urlparse(base).netloc,'Unexpected external dated PDF')
        dated=fetch(pdf_url);require(dated.status==200,'Dated PDF unavailable');check_pdf(dated.body)
        require(sha(dated.body)==sha(responses['weekly-pdf'].body),'latest.pdf differs from issue PDF')
        archive=[u for u in parser.links if re.search(r'archive-\d{4}-\d{2}-\d{2}\.html$',u)]
        require(archive,'Missing archive links')
        for url in archive:
            target=urllib.parse.urljoin(r.url,url);require(urllib.parse.urlparse(target).netloc==urllib.parse.urlparse(base).netloc,'External archive URL');check_html(fetch(target))
        if issue<due_brief(today,now.hour):report.add('weekly editorial review','WARN',f'Issue {issue}; expected at least {due_brief(today,now.hour)}. Source-reviewed catch-up required; do not generate a synthetic brief.')
        return f'Issue {issue}; published {published}; dated PDF and {len(archive)} archive links verified'
    report.check('weekly issue and archives',weekly)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-url',default=BASE);p.add_argument('--output',type=Path,default=Path('health-output'));args=p.parse_args()
    report=run(args.base_url.rstrip('/'));report.write(args.output);raise SystemExit(1 if report.failed else 0)
if __name__=='__main__':main()
