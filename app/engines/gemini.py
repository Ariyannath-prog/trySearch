"""Gemini Flash-Lite with Google Search grounding.

STATUS: BLOCKED, no adapter yet. Grounded generateContent returns 429
RESOURCE_EXHAUSTED ("check your plan and billing details") on an unbilled
project, so no real grounded response could be recorded. SPRINT T13 forbids
writing an adapter before its fixture exists, and an *ungrounded* fixture would
be worse than none: it measures a model reciting training data, produces no
citations, and every test would still pass. Enable billing on the key's Google
Cloud project, record the fixture, then write the adapter.

Source type is `api`, but what it measures is a *proxy* for Google AI Overviews,
not AI Overviews itself. The UI has to say so - see `engines.source_type` and the
display name.

Free tier is 5,000 grounded prompts per month **per Google Cloud project**, not
per workspace. That is a project-level allowance, not a per-request price, so the
adapter reports the paid unit rate and the allowance is reconciled against the
real bill rather than pretended away per call.
"""

import os
import time
from decimal import Decimal

from app.engines.base import Citation, EngineResult, guard
from app.http_client import ProviderAPIError, external_json_request

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta/models'


def gemini_model():
    # 2.5-flash-lite is closed to new API keys - the models list still offers it,
    # but generateContent refuses it. Recording a real call is what caught that.
    return os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite')


def call_gemini_answer(prompt):
    """One grounded generateContent call. Raw payload, no parsing.

    Written before the adapter so a real response can be recorded and the parser
    built against what the API actually returns.
    """
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise ProviderAPIError('GEMINI_API_KEY is not configured.')

    model = gemini_model()
    # google_search is the 2.x tool name; 1.5 used google_search_retrieval.
    payload = {
        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
        'tools': [{'google_search': {}}],
    }
    return external_json_request(
        f'{API_ROOT}/{model}:generateContent?key={api_key}',
        method='POST', payload=payload, timeout=60,
    )


def call_gemini_text(system_prompt, user_prompt):
    """Ungrounded generation, for writing prompts rather than measuring visibility.

    Legitimate without Search grounding: this asks the model to *compose* a prompt
    set, not to report what the web says. Nothing derived from this becomes a
    metric - T14's output is reviewed and approved by a human before any scan runs.
    """
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise ProviderAPIError('GEMINI_API_KEY is not configured.')

    payload = {
        'systemInstruction': {'parts': [{'text': system_prompt}]},
        'contents': [{'role': 'user', 'parts': [{'text': user_prompt}]}],
        'generationConfig': {'responseMimeType': 'application/json'},
    }
    response = external_json_request(
        f'{API_ROOT}/{gemini_model()}:generateContent?key={api_key}',
        method='POST', payload=payload, timeout=90,
    )
    return gemini_answer_text(response)


def gemini_answer_text(payload):
    """Concatenate the text parts of the first candidate."""
    for candidate in (payload or {}).get('candidates') or []:
        parts = ((candidate.get('content') or {}).get('parts')) or []
        text = ''.join(p.get('text') or '' for p in parts if isinstance(p, dict))
        if text.strip():
            return text
    return ''
