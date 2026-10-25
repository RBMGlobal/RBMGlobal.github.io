import copy
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_brief import InvalidBrief, build, inventory, read_json, sha, validate_packet, write_json
from fixtures import fixture, attest


class BriefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packet, self.manifest = fixture(self.root)
        self.old = inventory(self.root / 'accepted')

    def save(self):
        write_json(self.root / 'packet.json', self.packet)
        write_json(self.root / 'manifest.json', self.manifest)
        attest(self.root)

    def validate(self, public=False):
        return validate_packet(self.root / 'packet.json', self.root / 'attestation.json',
                               self.root / 'manifest.json', None if public else self.root / 'sources',
                               self.root / 'accepted', date(2026, 10, 25), public)

    def build(self, name='output', public=False):
        return build(self.root / 'packet.json', self.root / 'attestation.json', self.root / 'manifest.json',
                     None if public else self.root / 'sources', self.root / 'accepted',
                     self.root / 'release.json', self.root / 'lastweek.png', self.root / name,
                     date(2026, 10, 25), public)

    def test_complete_deterministic_package_preserves_archives(self):
        result = self.build()
        self.build('second')
        self.assertEqual(inventory(self.root / 'output'), inventory(self.root / 'second'))
        self.assertEqual(self.old, inventory(self.root / 'accepted'))
        site = self.root / 'output/site'
        self.assertEqual((site / 'archive-2026-10-12.html').read_bytes(), (self.root / 'accepted/index.html').read_bytes())
        self.assertEqual((site / 'archive-2026-10-05.html').read_bytes(), (self.root / 'accepted/archive-2026-10-05.html').read_bytes())
        self.assertEqual(sha(site / 'latest.pdf'), sha(site / 'Nigeria_Weekly_Brief_2026-10-19.pdf'))
        self.assertGreaterEqual(result['validation']['pdf_pages'], 3)
        self.assertNotIn('sources/fixture.txt', result['evidence_files'])
        self.assertEqual(result['network_requests'], 0)

    def test_public_receipt_mode_requires_explicit_switch(self):
        with self.assertRaisesRegex(InvalidBrief, 'Full extracts required'):
            validate_packet(self.root / 'packet.json', self.root / 'attestation.json', self.root / 'manifest.json',
                            None, self.root / 'accepted', date(2026, 10, 25))
        result = self.build(public=True)
        self.assertEqual(result['verification_mode'], 'trusted-public-receipt')

    def test_unbound_attestation_rejected(self):
        self.packet['overview'][0]['text'] += ' Changed after review.'
        write_json(self.root / 'packet.json', self.packet)
        with self.assertRaisesRegex(InvalidBrief, 'not bound'):
            self.validate()

    def test_manifest_binding_and_review_issues(self):
        self.manifest['sources'][0]['title'] = 'Changed after review'
        write_json(self.root / 'manifest.json', self.manifest)
        with self.assertRaisesRegex(InvalidBrief, 'not bound'):
            self.validate()
        attest(self.root)
        approval = read_json(self.root / 'attestation.json'); approval['issues'] = ['Unresolved contradiction']
        write_json(self.root / 'attestation.json', approval)
        with self.assertRaisesRegex(InvalidBrief, 'unresolved'):
            self.validate()

    def test_future_and_overlapping_dates_fail(self):
        self.packet['publication_date'] = '2099-10-19'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'future'):
            self.validate()
        self.packet['publication_date'] = '2026-10-19'
        self.packet['reporting_window']['from'] = '2026-10-11'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'gap/overlap'):
            self.validate()

    def test_unsupported_numbers_fail(self):
        self.packet['overview'][0]['text'] += ' The invented value is 500.'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'unsupported numeric'):
            self.validate()

    def test_unsupported_title_numbers_fail(self):
        self.packet['developments'][0]['title'] = '500 clinics'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'unsupported numeric'):
            self.validate()

    def test_source_tampering_and_nonexistent_quote_fail(self):
        (self.root / 'sources/fixture.txt').write_text('Tampered source')
        with self.assertRaisesRegex(InvalidBrief, 'hash/size'):
            self.validate()

    def test_quote_must_occur_in_source(self):
        self.packet['overview'][0]['evidence'][0]['quote'] = 'This sentence is not in the source.'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'does not occur'):
            self.validate()

    def test_all_six_zones_required(self):
        self.packet['zones'].pop(); self.save()
        with self.assertRaisesRegex(InvalidBrief, 'six zones'):
            self.validate()

    def test_private_url_and_path_traversal_fail(self):
        self.manifest['sources'][0]['url'] = 'https://127.0.0.1/private'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'Private source IP'):
            self.validate()
        self.manifest['sources'][0]['url'] = 'https://publisher.example.test/fixture'
        self.manifest['sources'][0]['extract_path'] = '../secret'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'Unsafe evidence path'):
            self.validate()

    def test_copyright_budgets(self):
        self.packet['overview'][0]['text'] = 'word ' * 201; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'derived-word budget'):
            self.validate()

    def test_unique_quote_budget(self):
        quote = ' '.join('term' + chr(97 + i) for i in range(26))
        (self.root / 'sources/fixture.txt').write_text(quote)
        self.manifest['sources'][0]['text_sha256'] = sha(self.root / 'sources/fixture.txt')
        for key in ['overview', 'humanitarian_context']:
            self.packet[key][0]['evidence'][0]['quote'] = quote
        self.packet['developments'][0]['paragraphs'][0]['evidence'][0]['quote'] = quote
        self.packet['developments'][0]['paragraphs'][0]['text'] = 'Synthetic service example.'
        self.save()
        with self.assertRaisesRegex(InvalidBrief, 'exact-excerpt budget'):
            self.validate()

    def test_hash_mismatch_preserves_last_site_and_has_no_partial_output(self):
        (self.root / 'lastweek.png').write_bytes(b'broken image')
        with self.assertRaisesRegex(InvalidBrief, 'does not match'):
            self.build()
        self.assertFalse((self.root / 'output').exists())
        self.assertEqual(inventory(self.root / 'accepted'), self.old)

    def test_image_date_and_ingestion_coverage_are_separate(self):
        self.build()
        html = (self.root / 'output/site/index.html').read_text()
        self.assertIn('24 September 2026 to 30 September 2026', html)
        self.assertIn('12 October 2026 to 18 October 2026', html)
        self.assertIn('Daily input coverage is complete from 23 September 2026', html)
        self.assertIn('map-2026-10-19.png', html)
        self.assertIn('AI-generated, source-checked', html)

    def test_untrusted_markup_is_escaped(self):
        self.packet['overview'][0]['text'] = '<script>alert("example")</script> & synthetic text'
        self.save(); self.build()
        page = (self.root / 'output/site/index.html').read_text()
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)

    def test_symlink_and_duplicate_json_rejected(self):
        (self.root / 'accepted/link').symlink_to(self.root / 'packet.json')
        with self.assertRaisesRegex(InvalidBrief, 'Symlinks'):
            self.build()
        bad = self.root / 'duplicate.json'; bad.write_text('{"a":1,"a":2}')
        with self.assertRaisesRegex(InvalidBrief, 'Duplicate JSON'):
            read_json(bad)

    def test_incomplete_ingestion_and_filename_collision_rejected(self):
        release = read_json(self.root / 'release.json'); release['coverage']['gdelt']['input_days'] = 25
        write_json(self.root / 'release.json', release)
        with self.assertRaisesRegex(InvalidBrief, 'incomplete'):
            self.build()
        release['coverage']['gdelt']['input_days'] = 26; write_json(self.root / 'release.json', release)
        (self.root / 'accepted/map-2026-10-19.png').write_bytes(b'old archive bytes')
        with self.assertRaisesRegex(InvalidBrief, 'collision'):
            self.build()

    def test_public_packet_rejects_hidden_article_body(self):
        self.manifest['sources'][0]['full_article'] = 'A full article must not be packaged.'
        self.save()
        with self.assertRaisesRegex(InvalidBrief, 'unsupported fields'):
            self.validate(public=True)

    def test_public_packet_rejects_absolute_source_paths(self):
        self.manifest['sources'][0]['extract_path'] = '/private/source.txt'; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'Unsafe evidence path'):
            self.validate(public=True)

    def test_unknown_source_reference_fails(self):
        self.packet['overview'][0]['source_ids'] = ['missing']; self.save()
        with self.assertRaisesRegex(InvalidBrief, 'unknown/duplicate source'):
            self.validate()

    def test_existing_archive_cannot_be_overwritten(self):
        (self.root / 'accepted/archive-2026-10-12.html').write_text('Different accepted archive')
        with self.assertRaisesRegex(InvalidBrief, 'Previous archive differs'):
            self.build()

    def test_failed_pdf_validation_leaves_no_partial_target(self):
        from unittest.mock import patch
        with patch('build_brief.validate_output', side_effect=InvalidBrief('PDF failure')):
            with self.assertRaisesRegex(InvalidBrief, 'PDF failure'):
                self.build()
        self.assertFalse((self.root / 'output').exists())
        self.assertFalse(list(self.root.glob('.output-*')))
        self.assertEqual(self.old, inventory(self.root / 'accepted'))

    def test_public_snapshot_omits_automation_and_private_work(self):
        existing = self.root / 'accepted'
        for name in ('automation/brief/tests/fixture.json', 'automation/pipeline.py',
                     '__pycache__/private.pyc', 'private-work.json', '.github/workflows/publish.yml'):
            path = existing / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Never publish this source/test/private file.')
        self.build()
        published = inventory(self.root / 'output/site')
        self.assertFalse(any('automation' in p or '__pycache__' in p or 'private' in p or '.github' in p for p in published))


if __name__ == '__main__':
    unittest.main()
