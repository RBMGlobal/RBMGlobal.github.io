"""Mocked discovery/reviewer calls with the real weekly PDF/publication validator.

All articles and model responses are synthetic; no API or network use is allowed.
"""
import copy
import datetime as dt
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_research as runner
import publication
from brief.tests.fixtures import fixture

HERE = Path(__file__).resolve().parents[1]
FULL_TEXT_MARKER = 'PRIVATE_FULL_ARTICLE_ONLY_NOT_FOR_PUBLICATION'


class WeeklyOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'fixture'
        self.candidate, self.manifest = fixture(self.input)
        # Each publisher has a short independently cited source. A third fetched
        # source is deliberately unused and must not enter the public packet.
        for i in (2, 3):
            quote = f'Synthetic report {i} describes service continuity in the example area.'
            source = dict(self.manifest['sources'][0], id=f'source-{i}',
                          url=f'https://publisher{i}.example.test/report',
                          final_url=f'https://publisher{i}.example.test/report',
                          publisher=f'Synthetic Publisher {i}', title=f'Synthetic report {i}',
                          extract_path=f'source-{i}.txt')
            (self.input / 'sources' / source['extract_path']).write_text(quote + '\n' + FULL_TEXT_MARKER)
            source['text_sha256'] = source['body_sha256'] = runner.sha(self.input / 'sources' / source['extract_path'])
            self.manifest['sources'].append(source)
            if i == 2:
                self.candidate['humanitarian_context'] = [{
                    'kind': 'reported', 'text': 'The second synthetic source also describes continued services.',
                    'source_ids': [source['id']], 'evidence': [{'source_id': source['id'], 'quote': quote}]}]
        original = self.input / 'sources/fixture.txt'
        original.write_text(original.read_text() + FULL_TEXT_MARKER)
        self.manifest['sources'][0]['text_sha256'] = self.manifest['sources'][0]['body_sha256'] = runner.sha(original)
        self.repo = self.root / 'repo'
        (self.repo / 'sentiment').mkdir(parents=True)
        (self.repo / 'sentiment/data.json').write_text('{"fixture":"unchanged sentiment baseline"}')
        self.config = self.root / 'config'
        (self.config / 'brief').mkdir(parents=True)
        shutil.copyfile(HERE / 'brief/candidate.schema.json', self.config / 'brief/candidate.schema.json')
        runner.write_json(self.config / 'policy.json', {
            'schema_version': 1, 'monthly_limit_usd': 10, 'max_sources': 10,
            'max_model_evidence_chars': 65000,
            'publishers': {f'publisher{i if i > 1 else ""}.example.test': f'Synthetic Publisher {i}' for i in (1, 2, 3)}})
        self.work, self.output = self.root / 'work', self.root / 'output'
        self.work.mkdir(); self.output.mkdir()
        self.now = dt.datetime(2026, 10, 19, 10, 0, tzinfo=dt.timezone.utc)
        self.calls = []
        self.bad_binding = None
        self.source_mode = 'normal'
        self.reject_review = False
        self.incomplete_checklist = False
        self.fetch_count = 0
        self.initial_source_count = 3
        self.recovery_source_count = 3

    def fake_collect(self, proposals, policy, output, urls):
        shutil.copytree(self.input / 'sources', output)
        manifest = copy.deepcopy(self.manifest)
        count = self.recovery_source_count if output.name == 'recovered-sources' else self.initial_source_count
        manifest['sources'] = manifest['sources'][:count]
        runner.write_json(output / 'manifest.json', manifest)
        runner.write_json(output / 'collection.json', {'accepted': count, 'rejected': []})
        if count == 0:
            raise ValueError('No source passed collection and publication-date checks')
        return manifest

    def fake_events(self, output):
        self.fetch_count += 1
        output.mkdir()
        for name in ('release.json', 'lastweek.png'):
            shutil.copyfile(self.input / name, output / name)
        return output / 'release.json', output / 'lastweek.png'

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        role = kwargs['request_id'].split(':')[-1]
        if role in ('discovery', 'source-recovery'):
            data = {'sources': [{'url': source['url']} for source in self.manifest['sources']],
                    'limitations': 'Synthetic bounded discovery'}
        elif role == 'candidate':
            data = copy.deepcopy(self.candidate)
            data['reviewed_at'] = self.now.astimezone(runner.LONDON).isoformat()
            data['publication_date'] = self.now.astimezone(runner.LONDON).date().isoformat()
            if self.bad_binding:
                data[self.bad_binding] = {'from': '2026-10-05', 'to': '2026-10-11'} if self.bad_binding == 'reporting_window' else '2026-10-26'
            if self.source_mode == 'single':
                data['humanitarian_context'] = copy.deepcopy(data['overview'])
            elif self.source_mode == 'unknown':
                data['humanitarian_context'][0]['source_ids'] = ['unknown-source']
        elif role == 'review':
            data = {'approved': not self.reject_review,
                    'issues': ['Unsupported fixture claim'] if self.reject_review else [],
                    'checks': {key: not self.reject_review for key in runner.REVIEW['properties']['checks']['properties']}}
            if self.incomplete_checklist:
                data['checks'] = {}
        else:
            raise AssertionError(role)
        return SimpleNamespace(data=data, sources=[{'url': source['url']} for source in self.manifest['sources']],
                               model='synthetic-review-model', response_id='synthetic-response-' + role)

    def run_weekly(self):
        now = self.now
        class FixedClock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz or dt.timezone.utc)
        env = {'AI_RESEARCH_ENABLED': 'true', 'OPENAI_HARD_LIMIT_CONFIRMED': '10', 'GITHUB_RUN_ID': '67890', 'GITHUB_RUN_ATTEMPT': '1'}
        ledger = SimpleNamespace(remaining=lambda: 10_000_000)
        store = SimpleNamespace(read=lambda: ({}, 'unused'), persist=lambda *_: True)
        with patch.dict('os.environ', env), patch.object(runner, 'HERE', self.config), \
             patch.object(runner, 'datetime', FixedClock), \
             patch.object(runner, 'GitHubLedgerStore', return_value=store) as store_factory, \
             patch.object(runner, 'BudgetLedger', return_value=ledger), \
             patch.object(runner, 'OpenAIEditorialClient', return_value=SimpleNamespace(generate=self.generate)) as client_factory, \
             patch.object(runner, 'collect', side_effect=self.fake_collect), \
             patch.object(publication, 'fetch_events', side_effect=self.fake_events), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('Network forbidden in fixture tests')):
            try:
                runner.run('weekly', self.repo, self.input / 'accepted', self.output, self.work)
            finally:
                self.store_created = store_factory.call_count
                self.client_created = client_factory.call_count

    def test_future_week_not_due_makes_no_api_or_ledger_calls(self):
        self.now = dt.datetime(2026, 10, 18, 10, 0, tzinfo=dt.timezone.utc)
        self.run_weekly()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store_created, 0)
        self.assertEqual(self.client_created, 0)
        self.assertEqual(self.fetch_count, 0)
        self.assertFalse((self.output / 'files').exists())
        self.assertEqual(json.loads((self.output / 'run-summary.json').read_text()),
                         {'status': 'not_due', 'next_issue': '2026-10-19'})

    def test_full_chain_trims_sources_and_emits_complete_short_public_packet(self):
        previous = (self.input / 'accepted/index.html').read_bytes()
        baseline = (self.repo / 'sentiment/data.json').read_bytes()
        self.run_weekly()
        self.assertEqual([c['request_id'] for c in self.calls],
                         ['67890.1:weekly:discovery', '67890.1:weekly:candidate', '67890.1:weekly:review'])
        self.assertTrue(self.calls[0]['search'])
        self.assertFalse(self.calls[1]['search'])
        self.assertFalse(self.calls[2]['search'])
        self.assertEqual(self.calls[2]['kind'], 'review')
        folder = self.output / 'files/research/public/weekly/2026-10-19'
        names = {'candidate.json', 'sources.json', 'attestation.json', 'events-release.json', 'lastweek.png'}
        self.assertEqual({p.name for p in folder.iterdir()}, names)
        manifest = json.loads((folder / 'sources.json').read_text())
        self.assertEqual([s['id'] for s in manifest['sources']], ['source-1', 'source-2'])
        self.assertNotIn('source-3', self.calls[2]['prompt'])
        attestation = json.loads((folder / 'attestation.json').read_text())
        self.assertEqual(attestation['candidate_sha256'], runner.sha(folder / 'candidate.json'))
        self.assertEqual(attestation['evidence_manifest_sha256'], runner.sha(folder / 'sources.json'))
        latest = json.loads((folder.parent / 'latest.json').read_text())
        self.assertEqual(set(latest['files']), names)
        for name, record in latest['files'].items():
            publication.equal((folder / name).read_bytes(), record)
        for path in (self.output / 'files').rglob('*'):
            if path.is_file() and path.suffix == '.json':
                self.assertNotIn(FULL_TEXT_MARKER, path.read_text())
        self.assertFalse(any(p.suffix == '.txt' for p in (self.output / 'files').rglob('*')))
        plan = json.loads((self.output / 'plan.json').read_text())
        self.assertEqual(len(plan['files']), 6)
        self.assertTrue(all(name.startswith('research/public/weekly/') for name in plan['files']))
        self.assertEqual(baseline, (self.repo / 'sentiment/data.json').read_bytes())
        self.assertEqual(previous, (self.input / 'accepted/index.html').read_bytes())
        self.assertTrue((self.work / 'validated-brief/site/latest.pdf').is_file())
        summary = json.loads((self.output / 'run-summary.json').read_text())
        self.assertEqual(summary['sources'], 2)
        self.assertFalse(summary['publication_changed'])

    def test_issue_binding_cannot_be_changed(self):
        self.bad_binding = 'issue_date'
        with self.assertRaisesRegex(ValueError, 'trusted binding: issue_date'):
            self.run_weekly()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.output / 'files').exists())

    def test_reporting_week_binding_cannot_be_changed(self):
        self.bad_binding = 'reporting_window'
        with self.assertRaisesRegex(ValueError, 'trusted binding: reporting_window'):
            self.run_weekly()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.output / 'files').exists())

    def test_one_source_stops_before_review(self):
        self.source_mode = 'single'
        with self.assertRaisesRegex(ValueError, 'at least two retrieved sources'):
            self.run_weekly()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.output / 'files').exists())
        summary = json.loads((self.output / 'run-summary.json').read_text())
        self.assertEqual(summary['status'], 'candidate_sources_withheld')
        self.assertNotIn(FULL_TEXT_MARKER, json.dumps(summary))

    def test_insufficient_collection_recovers_once_before_drafting(self):
        self.initial_source_count = 1
        self.run_weekly()
        self.assertEqual([c['request_id'].split(':')[-1] for c in self.calls],
                         ['discovery', 'source-recovery', 'candidate', 'review'])
        self.assertTrue(self.calls[1]['search'])
        self.assertIn('67890.1:weekly:source-recovery', self.calls[1]['request_id'])
        self.assertTrue((self.output / 'plan.json').exists())

    def test_recovery_excludes_blocked_publisher_not_unrelated_domains(self):
        policy = {'publishers': dict.fromkeys(['apnews.com', 'other.example', 'another.example'])}
        collection = {'rejected': [
            {'url': 'https://www.apnews.com/article/example', 'reason': '403 Client Error: Forbidden'},
            {'url': 'https://another.example/story', 'reason': '429 Client Error: Too Many Requests'},
            {'url': 'https://other.example/story', 'reason': 'Source publication date is ambiguous'}]}
        self.assertEqual(runner.recovery_domains(policy, collection), ['other.example'])
        self.assertEqual(runner.recovery_domains(policy, {}), list(policy['publishers']))

    def test_zero_sources_can_recover_but_cannot_be_published_as_evidence(self):
        self.initial_source_count = 0
        self.run_weekly()
        self.assertEqual(len(self.calls), 4)
        self.assertTrue((self.output / 'plan.json').exists())

    def test_failed_recovery_stops_before_drafting_or_review(self):
        self.initial_source_count = self.recovery_source_count = 1
        with self.assertRaisesRegex(ValueError, 'before drafting'):
            self.run_weekly()
        self.assertEqual([c['request_id'].split(':')[-1] for c in self.calls],
                         ['discovery', 'source-recovery'])
        self.assertFalse((self.output / 'files').exists())
        summary = json.loads((self.output / 'run-summary.json').read_text())
        self.assertEqual(summary['minimum_sources'], 2)
        self.assertFalse(summary['publication_changed'])
        self.assertNotIn(FULL_TEXT_MARKER, json.dumps(summary))

    def test_unknown_source_stops_before_review(self):
        self.source_mode = 'unknown'
        with self.assertRaisesRegex(ValueError, 'no unknown references'):
            self.run_weekly()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.output / 'files').exists())

    def test_review_rejection_preserves_current_issue(self):
        self.reject_review = True
        previous = (self.input / 'accepted/index.html').read_bytes()
        with self.assertRaisesRegex(ValueError, 'withheld'):
            self.run_weekly()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.fetch_count, 0)
        self.assertFalse((self.output / 'files').exists())
        self.assertEqual(previous, (self.input / 'accepted/index.html').read_bytes())
        self.assertFalse(json.loads((self.output / 'run-summary.json').read_text())['publication_changed'])

    def test_empty_checklist_is_not_approval(self):
        self.incomplete_checklist = True
        with self.assertRaisesRegex(ValueError, 'withheld'):
            self.run_weekly()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.fetch_count, 0)
        self.assertFalse((self.output / 'files').exists())


if __name__ == '__main__':
    unittest.main()
