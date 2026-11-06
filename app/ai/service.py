"""Builds and caches the retriever for the running app. The cache is keyed on the scheme records' content hashes, so a
re-seeded database is picked up without a restart. Semantic search is optional: if it is off or cannot load, keyword
search still works and the reply says which mode answered."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.chunks import build_chunks
from app.ai.embeddings import Embedder, EmbeddingUnavailable, SentenceTransformerEmbedder
from app.ai.keyword_index import KeywordIndex
from app.ai.retrieval import Retriever
from app.ai.vector_store import VectorIndex, fingerprint
from app.config import Settings
from app.database import models as m


def corpus_key(session: Session) -> str:
    rows = session.execute(select(m.Scheme.scheme_id, m.Scheme.source_hash, m.Scheme.verification_status,
                                  m.Scheme.last_verified).order_by(m.Scheme.scheme_id)).all()
    return repr([tuple(map(str, r)) for r in rows])


def build_retriever(session: Session, today: date, settings: Settings | None = None,
                    embedder: Embedder | None = None) -> Retriever:
    chunks = build_chunks(session, today)
    kw = KeywordIndex()
    kw.rebuild(chunks)
    vectors = None
    if embedder is None and settings is not None and settings.semantic_search:
        embedder = SentenceTransformerEmbedder(settings.embedding_model)
    if embedder is not None:
        folder = Path(settings.vector_store_dir) if settings else None
        fp = fingerprint(chunks)
        try:
            vectors = VectorIndex.load(folder, model=embedder.name, fp=fp) if folder else None
            if vectors is None:
                vectors = VectorIndex.build(chunks, embedder)
                if folder:
                    vectors.save(folder)
        except EmbeddingUnavailable:
            vectors, embedder = None, None
    return Retriever(chunks, kw, vectors, embedder, min_vector_z=settings.semantic_min_z if settings else None)


def get_retriever(app_state, session: Session, today: date, settings: Settings | None = None) -> Retriever:
    key = corpus_key(session)
    cached = getattr(app_state, "retriever", None)
    if cached is None or cached[0] != key:
        app_state.retriever = (key, build_retriever(session, today, settings, getattr(app_state, "embedder", None)))
    return app_state.retriever[1]
