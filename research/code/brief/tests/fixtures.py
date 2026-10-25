"""Synthetic records only. No content here is a real Nigeria assessment."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_brief import ZONES, sha, write_json
from PIL import Image, ImageDraw


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    accepted, evidence = root / 'accepted', root / 'sources'
    accepted.mkdir(); evidence.mkdir()
    # The saved prior PDF is a fixture used only to test preservation/binding.
    prior = b'%PDF-1.4\nSYNTHETIC PRIOR ISSUE: unchanged archive fixture\n%%EOF\n'
    (accepted / 'latest.pdf').write_bytes(prior)
    (accepted / 'Nigeria_Weekly_Brief_2026-10-12.pdf').write_bytes(prior)
    css = ('body{margin:0;background:#f2f5f8;color:#172a3b;font:17px/1.6 system-ui,sans-serif}'
           'main{max-width:1040px;margin:auto;background:white;padding:42px}h1{font-size:38px;line-height:1.15}'
           'h2{color:#0b5269;margin-top:38px}h3{font-size:20px}nav{display:flex;gap:16px;flex-wrap:wrap}'
           'a{color:#075d83}.muted{color:#526879}.notice{background:#e8f3f6;padding:18px;border-left:5px solid #1384a3}'
           '.map{width:100%;height:auto}table{width:100%;border-collapse:collapse}td,th{padding:12px;border:1px solid #ccd8df;text-align:left;vertical-align:top}'
           'footer{border-top:1px solid #ccd8df;margin-top:35px;padding-top:20px}@media(max-width:650px){main{padding:20px}h1{font-size:30px}}')
    (accepted / 'index.html').write_text('<!doctype html><html><head><style>' + css + '</style></head><body>Fixture prior issue 12 October 2026. Reporting window 5-11 October 2026.</body></html>')
    (accepted / 'archive-2026-10-05.html').write_text('SYNTHETIC older archive must be preserved.')
    quote = 'This synthetic source reports that 12 clinics continued providing services during the example period.'
    (evidence / 'fixture.txt').write_text(quote + '\nThis is invented test evidence, not a real article.\n')
    source_hash = sha(evidence / 'fixture.txt')
    manifest = {'schema_version': 1, 'sources': [{
        'id': 'source-1', 'url': 'https://publisher.example.test/fixture',
        'final_url': 'https://publisher.example.test/fixture',
        'title': 'Synthetic service-continuity example - not real reporting',
        'publisher': 'Synthetic Test Publisher', 'published_at': '2026-10-18',
        'fetched_at': '2026-10-19T08:00:00Z', 'http_status': 200,
        'body_sha256': source_hash, 'text_sha256': source_hash, 'extract_path': 'fixture.txt'}]}
    def para(value, kind='reported'):
        return {'kind': kind, 'text': value, 'source_ids': ['source-1'],
                'evidence': [{'source_id': 'source-1', 'quote': quote}]}
    packet = {
        'schema_version': 1, 'issue_date': '2026-10-19', 'previous_issue_date': '2026-10-12',
        'reporting_window': {'from': '2026-10-12', 'to': '2026-10-18'},
        'publication_date': '2026-10-19', 'reviewed_at': '2026-10-19T09:00:00Z',
        'overview': [para('SYNTHETIC DEMONSTRATION ONLY. This fixture describes continued services; it is not a real security assessment.')],
        'developments': [{'title': 'Synthetic services example', 'event_window': None,
                          'event_timing_note': 'The example does not establish a separate event date.',
                          'paragraphs': [para('The synthetic source describes 12 clinics continuing services. No national trend follows from this fixture.')]}],
        'humanitarian_context': [para('The example concerns service continuity, with no verified wider implication.')],
        'limitations': [{'kind': 'limitation', 'text': 'All research text and data in this demonstration are synthetic. Do not publish this fixture as a real brief.',
                        'source_ids': [], 'evidence': []}],
        'zones': [{'zone': zone, 'paragraphs': [{'kind': 'coverage_gap', 'text': 'Synthetic fixture: this zone was not reviewed.',
                                               'source_ids': [], 'evidence': []}]} for zone in ZONES]}
    write_json(root / 'packet.json', packet)
    write_json(root / 'manifest.json', manifest)
    attest(root)
    image = Image.new('RGB', (1400, 620), '#edf4f8')
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 90, 680, 550), outline='#0b5269', width=3)
    draw.rectangle((720, 90, 1370, 550), outline='#0b5269', width=3)
    draw.text((50, 40), 'SYNTHETIC TEST IMAGE - NOT AN EVENTS MAP', fill='#172a3b')
    draw.text((50, 120), 'UCDP fixture: 24-30 September', fill='#172a3b')
    draw.text((740, 120), 'GDELT fixture: 12-18 October', fill='#172a3b')
    image.save(root / 'lastweek.png')
    release = {'schema_version': 1, 'generated_at': '2026-10-19T08:00:00Z', 'warnings': [],
               'coverage': {'ucdp': {'min_event_date': '2026-09-01', 'max_event_date': '2026-09-30', 'events': 99},
                            'gdelt': {'min_event_date': '2026-09-23', 'max_event_date': '2026-10-18', 'events': 101,
                                      'input_from': '2026-09-23', 'input_through': '2026-10-18', 'input_days': 26}},
               'files': {'lastweek.png': {'sha256': sha(root / 'lastweek.png'), 'bytes': (root / 'lastweek.png').stat().st_size}}}
    write_json(root / 'release.json', release)
    return packet, manifest


def attest(root):
    write_json(root / 'attestation.json', {
        'schema_version': 1, 'approved': True, 'candidate_sha256': sha(root / 'packet.json'),
        'evidence_manifest_sha256': sha(root / 'manifest.json'),
        'reviewed_at': '2026-10-19T10:00:00Z', 'model': 'synthetic-test-verifier',
        'response_id': 'fixture-only-not-a-provider-response', 'issues': []})
