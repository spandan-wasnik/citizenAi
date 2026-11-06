from datetime import date
from pathlib import Path

import pytest

from app.ai.chunks import MAX_CHARS, build_chunks, split_text
from app.ai.keyword_index import KeywordIndex, tokens
from app.database.connection import make_engine, session_scope
from app.services.scheme_loader import load_registry, load_scheme_dir, seed_schemes
from scripts.init_db import init_database

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_registry(ROOT / "data" / "sources" / "registry.yaml")
SCHEMES, _ = load_scheme_dir(ROOT / "data" / "schemes", REGISTRY)


@pytest.fixture(scope="module")
def chunks(tmp_path_factory):
    url = f"sqlite:///{tmp_path_factory.mktemp('rag') / 'r.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
        return build_chunks(s, date(2026, 11, 5))


@pytest.fixture(scope="module")
def index(chunks):
    ix = KeywordIndex()
    ix.rebuild(chunks)
    return ix


# ------------------------------------------------------------------ chunks
def test_every_scheme_is_covered_and_ids_are_unique(chunks):
    assert {c.scheme_id for c in chunks} == {s.scheme_id for s in SCHEMES}
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_every_chunk_is_traceable_to_an_official_source(chunks):
    for c in chunks:
        assert c.sources and all(u.startswith("https://") for u, _ in c.sources), c
        assert c.locator, c
        assert c.text.startswith(c.scheme_name) and c.body in c.text
        assert len(c.body) <= MAX_CHARS or len(split_text(c.body)) == 1


def test_titles_are_not_bare_urls_when_the_source_has_a_title(chunks):
    titled = [t for c in chunks for u, t in c.sources if t != u]
    assert titled


def test_english_fallback_text_is_not_indexed_as_hindi_or_marathi(chunks):
    for c in chunks:
        if c.language != "en":
            assert c.section == "eligibility"          # only text that really exists in hi/mr (rules are rendered by template)


def test_chunk_ids_are_stable_between_builds(chunks, tmp_path):
    url = f"sqlite:///{tmp_path / 'again.db'}"
    init_database(url)
    eng = make_engine(url)
    with session_scope(eng) as s:
        seed_schemes(s, SCHEMES, REGISTRY)
        again = build_chunks(s, date(2026, 11, 5))
    assert [c.chunk_id for c in again] == [c.chunk_id for c in chunks]


def test_split_keeps_sentences_whole_and_handles_empty():
    assert split_text("") == [] and split_text("One.") == ["One."]
    long = " ".join(f"Sentence number {i} is here." for i in range(200))
    parts = split_text(long)
    assert len(parts) > 1 and all(len(p) <= MAX_CHARS for p in parts) and all(p.endswith(".") for p in parts)
    assert " ".join(parts) == long


# ------------------------------------------------------------------ keyword index
def top(index, chunks, q, **kw):
    by = {c.chunk_id: c for c in chunks}
    return [by[i] for i, _ in index.search(q, **kw)]


def test_english_query_finds_the_right_scheme(index, chunks):
    assert top(index, chunks, "monthly pension after retirement age 60")[0].scheme_id == "atal-pension-yojana"
    assert top(index, chunks, "Rs. 6,000 per year farmers cultivable land")[0].scheme_id == "pm-kisan"


def test_documents_question_ranks_the_documents_chunk_first_for_that_scheme(index, chunks):
    hit = top(index, chunks, "documents needed land records", scheme_ids=("pm-kisan",))[0]
    assert hit.scheme_id == "pm-kisan" and hit.section == "documents"


def test_devanagari_words_are_kept_whole(index):
    assert tokens("शेतकरी कुटुंब") == ["शेतकरी", "कुटुंब"]
    assert tokens("६००० रुपये") == ["6000", "रुपये"]
    assert tokens("What is the age?") == ["age"] and tokens("") == [] and tokens("   ") == []


def test_hindi_query_matches_hindi_text_only_where_it_exists(index, chunks):
    hits = top(index, chunks, "आयु सीमा", languages=("hi",))
    assert all(h.language == "hi" for h in hits)


def test_unusable_or_hostile_queries_return_empty_not_errors(index):
    for q in ("", "the of a", '"', "AND OR NOT", "* ^ ( )", "x" * 5000, "'; DROP TABLE kw_chunks; --"):
        assert isinstance(index.search(q), list)
    assert index.count() > 0


def test_filters_restrict_results(index, chunks):
    only = top(index, chunks, "eligible income", scheme_ids=("pm-kisan",), k=20)
    assert only and {c.scheme_id for c in only} == {"pm-kisan"}
    assert top(index, chunks, "eligible", languages=("mr",), k=20) == [] or all(c.language == "mr" for c in top(index, chunks, "eligible", languages=("mr",), k=20))


def test_rebuild_replaces_everything(chunks):
    ix = KeywordIndex()
    ix.rebuild(chunks)
    ix.rebuild(chunks[:3])
    assert ix.count() == 3


# ------------------------------------------------------------------ vectors (deterministic stand-in embedder: word overlap only)
import numpy as np  # noqa: E402

from app.ai.embeddings import EmbeddingUnavailable, HashingEmbedder, SentenceTransformerEmbedder  # noqa: E402
from app.ai.vector_store import VectorIndex, fingerprint  # noqa: E402


@pytest.fixture(scope="module")
def vindex(chunks):
    return VectorIndex.build(chunks, HashingEmbedder())


def test_vectors_are_unit_length_and_search_finds_overlapping_text(chunks, vindex):
    assert np.allclose(np.linalg.norm(vindex.vectors, axis=1), 1.0, atol=1e-5)
    emb = HashingEmbedder()
    by = {c.chunk_id: c for c in chunks}
    best = by[vindex.search(emb.embed_query("land records farmers rupees 6000"), k=3)[0][0]]
    assert best.scheme_id == "pm-kisan"


def test_numpy_and_faiss_paths_agree(chunks, vindex):
    emb = HashingEmbedder()
    q = emb.embed_query("pension at age 60")
    a = [i for i, _ in vindex.search(q, k=5)]
    b = [i for i, _ in vindex.search(q, k=5, allowed={c.chunk_id for c in chunks})]
    assert a == b and vindex.backend in ("faiss", "numpy")


def test_allowed_filter_and_empty_and_bad_queries(chunks, vindex):
    emb = HashingEmbedder()
    only = {c.chunk_id for c in chunks if c.scheme_id == "pm-kisan"}
    assert {i for i, _ in vindex.search(emb.embed_query("pension"), k=50, allowed=only)} <= only
    assert vindex.search(np.full(256, np.nan, dtype="float32")) == []
    assert VectorIndex([], np.zeros((0, 256), "float32"), "m", "f").search(emb.embed_query("x")) == []


def test_saved_index_is_reused_only_if_model_and_text_match(chunks, vindex, tmp_path):
    vindex.save(tmp_path)
    fp = fingerprint(chunks)
    assert VectorIndex.load(tmp_path, model=vindex.model, fp=fp).ids == vindex.ids
    assert VectorIndex.load(tmp_path, model="another-model", fp=fp) is None
    assert VectorIndex.load(tmp_path, model=vindex.model, fp=fingerprint(chunks[:-1])) is None
    (tmp_path / "meta.json").write_text("{broken", encoding="utf-8")
    assert VectorIndex.load(tmp_path, model=vindex.model, fp=fp) is None
    assert VectorIndex.load(tmp_path / "missing", model="m", fp="f") is None


def test_missing_model_is_reported_as_unavailable_not_a_crash():
    emb = SentenceTransformerEmbedder("definitely/not-a-real-model-name-xyz")
    with pytest.raises(EmbeddingUnavailable):
        emb.embed_query("hello")


# ------------------------------------------------------------------ the relative (z-score) evidence rule
from app.ai.retrieval import Retriever  # noqa: E402


def test_z_score_gate_is_off_by_default_and_only_adds_evidence_when_set(chunks, index, vindex):
    emb = HashingEmbedder()
    off = Retriever(chunks, index, vindex, emb)
    q = "zzzz qqqq land records"                       # one word the records contain: too little for the lexical rule
    assert all(h.vector_z is not None for h in off.search(q, language="en").hits)
    strict = Retriever(chunks, index, vindex, emb, min_vector_z=50.0)
    assert not any(h.evidence for h in strict.search("zzzz qqqq", language="en").hits)
    loose = Retriever(chunks, index, vindex, emb, min_vector_z=0.5)
    assert any(h.evidence for h in loose.search("land records pm kisan", language="en").hits)
