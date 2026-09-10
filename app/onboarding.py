"""Domain in, approved prompt set out.

One structured LLM call turns a homepage into a brand profile and ~25 tracked
prompts. The model writes prose and suggests entities; it is never asked for a
number or a flag that the code can compute itself.

Two rules that decide whether the whole product measures anything real:

* **The majority of prompts must be unbranded.** The question is whether an engine
  mentions the brand *organically* when someone asks a category question. A prompt
  containing the brand name answers a different, useless question.
* **`branded` is computed in Python**, by matching the prompt text against the
  alias list with the same word-boundary regex extraction uses. Asking the model
  to label it invites a wrong label that silently biases every later metric.
"""

import json
import re

from app.crawler.fetch import fetch_public_resource
from app.extraction.mentions import alias_offsets
from app.llm import parse_json_from_model

CATEGORIES = ('discovery', 'comparison', 'purchase', 'brand')

MAX_COMPETITORS = 10
TARGET_PROMPTS = 25
MAX_PROMPT_WORDS = 12
MIN_UNBRANDED_FRACTION = 0.5

SYSTEM_PROMPT = """You profile a company from its homepage so its visibility in AI \
search can be measured.

Return ONLY JSON matching this shape:
{
  "brand_name": "canonical name",
  "aliases": ["other names people type"],
  "domains": ["example.com"],
  "competitors": [{"name": "...", "domains": ["..."], "aliases": ["..."]}],
  "prompts": [{"text": "...", "category": "discovery|comparison|purchase|brand", "tags": ["..."]}]
}

Rules, all of which matter:
- Produce about 25 prompts. THE MAJORITY MUST NOT CONTAIN THE BRAND NAME. We are \
testing whether an AI engine names this brand on its own when someone asks a \
generic category question. A prompt containing the brand name cannot test that.
- Each prompt is a short search-style fragment, lowercase, under 12 words. Not a \
sentence, no question mark, no punctuation at the end. Write what someone types, \
not what they would say aloud.
- Never invent an alias that is a substring of the canonical brand name. Matching \
is word-boundary, so "Aspire Asia" for "Aspire" is redundant and inflates nothing.
- At most 10 competitors, real companies only, each with its real domain.
- Do NOT label prompts as branded or unbranded. That is computed, not asked.
"""


class OnboardingError(RuntimeError):
    """Raised when a profile cannot be produced and manual entry is the fallback."""


def fetch_homepage(url):
    """Fetch a homepage through the existing SSRF-guarded fetcher.

    Deliberately reuses app/crawler/fetch.py rather than adding a second fetcher -
    a second one is a second place for an SSRF guard to be forgotten.
    """
    # fetch_public_resource validates internally; calling the guard here as well
    # would pass it a ParseResult instead of a URL string. It returns a dict with
    # the raw bytes under 'body' and the charset the server declared.
    resource = fetch_public_resource(
        url, max_bytes=800_000, accepted_types=('text/html',), timeout=15)
    payload = resource.get('body') or b''
    charset = resource.get('charset') or 'utf-8'
    if isinstance(payload, bytes):
        payload = payload.decode(charset, errors='replace')
    return payload


def visible_text(html, limit=12_000):
    """Crude tag strip. The model only needs the gist, not a faithful render."""
    text = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html or '')
    text = re.sub(r'(?s)<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()[:limit]


def is_branded(prompt_text, aliases):
    """Computed, never trusted from the model.

    Same word-boundary matcher extraction uses, so a prompt counted as unbranded
    here is one the extractor would also see as containing no brand token.
    """
    return alias_offsets(prompt_text, aliases) is not None


def drop_substring_aliases(brand_name, aliases):
    """Remove aliases that can never match anywhere the brand does not.

    SPRINT words this as "never an alias that is a substring of the canonical brand
    name", but its own example - "Aspire Asia" for "Aspire" - is the other way
    round: the alias *contains* the brand. The example is the one that matters. An
    alias containing the brand is redundant because wherever it matches, the brand
    already matched.

    Naive containment would be wrong too: "LinearApp" contains "Linear" but is a
    genuinely useful alias, because word-boundary matching will not find "Linear"
    inside it. So redundancy is tested with the same regex extraction uses - the
    alias is redundant only if the brand matches inside it *at a word boundary*.

    Both directions are dropped, so the literal wording and the example agree.
    """
    canonical = (brand_name or '').strip()
    canonical_lower = canonical.lower()
    kept = []
    for alias in aliases or ():
        candidate = (alias or '').strip()
        if not candidate or candidate.lower() == canonical_lower:
            continue
        # Literal wording: alias is a substring of the brand.
        if candidate.lower() in canonical_lower:
            continue
        # The example, done precisely: the brand matches inside the alias as a whole
        # word, so the alias adds no reach.
        if canonical and alias_offsets(candidate, [canonical]) is not None:
            continue
        kept.append(candidate)
    return list(dict.fromkeys(kept))


def normalise_prompt(text):
    """Search-style fragment: lowercase, no trailing punctuation, <= 12 words."""
    cleaned = re.sub(r'\s+', ' ', (text or '').strip().lower())
    cleaned = cleaned.rstrip('?.!,;:')
    words = cleaned.split()
    return ' '.join(words[:MAX_PROMPT_WORDS])


def validate_profile(payload):
    """Schema-validate the model's output. Raises OnboardingError on a bad shape."""
    if not isinstance(payload, dict):
        raise OnboardingError('model returned a non-object')

    brand_name = (payload.get('brand_name') or '').strip()
    if not brand_name:
        raise OnboardingError('missing brand_name')

    prompts_in = payload.get('prompts')
    if not isinstance(prompts_in, list) or not prompts_in:
        raise OnboardingError('missing prompts')

    aliases = drop_substring_aliases(brand_name, payload.get('aliases') or [])
    domains = [d.strip().lower() for d in (payload.get('domains') or []) if d and d.strip()]

    competitors = []
    for entry in (payload.get('competitors') or [])[:MAX_COMPETITORS]:
        if not isinstance(entry, dict):
            continue
        name = (entry.get('name') or '').strip()
        if not name:
            continue
        competitors.append({
            'name': name,
            'domains': [d.strip().lower() for d in (entry.get('domains') or []) if d and d.strip()],
            'aliases': drop_substring_aliases(name, entry.get('aliases') or []),
        })

    # The alias set a prompt is tested against: the brand, its aliases and its
    # domains - the same inputs extraction uses.
    brand_tokens = [brand_name, *aliases, *domains]

    prompts = []
    seen = set()
    for entry in prompts_in:
        if not isinstance(entry, dict):
            continue
        text = normalise_prompt(entry.get('text'))
        if not text or text in seen:
            continue
        seen.add(text)
        category = (entry.get('category') or '').strip().lower()
        if category not in CATEGORIES:
            category = 'discovery'
        prompts.append({
            'text': text,
            'category': category,
            'tags': [t for t in (entry.get('tags') or []) if isinstance(t, str)][:5],
            # Computed here. The model was told not to supply this, and anything it
            # did supply is ignored.
            'branded': is_branded(text, brand_tokens),
        })

    if not prompts:
        raise OnboardingError('no usable prompts after validation')

    return {
        'brand_name': brand_name,
        'aliases': aliases,
        'domains': domains,
        'competitors': competitors,
        'prompts': prompts,
        'unbranded_fraction': unbranded_fraction(prompts),
    }


def unbranded_fraction(prompts):
    if not prompts:
        return None
    return sum(1 for p in prompts if not p['branded']) / len(prompts)


def build_user_prompt(domain, page_text):
    return (
        f'Homepage domain: {domain}\n\n'
        f'Homepage text:\n{page_text}\n\n'
        f'Produce about {TARGET_PROMPTS} prompts. Remember: the majority must not '
        f'contain the brand name.'
    )


def generate_profile(domain, page_text, *, call_model):
    """One structured call, one repair retry, then manual entry.

    call_model(system, user) -> str is injected so the route, the tests and a
    future provider swap all drive the same code path.
    """
    messages = build_user_prompt(domain, page_text)
    last_error = None

    for attempt in (1, 2):
        user_message = messages
        if attempt == 2:
            # One repair attempt, telling the model exactly what was wrong.
            user_message = (
                f'{messages}\n\nYour previous reply was rejected: {last_error}. '
                f'Return only valid JSON in the required shape.'
            )
        try:
            raw = call_model(SYSTEM_PROMPT, user_message)
            payload = parse_json_from_model(raw)
            if payload is None:
                payload = json.loads(raw)
            return validate_profile(payload)
        except Exception as error:  # noqa: BLE001 - any bad shape is a repair case
            last_error = str(error)[:300]

    raise OnboardingError(
        f'Could not generate a profile after a repair attempt: {last_error}')
