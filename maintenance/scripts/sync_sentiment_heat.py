#!/usr/bin/env python3
"""Copy a verified public events heat release into this repository; never refresh polls."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo
from site_health import BASE, fetch, json_body, require, validate_release, validate_heat, verify_file, iso, sha


def verified_heat(release_response, heat_response, confirm_response, today):
    release=json_body(release_response)
    through,_=validate_release(release,today)
    require(through>=today-timedelta(days=1),f'Events inputs only through {through}; retain current company heat')
    require(release==json_body(confirm_response),'Events release changed during download; retain current company heat and retry')
    require(heat_response.status==200,'Heat download HTTP '+str(heat_response.status))
    verify_file(release,'sentiment-gdelt.json',heat_response.body)
    data=json.loads(heat_response.body);first,last=validate_heat(data)
    require(last<=through,'Heat event date exceeds input coverage')
    require('heat' in release['coverage'],'Release must declare the actual heat event window')
    window=release['coverage']['heat'];require((iso(window['from']),iso(window['to']))==(first,last),'Release heat window differs from file')
    return data,release


def sync(destination,base=BASE):
    today=datetime.now(ZoneInfo('Europe/London')).date();prefix=base.rstrip('/')+'/nigeria-activity-map/'
    release=fetch(prefix+'release.json');heat=fetch(prefix+'sentiment-gdelt.json');confirm=fetch(prefix+'release.json')
    data,meta=verified_heat(release,heat,confirm,today)
    changed=not destination.exists() or destination.read_bytes()!=heat.body
    if changed:
        destination.parent.mkdir(parents=True,exist_ok=True)
        name=None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.gdelt-',suffix='.tmp',delete=False) as f:
                name=f.name;f.write(heat.body);f.flush();os.fsync(f.fileno())
            os.replace(name,destination)
        finally:
            if name and os.path.exists(name):os.unlink(name)
    result={'changed':changed,'source_release':meta['generated_at'],'input_through':meta['coverage']['gdelt']['input_through'],'event_from':data['from'],'event_to':data['to'],'points':data['count'],'sha256':sha(heat.body)}
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('changed='+str(changed).lower()+'\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Sentiment news heat\n\n'+('Updated' if changed else 'Unchanged')+f"; inputs through **{result['input_through']}**, heat event window **{data['from']}–{data['to']}**, {data['count']} cells. Polling, X sample and assessment dates were not changed.\n")
    print(json.dumps(result,indent=2));return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-url',default=BASE);p.add_argument('--destination',type=Path,default=Path('sentiment/gdelt.json'));args=p.parse_args();sync(args.destination,args.base_url)
if __name__=='__main__':main()
