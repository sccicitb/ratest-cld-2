"""BGE-M3 embedder — dense + sparse vectors in one pass (§8.1, §8.5).

Uses FlagEmbedding's BGEM3FlagModel, NOT Qdrant's FastEmbed (ONNX-only, no
BGE-M3). The model is loaded once per process via `get_embedder()`.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TypedDict

from app.config import settings


class SparseVector(TypedDict):
    indices: list[int]
    values: list[float]


class Embedding(TypedDict):
    dense: list[float]
    sparse: SparseVector


def resolve_device() -> str:
    """Pick the compute device: explicit override, else cuda > mps > cpu."""
    if settings.embed_device != "auto":
        return settings.embed_device

    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_use_fp16(device: str) -> bool:
    """fp16 only makes sense on CUDA; never hardcode True (breaks on MPS/CPU)."""
    if settings.embed_use_fp16 is not None:
        return settings.embed_use_fp16
    return device == "cuda"


class Embedder:
    """Thin wrapper around BGEM3FlagModel returning Qdrant-ready vectors."""

    def __init__(self) -> None:
        from FlagEmbedding import BGEM3FlagModel

        device = resolve_device()
        use_fp16 = resolve_use_fp16(device)
        self._model = BGEM3FlagModel(settings.embed_model, use_fp16=use_fp16, devices=device)

    def embed_passages(self, texts: list[str]) -> list[Embedding]:
        return self._encode(texts)

    def embed_query(self, text: str) -> Embedding:
        return self._encode([text])[0]

    def _encode(self, texts: list[str]) -> list[Embedding]:
        out = self._model.encode(texts, return_dense=True, return_sparse=True)
        dense_vecs = out["dense_vecs"]
        lexical_weights = out["lexical_weights"]
        results: list[Embedding] = []
        for dense_row, weights in zip(dense_vecs, lexical_weights):
            indices = [int(k) for k in weights]
            values = [float(v) for v in weights.values()]
            results.append(
                {
                    "dense": [float(x) for x in dense_row],
                    "sparse": {"indices": indices, "values": values},
                }
            )
        return results


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Process-singleton: loads BGE-M3 once (~10-30s)."""
    return Embedder()
