"""Chunk indexing and index summaries."""

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
import hashlib
import json
import re

from app.config import RAG_DOCUMENT_MAX_CHARS
from app.db import engine
from app.models import analytics_rag_chunks, analytics_rag_documents, analytics_rag_insights
from app.rag.chunking import chunk_visible_text, rag_terms
from app.routes.pages import index
from app.utils import row_to_dict

def index_rag_page(conn, *, workspace_id, audit_id, page_id, page, created_at):
    """Persist normalized public copy and its retrieval chunks in one audit transaction."""
    content_text = re.sub(r'\s+', ' ', (page.get('content_text') or '')).strip()[:RAG_DOCUMENT_MAX_CHARS]
    if not content_text:
        return None
    content_hash = hashlib.sha256(content_text.encode('utf-8')).hexdigest()
    duplicate_document_id = conn.execute(select(analytics_rag_documents.c.id).where(
        (analytics_rag_documents.c.audit_id == audit_id) &
        (analytics_rag_documents.c.content_hash == content_hash)
    ).limit(1)).scalar_one_or_none()
    if duplicate_document_id:
        return duplicate_document_id
    document_result = conn.execute(insert(analytics_rag_documents).values(
        workspace_id=workspace_id, audit_id=audit_id, page_id=page_id,
        url=(page.get('url') or page.get('requested_url') or '')[:2048],
        title=page.get('title'), content_hash=content_hash, content_text=content_text,
        word_count=len(re.findall(r"\b[\w'-]+\b", content_text)), created_at=created_at,
    ))
    document_id = document_result.inserted_primary_key[0]
    chunks = chunk_visible_text(content_text)
    if chunks:
        conn.execute(insert(analytics_rag_chunks), [
            {
                'workspace_id': workspace_id, 'audit_id': audit_id, 'document_id': document_id,
                'chunk_index': index, 'content_hash': hashlib.sha256(chunk.encode('utf-8')).hexdigest(),
                'content_text': chunk, 'token_count': len(rag_terms(chunk)), 'created_at': created_at,
            }
            for index, chunk in enumerate(chunks)
        ])
    return document_id

def rag_index_summary(audit_id, *, include_insights=True):
    with engine.connect() as conn:
        documents_count = conn.execute(select(func.count()).select_from(analytics_rag_documents).where(
            analytics_rag_documents.c.audit_id == audit_id
        )).scalar_one()
        chunks_count = conn.execute(select(func.count()).select_from(analytics_rag_chunks).where(
            analytics_rag_chunks.c.audit_id == audit_id
        )).scalar_one()
        insight_rows = []
        if include_insights:
            insight_rows = conn.execute(select(analytics_rag_insights).where(
                analytics_rag_insights.c.audit_id == audit_id
            ).order_by(analytics_rag_insights.c.id)).mappings().all()
    insights = []
    referenced_chunk_ids = set()
    for row in insight_rows:
        item = row_to_dict(row)
        try:
            item['evidence_refs'] = json.loads(item.get('evidence_refs') or '[]')
        except json.JSONDecodeError:
            item['evidence_refs'] = []
        if not isinstance(item['evidence_refs'], list):
            item['evidence_refs'] = []
        referenced_chunk_ids.update(
            int(match.group(1)) for ref in item['evidence_refs']
            if (match := re.fullmatch(r'chunk:(\d+)', str(ref)))
        )
        insights.append(item)
    evidence_by_id = {}
    if referenced_chunk_ids:
        with engine.connect() as conn:
            evidence_rows = conn.execute(select(
                analytics_rag_chunks.c.id, analytics_rag_chunks.c.chunk_index,
                analytics_rag_chunks.c.content_text,
                analytics_rag_documents.c.url.label('document_url'),
                analytics_rag_documents.c.title.label('document_title'),
            ).join(
                analytics_rag_documents,
                analytics_rag_chunks.c.document_id == analytics_rag_documents.c.id,
            ).where(
                (analytics_rag_chunks.c.audit_id == audit_id) &
                (analytics_rag_chunks.c.id.in_(referenced_chunk_ids))
            )).mappings().all()
        for row in evidence_rows:
            excerpt = re.sub(r'\s+', ' ', row['content_text']).strip()
            if len(excerpt) > 700:
                excerpt = excerpt[:697].rstrip() + '...'
            evidence_by_id[row['id']] = {
                'evidence_ref': f"chunk:{row['id']}", 'url': row['document_url'],
                'title': row['document_title'], 'chunk_index': row['chunk_index'],
                'excerpt': excerpt,
            }
    for item in insights:
        item['evidence'] = [
            evidence_by_id[int(ref.split(':', 1)[1])]
            for ref in item['evidence_refs']
            if str(ref).startswith('chunk:') and str(ref).split(':', 1)[1].isdigit()
            and int(str(ref).split(':', 1)[1]) in evidence_by_id
        ]
    return {
        'version': '1.0',
        'source_type': 'website_crawl_rag',
        'retrieval_method': 'local_sparse_bm25_v1',
        'corpus_scope': 'normalized_visible_text_from_the_selected_audit',
        'measurement_scope': 'content_analysis_only',
        'disclaimer': (
            'RAG insights are grounded in fetched first-party website copy. They deepen the content audit but do not measure '
            'answer visibility, share of voice, citations, or source rank; those require saved third-party provider evidence.'
        ),
        'documents_indexed': documents_count,
        'chunks_indexed': chunks_count,
        'insights': insights,
    }
