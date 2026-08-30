"""Page snapshot capture and readiness scoring."""

from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
import re

from app.config import AUDIT_PAGE_BYTES, RAG_DOCUMENT_MAX_CHARS
from app.crawler.fetch import fetch_public_resource, normalise_site_host
from app.crawler.parser import WebsiteAuditParser

def fetch_website_snapshot(website_url):
    """Fetch a public page and return transparent, first-party page evidence.

    These facts are intentionally limited to what the site itself exposes. They
    are not presented as a measurement of third-party AI model results.
    """
    start_url = website_url if website_url.startswith(('http://', 'https://')) else f'https://{website_url}/'
    try:
        resource = fetch_public_resource(
            start_url,
            max_bytes=AUDIT_PAGE_BYTES,
            accepted_types={'text/html', 'application/xhtml+xml'},
        )
        parser = WebsiteAuditParser()
        parser.feed(resource['body'].decode(resource['charset'], errors='replace'))
        parser.close()
        final_host = urlparse(resource['url']).hostname
        internal_links = []
        external_links = 0
        for href in parser.link_hrefs:
            absolute = urljoin(resource['url'], href)
            parsed_link = urlparse(absolute)
            if parsed_link.scheme in {'http', 'https'} and parsed_link.hostname:
                if normalise_site_host(parsed_link.hostname) == normalise_site_host(final_host):
                    internal_links.append(absolute)
                else:
                    external_links += 1
        return {
            'fetched': True,
            'url': resource['url'],
            'http_status': resource['status'],
            'title': ' '.join(parser.title_parts).strip(),
            'description': parser.description,
            'headings': parser.heading_parts[:12],
            'word_count': len(re.findall(r"\b[\w'-]+\b", ' '.join(parser.text_parts))),
            'schema_blocks': parser.schema_blocks,
            'canonical': parser.canonical,
            'noindex': 'noindex' in parser.robots,
            'language': parser.language,
            'internal_links': len(internal_links),
            'external_links': external_links,
            'links': internal_links,
            # Store normalized visible copy, not raw HTML. This both reduces
            # retained data and prevents scripts/styles from entering the RAG
            # corpus. The persisted copy is capped again during indexing.
            'content_text': ' '.join(parser.text_parts).strip()[:RAG_DOCUMENT_MAX_CHARS],
        }
    except HTTPError as error:
        return {'fetched': False, 'url': start_url, 'http_status': error.code, 'error': f'HTTP {error.code}: {error.reason}'}
    except (URLError, TimeoutError, ValueError, OSError) as error:
        return {'fetched': False, 'url': start_url, 'http_status': None, 'error': str(error)}

def score_website_snapshot(snapshot):
    """Score one fetched page and return explicit, page-level findings."""
    if not snapshot.get('fetched'):
        return {
            'readiness_score': None, 'metadata_score': None, 'content_score': None,
            'crawlability_score': None, 'structured_data_score': None,
            'findings': [{
                'code': 'fetch_failed', 'area': 'Access', 'severity': 'high',
                'evidence': snapshot.get('error') or 'The page could not be fetched.',
                'recommendation': 'Confirm the exact public URL, DNS, status code, and crawler access before retrying.',
            }],
        }

    title_present = bool(snapshot.get('title'))
    description_present = bool(snapshot.get('description'))
    metadata_score = (50 if title_present else 0) + (50 if description_present else 0)
    headings_count = len(snapshot.get('headings') or [])
    word_count = snapshot.get('word_count') or 0
    content_score = min(100, round(min(word_count, 900) / 9 * 0.72 + min(headings_count, 6) / 6 * 35))
    structured_data_score = 100 if snapshot.get('schema_blocks') else 0
    if snapshot.get('noindex'):
        crawlability_score = 0
    elif snapshot.get('canonical'):
        crawlability_score = 100
    else:
        crawlability_score = 78
    readiness_score = round((metadata_score + content_score + structured_data_score + crawlability_score) / 4)

    findings = []
    if not title_present:
        findings.append({'code': 'missing_title', 'area': 'On-page', 'severity': 'high', 'evidence': 'No HTML title was found.', 'recommendation': 'Add a unique, descriptive title that names the page topic and brand.'})
    if not description_present:
        findings.append({'code': 'missing_description', 'area': 'On-page', 'severity': 'medium', 'evidence': 'No meta or Open Graph description was found.', 'recommendation': 'Add a concise description of the page answer, offer, and audience.'})
    if headings_count == 0:
        findings.append({'code': 'missing_headings', 'area': 'Content', 'severity': 'high', 'evidence': 'No H1–H3 headings were found.', 'recommendation': 'Organise the page with one clear H1 and question-led supporting headings.'})
    if word_count < 250:
        findings.append({'code': 'thin_content', 'area': 'Content', 'severity': 'medium', 'evidence': f'{word_count} visible words were found.', 'recommendation': 'Add direct answers, original examples, definitions, and supporting evidence for the page topic.'})
    if not snapshot.get('schema_blocks'):
        findings.append({'code': 'missing_schema', 'area': 'Structured data', 'severity': 'medium', 'evidence': 'No JSON-LD blocks were found.', 'recommendation': 'Add valid JSON-LD that accurately describes the organisation, service, product, article, or page.'})
    if snapshot.get('noindex'):
        findings.append({'code': 'noindex', 'area': 'Crawlability', 'severity': 'critical', 'evidence': 'A noindex robots directive was found.', 'recommendation': 'Remove noindex if this page should be discoverable in public search.'})
    if not snapshot.get('canonical'):
        findings.append({'code': 'missing_canonical', 'area': 'Crawlability', 'severity': 'low', 'evidence': 'No canonical link was found.', 'recommendation': 'Add a self-referencing canonical URL when this is the preferred public version.'})
    if not snapshot.get('language'):
        findings.append({'code': 'missing_language', 'area': 'Accessibility', 'severity': 'low', 'evidence': 'The HTML element has no lang attribute.', 'recommendation': 'Set the document language so parsers can interpret the page correctly.'})
    return {
        'readiness_score': readiness_score,
        'metadata_score': metadata_score,
        'content_score': content_score,
        'crawlability_score': crawlability_score,
        'structured_data_score': structured_data_score,
        'findings': findings,
    }
