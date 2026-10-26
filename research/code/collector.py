"""Fetch bounded public evidence. Model text never controls credentials or file paths."""
import hashlib
import io
import ipaddress
import json
import re
import socket
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

MAX_BYTES = 5_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def publisher_for(url, policy):
    if not isinstance(url, str) or len(url) > 4096 or re.search(r'[\s\\\x00-\x1f]', url):
        raise ValueError('Evidence URL contains invalid characters')
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise ValueError('Evidence URL must be ordinary public HTTPS without credentials or fragments')
    host = (p.hostname or '').lower()
    for domain, publisher in policy['publishers'].items():
        if host == domain or host.endswith('.' + domain):
            return publisher
    raise ValueError('Evidence host is not in the approved publisher list: ' + host)


def public_dns(url):
    host = urlsplit(url).hostname
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not answers or any(not ipaddress.ip_address(a[4][0]).is_global for a in answers):
        raise ValueError('Evidence host does not resolve exclusively to public IP addresses')


def fetch(url, policy, session=None):
    """Allowlisted publishers only; check every redirect and never carry auth/cookies."""
    session = session or requests.Session()
    session.trust_env = False
    session.auth = None
    session.params = {}
    session.proxies = {}
    session.cert = None
    session.verify = True
    session.headers.clear()
    start_publisher = publisher_for(url, policy)
    for _ in range(6):
        if publisher_for(url, policy) != start_publisher:
            raise ValueError('Cross-publisher redirect requires editorial configuration')
        public_dns(url)
        session.cookies.clear()
        with session.get(url, timeout=(15, 45), allow_redirects=False, stream=True,
                         headers={'User-Agent': 'RBM-Public-Research/1.0 (+https://rbmglobal.co.uk/)',
                                  'Accept': 'text/html,application/pdf,text/plain'}) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers['Location'])
                continue
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError('Evidence response is not complete HTTP 200')
            length = response.headers.get('Content-Length')
            if length and length.isdigit() and int(length) > MAX_BYTES:
                raise ValueError('Evidence exceeds the bounded download limit')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Evidence exceeds the bounded download limit')
                chunks.append(chunk)
            body = b''.join(chunks)
            return body, response.headers.get('Content-Type', ''), url
    raise ValueError('Too many evidence redirects')


def extract(body, content_type):
    metadata_dates = []
    first_publication_dates = []
    if body.startswith(b'%PDF-'):
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted or len(reader.pages) > 250:
            raise ValueError('Encrypted or oversized PDF evidence is unsupported')
        text = '\n\n'.join(page.extract_text() or '' for page in reader.pages)
    elif 'html' in content_type:
        soup = BeautifulSoup(body, 'html.parser')
        for meta in soup.find_all('meta'):
            key = meta.get('property', meta.get('name', '')).lower()
            if key in (
                    'article:published_time', 'datepublished', 'date', 'dc.date', 'dcterms.date'):
                value = meta.get('content', '')
                if re.match(r'^\d{4}-\d{2}-\d{2}', value):
                    metadata_dates.append(value[:10])
                    if key in ('article:published_time', 'datepublished'):
                        first_publication_dates.append(value[:10])
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                def walk(obj):
                    if isinstance(obj, dict):
                        kinds = obj.get('@type', [])
                        kinds = [kinds] if isinstance(kinds, str) else kinds
                        if set(kinds) & {'Article', 'NewsArticle', 'Report', 'ScholarlyArticle', 'BlogPosting', 'WebPage'} and isinstance(obj.get('datePublished'), str):
                            metadata_dates.append(obj['datePublished'][:10])
                            first_publication_dates.append(obj['datePublished'][:10])
                        # Do not treat nested recommended-story, publisher or
                        # breadcrumb dates as the current article's publication.
                        if isinstance(obj.get('@graph'), list):
                            walk(obj['@graph'])
                    elif isinstance(obj, list):
                        for item in obj:
                            walk(item)
                walk(json.loads(script.string or script.get_text()))
            except (ValueError, TypeError):
                pass
        for el in soup(['script', 'style', 'nav', 'footer', 'noscript', 'form']):
            el.decompose()
        text = soup.get_text(' ', strip=True)
    elif 'text/plain' in content_type:
        text = body.decode('utf-8')
    else:
        raise ValueError('Evidence must be readable HTML, PDF or UTF-8 text')
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) < 150 or len(text.encode()) > MAX_BYTES:
        raise ValueError('Evidence is empty, inaccessible or too large')
    valid_dates = []
    for value in metadata_dates:
        try:
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                date.fromisoformat(value)
                valid_dates.append(value)
        except ValueError:
            pass
    return text, sorted(set(valid_dates)), sorted(set(first_publication_dates) & set(valid_dates))


def date_supported(value, quote, text, metadata_dates, first_publication_dates=()):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Publication date must be YYYY-MM-DD')
    d = date.fromisoformat(value)
    if d > datetime.now(timezone.utc).date():
        raise ValueError('Future source publication date')
    if metadata_dates and set(metadata_dates) != {value}:
        raise ValueError('Source publication date is ambiguous or contradicts page metadata')
    # Models may wrap a copied date in quotation marks. Remove only a matched
    # outer delimiter; the remaining full excerpt must still occur verbatim.
    if isinstance(quote, str):
        quote = quote.strip()
        if len(quote) > 1 and (quote[0], quote[-1]) in {('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’')}:
            quote = quote[1:-1]
    if not isinstance(quote, str) or not 4 <= len(quote) <= 300 or quote not in text:
        raise ValueError('Source publication date needs an exact visible excerpt')
    compact = re.sub(r'[^a-z0-9]', '', quote.lower())
    if re.search(r'\b(updated|modified|revised)\b', quote, re.I) and not re.search(r'\b(published|issued|released)\b', quote, re.I):
        # Some publishers label every visible date "Updated". Accept that day
        # only when explicit first-publication metadata independently agrees.
        # Generic date metadata and a later update never establish first publication.
        if set(first_publication_dates) != {value}:
            raise ValueError('An update date alone is not a publication date')
    formats = [d.isoformat(), d.strftime('%d %B %Y'), d.strftime('%B %d %Y'),
               d.strftime('%d %b %Y'), d.strftime('%b %d %Y'), d.strftime('%d/%m/%Y')]
    formats += [x.replace(' 0', ' ').lstrip('0') for x in formats]
    if not any(re.search(r'(?<!\d)' + re.escape(re.sub(r'[^a-z0-9]', '', f.lower())) + r'(?!\d)', compact) for f in formats):
        raise ValueError('Source date cannot be independently located in the excerpt')


def source_identity(url):
    """Queries can identify distinct reports; do not authorize one via another."""
    parts = urlsplit(url)
    return ((parts.hostname or '').lower(), parts.path.rstrip('/'), parts.query)


def collect(proposals, policy, output, consulted_urls):
    output = Path(output)
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= policy['max_sources']:
        raise ValueError('Discovery must supply a bounded nonempty source set')
    output.mkdir(parents=True, exist_ok=False)
    consulted = {source_identity(u) for u in consulted_urls if isinstance(u, str)}
    sources, rejected, seen, seen_text = [], [], set(), set()
    publication_evidence = {}
    for proposal in proposals:
        url = proposal.get('url', '') if isinstance(proposal, dict) else ''
        try:
            publisher = publisher_for(url, policy)
            identity = source_identity(url)
            if identity not in consulted or identity in seen:
                raise ValueError('Source was not returned by real web research or is duplicated')
            body, content_type, final_url = fetch(url, policy)
            text, metadata_dates, first_publication_dates = extract(body, content_type)
            final_identity = source_identity(final_url)
            text_hash = digest(text.encode('utf-8'))
            if final_identity in seen or text_hash in seen_text:
                raise ValueError('Duplicate final source or identical extracted content')
            date_supported(proposal['published_at'], proposal['publication_date_quote'], text,
                           metadata_dates, first_publication_dates)
            title = proposal['title']
            if not isinstance(title, str) or not 3 <= len(title) <= 500:
                raise ValueError('Invalid source title')
            if re.sub(r'\s+', ' ', title).strip().casefold() not in text.casefold():
                raise ValueError('Proposed title is not present in the fetched source')
            sid = 's' + str(len(sources) + 1)
            extract_path = sid + '.txt'
            content = text.encode('utf-8')
            (output / extract_path).write_bytes(content)
            sources.append(dict(id=sid, url=url, final_url=final_url, title=title, publisher=publisher,
                                published_at=proposal['published_at'],
                                fetched_at=datetime.now(timezone.utc).isoformat(), http_status=200,
                                body_sha256=digest(body), text_sha256=digest(content), extract_path=extract_path))
            publication_evidence[sid] = {'visible_excerpt': proposal['publication_date_quote'],
                                        'first_publication_metadata': first_publication_dates,
                                        'all_publication_metadata': metadata_dates}
            seen.add(identity)
            seen.add(final_identity)
            seen_text.add(text_hash)
        except (ValueError, KeyError, requests.RequestException, OSError) as exc:
            rejected.append({'url': url, 'reason': str(exc)[:300]})
    write_json(output / 'collection.json', {'scope': 'Bounded public-source search, not exhaustive',
                                           'accepted': len(sources), 'rejected': rejected})
    if not sources:
        raise ValueError('No source passed collection and publication-date checks')
    manifest = {'schema_version': 1, 'sources': sources}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'publication-evidence.json', publication_evidence)
    return manifest
