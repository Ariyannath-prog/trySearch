"""Citation categories.

T9 needs `own` and `competitor` working. T15 adds the editorial/social/forum/
developer lists and the redirect resolution; the order below is already the final
one so that adding those lists is the only change T15 makes here.

Classification is first-match-wins, and matching is on the registrable domain or a
dotted suffix of it - never a substring. `notreddit.com` is not Reddit.
"""

from app.extraction.mentions import domain_matches

CATEGORY_ORDER = (
    'own', 'competitor', 'editorial', 'social', 'forum', 'developer', 'other',
)


def classify_citation(url, *, own_domains, competitor_domains):
    """Return one of CATEGORY_ORDER for a citation URL.

    own beats competitor when both match: a page on your own domain that also
    mentions a competitor is still your citation.
    """
    if not url:
        return 'other'

    for domain in own_domains or ():
        if domain and domain_matches(url, domain):
            return 'own'

    for domain in competitor_domains or ():
        if domain and domain_matches(url, domain):
            return 'competitor'

    # T15 inserts editorial / social / forum / developer here.
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
