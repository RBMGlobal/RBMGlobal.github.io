"""Offline tests for stable approved receipts and narrow publication adapters."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publication
from brief.tests.fixtures import fixture


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def record(data):
    return {'sha256': publication.sha(data), 'bytes': len(data)}


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root / 'input')
        self.site = self.root / 'input/accepted'
        names = {'candidate.json': 'packet.json', 'sources.json': 'manifest.json',
                 'attestation.json': 'attestation.json', 'events-release.json': 'release.json',
                 'lastweek.png': 'lastweek.png'}
        self.payloads = {name: (self.root / 'input' / path).read_bytes() for name, path in names.items()}
        self.receipt = {'schema_version': 1, 'issue_date': '2026-10-19',
                        'files': {name: record(data) for name, data in self.payloads.items()}}
        self.urls = {publication.WEEKLY + '2026-10-19/' + name: data for name, data in self.payloads.items()}
        self.urls[publication.WEEKLY + 'latest.json'] = encoded(self.receipt)

    def fetch(self, url, **kwargs):
        return self.urls[url]

    def test_stable_receipt_fetches_only_complete_verified_public_files(self):
        with patch('publication.get', side_effect=self.fetch):
            result = publication.fetch_weekly(self.root / 'approved')
        self.assertEqual(result, self.receipt)
        self.assertEqual((self.root / 'approved/candidate.json').read_bytes(), self.payloads['candidate.json'])
        self.assertEqual({p.name for p in (self.root / 'approved').iterdir()}, set(self.payloads) | {'receipt.json'})

    def test_changed_receipt_leaves_no_partial_output(self):
        reads = []
        def get(url, **kwargs):
            if url.endswith('/latest.json'):
                reads.append(url)
                if len(reads) > 1:
                    return b'changed receipt'
            return self.fetch(url, **kwargs)
        with patch('publication.get', side_effect=get), self.assertRaisesRegex(ValueError, 'changed during'):
            publication.fetch_weekly(self.root / 'approved')
        self.assertFalse((self.root / 'approved').exists())

    def test_hash_mismatch_and_extra_public_files_fail(self):
        self.urls[publication.WEEKLY + '2026-10-19/candidate.json'] = b'changed candidate'
        with patch('publication.get', side_effect=self.fetch), self.assertRaisesRegex(ValueError, 'accepted receipt'):
            publication.fetch_weekly(self.root / 'approved')
        self.assertFalse((self.root / 'approved').exists())
        self.receipt['files']['full-article.txt'] = record(b'not permitted')
        self.urls[publication.WEEKLY + 'latest.json'] = encoded(self.receipt)
        with patch('publication.get', side_effect=self.fetch), self.assertRaisesRegex(ValueError, 'only public evidence'):
            publication.fetch_weekly(self.root / 'approved')

    def test_missing_receipt_does_not_touch_publication(self):
        with patch('publication.get', return_value=None):
            self.assertIsNone(publication.fetch_weekly(self.root / 'approved'))
        self.assertFalse((self.root / 'approved').exists())

    def test_invalid_calendar_date_fails_before_payload_fetch(self):
        self.receipt['issue_date'] = '2026-99-99'
        with patch('publication.get', return_value=encoded(self.receipt)) as get:
            with self.assertRaises(ValueError):
                publication.fetch_weekly(self.root / 'approved')
        self.assertEqual(get.call_count, 1)

    def test_receipt_type_guards(self):
        with self.assertRaises(ValueError):
            publication.equal(b'a', {'sha256': publication.sha(b'a'), 'bytes': True})
        with self.assertRaises(ValueError):
            publication.equal(b'a', {'sha256': 'invalid', 'bytes': 1})

    def test_consume_builds_then_retry_is_unchanged_and_checks_saved_hashes(self):
        work = self.root / 'work'; work.mkdir()
        before = (self.site / 'index.html').read_bytes()
        with patch('publication.get', side_effect=self.fetch):
            result = publication.consume_weekly(self.site, work)
        self.assertEqual(result['status'], 'validated')
        self.assertEqual((self.site / 'archive-2026-10-12.html').read_bytes(), before)
        work = self.root / 'retry'; work.mkdir()
        with patch('publication.get', side_effect=self.fetch):
            result = publication.consume_weekly(self.site, work)
        self.assertEqual(result['status'], 'unchanged')
        (self.site / 'latest.pdf').write_bytes(b'corrupted PDF')
        work = self.root / 'corrupt-retry'; work.mkdir()
        with patch('publication.get', side_effect=self.fetch), self.assertRaisesRegex(ValueError, 'accepted receipt'):
            publication.consume_weekly(self.site, work)

    def test_public_snapshot_omits_automation_and_private_files(self):
        (self.site / 'automation').mkdir()
        (self.site / 'automation/provider-token.txt').write_text('synthetic excluded value')
        (self.site / 'private-research.json').write_text('synthetic private work')
        result = publication.public_weekly_snapshot(self.site, self.root / 'artifact')
        self.assertNotIn('private-research.json', result['files'])
        self.assertFalse((self.root / 'artifact/automation').exists())

    def test_symlink_ancestor_cannot_supply_company_publication(self):
        company = self.root / 'company'; (company / 'sentiment').mkdir(parents=True)
        (company / 'sentiment/data.json').write_bytes(b'baseline')
        plan = self.root / 'plan'; (plan / 'files').mkdir(parents=True)
        other = self.root / 'outside'; other.mkdir()
        (other / 'data.json').write_bytes(b'new')
        (plan / 'files/sentiment').symlink_to(other, target_is_directory=True)
        (plan / 'plan.json').write_bytes(encoded({'baseline_sha256': publication.sha(b'baseline'),
                                                'files': {'sentiment/data.json': record(b'new')}}))
        with self.assertRaisesRegex(ValueError, 'Linked publication'):
            publication.apply_company(plan, company)
        self.assertEqual((company / 'sentiment/data.json').read_bytes(), b'baseline')

    def test_live_verification_requires_one_complete_matching_pass(self):
        results = iter([b'a', b'wrong', b'wrong', b'b'])
        manifest = {'files': {'a': record(b'a'), 'b': record(b'b')}}
        with patch('publication.get', side_effect=lambda *args: next(results)), \
             patch('publication.time.sleep'), patch('publication.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaisesRegex(ValueError, 'Live byte verification failed'):
                publication.verify_live(manifest, 'https://example.test/', timeout=1)


if __name__ == '__main__':
    unittest.main()
