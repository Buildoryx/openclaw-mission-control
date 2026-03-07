"""Embedding service abstraction for semantic search.

Provides a protocol-based interface for generating text embeddings, with
concrete implementations for:

- ``FastEmbedService`` — lightweight ONNX-based embeddings via ``fastembed``
- ``NoopEmbedService`` — returns ``None`` (used when embeddings are disabled)

The active implementation is selected based on ``settings.embedding_provider``.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from app.core.config import settings

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingService(Protocol):
    """Protocol for embedding text into dense vectors."""

    async def embed(self, text: str) -> list[float] | None:
        """Return a dense vector for *text*, or ``None`` on failure."""
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of the vectors produced by this service."""
        ...


class NoopEmbedService:
    """Embedding service that always returns ``None``.

    Used when ``EMBEDDING_PROVIDER=none`` — semantic search is disabled but
    the rest of the deliberation module works normally.
    """

    async def embed(self, text: str) -> list[float] | None:
        return None

    @property
    def dimension(self) -> int:
        return settings.embedding_dim


class FastEmbedService:
    """Embedding service backed by the ``fastembed`` ONNX library.

    The model is loaded lazily on the first call to :meth:`embed` to avoid
    import-time overhead and to keep startup fast when embeddings are not
    immediately needed.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or settings.embedding_model
        self._model: object | None = None

    def _load_model(self) -> object:
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding  # type: ignore[import-untyped]

            self._model = TextEmbedding(model_name=self._model_name)
            logger.info(
                "embedding.fastembed.loaded model=%s dim=%d",
                self._model_name,
                self.dimension,
            )
        except Exception:
            logger.exception(
                "embedding.fastembed.load_failed model=%s",
                self._model_name,
            )
            raise
        return self._model

    async def embed(self, text: str) -> list[float] | None:
        if not text or not text.strip():
            return None
        try:
            model = self._load_model()
            # fastembed's embed() returns a generator of numpy arrays.
            embeddings = list(model.embed([text]))  # type: ignore[union-attr]
            if not embeddings:
                return None
            vector = embeddings[0]
            # Convert numpy array to plain Python list of floats.
            return [float(v) for v in vector]
        except Exception:
            logger.exception("embedding.fastembed.embed_failed")
            return None

    @property
    def dimension(self) -> int:
        return settings.embedding_dim


def _build_embedding_service() -> EmbeddingService:
    """Construct the embedding service based on configuration."""
    provider = settings.embedding_provider.lower().strip()

    if provider == "fastembed":
        logger.info(
            "embedding.provider=fastembed model=%s",
            settings.embedding_model,
        )
        return FastEmbedService()

    if provider == "openai":
        # OpenAI embedding support is deferred to a follow-up PR.
        # Fall back to noop with a warning.
        logger.warning(
            "embedding.provider=openai is not yet implemented; "
            "falling back to noop. Semantic search will be disabled.",
        )
        return NoopEmbedService()

    if provider not in {"none", ""}:
        logger.warning(
            "embedding.provider=%s is not recognised; falling back to noop.",
            provider,
        )

    logger.info("embedding.provider=none — semantic search disabled")
    return NoopEmbedService()


# Module-level singleton following MC convention.
embedding_service: EmbeddingService = _build_embedding_service()
