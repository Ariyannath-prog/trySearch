"""Outbound JSON helper shared by every provider call."""

from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import json

from app.crawler.fetch import verified_http_opener

class ProviderAPIError(RuntimeError):
    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.status = status
        self.payload = payload

def external_json_request(url, *, method='GET', payload=None, form=None, headers=None, timeout=30):
    """Call a fixed third-party API without ever exposing its credential to the browser."""
    request_headers = {'Accept': 'application/json', **(headers or {})}
    body = None
    if payload is not None:
        request_headers['Content-Type'] = 'application/json'
        body = json.dumps(payload).encode('utf-8')
    elif form is not None:
        request_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        body = urlencode(form).encode('utf-8')
    api_request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with verified_http_opener().open(api_request, timeout=timeout) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ProviderAPIError('The provider response exceeded the safe size limit.')
            return json.loads(raw.decode('utf-8')) if raw else {}
    except HTTPError as error:
        raw = error.read(200_000).decode('utf-8', errors='replace')
        try:
            error_payload = json.loads(raw)
        except json.JSONDecodeError:
            error_payload = {'message': raw}
        nested_error = error_payload.get('error')
        nested_message = nested_error.get('message') if isinstance(nested_error, dict) else None
        message = error_payload.get('error_description') or nested_message or error_payload.get('message')
        if not message and isinstance(nested_error, str):
            message = nested_error
        message = message or f'Provider returned HTTP {error.code}.'
        raise ProviderAPIError(str(message), status=error.code, payload=error_payload) from error
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise ProviderAPIError(f'Provider request failed: {error}') from error
