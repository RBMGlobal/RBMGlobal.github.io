#!/usr/bin/env python3
"""Verify every allowlisted artifact byte against the live site after Pages deployment."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
import urllib.parse
from site_health import BASE, fetch, require, sha


def validate_manifest(manifest):
    require(isinstance(manifest,dict) and manifest.get('files'), 'Empty company artifact manifest')
    for name,info in manifest['files'].items():
        require(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name,'Unsafe manifest path')
        require(type(info['bytes']) is int and info['bytes']>=0 and len(info['sha256'])==64,'Invalid file metadata')
    return manifest


def verify(manifest,base=BASE,timeout=420,interval=10):
    manifest=validate_manifest(manifest);deadline=time.monotonic()+timeout
    remaining=dict(manifest['files']);verified={};attempt=0
    def one(item):
        name,info=item
        try:
            url=base.rstrip('/')+'/'+urllib.parse.quote(name,safe='/')+'?rbm_verify='+str(time.time_ns())
            response=fetch(url,attempts=1,timeout=min(40,max(0.1,deadline-time.monotonic())))
            require(response.status==200,'HTTP '+str(response.status))
            require(len(response.body)==info['bytes'] and sha(response.body)==info['sha256'],'Bytes/hash differ')
            return name,None
        except Exception as error:return name,str(error)
    failures={}
    while remaining:
        attempt+=1
        with ThreadPoolExecutor(max_workers=5) as pool:
            for name,error in pool.map(one,remaining.items()):
                if error:failures[name]=error
                else:verified[name]=remaining[name];failures.pop(name,None)
        remaining={n:i for n,i in remaining.items() if n not in verified}
        if not remaining:break
        print(f'Live verification attempt {attempt}: {len(verified)} matched, {len(remaining)} waiting',flush=True)
        if time.monotonic()>=deadline:raise ValueError('Live company artifact not verified: '+json.dumps(failures,sort_keys=True))
        time.sleep(min(interval,max(0,deadline-time.monotonic())))
    summary='## Company deployment byte verification\n\nVerified all **'+str(len(verified))+'** public files against the complete Pages artifact (size and SHA-256).\n\n'+ '\n'.join('- `'+name+'`: `'+info['sha256']+'`' for name,info in sorted(verified.items()))+'\n'
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(summary)
    print(summary);return verified


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,default=Path('company-artifact-manifest.json'));p.add_argument('--base-url',default=BASE);p.add_argument('--timeout',type=int,default=420);a=p.parse_args()
    verify(json.loads(a.manifest.read_text()),a.base_url,a.timeout)
