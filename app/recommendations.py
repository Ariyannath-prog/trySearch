"""Rule-based and open-model content opportunities."""

import json
import re

from app.http_client import ProviderAPIError, external_json_request
from app.llm import open_model_settings, parse_json_from_model

def rule_based_opportunities(project, evidence_rows):
    opportunities = []
    missing_mentions = [row for row in evidence_rows if row.get('answer_text') and not row.get('brand_mentioned')]
    missing_citations = [row for row in evidence_rows if row.get('answer_text') and not row.get('brand_cited')]
    missing_sources = [row for row in evidence_rows if row.get('source_present') is False]
    if missing_mentions:
        sample = missing_mentions[0]
        opportunities.append({
            'title': 'Build a direct answer for an unmentioned prompt',
            'rationale': f"{project['brand_name']} was absent from the stored answer to: {sample['prompt']}",
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'high',
        })
    if missing_citations:
        sample = missing_citations[0]
        opportunities.append({
            'title': 'Publish sourceable proof for an uncited topic',
            'rationale': f"The provider answer did not cite {project['domain']} for: {sample['prompt']}",
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'high',
        })
    if missing_sources:
        sample = missing_sources[0]
        opportunities.append({
            'title': 'Close a ranked-source coverage gap',
            'rationale': f"The tracked domain did not appear in the saved Perplexity Search results for: {sample['prompt']}",
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'medium',
        })
    if not opportunities and evidence_rows:
        sample = evidence_rows[0]
        opportunities.append({
            'title': 'Protect and deepen measured coverage',
            'rationale': 'Current evidence contains brand or source coverage. Add fresh first-party proof and monitor the same approved prompt set over time.',
            'evidence_refs': f"answer:{sample['id']}", 'priority': 'medium',
        })
    return opportunities[:5]

def open_model_evidence_opportunities(project, evidence_rows):
    """Use an open-weight model only to summarize stored evidence, never to invent metrics."""
    settings = open_model_settings()
    if not settings['configured'] or not evidence_rows:
        return None
    model = settings['model']
    evidence = [
        {
            'evidence_id': f"answer:{row['id']}", 'prompt': row['prompt'],
            'brand_mentioned': row.get('brand_mentioned'), 'brand_cited': row.get('brand_cited'),
            'source_present': row.get('source_present'), 'best_source_rank': row.get('best_source_rank'),
            'answer_excerpt': (row.get('answer_text') or '')[:700],
        }
        for row in evidence_rows[:20]
    ]
    payload = external_json_request(
        settings['base_url'].rstrip('/') + '/chat/completions',
        method='POST', headers=settings['headers'], timeout=90,
        payload={
            'model': model, 'temperature': 0.1, 'max_tokens': 900,
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        'You are an AEO analyst. Use only the supplied evidence. Do not create or recalculate statistics. '
                        'Return JSON with an opportunities array. Each item must contain title, rationale, evidence_refs, and priority. '
                        'evidence_refs must cite one or more supplied evidence_id values. Return at most five items.'
                    ),
                },
                {
                    'role': 'user',
                    'content': json.dumps({'brand': project['brand_name'], 'domain': project['domain'], 'evidence': evidence}),
                },
            ],
        },
    )
    choices = payload.get('choices') or []
    if not choices:
        raise ProviderAPIError('The open model returned no recommendation content.')
    content = (choices[0].get('message') or {}).get('content') or ''
    parsed = parse_json_from_model(content)
    items = parsed.get('opportunities') if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        raise ProviderAPIError('The open model returned an invalid opportunity format.')
    allowed_refs = {f"answer:{row['id']}" for row in evidence_rows}
    normalized = []
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        refs = item.get('evidence_refs') or ''
        if isinstance(refs, list):
            refs = ','.join(str(value) for value in refs)
        cited_refs = [ref for ref in re.findall(r'answer:\d+', str(refs)) if ref in allowed_refs]
        if not cited_refs:
            continue
        title = str(item.get('title') or '').strip()
        rationale = str(item.get('rationale') or '').strip()
        if title and rationale:
            normalized.append({
                'title': title[:255], 'rationale': rationale[:6000],
                'evidence_refs': ','.join(dict.fromkeys(cited_refs)),
                'priority': str(item.get('priority') or 'medium').lower() if str(item.get('priority') or '').lower() in {'high', 'medium', 'low'} else 'medium',
            })
    return normalized or None
