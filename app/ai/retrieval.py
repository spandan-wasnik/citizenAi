"""Hybrid retrieval with an evidence gate.

Keyword (FTS5) and, when available, vector search are fused with Reciprocal Rank Fusion. A chunk counts as *evidence*
only if the question's own words appear in it (lexical coverage) or, for cross-language matches, its vector similarity
reaches a threshold. The vector threshold is deliberately OFF (None) until `scripts/eval_retrieval.py` has measured one
with the real model on a real machine: we do not invent a number we have not measured.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.ai.chunks import Chunk
from app.ai.embeddings import Embedder, EmbeddingUnavailable
from app.ai.keyword_index import KeywordIndex, tokens
from app.ai.vector_store import VectorIndex

RRF_K = 60
MIN_COVERAGE = 0.34            # share of the question's content words found in a chunk, to count as lexical evidence
SECTION_FOR_INTENT = {"documents": ("documents",), "how_to_apply": ("steps",), "eligibility": ("eligibility", "condition"),
                      "scheme_info": ("overview", "benefits", "who_for")}
SECTION_BOOST = 0.004          # small: only reorders near-ties, never lifts an unrelated chunk above a relevant one


@dataclass
class Hit:
    chunk: Chunk
    score: float
    coverage: float = 0.0
    keyword_rank: int | None = None
    vector_rank: int | None = None
    vector_sim: float | None = None
    vector_z: float | None = None           # how far above the average chunk this similarity is, in standard deviations
    evidence: bool = False


@dataclass
class Retrieval:
    hits: list[Hit] = field(default_factory=list)
    mode: str = "keyword_only"              # keyword_only | hybrid
    note: str | None = None                 # why vector search was not used, if it was not


NAME_ONLY_SECTIONS = ("overview", "benefits", "who_for")


def coverage(question: str, chunk: Chunk) -> float:
    """Share of the question's topic words found in the chunk. The scheme's own name is ignored: it is in every chunk of
    that scheme, so counting it would make any chunk look relevant to a question that merely names the scheme."""
    name = set(tokens(chunk.scheme_name))
    q = set(tokens(question)) - name
    if not q:
        # the question only names the scheme: its overview is an answer, its other sections are not
        return 0.5 if set(tokens(question)) & name and chunk.section in NAME_ONLY_SECTIONS else 0.0
    body = set(tokens(chunk.text))
    return len(q & body) / len(q)


class Retriever:
    def __init__(self, chunks: list[Chunk], keyword: KeywordIndex, vectors: VectorIndex | None = None,
                 embedder: Embedder | None = None, min_vector_similarity: float | None = None, min_vector_z: float | None = None):
        self.chunks = {c.chunk_id: c for c in chunks}
        self.keyword, self.vectors, self.embedder = keyword, vectors, embedder
        self.min_vector_similarity = min_vector_similarity
        self.min_vector_z = min_vector_z

    def search(self, question: str, *, language: str, scheme_ids: tuple[str, ...] | None = None,
               intent: str | None = None, k: int = 5) -> Retrieval:
        languages = tuple(dict.fromkeys((language, "en")))
        kw = self.keyword.search(question, k=20, languages=languages, scheme_ids=scheme_ids)
        out = Retrieval()
        vec: list[tuple[str, float]] = []
        if self.vectors is not None and self.embedder is not None:
            try:
                allowed = {cid for cid, c in self.chunks.items() if not scheme_ids or c.scheme_id in scheme_ids}
                vec = self.vectors.search(self.embedder.embed_query(question), k=len(self.vectors), allowed=allowed)
                out.mode = "hybrid"
            except EmbeddingUnavailable:
                out.note = "semantic search unavailable"
        else:
            out.note = "semantic search not enabled"
        scores: dict[str, Hit] = {}
        for rank, (cid, _) in enumerate(kw, 1):
            if cid in self.chunks:
                h = scores.setdefault(cid, Hit(self.chunks[cid], 0.0))
                h.keyword_rank, h.score = rank, h.score + 1.0 / (RRF_K + rank)
        sims = np.array([x for _, x in vec], dtype="float64")
        mean, std = (float(sims.mean()), float(sims.std())) if len(sims) > 1 else (0.0, 0.0)
        for rank, (cid, sim) in enumerate(vec[:20], 1):
            if cid in self.chunks:
                h = scores.setdefault(cid, Hit(self.chunks[cid], 0.0))
                h.vector_rank, h.vector_sim, h.score = rank, sim, h.score + 1.0 / (RRF_K + rank)
                h.vector_z = (sim - mean) / std if std > 1e-9 else 0.0
        wanted = SECTION_FOR_INTENT.get(intent or "", ())
        for h in scores.values():
            h.coverage = coverage(question, h.chunk)
            if h.chunk.section in wanted:
                h.score += SECTION_BOOST
            lexical = h.keyword_rank is not None and h.coverage >= MIN_COVERAGE
            semantic = (self.min_vector_similarity is not None and h.vector_sim is not None
                        and h.vector_sim >= self.min_vector_similarity)
            relative = (self.min_vector_z is not None and h.vector_z is not None and h.vector_z >= self.min_vector_z)
            h.evidence = lexical or semantic or relative
        out.hits = sorted(scores.values(), key=lambda h: (-h.score, h.chunk.chunk_id))[:k]
        return out
