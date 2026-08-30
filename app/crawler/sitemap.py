"""Sitemap discovery."""

from collections import Counter, deque
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
import re
import xml.etree.ElementTree as ET

from app.config import AUDIT_SITEMAP_BYTES
from app.crawler.fetch import canonicalise_crawl_url, fetch_public_resource

def sitemap_locations(xml_body):
    root = ET.fromstring(xml_body)
    root_name = root.tag.rsplit('}', 1)[-1].lower()
    locations = []
    for element in root.iter():
        if element.tag.rsplit('}', 1)[-1].lower() == 'loc' and element.text:
            locations.append(element.text.strip())
    return root_name, locations

def discover_sitemap_pages(base_url, allowed_hosts, max_candidates):
    """Read robots.txt and bounded sitemap indexes without leaving the site."""
    parsed_base = urlparse(base_url)
    origin = f'{parsed_base.scheme}://{parsed_base.netloc}'
    robots_url = urljoin(origin, '/robots.txt')
    sitemap_queue = deque()
    sitemap_records = []
    robots_parser = RobotFileParser()
    robots_parser.set_url(robots_url)
    try:
        resource = fetch_public_resource(robots_url, max_bytes=500_000, accepted_types={'text/plain', 'text/html'})
        robots_text = resource['body'].decode(resource['charset'], errors='replace')
        robots_parser.parse(robots_text.splitlines())
        for line in robots_text.splitlines():
            match = re.match(r'^\s*sitemap\s*:\s*(\S+)\s*$', line, flags=re.I)
            if match:
                candidate = canonicalise_crawl_url(origin, match.group(1), allowed_hosts)
                if candidate:
                    sitemap_queue.append(candidate)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        # Absence of robots.txt does not block a public audit.
        robots_parser.parse([])

    default_sitemap = canonicalise_crawl_url(origin, '/sitemap.xml', allowed_hosts)
    if default_sitemap and default_sitemap not in sitemap_queue:
        sitemap_queue.append(default_sitemap)

    page_urls = []
    visited_sitemaps = set()
    while sitemap_queue and len(visited_sitemaps) < 8 and len(page_urls) < max_candidates:
        sitemap_url = sitemap_queue.popleft()
        if sitemap_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sitemap_url)
        record = {'url': sitemap_url, 'status': 'failed', 'urls_discovered': 0, 'error': None}
        try:
            resource = fetch_public_resource(
                sitemap_url,
                max_bytes=AUDIT_SITEMAP_BYTES,
                accepted_types={'application/xml', 'text/xml', 'application/rss+xml', 'text/plain'},
            )
            root_name, locations = sitemap_locations(resource['body'])
            if root_name == 'sitemapindex':
                for location in locations:
                    child = canonicalise_crawl_url(sitemap_url, location, allowed_hosts)
                    if child and child not in visited_sitemaps and len(sitemap_queue) < 16:
                        sitemap_queue.append(child)
            else:
                for location in locations:
                    page_url = canonicalise_crawl_url(sitemap_url, location, allowed_hosts)
                    if page_url and page_url not in page_urls:
                        page_urls.append(page_url)
                        if len(page_urls) >= max_candidates:
                            break
            record.update(status='fetched', urls_discovered=len(locations))
        except (HTTPError, URLError, TimeoutError, ValueError, OSError, ET.ParseError) as error:
            record['error'] = str(error)
        sitemap_records.append(record)
    return page_urls, sitemap_records, robots_parser
