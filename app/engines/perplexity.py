"""Perplexity Search + Agent calls.

T12 turns this into an EngineAdapter behind app/engines/base.py. Moved
unchanged here so T1 stays a pure move."""

import os
import re

from app.extraction.mentions import evidence_url
from app.http_client import ProviderAPIError, external_json_request
from app.utils import normalise_domain

def call_perplexity_search(prompt, region=None):
    api_key = os.environ.get('PERPLEXITY_API_KEY')
    if not api_key:
        raise ProviderAPIError('PERPLEXITY_API_KEY is not configured.')
    max_results = max(1, min(int(os.environ.get('PERPLEXITY_MAX_RESULTS', '10')), 20))
    payload = {
        'query': prompt, 'max_results': max_results,
        'search_context_size': os.environ.get('PERPLEXITY_SEARCH_CONTEXT', 'medium'),
    }
    if region and re.fullmatch(r'[A-Z]{2}', region):
        payload['country'] = region
    return external_json_request(
        'https://api.perplexity.ai/search', method='POST', payload=payload,
        headers={'Authorization': f'Bearer {api_key}'}, timeout=45,
    )

def call_perplexity_answer(prompt):
    api_key = os.environ.get('PERPLEXITY_API_KEY')
    if not api_key:
        raise ProviderAPIError('PERPLEXITY_API_KEY is not configured.')
    payload = external_json_request(
        'https://api.perplexity.ai/v1/agent', method='POST',
        headers={'Authorization': f'Bearer {api_key}'}, timeout=60,
        payload={
            'preset': os.environ.get('PERPLEXITY_AGENT_PRESET', 'low'),
            'input': prompt,
            'tools': [{'type': 'web_search'}],
            'max_output_tokens': max(256, min(int(os.environ.get('PERPLEXITY_AGENT_MAX_OUTPUT_TOKENS', '1200')), 4000)),
            'instructions': (
                'Answer the user directly using current web evidence. Use the web search tool, '
                'preserve source annotations, and do not invent citations.'
            ),
        },
    )
    status = payload.get('status')
    if status and status != 'completed':
        provider_error = payload.get('error') or {}
        message = provider_error.get('message') if isinstance(provider_error, dict) else provider_error
        raise ProviderAPIError(message or f'Perplexity Agent response ended with status {status}.', payload=payload)
    return payload

def perplexity_answer_text(payload):
    output_text = payload.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    agent_parts = []
    for output_item in payload.get('output') or []:
        if not isinstance(output_item, dict) or output_item.get('type') != 'message':
            continue
        for content in output_item.get('content') or []:
            if isinstance(content, dict) and content.get('type') == 'output_text' and isinstance(content.get('text'), str):
                agent_parts.append(content['text'])
    if agent_parts:
        return '\n'.join(agent_parts).strip()
    choices = payload.get('choices') or []
    if not choices:
        return ''
    content = (choices[0].get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(item.get('text', '') for item in content if isinstance(item, dict))
    return ''

def perplexity_answer_citations(payload):
    """Return URL-bearing citations from current Agent and legacy Sonar payloads."""
    citations = []
    for item in payload.get('citations') or []:
        citations.append(item)
    for output_item in payload.get('output') or []:
        if not isinstance(output_item, dict) or output_item.get('type') != 'message':
            continue
        for content in output_item.get('content') or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get('annotations') or []:
                if isinstance(annotation, dict) and annotation.get('url'):
                    citations.append(annotation)
    return citations

def normalise_perplexity_sources(search_payload, answer_payload):
    sources = []
    seen = set()

    def add_source(item, source_kind, rank):
        if isinstance(item, str):
            item = {'url': item}
        if not isinstance(item, dict):
            return
        url = evidence_url(item.get('url'))
        evidence_key = (source_kind, url)
        if not url or evidence_key in seen:
            return
        seen.add(evidence_key)
        sources.append({
            'rank': rank, 'source_kind': source_kind,
            'title': (item.get('title') or '')[:2000] or None,
            'url': url, 'domain': normalise_domain(url),
            'snippet': (item.get('snippet') or '')[:8000] or None,
            'published_at': str(item.get('date') or item.get('last_updated') or '')[:80] or None,
        })

    for rank, item in enumerate((search_payload or {}).get('results') or [], 1):
        add_source(item, 'search_result', rank)
    answer_results = (answer_payload or {}).get('search_results') or []
    for rank, item in enumerate(answer_results, 1):
        add_source(item, 'answer_source', rank)
    for output_item in (answer_payload or {}).get('output') or []:
        if not isinstance(output_item, dict):
            continue
        if output_item.get('type') == 'search_results':
            for rank, item in enumerate(output_item.get('results') or [], 1):
                add_source(item, 'agent_search_result', rank)
        elif output_item.get('type') == 'fetch_url_results':
            for rank, item in enumerate(output_item.get('contents') or [], 1):
                add_source(item, 'agent_fetched_source', rank)
    for rank, item in enumerate(perplexity_answer_citations(answer_payload or {}), 1):
        add_source(item, 'answer_citation', rank)
    return sources
