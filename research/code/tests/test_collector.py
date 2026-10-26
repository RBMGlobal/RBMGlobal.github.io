"""Offline collector tests: no provider calls or real network requests."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collector

POLICY = {'max_sources': 4, 'publishers': {'publisher.example.test': 'Publisher',
                                          'other.example.test': 'Other'}}
URL = 'https://publisher.example.test/report'
TITLE = 'Synthetic services report'
DATE_QUOTE = 'Published 12 October 2026'
HTML = ('<html><head><title>' + TITLE + '</title><meta property="article:published_time" '
        'content="2026-10-12T08:00:00Z"></head><body><h1>' + TITLE + '</h1><p>' + DATE_QUOTE + '</p><article>'
        + 'Synthetic public-source information for offline validation. ' * 5 + '</article></body></html>').encode()


class Response:
    def __init__(self, body=HTML, status=200, headers=None):
        self.status_code = status
        self.is_redirect = status in (301, 302, 303, 307, 308)
        self.headers = headers or {'Content-Type': 'text/html'}
        self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))
    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start+size]


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proposal = {'url': URL, 'title': TITLE, 'published_at': '2026-10-12',
                         'publication_date_quote': DATE_QUOTE}

    def collect(self, proposals=None, urls=None, fetched=None):
        with patch('collector.fetch', return_value=fetched or (HTML, 'text/html', URL)):
            return collector.collect(proposals or [self.proposal], POLICY, self.root / 'out', urls or [URL])

    def test_valid_collection_produces_hash_bound_manifest(self):
        manifest = self.collect()
        source = manifest['sources'][0]
        extract = (self.root / 'out' / source['extract_path']).read_bytes()
        self.assertEqual(source['text_sha256'], collector.digest(extract))
        self.assertEqual(source['body_sha256'], collector.digest(HTML))
        self.assertEqual(source['published_at'], '2026-10-12')
        self.assertEqual(source['publisher'], 'Publisher')

    def test_date_must_occur_even_when_metadata_matches(self):
        with self.assertRaisesRegex(ValueError, 'independently located'):
            collector.date_supported('2026-10-12', 'Synthetic services report',
                                     'Synthetic services report', ['2026-10-12'])

    def test_quoted_date_delimiters_do_not_change_evidence(self):
        collector.date_supported('2026-10-12', '“' + DATE_QUOTE + '”', DATE_QUOTE, ['2026-10-12'])
        with self.assertRaises(ValueError):
            collector.date_supported('2026-10-11', '“Published 11 October 2026”', DATE_QUOTE, ['2026-10-12'])
        with self.assertRaises(ValueError):
            collector.date_supported('2026-10-12', '“October 2026”', 'October 2026', [])

    def test_first_of_month_does_not_match_eleventh(self):
        quote = 'Published 11 October 2026'
        with self.assertRaisesRegex(ValueError, 'independently located'):
            collector.date_supported('2026-10-01', quote, quote, [])

    def test_updated_date_is_not_publication_date(self):
        quote = 'Last updated 12 October 2026'
        with self.assertRaisesRegex(ValueError, 'update date alone'):
            collector.date_supported('2026-10-12', quote, quote, [])

    def test_same_day_update_requires_explicit_first_publication_metadata(self):
        quote = 'Updated 12 October 2026'
        collector.date_supported('2026-10-12', quote, quote, ['2026-10-12'], ['2026-10-12'])
        with self.assertRaisesRegex(ValueError, 'update date alone'):
            collector.date_supported('2026-10-12', quote, quote, ['2026-10-12'])
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            collector.date_supported('2026-10-12', quote, quote, ['2026-10-11'], ['2026-10-11'])

    def test_updated_source_keeps_first_publication_proof_for_review(self):
        self.proposal['publication_date_quote'] = 'Updated 12 October 2026'
        body = HTML.replace(DATE_QUOTE.encode(), self.proposal['publication_date_quote'].encode())
        self.collect(fetched=(body, 'text/html', URL))
        proof = json.loads((self.root / 'out/publication-evidence.json').read_text())['s1']
        self.assertEqual(proof['first_publication_metadata'], ['2026-10-12'])
        self.assertEqual(proof['visible_excerpt'], self.proposal['publication_date_quote'])

    def test_generic_metadata_does_not_establish_first_publication(self):
        body = HTML.replace(b'article:published_time', b'date')
        _, dates, first = collector.extract(body, 'text/html')
        self.assertEqual(dates, ['2026-10-12'])
        self.assertEqual(first, [])

    def test_ambiguous_metadata_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            collector.date_supported('2026-10-12', DATE_QUOTE, DATE_QUOTE,
                                     ['2026-10-12', '2026-10-11'])

    def test_nested_recommended_article_date_ignored(self):
        body = b'<html><body>' + b'Public test text. ' * 20 + b'''<script type="application/ld+json">{
          "@type":"NewsArticle", "datePublished":"2026-10-12",
          "related":{"@type":"NewsArticle","datePublished":"2026-10-11"}}
          </script></body></html>'''
        _, dates, first = collector.extract(body, 'text/html')
        self.assertEqual(dates, ['2026-10-12'])
        self.assertEqual(first, ['2026-10-12'])

    def test_future_source_date_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Future'):
            collector.date_supported('9999-10-12', '12 October 9999', '12 October 9999', [])

    def test_invented_title_rejected_with_diagnostic(self):
        self.proposal['title'] = 'Invented article heading'
        with self.assertRaisesRegex(ValueError, 'No source passed'):
            self.collect()
        record = json.loads((self.root / 'out/collection.json').read_text())
        self.assertIn('title is not present', record['rejected'][0]['reason'])
        self.assertFalse((self.root / 'out/manifest.json').exists())

    def test_query_string_is_part_of_consulted_identity(self):
        self.proposal['url'] = URL + '?id=2'
        with self.assertRaisesRegex(ValueError, 'No source passed'):
            self.collect(urls=[URL + '?id=1'])

    def test_duplicate_final_url_or_content_not_counted_twice(self):
        second = dict(self.proposal, url=URL + '?mirror=1')
        manifest = self.collect([self.proposal, second], [URL, second['url']])
        self.assertEqual(len(manifest['sources']), 1)
        result = json.loads((self.root / 'out/collection.json').read_text())
        self.assertIn('Duplicate final', result['rejected'][0]['reason'])

    def test_malformed_proposal_does_not_abort_other_sources(self):
        manifest = self.collect([{}, self.proposal])
        self.assertEqual(len(manifest['sources']), 1)

    def test_bad_proposal_count_creates_no_directory(self):
        with self.assertRaisesRegex(ValueError, 'bounded nonempty'):
            collector.collect([], POLICY, self.root / 'out', [])
        self.assertFalse((self.root / 'out').exists())

    def test_private_dns_and_malformed_urls_rejected(self):
        answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch('collector.socket.getaddrinfo', return_value=answers):
            with self.assertRaisesRegex(ValueError, 'public IP'):
                collector.public_dns(URL)
        for value in (URL + '\n', 'https://publisher.example.test.evil.test/report',
                      'https://name:password@publisher.example.test/report'):
            with self.assertRaises(ValueError):
                collector.publisher_for(value, POLICY)

    def test_auth_cookies_env_and_query_state_not_forwarded(self):
        session = requests.Session()
        session.auth = ('secret', 'secret')
        session.headers['Authorization'] = 'never send'
        session.headers['Cookie'] = 'never send'
        session.params = {'api_key': 'never send'}
        session.cookies.set('private', 'never send')
        with patch('collector.public_dns'), patch.object(session, 'get', return_value=Response()) as get:
            collector.fetch(URL, POLICY, session=session)
        self.assertFalse(session.trust_env)
        self.assertIsNone(session.auth)
        self.assertEqual(session.params, {})
        self.assertEqual(dict(session.headers), {})
        self.assertEqual(len(session.cookies), 0)
        self.assertEqual(get.call_args.kwargs['allow_redirects'], False)
        self.assertTrue(session.verify)

    def test_redirects_checked_and_download_bounded(self):
        session = requests.Session()
        with patch('collector.public_dns'), patch.object(session, 'get', return_value=Response(
                status=302, headers={'Location': 'https://other.example.test/report'})):
            with self.assertRaisesRegex(ValueError, 'Cross-publisher'):
                collector.fetch(URL, POLICY, session=session)
        with patch('collector.public_dns'), patch.object(session, 'get', return_value=Response(
                headers={'Content-Length': str(collector.MAX_BYTES + 1)})):
            with self.assertRaisesRegex(ValueError, 'download limit'):
                collector.fetch(URL, POLICY, session=session)


if __name__ == '__main__':
    unittest.main()
