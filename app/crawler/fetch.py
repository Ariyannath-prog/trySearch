"""SSRF-guarded fetching and crawl URL canonicalisation."""

from flask import Flask, jsonify, request, send_from_directory, abort, session, redirect
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import ipaddress
import re
import socket
import ssl

from app.config import AUDIT_USER_AGENT

class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        validate_public_web_url(urljoin(request.full_url, new_url))
        return super().redirect_request(request, fp, code, msg, headers, new_url)

def verified_http_opener(*handlers):
    """Use an explicit CA bundle so TLS verification is consistent on macOS and Linux."""
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    return build_opener(*handlers, HTTPSHandler(context=context))

def validate_public_web_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        raise ValueError('Only public HTTP(S) website URLs can be audited.')
    if parsed.username or parsed.password:
        raise ValueError('Website URLs cannot contain credentials.')
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError('The website URL contains an invalid port.') from error
    if port not in {None, 80, 443}:
        raise ValueError('Only standard web ports can be audited.')
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith('.local'):
        raise ValueError('Local network addresses cannot be audited.')
    try:
        default_port = 443 if parsed.scheme == 'https' else 80
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port or default_port, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise ValueError('The website domain could not be resolved.') from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Only publicly routable website addresses can be audited.')
    return parsed

def fetch_public_resource(url, *, max_bytes, accepted_types=None, timeout=12):
    """Fetch one public resource after validating every redirect target."""
    validate_public_web_url(url)
    http_request = Request(url, headers={
        'User-Agent': AUDIT_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml,text/xml,text/plain;q=0.9,*/*;q=0.2',
    })
    opener = verified_http_opener(SafeRedirectHandler())
    with opener.open(http_request, timeout=timeout) as response:
        final_url = response.geturl()
        validate_public_web_url(final_url)
        content_type = response.headers.get_content_type().lower()
        if accepted_types and content_type not in accepted_types:
            raise ValueError(f'The resource returned unsupported content type {content_type}.')
        payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError('The resource is too large to audit safely.')
        return {
            'url': final_url,
            'status': getattr(response, 'status', 200),
            'content_type': content_type,
            'charset': response.headers.get_content_charset() or 'utf-8',
            'body': payload,
        }

def normalise_site_host(host):
    return (host or '').lower().rstrip('.').removeprefix('www.')

def same_site_host(host, allowed_hosts):
    return normalise_site_host(host) in {normalise_site_host(item) for item in allowed_hosts if item}

def canonicalise_crawl_url(base_url, href, allowed_hosts):
    """Return a stable same-site HTTP URL, removing fragments and tracking parameters."""
    if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:', 'data:')):
        return None
    candidate = urljoin(base_url, href.strip())
    parsed = urlparse(candidate)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or not same_site_host(parsed.hostname, allowed_hosts):
        return None
    try:
        parsed_port = parsed.port
    except ValueError:
        return None
    tracking_prefixes = ('utm_',)
    tracking_names = {'gclid', 'fbclid', 'msclkid', 'mc_cid', 'mc_eid'}
    query_pairs = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in tracking_names and not key.lower().startswith(tracking_prefixes)
    ]
    query = urlencode(sorted(query_pairs))
    path = re.sub(r'/+', '/', parsed.path or '/')
    netloc = parsed.hostname.lower()
    if parsed_port and not ((parsed.scheme == 'https' and parsed_port == 443) or (parsed.scheme == 'http' and parsed_port == 80)):
        netloc = f'{netloc}:{parsed_port}'
    return urlunparse((parsed.scheme.lower(), netloc, path, '', query, ''))
