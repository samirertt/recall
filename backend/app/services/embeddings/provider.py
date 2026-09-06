"""Embedding provider abstraction (Phase 8; docs/RESEARCH.md § Local Embeddings).

Default: `BAAI/bge-small-en-v1.5` served through `fastembed` (ONNX runtime, no
PyTorch dependency) — see docs/ARCHITECTURE.md § 6. Every stored embedding is tagged
with `model_name`/`model_revision`/`dims` so a model change never silently mixes
incompatible vector spaces (docs/ARCHITECTURE.md § 10 degradation matrix): if the
provider is unavailable (import failure, no network on first download, ...),
`get_embedding_provider()` returns None and every caller falls back to lexical-only
search rather than raising.
"""

from functools import lru_cache
from typing import Protocol

import numpy as np

from app.core.config import get_settings

DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_MODEL_REVISION = "v1.5"  # fastembed doesn't expose a HF commit hash directly;
# this is the model's own version tag, bumped manually if we ever pin a different revision.


class EmbeddingProvider(Protocol):
    model_name: str
    model_revision: str
    dims: int

    def embed_document(self, text: str) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedProvider:
    model_name = DEFAULT_MODEL_NAME
    model_revision = DEFAULT_MODEL_REVISION
    dims = 384

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=self.model_name)

    def embed_document(self, text: str) -> np.ndarray:
        vec = next(iter(self._model.embed([text])))
        return np.asarray(vec, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        vec = next(iter(self._model.query_embed([text])))
        return np.asarray(vec, dtype=np.float32)


@lru_cache
def get_embedding_provider() -> EmbeddingProvider | None:
    """Lazy, cached singleton — the ONNX model load is slow enough (model download
    on first use, then load into memory) that it must happen at most once per process,
    but must never happen at import time (would break `--no-embeddings` / offline use
    and slow down every CLI/test invocation that doesn't need it).

    Respects `Settings.embeddings_enabled` as an explicit user opt-out; otherwise
    availability is auto-detected (model loads successfully) rather than config-gated,
    matching how FTS5 availability is handled (`is_fts5_available()`)."""
    if not get_settings().embeddings_enabled:
        return None
    try:
        return FastEmbedProvider()
    except Exception:
        return None


def reset_embedding_provider_cache() -> None:
    """Test-only."""
    get_embedding_provider.cache_clear()
