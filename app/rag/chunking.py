"""Text chunking and term extraction for BM25."""

import re

from app.config import RAG_CHUNK_OVERLAP_WORDS, RAG_CHUNK_WORDS, RAG_MAX_CHUNKS_PER_PAGE

RAG_STOP_WORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'been', 'but', 'by', 'can',
    'do', 'for', 'from', 'had', 'has', 'have', 'how', 'if', 'in', 'into',
    'is', 'it', 'its', 'more', 'of', 'on', 'or', 'our', 'should', 'that',
    'the', 'their', 'this', 'to', 'was', 'we', 'what', 'when', 'where',
    'which', 'who', 'why', 'will', 'with', 'your',
}

def rag_terms(value):
    """Tokenize public page copy for deterministic local sparse retrieval."""
    return [
        token for token in re.findall(r"[\w'-]+", (value or '').lower(), flags=re.UNICODE)
        if len(token) > 1 and token not in RAG_STOP_WORDS
    ]

def chunk_visible_text(value, *, chunk_words=None, overlap_words=None, max_chunks=None):
    """Split normalized visible page copy into bounded, overlapping chunks."""
    words = re.findall(r'\S+', (value or '').strip())
    if not words:
        return []
    chunk_words = chunk_words or RAG_CHUNK_WORDS
    overlap_words = RAG_CHUNK_OVERLAP_WORDS if overlap_words is None else overlap_words
    max_chunks = max_chunks or RAG_MAX_CHUNKS_PER_PAGE
    chunk_words = max(20, int(chunk_words))
    overlap_words = max(0, min(int(overlap_words), chunk_words // 2))
    step = max(1, chunk_words - overlap_words)
    chunks = []
    for start in range(0, len(words), step):
        text_value = ' '.join(words[start:start + chunk_words]).strip()
        if text_value:
            chunks.append(text_value)
        if len(chunks) >= max_chunks or start + chunk_words >= len(words):
            break
    return chunks
