#!/usr/bin/env python3
"""Build only the current public company files; never publish maintenance/tests/.git."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

PUBLIC_FILES = ('CNAME','index.html','og.png','privacy.html','robots.txt','sentiment/index.html','sentiment/dashboard-utils.js','sentiment/data.json','sentiment/gdelt.json','sentiment/x_sample.json')

def build(root, output, manifest=None):
    root=root.resolve();output=output.resolve()
    if output==root or root.is_relative_to(output):
        raise ValueError('Output must not contain the source repository')
    if manifest:
        manifest=manifest.resolve()
        if manifest.is_relative_to(output):raise ValueError('Manifest must remain outside the public artifact')
    # Validate the whole allowlist before replacing an earlier build.
    for name in PUBLIC_FILES:
        source=root/name
        if not source.is_file() or source.is_symlink() or source.stat().st_size==0:
            raise ValueError('Missing, empty or linked public file: '+name)
    if output.exists():shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in PUBLIC_FILES:
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,target)
    entries={name:{'bytes':(output/name).stat().st_size,'sha256':hashlib.sha256((output/name).read_bytes()).hexdigest()} for name in PUBLIC_FILES}
    if manifest:
        manifest.write_text(json.dumps({'files':{n:i for n,i in entries.items() if n!='CNAME'},'hosting_configuration':{'CNAME':entries['CNAME']},'note':'CNAME is retained in the artifact as hosting configuration. The host does not expose /CNAME as a public route.'},indent=2)+'\n')
    print('Prepared '+str(len(entries))+' public files')
    return entries

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--output',type=Path,default=Path('_site'));p.add_argument('--manifest',type=Path,default=Path('company-artifact-manifest.json'));a=p.parse_args();build(a.root,a.output,a.manifest)
