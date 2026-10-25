#!/usr/bin/env python3
"""Validate a verified research packet and stage a complete weekly publication.

No network, credentials, source ingestion, repository writes or publication.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
from html import escape
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit

from PIL import Image as PILImage
import pdfplumber
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ZONES = ('North East', 'North Central', 'North West', 'South East', 'South West', 'South South')
NUMBER = re.compile(r'(?<![\w])\d+(?:[.,]\d+)*(?:%)?')
BRIEF_URL = 'https://rbmglobal.co.uk/weekly-threat-brief/'
MAP_URL = 'https://rbmglobal.co.uk/nigeria-activity-map/'
TITLE = 'Nigeria - Weekly Threat Brief'
AI_LABEL = 'AI-generated, source-checked overview. Automated review does not replace independent field verification.'
MONTHS = ('January February March April May June July August September October November December').split()
GAP = 'No separately verified development selected. This does not establish absence of incidents.'
LIMIT = ('A selected, source-based review, not an exhaustive incident census. '
         'Source publication, event, reporting, issue and publication dates have different meanings.')
TYPOGRAPHY = str.maketrans({'\u2013': '-', '\u2014': '-', '\u2011': '-', '\u2010': '-',
                          '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
                          '\u00a0': ' ', '\u2026': '...'})


class InvalidBrief(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidBrief(message)


def fields(value, allowed, label):
    require(isinstance(value, dict) and set(value) <= set(allowed), label + ' contains unsupported fields')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    def pairs(items):
        obj = {}
        for key, value in items:
            require(key not in obj, 'Duplicate JSON key: ' + key)
            obj[key] = value
        return obj
    return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')


def day(value, name):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), name + ' must be YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidBrief(name + ' is not a valid date') from error


def instant(value, name):
    require(isinstance(value, str), name + ' must be a timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise InvalidBrief(name + ' is not a valid timestamp') from error
    require(result.tzinfo is not None, name + ' must include timezone')
    return result.astimezone(timezone.utc)


def text(value, name, maximum=10000):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum, name + ' is empty/too long')
    require(not any(ord(c) < 32 and c not in '\n\t' for c in value), name + ' contains control characters')
    value = value.strip().translate(TYPOGRAPHY)
    # Built-in PDF fonts are portable and deterministic. Unsupported glyphs fail
    # rather than silently rendering a black square or losing a name.
    try:
        value.encode('cp1252')
    except UnicodeEncodeError as error:
        raise InvalidBrief(name + ' has unsupported PDF glyphs; use an embedded Unicode font before publishing') from error
    return value


def long_date(value):
    d = day(value, 'date') if isinstance(value, str) else value
    return f'{d.day} {MONTHS[d.month - 1]} {d.year}'


def public_url(value):
    require(isinstance(value, str) and len(value) <= 4096, 'Invalid source URL')
    try:
        parsed = urlsplit(value)
        hostname, port = parsed.hostname, parsed.port
    except ValueError as error:
        raise InvalidBrief('Invalid source URL') from error
    require(parsed.scheme == 'https' and hostname and '.' in hostname and not parsed.username
            and not parsed.password and port in (None, 443) and not re.search(r'[\s\\\x00-\x1f]', value),
            'Source URL must be public HTTPS without credentials or custom port')
    require(not hostname.endswith(('.local', '.localhost', '.internal')) and hostname != 'localhost',
            'Private source URL is not permitted')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    require(address is None or address.is_global, 'Private source IP is not permitted')
    return value


def regular_tree(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), 'Input directory must be a regular directory')
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'Symlinks are not accepted: ' + str(path))


def local_file(root, value):
    require(isinstance(value, str) and value and not Path(value).is_absolute(), 'Evidence path must be relative')
    rel = Path(value)
    require('..' not in rel.parts and not any(p.startswith('.') for p in rel.parts), 'Unsafe evidence path')
    path = root / rel
    require(not path.is_symlink() and path.is_file(), 'Missing regular evidence file: ' + value)
    path.resolve().relative_to(root.resolve())
    return path


def inventory(root):
    regular_tree(root)
    return {str(p.relative_to(root)): {'sha256': sha(p), 'bytes': p.stat().st_size}
            for p in sorted(root.rglob('*')) if p.is_file()}


def accepted_snapshot(root):
    require(root.is_dir() and not root.is_symlink(), 'Accepted site must be a regular directory')
    files = {}
    public_root = re.compile(r'(?:index\.html|archive-\d{4}-\d{2}-\d{2}\.html|'
                             r'Nigeria_Weekly_Brief_\d{4}-\d{2}-\d{2}\.pdf|latest\.pdf|'
                             r'map-\d{4}-\d{2}-\d{2}\.png|brief-release\.json|README\.md|CNAME|\.nojekyll)')
    candidates = list(root.iterdir())
    if (root / 'assets').exists():
        regular_tree(root / 'assets')
        candidates += list((root / 'assets').rglob('*'))
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if relative.parts[0] in ('.git', '.github', 'automation', '__pycache__', 'node_modules'):
            continue
        require(not path.is_symlink(), 'Symlinks are not accepted: ' + str(path))
        if not path.is_file():
            continue
        allowed = bool(public_root.fullmatch(str(relative))) or (
            relative.parts[0] == 'assets' and path.suffix.lower() in
            ('.png', '.jpg', '.jpeg', '.svg', '.css', '.js', '.ico', '.webp', '.woff', '.woff2'))
        if not allowed:
            continue
        require(not any(part.startswith('.') for part in relative.parts) or str(relative) == '.nojekyll',
                'Unexpected hidden file in existing site: ' + str(relative))
        require(path.suffix.lower() not in ('.key', '.pem', '.env', '.pyc'), 'Private/nonpublic site file')
        files[str(relative)] = {'sha256': sha(path), 'bytes': path.stat().st_size}
    require('index.html' in files and 'latest.pdf' in files, 'Existing site must contain complete latest HTML/PDF')
    return files


def previous_issue(root):
    release = root / 'brief-release.json'
    if release.exists():
        meta = read_json(release)
        prior = day(meta['issue_date'], 'previous release issue date')
        end = day(meta['reporting_window']['to'], 'previous reporting end')
        require(prior == end + timedelta(days=1), 'Existing release has inconsistent dates')
    else:
        digest = sha(root / 'latest.pdf')
        matches = [p for p in root.glob('Nigeria_Weekly_Brief_????-??-??.pdf') if sha(p) == digest]
        require(len(matches) == 1, 'Cannot identify unique previous issue from latest PDF')
        prior = day(matches[0].stem[-10:], 'previous issue date')
        end = prior - timedelta(days=1)
    html = (root / 'index.html').read_text()
    require(long_date(prior) in html, 'Existing HTML and PDF issue dates disagree')
    return prior, end


def validate_paragraph(item, sources, label, allow_gap=False):
    fields(item, ('kind', 'text', 'source_ids', 'evidence'), label)
    kind = item.get('kind')
    require(kind in ('reported', 'analysis', 'limitation', 'coverage_gap'), 'Unknown paragraph kind')
    require(kind != 'coverage_gap' or allow_gap, 'Coverage gaps belong only to zone coverage')
    content = text(item.get('text'), label)
    ids, refs = item.get('source_ids'), item.get('evidence')
    require(isinstance(ids, list) and len(ids) == len(set(ids)) and all(x in sources for x in ids),
            label + ' has unknown/duplicate source IDs')
    require(isinstance(refs, list), label + ' evidence must be an array')
    if kind in ('reported', 'analysis'):
        require(ids and refs, label + ' needs cited evidence')
    if kind == 'coverage_gap':
        require(not ids and not refs and not NUMBER.search(content), 'Coverage gap cannot introduce claims/numbers')
    quotes = []
    seen = set()
    for ref in refs:
        fields(ref, ('source_id', 'quote'), 'evidence reference')
        sid = ref.get('source_id')
        require(sid in ids, label + ' evidence source must be cited')
        quote = ref.get('quote')
        require(isinstance(quote, str) and 12 <= len(quote) <= 2000, label + ' needs a bounded evidence excerpt')
        if sources[sid].get('_content') is not None:
            require(quote in sources[sid]['_content'], label + ' evidence excerpt does not occur in captured source')
        seen.add(sid)
        quotes.append(quote)
    require(set(ids) == seen, label + ' every cited source needs exact evidence')
    # A mechanical guard only. Upstream numeric entailment checking remains required.
    supported = {x.replace(',', '') for x in NUMBER.findall(' '.join(quotes))}
    required = {x.replace(',', '') for x in NUMBER.findall(content)}
    require(required <= supported, label + ' includes unsupported numeric tokens: ' + ', '.join(sorted(required - supported)))
    return {'kind': kind, 'text': content, 'source_ids': ids, 'evidence': refs}


def validate_packet(packet_path, attestation_path, manifest_path, evidence_root, existing, as_of,
                    verified_public_input=False):
    packet, attest = read_json(packet_path), read_json(attestation_path)
    manifest = read_json(manifest_path)
    fields(packet, ('schema_version', 'issue_date', 'previous_issue_date', 'publication_date', 'reviewed_at',
                    'reporting_window', 'overview', 'humanitarian_context', 'limitations', 'developments', 'zones'), 'Candidate packet')
    fields(attest, ('schema_version', 'approved', 'candidate_sha256', 'evidence_manifest_sha256',
                    'reviewed_at', 'model', 'response_id', 'issues'), 'Reviewer attestation')
    fields(manifest, ('schema_version', 'sources'), 'Evidence manifest')
    fields(packet.get('reporting_window'), ('from', 'to'), 'Reporting window')
    require(packet.get('schema_version') == 1 and attest.get('schema_version') == 1
            and manifest.get('schema_version') == 1, 'Unsupported packet/attestation/manifest version')
    require(attest.get('approved') is True and attest.get('candidate_sha256') == sha(packet_path)
            and attest.get('evidence_manifest_sha256') == sha(manifest_path),
            'Verifier attestation is missing or not bound to this packet')
    require(attest.get('issues') == [], 'Reviewer raised unresolved issues')
    text(attest.get('model'), 'review model', 200)
    text(attest.get('response_id'), 'review response ID', 200)
    verified = instant(attest.get('reviewed_at'), 'reviewer reviewed_at')
    reviewed = instant(packet.get('reviewed_at'), 'reviewed_at')
    pub = day(packet.get('publication_date'), 'publication_date')
    issue = day(packet.get('issue_date'), 'issue_date')
    start = day(packet.get('reporting_window', {}).get('from'), 'reporting start')
    end = day(packet.get('reporting_window', {}).get('to'), 'reporting end')
    prior, prior_end = previous_issue(existing)
    require(day(packet.get('previous_issue_date'), 'previous_issue_date') == prior, 'Previous accepted issue mismatch')
    require(start == prior_end + timedelta(days=1) and end == start + timedelta(days=6), 'Reporting week must follow the accepted issue without a gap/overlap')
    require(start.weekday() == 0 and end.weekday() == 6 and issue == end + timedelta(days=1), 'Require Monday-Sunday reporting and following Monday issue')
    require(issue <= pub <= as_of and end < pub, 'Issue/publication dates are invalid or in the future')
    require(end <= reviewed.date() <= verified.date() <= pub and reviewed <= verified, 'Review/verification dates are inconsistent')
    require(evidence_root is not None or verified_public_input, 'Full extracts required unless trusted public input is explicitly selected')
    if evidence_root is not None:
        regular_tree(evidence_root)
    source_list = manifest.get('sources')
    require(isinstance(source_list, list) and 1 <= len(source_list) <= 60, 'Require 1-60 sources')
    sources = {}
    for source in source_list:
        fields(source, ('id', 'url', 'final_url', 'title', 'publisher', 'published_at', 'fetched_at',
                        'http_status', 'body_sha256', 'text_sha256', 'extract_path'), 'Source manifest record')
        sid = source.get('id')
        require(isinstance(sid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,40}', sid) and sid not in sources, 'Invalid/duplicate source ID')
        public_url(source.get('url'))
        public_url(source.get('final_url'))
        require(source.get('http_status') == 200, 'Only successfully fetched evidence is accepted')
        title = text(source.get('title'), 'source title', 500)
        publisher = text(source.get('publisher'), 'source publisher', 200)
        published = day(source.get('published_at'), 'source published_at')
        accessed = instant(source.get('fetched_at'), 'source fetched_at')
        require(published <= accessed.date() <= reviewed.date() and accessed <= reviewed,
                'Source publication/access/review dates disagree')
        require(all(isinstance(source.get(k), str) and re.fullmatch('[0-9a-f]{64}', source[k])
                    for k in ('body_sha256', 'text_sha256')), 'Source response/extract hashes are required')
        extract = source.get('extract_path')
        require(isinstance(extract, str) and extract and not Path(extract).is_absolute()
                and not any(x.startswith('.') for x in Path(extract).parts), 'Unsafe evidence path')
        content = None
        if evidence_root is not None:
            path = local_file(evidence_root, source.get('extract_path'))
            require(0 < path.stat().st_size <= 5_000_000 and sha(path) == source.get('text_sha256'), 'Evidence content hash/size mismatch')
            content = path.read_text(encoding='utf-8')
            require(content.strip(), 'Empty source evidence')
        sources[sid] = dict(source, title=title, publisher=publisher, _content=content)
    require(len({s['url'] for s in sources.values()}) == len(sources), 'Duplicate source URLs are not independent sources')
    def paragraphs(items, label, allow_gap=False):
        require(isinstance(items, list) and 1 <= len(items) <= 20, label + ' requires 1-20 paragraphs')
        return [validate_paragraph(p, sources, label, allow_gap) for p in items]
    result = dict(packet)
    for name in ('overview', 'humanitarian_context', 'limitations'):
        result[name] = paragraphs(packet.get(name), name)
    developments = packet.get('developments')
    require(isinstance(developments, list) and 1 <= len(developments) <= 18, 'Require 1-18 reviewed developments')
    result['developments'] = []
    for item in developments:
        fields(item, ('title', 'event_window', 'event_timing_note', 'paragraphs'), 'Development')
        title = text(item.get('title'), 'development title', 200)
        timing = item.get('event_window')
        note = text(item.get('event_timing_note'), 'event timing note', 1000)
        if timing is not None:
            fields(timing, ('from', 'to'), 'Event window')
            a, b = day(timing.get('from'), 'event start'), day(timing.get('to'), 'event end')
            require(a <= b <= reviewed.date(), 'Event window is inverted or future-dated')
        parts = paragraphs(item.get('paragraphs'), title)
        quote_numbers = set(NUMBER.findall(' '.join(e['quote'] for p in parts for e in p['evidence'])))
        require(set(NUMBER.findall(title + ' ' + note)) <= quote_numbers,
                'Development title/timing note includes unsupported numeric tokens')
        result['developments'].append(dict(item, title=title, event_timing_note=note, paragraphs=parts))
    zones = packet.get('zones')
    require(isinstance(zones, list) and len(zones) == 6 and {x.get('zone') for x in zones} == set(ZONES), 'Require exactly all six zones')
    for z in zones:
        fields(z, ('zone', 'paragraphs'), 'Zone')
    result['zones'] = [{'zone': z['zone'], 'paragraphs': paragraphs(z.get('paragraphs'), z['zone'], True)} for z in zones]
    result['_prior_issue'] = prior.isoformat()
    # Apply publisher-level use budgets across all substantive generated prose.
    # Reusing the same source quote does not increase its unique public excerpt.
    all_parts = result['overview'] + result['humanitarian_context'] + result['limitations']
    for item in result['developments']:
        all_parts += item['paragraphs']
    for item in result['zones']:
        all_parts += item['paragraphs']
    used = {sid for part in all_parts for sid in part['source_ids']}
    require(used, 'A brief must cite verified sources')
    result['_sources'] = {sid: source for sid, source in sources.items() if sid in used}
    quoted, derived = {}, {}
    word_count = lambda value: len(re.findall(r"\b[\w]+(?:['-][\w]+)*\b", value))
    for part in all_parts:
        publishers = {sources[s]['publisher'].casefold().strip() for s in part['source_ids']}
        for publisher in publishers:
            derived[publisher] = derived.get(publisher, 0) + word_count(part['text'])
        for reference in part['evidence']:
            publisher = sources[reference['source_id']]['publisher'].casefold().strip()
            quoted.setdefault(publisher, set()).add(reference['quote'])
    for item in result['developments']:
        publishers = {sources[s]['publisher'].casefold().strip() for p in item['paragraphs'] for s in p['source_ids']}
        for publisher in publishers:
            derived[publisher] += word_count(item['title'] + ' ' + item['event_timing_note'])
    for source in result['_sources'].values():
        publisher = source['publisher'].casefold().strip()
        # Bibliographic article titles also reproduce the publisher's words.
        quoted.setdefault(publisher, set()).add(source['title'])
        derived[publisher] = derived.get(publisher, 0) + word_count(source['title'])
    require(all(count <= 200 for count in derived.values()), 'Publisher derived-word budget exceeds 200 words')
    require(all(sum(word_count(q) for q in excerpts) <= 25 for excerpts in quoted.values()),
            'Publisher exact-excerpt budget (including source titles) exceeds 25 words')
    result['_word_budgets'] = {publisher: {'derived_words': derived.get(publisher, 0),
                              'quoted_words': sum(word_count(q) for q in excerpts)}
                               for publisher, excerpts in quoted.items()}
    return result


def validate_events(release_path, image_path, publication_date):
    meta = read_json(release_path)
    require(meta.get('schema_version') == 1, 'Unsupported events release schema')
    generated = instant(meta.get('generated_at'), 'events generated_at')
    require(generated.date() <= day(publication_date, 'publication date'), 'Events release is newer than brief publication')
    expected = meta.get('files', {}).get('lastweek.png', {})
    require(Path(image_path).is_file() and not Path(image_path).is_symlink() and expected.get('sha256') == sha(image_path)
            and expected.get('bytes') == Path(image_path).stat().st_size, 'Map image does not match accepted release')
    with PILImage.open(image_path) as image:
        require(image.format == 'PNG' and image.width >= 800 and image.height >= 300, 'Map image is not a complete full-size PNG')
        image.verify()
    with PILImage.open(image_path) as image:
        image.load()
    coverage = meta.get('coverage', {})
    for name in ('ucdp', 'gdelt'):
        item = coverage.get(name, {})
        a, b = day(item.get('min_event_date'), name + ' minimum'), day(item.get('max_event_date'), name + ' maximum')
        require(a <= b <= generated.date(), name + ' event range is invalid')
        require(type(item.get('events')) is int and item['events'] > 0, name + ' must have nonempty data')
    gdelt = coverage['gdelt']
    a, b = day(gdelt.get('input_from'), 'GDELT input_from'), day(gdelt.get('input_through'), 'GDELT input_through')
    require(a <= b <= generated.date() and day(gdelt['max_event_date'], 'GDELT max') <= b,
            'GDELT event date cannot exceed ingestion coverage')
    require(type(gdelt.get('input_days')) is int and gdelt['input_days'] == (b-a).days + 1, 'GDELT daily ingestion coverage is incomplete')
    require(isinstance(meta.get('warnings'), list), 'Events warnings are missing')
    return meta


def map_paragraphs(meta, reporting):
    coverage = meta['coverage']
    out = []
    for name, label in (('ucdp', 'UCDP'), ('gdelt', 'GDELT')):
        record = coverage[name]
        last = day(record['max_event_date'], label + ' max')
        panel_start = last - timedelta(days=6)
        kind = 'curated conflict-event records' if name == 'ucdp' else 'machine-coded reporting records'
        value = (f'{label}: {record["events"]:,} {kind} in the complete captured map, dated '
                 f'{long_date(record["min_event_date"])} to {long_date(last)}. '
                 f'The image panel covers {long_date(panel_start)} to {long_date(last)}.')
        if name == 'gdelt':
            value += (f' Daily input coverage is complete from {long_date(record["input_from"])} '
                      f'through {long_date(record["input_through"])}; the maximum matching event date is '
                      f'{long_date(last)}. These dates are not interchangeable.')
        else:
            value += ' UCDP is delayed and subject to revision; it is not a current-week census.'
        if panel_start.isoformat() != reporting['from'] or last.isoformat() != reporting['to']:
            value += ' This image panel differs from the brief reporting week.'
        out.append(value)
    out.append('GDELT reporting volume is not a verified incident count, casualty count or opinion poll. The UCDP and GDELT panels must not be added or treated as comparable trends.')
    out.append('Map snapshot generated ' + meta['generated_at'] + '. The captured map and its original windows are retained with this issue.')
    for warning in meta['warnings']:
        out.append('Map source warning: ' + text(str(warning), 'map warning'))
    return out


def paragraph_display(item, source_numbers):
    prefix = {'reported': 'Reported: ', 'analysis': 'Assessment: ', 'limitation': '', 'coverage_gap': ''}[item['kind']]
    content = prefix + item['text']
    if item['kind'] == 'coverage_gap':
        content += ' ' + GAP
    if item['source_ids']:
        content += ' [' + ', '.join(str(source_numbers[s]) for s in item['source_ids']) + ']'
    return content


def timing_display(item, reporting):
    window = item['event_window']
    if window is None:
        status = 'Event date not independently established.'
    else:
        status = 'Event window: ' + long_date(window['from']) + ' to ' + long_date(window['to']) + '.'
        if window['from'] < reporting['from'] or window['to'] > reporting['to']:
            status += ' Outside or extending beyond this reporting week; not counted as a new weekly total.'
    return status + ' ' + item['event_timing_note']


def scope_display(packet):
    return (f'Reporting window: {long_date(packet["reporting_window"]["from"])} to '
            f'{long_date(packet["reporting_window"]["to"])}. Issue date: {long_date(packet["issue_date"])}. '
            f'Published: {long_date(packet["publication_date"])}. ' + LIMIT)


def render_html(packet, meta, css, pdf_name, image_name):
    numbers = {sid: i for i, sid in enumerate(packet['_sources'], 1)}
    p = lambda value: '<p>' + escape(value) + '</p>'
    paras = lambda items: ''.join(p(paragraph_display(item, numbers)) for item in items)
    nav = (f'<nav><a href="https://rbmglobal.co.uk/">RBM Global</a><a href="{pdf_name}">Download PDF</a>'
           f'<a href="latest.pdf">Latest PDF</a><a href="{MAP_URL}">Activity map</a>'
           '<a href="https://rbmglobal.co.uk/sentiment/">Sentiment map</a></nav>')
    body = nav + '<p class="muted">RBM GLOBAL · OPEN-SOURCE SECURITY AND HUMANITARIAN OVERVIEW</p>'
    body += '<h1>' + TITLE + '</h1><p class="notice">' + escape(scope_display(packet)) + '</p>'
    body += '<p class="muted">' + escape(AI_LABEL) + '</p>'
    body += '<h2>Overview</h2>' + paras(packet['overview']) + '<h2>Reviewed developments</h2>'
    for development in packet['developments']:
        body += '<h3>' + escape(development['title']) + '</h3>' + paras(development['paragraphs'])
        body += '<p class="muted">' + escape(timing_display(development, packet['reporting_window'])) + '</p>'
    body += '<h2>Coverage and confidence</h2><table><tr><th>Zone</th><th>Selected coverage and interpretation</th></tr>'
    for row in packet['zones']:
        body += '<tr><td>' + escape(row['zone']) + '</td><td>' + paras(row['paragraphs']) + '</td></tr>'
    body += '</table><h2>Map and data status</h2><a href="' + MAP_URL + '"><img class="map" src="' + image_name + '" alt="UCDP and GDELT image panels with distinct dated source windows"/></a>'
    body += ''.join(p(value) for value in map_paragraphs(meta, packet['reporting_window']))
    body += '<h2>Humanitarian context</h2>' + paras(packet['humanitarian_context'])
    body += '<h2>Limitations</h2>' + paras(packet['limitations']) + '<h2>Sources</h2><ol>'
    for sid, source in packet['_sources'].items():
        body += '<li>' + escape(source['publisher'] + ' | ' + long_date(source['published_at']) + ': ')
        body += '<a href="' + escape(source['url'], quote=True) + '">' + escape(source['title']) + '</a></li>'
    prior = packet['_prior_issue']
    body += '</ol><footer>' + p('Sources checked ' + packet['reviewed_at'] + '. Published ' + long_date(packet['publication_date']) + '.')
    body += f'<p><a href="archive-{prior}.html">Previous HTML issue - {escape(long_date(prior))}</a> · <a href="Nigeria_Weekly_Brief_{prior}.pdf">Previous PDF</a></p></footer>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + TITLE + ', ' + long_date(packet['issue_date']) + '</title><style>' + css + '</style></head><body><main>' + body + '</main></body></html>'


def render_pdf(packet, meta, path, image_path):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='RBMTitle', fontName='Helvetica-Bold', fontSize=26, leading=29,
                              textColor=colors.HexColor('#163d54'), spaceAfter=16))
    styles.add(ParagraphStyle(name='RBMBody', fontName='Helvetica', fontSize=10, leading=14, spaceAfter=8))
    styles.add(ParagraphStyle(name='RBMHead', fontName='Helvetica-Bold', fontSize=13, leading=16,
                              textColor=colors.HexColor('#0b5269'), spaceBefore=10, spaceAfter=6, keepWithNext=True))
    styles.add(ParagraphStyle(name='RBMSmall', fontName='Helvetica', fontSize=8, leading=11, spaceAfter=7))
    par = lambda value, style='RBMBody': Paragraph(escape(value), styles[style])
    numbers = {sid: i for i, sid in enumerate(packet['_sources'], 1)}
    paras = lambda items: [par(paragraph_display(item, numbers)) for item in items]
    story = [par('RBM GLOBAL | ' + long_date(packet['issue_date']).upper(), 'RBMSmall'),
             par(TITLE, 'RBMTitle'), par(scope_display(packet)), par(AI_LABEL, 'RBMSmall'), par('Overview', 'RBMHead')]
    story += paras(packet['overview'])
    for development in packet['developments']:
        story += [par(development['title'], 'RBMHead')] + paras(development['paragraphs'])
        story += [par(timing_display(development, packet['reporting_window']), 'RBMSmall')]
    story += [PageBreak(), par('Coverage, map and data status', 'RBMTitle')]
    rows = [[par('Zone', 'RBMSmall'), par('Selected coverage and interpretation', 'RBMSmall')]]
    for zone in packet['zones']:
        rows.append([par(zone['zone'], 'RBMSmall'), [par(paragraph_display(item, numbers), 'RBMSmall') for item in zone['paragraphs']]])
    table = Table(rows, colWidths=[92, 387], repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e6f0f4')),
                              ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                              ('GRID', (0, 0), (-1, -1), .3, colors.HexColor('#c4d1d8')),
                              ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7)]))
    story += [table, Spacer(1, 14)]
    with PILImage.open(image_path) as image:
        ratio = image.height / image.width
    width = min(479, 270 / ratio)
    story += [Image(str(image_path), width=width, height=width * ratio)]
    story += [par(value) for value in map_paragraphs(meta, packet['reporting_window'])]
    story += [PageBreak(), par('Humanitarian context and sources', 'RBMTitle')]
    story += paras(packet['humanitarian_context']) + [par('Limitations', 'RBMHead')] + paras(packet['limitations'])
    story += [par('Source register', 'RBMHead')]
    for sid, source in packet['_sources'].items():
        label = str(numbers[sid]) + '. ' + source['publisher'] + ', ' + long_date(source['published_at'])
        story.append(Paragraph('<b>' + escape(label) + '</b><br/>' + escape(source['title'])
                               + '<br/><link href="' + escape(source['url'], quote=True)
                               + '" color="#075d83">Read source</link>', styles['RBMBody']))
    story += [Paragraph('Live brief: <link href="' + BRIEF_URL + '" color="#075d83">rbmglobal.co.uk/weekly-threat-brief/</link><br/>Interactive map: <link href="' + MAP_URL + '" color="#075d83">rbmglobal.co.uk/nigeria-activity-map/</link>', styles['RBMSmall'])]
    def footer(c, doc):
        c.setFont('Helvetica', 8)
        c.setFillColor(colors.HexColor('#526879'))
        c.drawString(52, 30, 'RBM Global | Published ' + long_date(packet['publication_date']))
        c.drawRightString(543, 30, str(doc.page))
    def fixed_canvas(*args, **kwargs):
        kwargs['invariant'] = 1
        return canvas.Canvas(*args, **kwargs)
    SimpleDocTemplate(str(path), pagesize=(595.28, 841.89), leftMargin=52, rightMargin=52,
                      topMargin=42, bottomMargin=48, title=TITLE + ' - ' + packet['issue_date'],
                      author='RBM Global').build(story, onFirstPage=footer, onLaterPages=footer,
                                                canvasmaker=fixed_canvas)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls, self.scripts = [], 0
    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.scripts += 1
        self.urls.extend(value for key, value in attrs if key in ('href', 'src'))


def validate_output(site, packet, old_inventory, generated_names):
    now = inventory(site)
    for name, saved in old_inventory.items():
        if name not in ('index.html', 'latest.pdf', 'brief-release.json'):
            require(now.get(name) == saved, 'Existing archived/unrelated file changed: ' + name)
    for name in ('index.html', f'archive-{packet["issue_date"]}.html'):
        page = (site / name).read_text()
        require(page.startswith('<!doctype html>') and page.endswith('</html>') and len(page) > 2000, 'Incomplete HTML')
        parser = Links()
        parser.feed(page)
        require(parser.scripts == 0, 'Generated brief must not contain scripts')
        for url in parser.urls:
            parts = urlsplit(url)
            if parts.scheme:
                public_url(url)
            elif parts.path:
                local_file(site, unquote(parts.path))
    pdf_path = site / f'Nigeria_Weekly_Brief_{packet["issue_date"]}.pdf'
    require(sha(pdf_path) == sha(site / 'latest.pdf'), 'Latest and dated PDFs differ')
    require(sha(site / 'index.html') == sha(site / f'archive-{packet["issue_date"]}.html'), 'Dated and latest HTML differ')
    reader = PdfReader(pdf_path, strict=True)
    require(not reader.is_encrypted and 3 <= len(reader.pages) <= 30, 'PDF is incomplete or unexpectedly long')
    extracted = '\n'.join(page.extract_text() or '' for page in reader.pages)
    for expected in (TITLE, 'AI-generated, source-checked', long_date(packet['issue_date']), long_date(packet['publication_date']),
                     'Source register', *ZONES):
        require(expected in extracted, 'PDF is missing required content: ' + expected)
    linked = set()
    for page in reader.pages:
        require((page.extract_text() or '').strip(), 'Blank PDF page')
        for ref in page.get('/Annots', []):
            annotation = ref.get_object()
            action = annotation.get('/A')
            if action and action.get('/S') == '/URI':
                linked.add(str(action['/URI']))
    require({s['url'] for s in packet['_sources'].values()} <= linked, 'PDF is missing source hyperlinks')
    glyphs = 0
    with pdfplumber.open(pdf_path) as document:
        for page in document.pages:
            require(page.width > 0 and page.height > 0, 'Invalid PDF page dimensions')
            for char in page.chars:
                # Includes the running footer. Any clipped/off-page glyph is a
                # failed build; the publishing workflow needs no manual override.
                require(char['x0'] >= 49 and char['x1'] <= page.width - 48
                        and char['top'] >= 30 and char['bottom'] <= page.height - 20,
                        'PDF text overflows its safe page bounds')
                glyphs += 1
            for image in page.images:
                require(image['x0'] >= 49 and image['x1'] <= page.width - 48
                        and image['top'] >= 30 and image['bottom'] <= page.height - 40,
                        'PDF image overflows its safe page bounds')
    require(set(generated_names) <= set(now), 'Publication package is missing generated files')
    return {'pdf_pages': len(reader.pages), 'pdf_source_links': len(linked),
            'pdf_glyphs_checked': glyphs, 'pdf_overflow_check_passed': True,
            'archives_preserved': True, 'local_asset_links_valid': True}


def build(packet_path, attestation_path, manifest_path, evidence_root, existing_site, events_release,
          events_image, output, as_of=None, verified_public_input=False):
    packet_path, attestation_path = Path(packet_path), Path(attestation_path)
    manifest_path = Path(manifest_path)
    evidence_root, existing_site = Path(evidence_root) if evidence_root is not None else None, Path(existing_site)
    events_release, events_image, output = Path(events_release), Path(events_image), Path(output)
    require(not output.exists() and not output.is_symlink(), 'Output must be a new directory')
    require(output.parent.is_dir(), 'Output parent directory must exist')
    initial_hashes = {p: sha(p) for p in (packet_path, attestation_path, manifest_path, events_release, events_image)}
    old = accepted_snapshot(existing_site)
    packet = validate_packet(packet_path, attestation_path, manifest_path, evidence_root, existing_site,
                             as_of or date.today(), verified_public_input)
    meta = validate_events(events_release, events_image, packet['publication_date'])
    css = re.search(r'<style>(.*?)</style>', (existing_site / 'index.html').read_text(), re.S)
    require(css is not None and len(css.group(1)) > 300, 'Existing layout stylesheet is missing')
    issue = packet['issue_date']
    pdf_name = f'Nigeria_Weekly_Brief_{issue}.pdf'
    html_name = f'archive-{issue}.html'
    image_name = 'map-' + instant(meta['generated_at'], 'events generated_at').date().isoformat() + '.png'
    new_names = (pdf_name, html_name)
    require(not any(name in old for name in new_names), 'Issue already exists; redeploy the accepted issue rather than regenerate it')
    if image_name in old:
        require(old[image_name]['sha256'] == sha(events_image), 'Dated image filename collision with different image')
    prior_archive = f'archive-{packet["_prior_issue"]}.html'
    # If an existing archive is present it must be the current accepted HTML.
    if prior_archive in old:
        require(old[prior_archive]['sha256'] == old['index.html']['sha256'], 'Previous archive differs from accepted latest HTML')
    temporary = Path(tempfile.mkdtemp(prefix='.' + output.name + '-', dir=output.parent))
    try:
        site = temporary / 'site'
        site.mkdir()
        for name in old:
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing_site / name, target)
        if prior_archive not in old:
            shutil.copy2(existing_site / 'index.html', site / prior_archive)
        shutil.copy2(events_image, site / image_name)
        html = render_html(packet, meta, css.group(1), pdf_name, image_name)
        (site / 'index.html').write_text(html, encoding='utf-8')
        (site / html_name).write_text(html, encoding='utf-8')
        render_pdf(packet, meta, site / pdf_name, site / image_name)
        shutil.copy2(site / pdf_name, site / 'latest.pdf')
        generated = ['index.html', 'latest.pdf', pdf_name, html_name, image_name, prior_archive]
        validation = validate_output(site, packet, old, generated)
        generated_inventory = inventory(site)
        write_json(site / 'brief-release.json', {
            'schema_version': 1, 'issue_date': issue, 'reporting_window': packet['reporting_window'],
            'publication_date': packet['publication_date'], 'reviewed_at': packet['reviewed_at'],
            'packet_sha256': sha(packet_path), 'events_release_sha256': sha(events_release),
            'events_coverage': meta['coverage'], 'files': {name: generated_inventory[name] for name in generated},
        })
        evidence = temporary / 'evidence'
        evidence.mkdir()
        # Full article extracts stay with the private producer, never in the
        # approved packet or static publication. Their hashes remain auditable.
        if evidence_root is not None:
            for source in packet['_sources'].values():
                require(sha(local_file(evidence_root, source['extract_path'])) == source['text_sha256'],
                        'Source evidence changed during build')
        for source, destination in ((packet_path, 'packet.json'), (attestation_path, 'attestation.json'),
                                    (events_release, 'events-release.json'), (manifest_path, 'source-manifest.json')):
            shutil.copy2(source, evidence / destination)
        require(all(sha(p) == value for p, value in initial_hashes.items()), 'Validated input changed during staging')
        require(accepted_snapshot(existing_site) == old, 'Accepted source site changed during staging')
        files = inventory(site)
        changes = {name: data for name, data in files.items() if old.get(name) != data}
        write_json(temporary / 'changes.json', {'schema_version': 1, 'repository': 'RBMGlobal/weekly-threat-brief',
                   'message': 'Publish sourced weekly brief ' + issue, 'files': changes, 'deletions': []})
        manifest = {'schema_version': 1, 'issue_date': issue, 'reporting_window': packet['reporting_window'],
                    'publication_date': packet['publication_date'], 'packet_sha256': sha(packet_path),
                    'attestation_sha256': sha(attestation_path), 'events_release_sha256': sha(events_release),
                    'evidence_manifest_sha256': sha(manifest_path), 'word_budgets': packet['_word_budgets'],
                    'verification_mode': 'trusted-public-receipt' if evidence_root is None else 'local-extracts-and-receipt',
                    'files': files, 'validation': validation, 'publication_performed': False,
                    'network_requests': 0, 'evidence_files': inventory(evidence)}
        write_json(temporary / 'publication.json', manifest)
        require(inventory(site) == files, 'Output changed after validation')
        temporary.rename(output)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('packet', 'attestation', 'manifest', 'existing-site', 'events-release', 'events-image', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--verified-public-input', action='store_true', help='Caller independently verified the public approved packet origin and hashes')
    args = parser.parse_args()
    result = build(args.packet, args.attestation, args.manifest, args.evidence, args.existing_site, args.events_release,
                   args.events_image, args.output, verified_public_input=args.verified_public_input)
    print(json.dumps({'status': 'staged', 'issue_date': result['issue_date'],
                      'files': len(result['files']), 'validation': result['validation'],
                      'publication_performed': False}, indent=2))


if __name__ == '__main__':
    main()
