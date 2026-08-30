"""Small shared value helpers."""

from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

def to_iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.isoformat() + 'Z'

def row_to_dict(row):
    d = dict(row)
    for key, value in list(d.items()):
        if isinstance(value, datetime):
            d[key] = to_iso(value)
        elif isinstance(value, date):
            d[key] = value.isoformat()
    return d

def normalise_domain(value):
    value = (value or '').strip().lower()
    if not value:
        return None
    parsed = urlparse(value if '://' in value else f'https://{value}')
    domain = (parsed.netloc or '').split('@')[-1].split(':')[0].strip('.')
    if not domain or '.' not in domain or any(char.isspace() for char in domain):
        return None
    return domain.removeprefix('www.')

def normalise_website_url(value):
    """Retain an optional public path instead of silently reducing it to a host."""
    value = (value or '').strip()
    if not value:
        return None
    parsed = urlparse(value if '://' in value else f'https://{value}')
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or any(char.isspace() for char in parsed.netloc):
        return None
    path = parsed.path or '/'
    return parsed._replace(path=path, params='', fragment='').geturl()
