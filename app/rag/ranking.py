"""BM25 ranking and chunk retrieval. No embeddings, by design."""

from collections import Counter, deque
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    UniqueConstraint,
    select,
    insert,
    update,
    desc,
    func,
    text,
)
import math

from app.config import RAG_DEFAULT_TOP_K
from app.db import engine
from app.models import analytics_rag_chunks, analytics_rag_documents
from app.rag.chunking import rag_terms
from app.utils import row_to_dict

def rank_rag_chunks(query, rows, *, limit=None):
    """Rank saved chunks with a compact BM25-style scorer.

    Sparse retrieval keeps the first production version dependency-free and
    auditable. It can later be complemented by embeddings without changing the
    persisted evidence contract or the API response shape.
    """
    limit = max(1, min(int(limit or RAG_DEFAULT_TOP_K), 12))
    rows = [dict(row) for row in rows]
    if not rows:
        return []
    query_tokens = rag_terms(query)
    tokenized = [rag_terms(row.get('content_text')) for row in rows]
    if not query_tokens:
        return []
    document_frequency = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))
    average_length = sum(len(tokens) for tokens in tokenized) / max(len(tokenized), 1)
    query_frequency = Counter(query_tokens)
    ranked = []
    for row, tokens in zip(rows, tokenized):
        frequencies = Counter(tokens)
        length_normalizer = 1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1)
        score = 0.0
        for term, query_count in query_frequency.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = math.log(1 + (len(rows) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += inverse_frequency * ((frequency * 2.2) / (frequency + 1.2 * length_normalizer)) * min(query_count, 2)
        title_terms = set(rag_terms(row.get('document_title')))
        score += sum(0.35 for term in set(query_tokens) if term in title_terms)
        if score <= 0:
            continue
        item = dict(row)
        item['score'] = round(score, 6)
        item['evidence_ref'] = f"chunk:{row['id']}"
        ranked.append(item)
    ranked.sort(key=lambda item: (-item['score'], item.get('chunk_index', 0), item['id']))
    return ranked[:limit]

def retrieve_audit_chunks(audit_id, query, *, limit=None):
    with engine.connect() as conn:
        rows = conn.execute(select(
            analytics_rag_chunks,
            analytics_rag_documents.c.url.label('document_url'),
            analytics_rag_documents.c.title.label('document_title'),
        ).join(
            analytics_rag_documents,
            analytics_rag_chunks.c.document_id == analytics_rag_documents.c.id,
        ).where(
            analytics_rag_chunks.c.audit_id == audit_id
        ).order_by(
            analytics_rag_chunks.c.document_id, analytics_rag_chunks.c.chunk_index,
        )).mappings().all()
    return rank_rag_chunks(query, rows, limit=limit)

def representative_audit_chunks(audit_id, *, limit=None):
    """Return one bounded lead chunk per substantial page for baseline synthesis."""
    limit = max(1, min(int(limit or RAG_DEFAULT_TOP_K), 12))
    with engine.connect() as conn:
        rows = conn.execute(select(
            analytics_rag_chunks,
            analytics_rag_documents.c.url.label('document_url'),
            analytics_rag_documents.c.title.label('document_title'),
        ).join(
            analytics_rag_documents,
            analytics_rag_chunks.c.document_id == analytics_rag_documents.c.id,
        ).where(
            (analytics_rag_chunks.c.audit_id == audit_id) &
            (analytics_rag_chunks.c.chunk_index == 0)
        ).order_by(
            desc(analytics_rag_documents.c.word_count), analytics_rag_documents.c.id,
        ).limit(limit)).mappings().all()
    result = []
    for row in rows:
        item = row_to_dict(row)
        item['score'] = 0.0
        item['evidence_ref'] = f"chunk:{item['id']}"
        result.append(item)
    return result
