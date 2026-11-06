"""Exact nearest-neighbour search over chunk vectors.

At this size (hundreds to a few thousand chunks) exact inner-product search takes milliseconds, so no approximate
index is needed. FAISS is used when installed; otherwise the same search runs in NumPy, so semantic search does not
depend on one optional package. Vectors are unit length, so inner product equals cosine similarity.

The index stores the model name and a fingerprint of the chunk texts. If either changes, `load` refuses the old
files, so stale vectors can never be matched against newer text.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from app.ai.chunks import Chunk
from app.ai.embeddings import Embedder, finite

try:                                              # optional
    import faiss  # type: ignore
except Exception:                                 # noqa: BLE001
    faiss = None


def fingerprint(chunks: list[Chunk]) -> str:
    h = hashlib.sha256()
    for c in sorted(chunks, key=lambda c: c.chunk_id):
        h.update(c.chunk_id.encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


class VectorIndex:
    def __init__(self, ids: list[str], vectors: np.ndarray, model: str, fp: str):
        self.ids, self.model, self.fp = ids, model, fp
        self.vectors = np.ascontiguousarray(vectors, dtype="float32")
        self._faiss = None
        if faiss is not None and len(ids):
            self._faiss = faiss.IndexFlatIP(self.vectors.shape[1])
            self._faiss.add(self.vectors)

    @property
    def backend(self) -> str:
        return "faiss" if self._faiss is not None else "numpy"

    def __len__(self) -> int:
        return len(self.ids)

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder) -> "VectorIndex":
        vecs = embedder.embed_passages([c.text for c in chunks])
        if len(chunks) and (vecs.shape[0] != len(chunks) or not finite(vecs)):
            raise ValueError("embedder returned unusable vectors")
        return cls([c.chunk_id for c in chunks], vecs, embedder.name, fingerprint(chunks))

    def search(self, query_vec: np.ndarray, k: int = 10, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        """(chunk_id, cosine) best first. `allowed` limits results to those chunk ids (language, scheme filters)."""
        if not len(self.ids) or not finite(query_vec):
            return []
        q = np.asarray(query_vec, dtype="float32")[None, :]
        if allowed is None and self._faiss is not None:
            scores, idx = self._faiss.search(q, min(k, len(self.ids)))
            return [(self.ids[i], float(s)) for s, i in zip(scores[0], idx[0]) if i >= 0]
        sims = (self.vectors @ q[0])
        order = np.argsort(-sims)
        out = []
        for i in order:
            if allowed is not None and self.ids[i] not in allowed:
                continue
            out.append((self.ids[i], float(sims[i])))
            if len(out) >= k:
                break
        return out

    # ---- persistence: a vectors file plus a small JSON header
    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        np.save(folder / "vectors.npy", self.vectors)
        (folder / "meta.json").write_text(json.dumps({"ids": self.ids, "model": self.model, "fingerprint": self.fp}), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path, *, model: str, fp: str) -> "VectorIndex | None":
        """The saved index, or None if it is missing, damaged, or built for another model or other chunk texts."""
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            vecs = np.load(folder / "vectors.npy", allow_pickle=False)
            if meta["model"] != model or meta["fingerprint"] != fp or vecs.shape[0] != len(meta["ids"]):
                return None
            return cls(list(meta["ids"]), vecs, meta["model"], meta["fingerprint"])
        except Exception:                                 # noqa: BLE001  missing, truncated, tampered: rebuild instead
            return None
