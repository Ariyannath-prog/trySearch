"""Word-boundary alias matching over answer text."""

from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse
import re

from app.crawler.fetch import normalise_site_host
from app.utils import normalise_domain

def evidence_url(value):
    value = (value or '').strip()
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return None
    return parsed._replace(fragment='').geturl()[:2048]

def domain_matches(candidate_url, tracked_domain):
    candidate_domain = normalise_domain(candidate_url)
    tracked = normalise_site_host(tracked_domain)
    return bool(candidate_domain and (
        normalise_site_host(candidate_domain) == tracked or
        normalise_site_host(candidate_domain).endswith('.' + tracked)
    ))

def alias_offsets(text_value, aliases):
    """Character offset of the earliest word-boundary match, or None.

    The single alias regex in the codebase. text_mentions_alias is defined in terms
    of it so matching can never drift between "was it mentioned" and "where".

    Word-boundary only, by design: brand "Aspire" must not match "Aspireship".
    CLAUDE.md settles this - no NER, no fuzzy matching, no model.
    """
    text_value = text_value or ''
    earliest = None
    for alias in aliases or ():
        alias = (alias or '').strip()
        if len(alias) < 2:
            continue
        match = re.search(rf'(?<!\w){re.escape(alias)}(?!\w)', text_value, flags=re.I)
        if match and (earliest is None or match.start() < earliest):
            earliest = match.start()
    return earliest

def text_mentions_alias(text_value, aliases):
    return alias_offsets(text_value, aliases) is not None

def project_brand_aliases(project, stored_aliases=None):
    """Aliases for a workspace: the brand_aliases table is the source of truth.

    The domain-derived labels remain the seed for a workspace whose alias table is
    still empty, which is every workspace until T14's onboarding fills it.
    """
    if stored_aliases:
        seed = [project['brand_name'], *stored_aliases]
    else:
        domain_label = project['domain'].split('.')[0].replace('-', ' ')
        seed = [project['brand_name'], project['domain'], domain_label]
    return list(dict.fromkeys(a for a in seed if a))
