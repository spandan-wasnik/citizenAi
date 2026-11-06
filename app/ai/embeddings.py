"""Text embedders behind one small interface.

* `SentenceTransformerEmbedder`: the real multilingual model, loaded only when first used, so the app starts fast
  and keeps working (keyword search only) when the ML packages or the model files are missing.
* `HashingEmbedder`: a deterministic bag-of-words stand-in used by tests. It captures word overlap, NOT meaning, and
  must never be offered to a person as semantic search. Its `name` says so.
"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

import numpy as np

from app.ai.keyword_index import tokens


class EmbeddingUnavailable(Exception):
    """The embedding model or its libraries could not be loaded. The caller falls back to keyword search."""


class Embedder(Protocol):
    name: str
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return (v / np.where(n == 0, 1, n)).astype("float32")


class HashingEmbedder:
    name = "test-only-hashing-bow"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype="float32")
        for t in tokens(text):
            h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0 if False else 1.0
        return v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return _unit(np.stack([self._vec(t) for t in texts])) if texts else np.zeros((0, self.dim), "float32")

    def embed_query(self, text: str) -> np.ndarray:
        return _unit(self._vec(text)[None, :])[0]


class SentenceTransformerEmbedder:
    """multilingual-e5 style models expect 'query: ' and 'passage: ' prefixes; other models do not use them."""

    def __init__(self, model_name: str, batch_size: int = 16):
        self.name = model_name
        self.batch_size = batch_size
        self._model = None
        self.dim = 0

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.name)
            except Exception as e:                      # missing package, no network, bad name: all mean "unavailable"
                raise EmbeddingUnavailable(f"{type(e).__name__}") from None
            self.dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    @property
    def _e5(self) -> bool:
        return "e5" in self.name.lower()

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        pre = "passage: " if self._e5 else ""
        return _unit(np.asarray(model.encode([pre + t for t in texts], batch_size=self.batch_size,
                                             normalize_embeddings=False, show_progress_bar=False)))

    def embed_query(self, text: str) -> np.ndarray:
        model = self._load()
        pre = "query: " if self._e5 else ""
        return _unit(np.asarray(model.encode([pre + text], normalize_embeddings=False, show_progress_bar=False)))[0]


def finite(v: np.ndarray) -> bool:
    return bool(np.isfinite(v).all()) and not math.isnan(float(v.sum()))
