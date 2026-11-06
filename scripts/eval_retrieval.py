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


def run(semantic: bool, threshold: float | None, zmin: float | None = None) -> int:
    settings = get_settings()
    if semantic:
        settings = settings.model_copy(update={"semantic_search": True})
    eng = make_engine(settings.database_url)
    with session_scope(eng) as s:
        r = build_retriever(s, date.today(), settings)
    r.min_vector_similarity = threshold
    r.min_vector_z = zmin
    mode = "hybrid" if r.vectors is not None else "keyword_only"
    if semantic and mode != "hybrid":
        print("Semantic search could not start (is requirements-ml.txt installed, and is the model downloaded?).")
        return 2
    cases = json.loads(GOLD.read_text(encoding="utf-8"))["cases"]
    stats: dict[str, dict[str, float]] = {}
    sims_ok, sims_none, z_ok, z_none, ranks = [], [], [], [], []
    scoped_z = {"answerable": [], "out_of_scope": []}
    unscoped = {"answerable": 0, "answered": 0, "out_of_scope": 0, "wrong": 0}   # questions that name no scheme
    for c in cases:
        it = classify_intent(c["q"])       # same scoping the API uses: a named scheme limits the search
        res = r.search(c["q"], language=c["lang"], scheme_ids=tuple(it.scheme_ids) or None, intent=it.primary)
        ev = [h for h in res.hits if h.evidence]
        top_sim = max((h.vector_sim for h in res.hits if h.vector_sim is not None), default=None)
        top_z = max((h.vector_z for h in res.hits if h.vector_z is not None), default=None)
        vrank = next((i for i, h in enumerate(sorted((h for h in res.hits if h.vector_rank), key=lambda h: h.vector_rank), 1)
                      if not c.get("none") and h.chunk.scheme_id == c["scheme"] and h.chunk.section in c["sections"]), None)
        scoped = bool(it.scheme_ids)
        kind = "out_of_scope" if c.get("none") else "answerable"
        if scoped and top_z is not None:
            scoped_z[kind].append(top_z)
        if not scoped:
            unscoped[kind] += 1
            if ev:
                unscoped["answered" if kind == "answerable" else "wrong"] += 1
        g = stats.setdefault(c["lang"], dict(n=0, hit3=0, rr=0.0, none=0, declined=0))
        if c.get("none"):
            g["none"] += 1
            g["declined"] += 0 if ev else 1
            if top_sim is not None:
                sims_none.append(top_sim)
                z_none.append(top_z)
            if ev:
                print(f"  WRONGLY ANSWERED [{c['lang']}] {c['q']} -> {ev[0].chunk.scheme_id}/{ev[0].chunk.section}")
            continue
        g["n"] += 1
        rank = next((i for i, h in enumerate(ev, 1) if h.chunk.scheme_id == c["scheme"] and h.chunk.section in c["sections"]), None)
        g["hit3"] += 1 if rank and rank <= 3 else 0
        g["rr"] += 1.0 / rank if rank else 0.0
        if top_sim is not None:
            ranks.append((c["lang"], vrank))
            sims_ok.append(top_sim)
            z_ok.append(top_z)
        if not rank:
            print(f"  MISSED [{c['lang']}] {c['q']}   (names a scheme: {scoped}; best z: {top_z if top_z is None else round(top_z, 2)}; "
                  f"correct passage rank by vector: {vrank})")
    print(f"\nmode: {mode}   threshold: {threshold}\n")
    for lang, g in sorted(stats.items()):
        n, none = g["n"], g["none"]
        print(f"{lang}: answerable={n:2d} recall@3={g['hit3'] / n if n else float('nan'):.2f} MRR={g['rr'] / n if n else float('nan'):.2f}"
              f" | out-of-scope={none} declined={g['declined']}")
    if sims_ok and sims_none:
        print(f"\nvector similarity: answerable top hits min={min(sims_ok):.3f}  out-of-scope top hits max={max(sims_none):.3f}")
        print("A threshold must sit between these to separate them; with overlap, no threshold is clean. Developer-written gold set: be sceptical.")
    if z_ok and z_none:
        print(f"z-score (similarity vs the average chunk): answerable top min={min(z_ok):.2f} median={sorted(z_ok)[len(z_ok) // 2]:.2f}"
              f"  out-of-scope top max={max(z_none):.2f}")
    print(f"\nquestions naming a scheme  : z answerable top = {sorted(round(z, 2) for z in scoped_z['answerable'])}")
    print(f"                             z out-of-scope top = {sorted(round(z, 2) for z in scoped_z['out_of_scope'])}")
    print(f"questions naming no scheme : {unscoped['answerable']} answerable, {unscoped['answered']} of them answered; "
          f"{unscoped['out_of_scope']} out-of-scope, {unscoped['wrong']} wrongly answered")
    if ranks:
        for lang in sorted({l for l, _ in ranks}):
            rs = [r for l, r in ranks if l == lang]
            print(f"vector-only (no gate) {lang}: correct passage in vector top-3 for {sum(1 for r in rs if r and r <= 3)}/{len(rs)} answerable questions")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--semantic", action="store_true")
    ap.add_argument("--threshold", type=float, default=None, help="minimum vector similarity that counts as evidence")
    ap.add_argument("--zmin", type=float, default=None, help="minimum z-score that counts as evidence")
    a = ap.parse_args()
    sys.exit(run(a.semantic, a.threshold, a.zmin))
