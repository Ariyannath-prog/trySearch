"""Citation categories.

Classification is first-match-wins in a fixed order:

    own -> competitor -> editorial -> social -> forum -> developer -> other

Order carries meaning. `own` beats `competitor` because a page on your own domain
that also names a rival is still your citation. Everything curated beats `other`,
so `other` means "we have not classified this yet", not "uninteresting".

Matching is on the **registrable domain**, exactly or as a suffix after a dot.
Never substring: `notreddit.com` is not Reddit, and `mycompany.github.io.evil.com`
is not GitHub.

The curated lists are **server-side only**. They are the moat - the reference
implementation of this product carries ~25,000 domains - and they must never be
serialised into an API response or a JS bundle. A test asserts that.
"""

import functools
import pathlib

from app.crawler.fetch import normalise_site_host
from app.extraction.mentions import domain_matches  # noqa: F401 - re-exported
from app.utils import normalise_domain

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / 'data'

CATEGORY_ORDER = (
    'own', 'competitor', 'editorial', 'social', 'forum', 'developer', 'other',
)

# Curated category -> file. `own` and `competitor` come from the workspace, not
# from a list, so they are not here.
CURATED_FILES = {
    'editorial': 'editorial_domains.txt',
    'social': 'social_domains.txt',
    'forum': 'forum_domains.txt',
    'developer': 'developer_domains.txt',
}


@functools.lru_cache(maxsize=None)
def curated_domains(category):
    """Load one curated list once, at first use."""
    path = DATA_DIR / CURATED_FILES[category]
    if not path.exists():
        return frozenset()
    domains = set()
    for line in path.read_text().splitlines():
        entry = line.strip()
        if not entry or entry.startswith('#'):
            continue
        normalised = normalise_site_host(entry.lower())
        if normalised:
            domains.add(normalised)
    return frozenset(domains)


def host_matches(host, domain):
    """True when host is `domain` or a subdomain of it.

    The whole point of this function is to *not* be a substring test:
    `notreddit.com` does not match `reddit.com`, but `www.reddit.com` and
    `old.reddit.com` do.
    """
    host = normalise_site_host(host or '')
    domain = normalise_site_host(domain or '')
    if not host or not domain:
        return False
    return host == domain or host.endswith('.' + domain)


def host_in(host, domains):
    host = normalise_site_host(host or '')
    if not host:
        return False
    if host in domains:
        return True
    # Walk the labels so a subdomain matches its registrable parent without ever
    # doing a substring comparison.
    parts = host.split('.')
    for index in range(1, len(parts) - 1):
        if '.'.join(parts[index:]) in domains:
            return True
    return False


def citation_host(url):
    """Registrable host for a citation URL, or None."""
    domain = normalise_domain(url)
    return normalise_site_host(domain) if domain else None


def classify_citation(url, *, own_domains, competitor_domains, host=None):
    """Return one of CATEGORY_ORDER. First match wins, in order.

    `host` may be supplied when the redirect chain has already been resolved, so
    the caller does not pay for resolution twice.
    """
    resolved = host or citation_host(url)
    if not resolved:
        return 'other'

    for domain in own_domains or ():
        if host_matches(resolved, domain):
            return 'own'

    for domain in competitor_domains or ():
        if host_matches(resolved, domain):
            return 'competitor'

    for category in ('editorial', 'social', 'forum', 'developer'):
        if host_in(resolved, curated_domains(category)):
            return category

    # Not "uninteresting" - unclassified. Growing the lists is weekly work.
    return 'other'


def workspace_domains(workspace):
    """Every domain that counts as the workspace's own."""
    domains = list(workspace.get('domains') or [])
    if workspace.get('domain'):
        domains.append(workspace['domain'])
    return [d for d in dict.fromkeys(domains) if d]


def competitor_domains(competitors):
    domains = []
    for competitor in competitors or ():
        domains.extend(competitor.get('domains') or [])
    return [d for d in dict.fromkeys(domains) if d]


def resolve_final_host(url, *, opener=None):
    """Follow the redirect chain and return the final registrable host.

    A citation to a link shortener or a tracking redirect names the wrong domain
    if taken at face value, and the whole module exists to attribute citations to
    the right publisher.

    Network work, so it is optional and injectable: `opener` is swapped in tests,
    and any failure falls back to the host in the URL as given rather than
    dropping the citation.
    """
    from app.crawler.fetch import verified_http_opener

    try:
        request_opener = opener or verified_http_opener()
        with request_opener.open(url, timeout=10) as response:
            final_url = getattr(response, 'url', None) or url
        return citation_host(final_url) or citation_host(url)
    except Exception:  # noqa: BLE001 - an unreachable link is still a citation
        return citation_host(url)
