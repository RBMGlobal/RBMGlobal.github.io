#!/usr/bin/env python3
"""Build only the current public company files; never publish maintenance/tests/.git."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re

PUBLIC_FILES = ('CNAME','index.html','og.png','privacy.html','robots.txt','sentiment/index.html','sentiment/dashboard-utils.js','sentiment/evidence-panel.js','sentiment/data.json','sentiment/gdelt.json','sentiment/x_sample.json')

def public_files(root):
    names = list(PUBLIC_FILES)
    if (root/'sentiment/review.json').exists():
        names.append('sentiment/review.json')
    packets=root/'research/public/weekly'
    if packets.exists():
        if packets.is_symlink():raise ValueError('Linked public research directory')
        for path in packets.rglob('*'):
            if path.is_symlink():raise ValueError('Linked public research file')
            if path.is_file():
                name=str(path.relative_to(root))
                if not re.fullmatch(r'research/public/weekly/(latest\.json|\d{4}-\d{2}-\d{2}/(candidate\.json|sources\.json|attestation\.json|events-release\.json|lastweek\.png))',name):
                    raise ValueError('Unexpected public research file: '+name)
                names.append(name)
    return tuple(names)

def build(root, output, manifest=None):
    root=root.resolve();output=output.resolve()
    if output==root or root.is_relative_to(output):
        raise ValueError('Output must not contain the source repository')
    if manifest:
        manifest=manifest.resolve()
        if manifest.is_relative_to(output):raise ValueError('Manifest must remain outside the public artifact')
    # Validate the whole allowlist before replacing an earlier build.
    names=public_files(root)
    for name in names:
        source=root/name
        if not source.is_file() or source.is_symlink() or source.stat().st_size==0:
            raise ValueError('Missing, empty or linked public file: '+name)
    if output.exists():shutil.rmtree(output)
    output.mkdir(parents=True)
    for name in names:
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,target)
    entries={name:{'bytes':(output/name).stat().st_size,'sha256':hashlib.sha256((output/name).read_bytes()).hexdigest()} for name in names}
    if manifest:
        manifest.write_text(json.dumps({'files':{n:i for n,i in entries.items() if n!='CNAME'},'hosting_configuration':{'CNAME':entries['CNAME']},'note':'CNAME is retained in the artifact as hosting configuration. The host does not expose /CNAME as a public route.'},indent=2)+'\n')
    print('Prepared '+str(len(entries))+' public files')
    return entries

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--output',type=Path,default=Path('_site'));p.add_argument('--manifest',type=Path,default=Path('company-artifact-manifest.json'));a=p.parse_args();build(a.root,a.output,a.manifest)
