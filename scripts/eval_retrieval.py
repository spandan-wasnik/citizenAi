"""Measure retrieval on eval/retrieval_gold.json. Run on your laptop (the real embedding model cannot be downloaded in
the build sandbox, so real semantic results can only come from you):

    python -m scripts.eval_retrieval                 # keyword-only
    python -m scripts.eval_retrieval --semantic      # adds the configured embedding model (needs requirements-ml.txt)

Reports recall@3 and MRR for answerable questions, how often out-of-scope questions are correctly declined, and the
vector-similarity ranges that a threshold should separate. It never changes settings; choosing a threshold is a human
decision made from these numbers."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from app.ai.service import build_retriever
from app.config import get_settings
from app.database.connection import make_engine, session_scope
from app.nlp.intent import classify_intent

GOLD = Path(__file__).resolve().parents[1] / "eval" / "retrieval_gold.json"


def run(semantic: bool, threshold: float | None) -> int:
    settings = get_settings()
    if semantic:
        settings = settings.model_copy(update={"semantic_search": True})
    eng = make_engine(settings.database_url)
    with session_scope(eng) as s:
        r = build_retriever(s, date.today(), settings)
    r.min_vector_similarity = threshold
    mode = "hybrid" if r.vectors is not None else "keyword_only"
    if semantic and mode != "hybrid":
        print("Semantic search could not start (is requirements-ml.txt installed, and is the model downloaded?).")
        return 2
    cases = json.loads(GOLD.read_text(encoding="utf-8"))["cases"]
    stats: dict[str, dict[str, float]] = {}
    sims_ok, sims_none = [], []
    for c in cases:
        it = classify_intent(c["q"])       # same scoping the API uses: a named scheme limits the search
        res = r.search(c["q"], language=c["lang"], scheme_ids=tuple(it.scheme_ids) or None, intent=it.primary)
        ev = [h for h in res.hits if h.evidence]
        top_sim = max((h.vector_sim for h in res.hits if h.vector_sim is not None), default=None)
        g = stats.setdefault(c["lang"], dict(n=0, hit3=0, rr=0.0, none=0, declined=0))
        if c.get("none"):
            g["none"] += 1
            g["declined"] += 0 if ev else 1
            if top_sim is not None:
                sims_none.append(top_sim)
            if ev:
                print(f"  WRONGLY ANSWERED [{c['lang']}] {c['q']} -> {ev[0].chunk.scheme_id}/{ev[0].chunk.section}")
            continue
        g["n"] += 1
        rank = next((i for i, h in enumerate(ev, 1) if h.chunk.scheme_id == c["scheme"] and h.chunk.section in c["sections"]), None)
        g["hit3"] += 1 if rank and rank <= 3 else 0
        g["rr"] += 1.0 / rank if rank else 0.0
        if top_sim is not None and rank:
            sims_ok.append(top_sim)
        if not rank:
            print(f"  MISSED [{c['lang']}] {c['q']}")
    print(f"\nmode: {mode}   threshold: {threshold}\n")
    for lang, g in sorted(stats.items()):
        n, none = g["n"], g["none"]
        print(f"{lang}: answerable={n:2d} recall@3={g['hit3'] / n if n else float('nan'):.2f} MRR={g['rr'] / n if n else float('nan'):.2f}"
              f" | out-of-scope={none} declined={g['declined']}")
    if sims_ok and sims_none:
        print(f"\nvector similarity: answerable top hits min={min(sims_ok):.3f}  out-of-scope top hits max={max(sims_none):.3f}")
        print("A threshold must sit between these to separate them; with overlap, no threshold is clean. Developer-written gold set: be sceptical.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--semantic", action="store_true")
    ap.add_argument("--threshold", type=float, default=None, help="minimum vector similarity that counts as evidence")
    a = ap.parse_args()
    sys.exit(run(a.semantic, a.threshold))
