"""Trusted publication adapters: stable public receipts, exact bytes, narrow writes."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time
import requests

EVENTS = 'https://rbmglobal.co.uk/nigeria-activity-map/'
WEEKLY = 'https://rbmglobal.co.uk/research/public/weekly/'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def get(url, limit=12_000_000, missing_ok=False):
    session = requests.Session()
    session.trust_env = False
    with session.get(url, timeout=(15, 45), stream=True, allow_redirects=False,
                     headers={'Cache-Control': 'no-cache'}) as response:
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ValueError('Public artifact request failed: ' + url)
        data = bytearray()
        for block in response.iter_content(65536):
            data.extend(block)
            if len(data) > limit:
                raise ValueError('Public artifact exceeds expected size')
        return bytes(data)


def equal(data, record):
    if (not isinstance(data, bytes) or not isinstance(record, dict)
            or set(record) != {'sha256', 'bytes'} or type(record['bytes']) is not int
            or record['bytes'] < 0 or not isinstance(record['sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])
            or len(data) != record['bytes'] or sha(data) != record['sha256']):
        raise ValueError('Public artifact does not match its accepted receipt')


def unlinked(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    relative = path.relative_to(root)
    if '..' in relative.parts:
        raise ValueError('Publication path escapes its workspace')
    candidate = root
    for name in ('', *relative.parts):
        candidate = candidate / name
        if candidate.is_symlink():
            raise ValueError('Linked publication path')


def verify_accepted_weekly(root):
    """A redeploy must validate the saved generated release, not just its date."""
    root = Path(root)
    path = root / 'brief-release.json'
    if not path.exists():
        return None  # Legacy site before the first automated brief is retained.
    unlinked(path, root)
    release = json.loads(path.read_text())
    issue = release['issue_date']
    date.fromisoformat(issue)
    files = release['files']
    required = {'index.html', 'latest.pdf', f'archive-{issue}.html',
                f'Nigeria_Weekly_Brief_{issue}.pdf'}
    if release.get('schema_version') != 1 or not required <= set(files):
        raise ValueError('Accepted weekly release is incomplete')
    for name, record in files.items():
        if not re.fullmatch(r'(index\.html|latest\.pdf|archive-\d{4}-\d{2}-\d{2}\.html|Nigeria_Weekly_Brief_\d{4}-\d{2}-\d{2}\.pdf|map-\d{4}-\d{2}-\d{2}\.png)', name):
            raise ValueError('Accepted weekly release contains unexpected paths')
        source = root / name
        unlinked(source, root)
        equal(source.read_bytes(), record)
    return release


def fetch_events(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    before = get(EVENTS + 'release.json')
    release = json.loads(before)
    image = get(EVENTS + 'lastweek.png')
    after = get(EVENTS + 'release.json')
    if before != after:
        raise ValueError('Events release changed during capture')
    equal(image, release['files']['lastweek.png'])
    (output / 'release.json').write_bytes(before)
    (output / 'lastweek.png').write_bytes(image)
    return output / 'release.json', output / 'lastweek.png'


def fetch_weekly(output):
    output = Path(output)
    before = get(WEEKLY + 'latest.json', missing_ok=True)
    if before is None:
        return None
    latest = json.loads(before)
    if set(latest) != {'schema_version', 'issue_date', 'files'} or latest['schema_version'] != 1:
        raise ValueError('Unrecognised approved weekly receipt')
    issue = latest['issue_date']
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', issue):
        raise ValueError('Invalid approved issue path')
    date.fromisoformat(issue)
    names = {'candidate.json', 'sources.json', 'attestation.json', 'events-release.json', 'lastweek.png'}
    if set(latest['files']) != names:
        raise ValueError('Approved packet must be complete and contain only public evidence')
    payload = {}
    for name in sorted(names):
        data = get(WEEKLY + issue + '/' + name)
        equal(data, latest['files'][name])
        payload[name] = data
    if get(WEEKLY + 'latest.json') != before:
        raise ValueError('Approved weekly receipt changed during capture')
    if json.loads(payload['candidate.json'])['issue_date'] != issue:
        raise ValueError('Candidate and public receipt issue disagree')
    output.mkdir(parents=True, exist_ok=False)
    for name, data in payload.items():
        (output / name).write_bytes(data)
    (output / 'receipt.json').write_bytes(before)
    return latest


def apply_company(plan_dir, root):
    plan_dir, root = Path(plan_dir), Path(root)
    plan = json.loads((plan_dir / 'plan.json').read_text())
    if sha((root / 'sentiment/data.json').read_bytes()) != plan['baseline_sha256']:
        raise ValueError('Sentiment baseline changed; research must be reviewed again')
    pending = []
    for name, record in plan['files'].items():
        allowed = name in ('sentiment/data.json', 'sentiment/review.json') or bool(re.fullmatch(
            r'research/public/weekly/(latest\.json|\d{4}-\d{2}-\d{2}/(candidate\.json|sources\.json|attestation\.json|events-release\.json|lastweek\.png))', name))
        if not allowed:
            raise ValueError('Research cannot write this repository path')
        path = plan_dir / 'files' / name
        unlinked(path, plan_dir)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or linked publication source')
        data = path.read_bytes()
        equal(data, record)
        target = root / name
        if any(p.is_symlink() for p in [target, *target.parents]):
            raise ValueError('Linked publication destination')
        if '/weekly/' in name and not name.endswith('/latest.json') and target.exists() and target.read_bytes() != data:
            raise ValueError('An approved dated packet is immutable')
        pending.append((target, data))
    for target, data in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def public_weekly_snapshot(root, output):
    from brief.build_brief import accepted_snapshot
    root, output = Path(root), Path(output)
    verify_accepted_weekly(root)
    files = accepted_snapshot(root)
    output.mkdir(parents=True, exist_ok=False)
    for name in files:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
        equal(target.read_bytes(), files[name])
    if accepted_snapshot(root) != files:
        raise ValueError('Accepted weekly site changed while staging the artifact')
    manifest = {'files': {name: record for name, record in files.items() if name not in ('.nojekyll', 'CNAME')}}
    return manifest


def consume_weekly(root, work):
    from brief.build_brief import build, previous_issue
    root, work = Path(root), Path(work)
    packet = work / 'approved'
    receipt = fetch_weekly(packet)
    if receipt is None:
        raise ValueError('No approved AI weekly packet exists; previous published brief preserved')
    prior, _ = previous_issue(root)
    verify_accepted_weekly(root)
    if receipt['issue_date'] <= prior.isoformat():
        if receipt['issue_date'] == prior.isoformat():
            release_path = root / 'brief-release.json'
            if not release_path.is_file() or json.loads(release_path.read_text())['packet_sha256'] != sha((packet / 'candidate.json').read_bytes()):
                raise ValueError('Existing issue does not match approved packet')
        return {'status': 'unchanged', 'issue_date': prior.isoformat()}
    package = work / 'package'
    result = build(packet / 'candidate.json', packet / 'attestation.json', packet / 'sources.json',
                   None, root, packet / 'events-release.json', packet / 'lastweek.png', package,
                   verified_public_input=True)
    changes = json.loads((package / 'changes.json').read_text())['files']
    # Generated code owns paths; model text is never a filesystem destination.
    for name, record in changes.items():
        if not re.fullmatch(r'(index\.html|latest\.pdf|brief-release\.json|archive-\d{4}-\d{2}-\d{2}\.html|Nigeria_Weekly_Brief_\d{4}-\d{2}-\d{2}\.pdf|map-\d{4}-\d{2}-\d{2}\.png)', name):
            raise ValueError('Unexpected weekly publication destination')
        equal((package / 'site' / name).read_bytes(), record)
        unlinked(root / name, root)
    for name in changes:
        shutil.copyfile(package / 'site' / name, root / name)
    return {'status': 'validated', 'issue_date': result['issue_date'], 'validation': result['validation']}


def verify_live(manifest, base, timeout=420):
    deadline = time.monotonic() + timeout
    while True:
        remaining = {}
        # Require a complete matching pass, not separate successes accumulated
        # across a changing CDN deployment.
        for name, record in manifest['files'].items():
            try:
                equal(get(base + name), record)
            except (ValueError, requests.RequestException):
                remaining[name] = record
        if not remaining:
            return
        if time.monotonic() > deadline:
            raise ValueError('Live byte verification failed: ' + ', '.join(remaining))
        time.sleep(15)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['apply-company', 'consume-weekly', 'build-weekly', 'verify-weekly'])
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--work', type=Path, required=True)
    a = p.parse_args()
    if a.mode == 'apply-company':
        apply_company(a.work, a.root)
    elif a.mode == 'consume-weekly':
        a.work.mkdir(parents=True, exist_ok=True)
        result = consume_weekly(a.root, a.work)
        (a.work / 'run-summary.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    elif a.mode == 'build-weekly':
        manifest = public_weekly_snapshot(a.root, a.work)
        Path('weekly-artifact-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    else:
        verify_live(json.loads(Path('weekly-artifact-manifest.json').read_text()), 'https://rbmglobal.co.uk/weekly-threat-brief/')
