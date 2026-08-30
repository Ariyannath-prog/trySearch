"""Brand rank from first-mention order.

PRD §13's PositionScore needs a rank, and rank here means "how early does the brand
appear relative to the competitors named in the same answer". Not a model's opinion
of prominence - the character offset of the first word-boundary match, which is
reproducible from the stored answer forever.
"""

from app.extraction.mentions import alias_offsets


def entity_offsets(answer_text, *, brand_aliases, competitors):
    """First-match offset per named entity.

    competitors is an iterable of dicts with 'id', 'name' and optional 'domains'
    and 'aliases'. Returns a list of dicts, each with entity_type, competitor_id
    and char_offset, for entities actually present in the text.
    """
    found = []

    brand_offset = alias_offsets(answer_text, brand_aliases)
    if brand_offset is not None:
        found.append({
            'entity_type': 'brand',
            'competitor_id': None,
            'char_offset': brand_offset,
        })

    for competitor in competitors or ():
        aliases = [competitor.get('name')]
        aliases.extend(competitor.get('aliases') or [])
        aliases.extend(competitor.get('domains') or [])
        offset = alias_offsets(answer_text, aliases)
        if offset is not None:
            found.append({
                'entity_type': 'competitor',
                'competitor_id': competitor.get('id'),
                'char_offset': offset,
            })

    return found


def rank_entities(answer_text, *, brand_aliases, competitors):
    """Return (brand_rank, mention_rows).

    brand_rank is the brand's 1-based position once every named entity is sorted by
    first-mention offset. None when the brand is absent - never 0, because "not
    measured" and "ranked zeroth" are different facts (CLAUDE.md).

    Brand present with no competitor named is rank 1: it was the only thing named,
    which is the best possible outcome, not a missing value.
    """
    found = entity_offsets(
        answer_text, brand_aliases=brand_aliases, competitors=competitors)

    # Stable within equal offsets, so a tie is resolved the same way every re-run.
    found.sort(key=lambda item: item['char_offset'])

    brand_rank = None
    rows = []
    for position, item in enumerate(found, start=1):
        rows.append({**item, 'rank': position})
        if item['entity_type'] == 'brand':
            brand_rank = position

    return brand_rank, rows
