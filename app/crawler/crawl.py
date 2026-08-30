"""Site crawl orchestration."""

from collections import Counter, deque
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
import time

from app.config import AUDIT_MAX_PAGES, AUDIT_REQUEST_DELAY_SECONDS, AUDIT_USER_AGENT
from app.crawler.fetch import canonicalise_crawl_url
from app.crawler.scoring import fetch_website_snapshot, score_website_snapshot
from app.crawler.sitemap import discover_sitemap_pages

def crawl_website(website_url, *, max_pages=None, progress_callback=None):
    """Perform a bounded, same-site crawl seeded by sitemaps and internal links."""
    max_pages = max_pages or AUDIT_MAX_PAGES
    start_url = website_url if website_url.startswith(('http://', 'https://')) else f'https://{website_url}/'
    first_snapshot = fetch_website_snapshot(start_url)
    if not first_snapshot.get('fetched'):
        scored = score_website_snapshot(first_snapshot)
        first_snapshot.update(scored)
        first_snapshot['requested_url'] = start_url
        first_snapshot['fetched_at'] = datetime.utcnow()
        return {
            'status': 'failed', 'start_url': start_url, 'final_url': None,
            'pages_discovered': 1, 'pages_audited': 0, 'pages_failed': 1,
            'pages': [first_snapshot], 'sitemaps': [], 'summary': f"Multi-page audit unavailable: {first_snapshot.get('error', 'The start page could not be fetched.')}",
            'readiness_score': None, 'metadata_score': None, 'content_score': None,
            'crawlability_score': None, 'structured_data_score': None,
        }

    start_host = urlparse(start_url).hostname
    final_host = urlparse(first_snapshot['url']).hostname
    allowed_hosts = {start_host, final_host}
    sitemap_urls, sitemap_records, robots_parser = discover_sitemap_pages(
        first_snapshot['url'], allowed_hosts, max(max_pages * 5, max_pages)
    )

    seed_url = canonicalise_crawl_url(first_snapshot['url'], first_snapshot['url'], allowed_hosts) or first_snapshot['url']
    queue = deque([seed_url])
    for page_url in sitemap_urls:
        if page_url != seed_url:
            queue.append(page_url)
    for href in first_snapshot.get('links', []):
        candidate = canonicalise_crawl_url(first_snapshot['url'], href, allowed_hosts)
        if candidate and candidate not in queue:
            queue.append(candidate)

    seen = set()
    pages = []
    while queue and len(pages) < max_pages:
        requested_url = queue.popleft()
        if requested_url in seen:
            continue
        seen.add(requested_url)
        if requested_url == seed_url:
            snapshot = dict(first_snapshot)
        elif not robots_parser.can_fetch(AUDIT_USER_AGENT, requested_url):
            snapshot = {'fetched': False, 'url': requested_url, 'http_status': None, 'error': 'Blocked by robots.txt for the trySearch audit user agent.'}
        else:
            if AUDIT_REQUEST_DELAY_SECONDS:
                time.sleep(AUDIT_REQUEST_DELAY_SECONDS)
            snapshot = fetch_website_snapshot(requested_url)
        snapshot['requested_url'] = requested_url
        snapshot['fetched_at'] = datetime.utcnow()
        snapshot.update(score_website_snapshot(snapshot))
        pages.append(snapshot)

        if snapshot.get('fetched'):
            for href in snapshot.get('links', []):
                candidate = canonicalise_crawl_url(snapshot['url'], href, allowed_hosts)
                if candidate and candidate not in seen and candidate not in queue and len(queue) < max_pages * 5:
                    queue.append(candidate)
        if progress_callback:
            progress_callback(len(pages), min(max_pages, len(pages) + len(queue)))

    successful = [page for page in pages if page.get('fetched')]
    failed_count = len(pages) - len(successful)
    metric_names = ('readiness_score', 'metadata_score', 'content_score', 'crawlability_score', 'structured_data_score')
    aggregates = {
        metric: round(sum(page[metric] for page in successful) / len(successful)) if successful else None
        for metric in metric_names
    }
    status = 'succeeded' if successful and not failed_count else ('partial' if successful else 'failed')
    discovered_count = max(len(seen) + len(queue), len(sitemap_urls), len(pages))
    summary = (
        f"Audited {len(successful)} of {min(discovered_count, max_pages)} selected pages from {len(sitemap_records)} sitemap source(s). "
        f"The aggregate AI-search readiness score is {aggregates['readiness_score']}% and is derived only from fetched website evidence."
        if successful else 'No public HTML pages could be audited, so no readiness score was calculated.'
    )
    return {
        'status': status,
        'start_url': start_url,
        'final_url': first_snapshot['url'],
        'pages_discovered': discovered_count,
        'pages_audited': len(successful),
        'pages_failed': failed_count,
        'pages': pages,
        'sitemaps': sitemap_records,
        'summary': summary,
        **aggregates,
    }
