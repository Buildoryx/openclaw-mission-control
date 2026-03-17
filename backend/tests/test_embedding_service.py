# ruff: noqa
"""Tests for the embedding service abstraction layer.

Covers:
- NoopEmbedService always returns None with correct dimension
- FastEmbedService lazy model loading and embed interface
- Provider selection logic in _build_embedding_service
- EmbeddingService protocol compliance
- Edge cases: empty strings, whitespace-only input, provider name variants
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.memory.embedding import (
    EmbeddingService,
    FastEmbedService,
    NoopEmbedService,
    _build_embedding_service,
    embedding_service,
)


# ---------------------------------------------------------------------------
# NoopEmbedService
# ---------------------------------------------------------------------------


class TestNoopEmbedService:
    """Tests for the noop embedding provider (semantic search disabled)."""

    def setup_method(self) -> None:
        self.service = NoopEmbedService()

    @pytest.mark.asyncio
    async def test_embed_returns_none(self) -> None:
        result = await self.service.embed("hello world")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_empty_string_returns_none(self) -> None:
        result = await self.service.embed("")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_whitespace_returns_none(self) -> None:
        result = await self.service.embed("   ")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_long_text_returns_none(self) -> None:
        result = await self.service.embed("a" * 10000)
        assert result is None

    def test_dimension_matches_settings(self) -> None:
        from app.core.config import settings

        assert self.service.dimension == settings.embedding_dim

    def test_dimension_is_positive_integer(self) -> None:
        assert isinstance(self.service.dimension, int)
        assert self.service.dimension > 0

    def test_implements_protocol(self) -> None:
        assert isinstance(self.service, EmbeddingService)

    @pytest.mark.asyncio
    async def test_embed_none_like_input(self) -> None:
        # Should handle gracefully even though type says str
        result = await self.service.embed("")
        assert result is None


# ---------------------------------------------------------------------------
# FastEmbedService
# ---------------------------------------------------------------------------


class TestFastEmbedService:
    """Tests for the fastembed ONNX-backed embedding service."""

    def test_init_default_model(self) -> None:
        service = FastEmbedService()
        from app.core.config import settings

        assert service._model_name == settings.embedding_model
        assert service._model is None  # Lazy, not loaded yet

    def test_init_custom_model(self) -> None:
        service = FastEmbedService(model_name="custom/model-v1")
        assert service._model_name == "custom/model-v1"
        assert service._model is None

    def test_dimension_matches_settings(self) -> None:
        service = FastEmbedService()
        from app.core.config import settings

        assert service.dimension == settings.embedding_dim

    def test_implements_protocol(self) -> None:
        service = FastEmbedService()
        assert isinstance(service, EmbeddingService)

    @pytest.mark.asyncio
    async def test_embed_empty_string_returns_none(self) -> None:
        service = FastEmbedService()
        result = await service.embed("")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_whitespace_only_returns_none(self) -> None:
        service = FastEmbedService()
        result = await service.embed("   \n\t  ")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_with_mocked_model(self) -> None:
        """Test embedding with a mocked fastembed TextEmbedding model."""
        import numpy as np

        service = FastEmbedService()

        fake_vector = np.array([0.1, 0.2, 0.3, 0.4])
        mock_model = MagicMock()
        mock_model.embed.return_value = iter([fake_vector])

        # Inject the mock model directly
        service._model = mock_model

        result = await service.embed("test query")
        assert result is not None
        assert isinstance(result, list)
        assert len(result) == 4
        assert all(isinstance(v, float) for v in result)
        assert result == pytest.approx([0.1, 0.2, 0.3, 0.4])
        mock_model.embed.assert_called_once_with(["test query"])

    @pytest.mark.asyncio
    async def test_embed_returns_list_of_floats(self) -> None:
        """Verify the output is a plain Python list of floats, not numpy."""
        import numpy as np

        service = FastEmbedService()
        fake_vector = np.array([1.0, 2.0, 3.0])
        mock_model = MagicMock()
        mock_model.embed.return_value = iter([fake_vector])
        service._model = mock_model

        result = await service.embed("hello")
        assert result is not None
        for v in result:
            assert type(v) is float

    @pytest.mark.asyncio
    async def test_embed_model_returns_empty(self) -> None:
        """When the model returns no embeddings, result should be None."""
        service = FastEmbedService()
        mock_model = MagicMock()
        mock_model.embed.return_value = iter([])
        service._model = mock_model

        result = await service.embed("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_model_raises_exception_returns_none(self) -> None:
        """Embedding failures should return None, not raise."""
        service = FastEmbedService()
        mock_model = MagicMock()
        mock_model.embed.side_effect = RuntimeError("model exploded")
        service._model = mock_model

        result = await service.embed("test")
        assert result is None

    def test_load_model_caches_result(self) -> None:
        """The model should only be loaded once (lazy singleton)."""
        service = FastEmbedService()
        mock_model = MagicMock()
        service._model = mock_model

        # Calling _load_model when already loaded should return same instance
        loaded = service._load_model()
        assert loaded is mock_model

    def test_load_model_raises_on_import_failure(self) -> None:
        """If fastembed is not installed, _load_model should raise."""
        service = FastEmbedService()

        with patch.dict("sys.modules", {"fastembed": None}):
            with patch("builtins.__import__", side_effect=ImportError("no fastembed")):
                with pytest.raises(ImportError):
                    service._load_model()

    @pytest.mark.asyncio
    async def test_embed_calls_load_model_lazily(self) -> None:
        """First call to embed should trigger model loading."""
        service = FastEmbedService()

        # Mock _load_model to return a fake model
        import numpy as np

        fake_model = MagicMock()
        fake_model.embed.return_value = iter([np.array([0.5, 0.6])])

        with patch.object(service, "_load_model", return_value=fake_model) as mock_load:
            result = await service.embed("hello")
            mock_load.assert_called_once()
            assert result is not None


# ---------------------------------------------------------------------------
# _build_embedding_service provider selection
# ---------------------------------------------------------------------------


class TestBuildEmbeddingService:
    """Tests for the provider factory function."""

    def test_none_provider_returns_noop(self) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "none"
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "test-model"
            service = _build_embedding_service()
            assert isinstance(service, NoopEmbedService)

    def test_empty_provider_returns_noop(self) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = ""
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "test-model"
            service = _build_embedding_service()
            assert isinstance(service, NoopEmbedService)

    def test_fastembed_provider_returns_fastembed(self) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "fastembed"
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "BAAI/bge-small-en-v1.5"
            service = _build_embedding_service()
            assert isinstance(service, FastEmbedService)

    def test_fastembed_provider_case_insensitive(self) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "FastEmbed"
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "test-model"
            service = _build_embedding_service()
            assert isinstance(service, FastEmbedService)

    def test_fastembed_provider_with_whitespace(self) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "  fastembed  "
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "test-model"
            service = _build_embedding_service()
            assert isinstance(service, FastEmbedService)

    def test_openai_provider_falls_back_to_noop(self) -> None:
        """OpenAI support is deferred; should log warning and return noop."""
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "openai"
            mock_settings.embedding_dim = 1536
            mock_settings.embedding_model = "text-embedding-ada-002"
            service = _build_embedding_service()
            assert isinstance(service, NoopEmbedService)

    def test_unknown_provider_falls_back_to_noop(self) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "unknown-provider"
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "test-model"
            service = _build_embedding_service()
            assert isinstance(service, NoopEmbedService)

    def test_unknown_provider_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "mysterybox"
            mock_settings.embedding_dim = 384
            mock_settings.embedding_model = "test-model"
            with caplog.at_level(
                logging.WARNING, logger="app.services.memory.embedding"
            ):
                _build_embedding_service()
            assert any("mysterybox" in record.message for record in caplog.records)

    def test_openai_provider_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch("app.services.memory.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "openai"
            mock_settings.embedding_dim = 1536
            mock_settings.embedding_model = "test-model"
            with caplog.at_level(
                logging.WARNING, logger="app.services.memory.embedding"
            ):
                _build_embedding_service()
            assert any("openai" in record.message.lower() for record in caplog.records)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    """Verify the module-level embedding_service singleton."""

    def test_singleton_exists(self) -> None:
        assert embedding_service is not None

    def test_singleton_implements_protocol(self) -> None:
        assert isinstance(embedding_service, EmbeddingService)

    def test_singleton_has_embed_method(self) -> None:
        assert callable(getattr(embedding_service, "embed", None))

    def test_singleton_has_dimension_property(self) -> None:
        dim = embedding_service.dimension
        assert isinstance(dim, int)
        assert dim > 0


# ---------------------------------------------------------------------------
# EmbeddingService protocol
# ---------------------------------------------------------------------------


class TestEmbeddingServiceProtocol:
    """Verify the protocol is runtime-checkable."""

    def test_noop_satisfies_protocol(self) -> None:
        assert isinstance(NoopEmbedService(), EmbeddingService)

    def test_fastembed_satisfies_protocol(self) -> None:
        assert isinstance(FastEmbedService(), EmbeddingService)

    def test_arbitrary_object_does_not_satisfy(self) -> None:
        assert not isinstance("not a service", EmbeddingService)
        assert not isinstance(42, EmbeddingService)
        assert not isinstance({}, EmbeddingService)

    def test_partial_implementation_does_not_satisfy(self) -> None:
        """An object with embed() but no dimension property should not match."""

        class PartialEmbed:
            async def embed(self, text: str) -> list[float] | None:
                return None

        # Protocol requires both embed AND dimension
        obj = PartialEmbed()
        assert not isinstance(obj, EmbeddingService)

    def test_full_custom_implementation_satisfies(self) -> None:
        """A custom class implementing both methods should satisfy the protocol."""

        class CustomEmbed:
            async def embed(self, text: str) -> list[float] | None:
                return [0.0] * 10

            @property
            def dimension(self) -> int:
                return 10

        assert isinstance(CustomEmbed(), EmbeddingService)


# ---------------------------------------------------------------------------
# Integration-style tests (noop provider, no external deps)
# ---------------------------------------------------------------------------


class TestNoopIntegration:
    """End-to-end tests using the noop provider to verify the full call chain."""

    @pytest.mark.asyncio
    async def test_multiple_embeds_all_return_none(self) -> None:
        service = NoopEmbedService()
        texts = [
            "first query",
            "second query",
            "third query with special chars: éàü™",
            "",
            "   ",
        ]
        for text in texts:
            result = await service.embed(text)
            assert result is None

    @pytest.mark.asyncio
    async def test_embed_unicode_text(self) -> None:
        service = NoopEmbedService()
        result = await service.embed("日本語テスト 🎉 Ελληνικά")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_very_long_text(self) -> None:
        service = NoopEmbedService()
        result = await service.embed("word " * 100000)
        assert result is None


# ---------------------------------------------------------------------------
# FastEmbedService dimension and model name
# ---------------------------------------------------------------------------


class TestFastEmbedServiceConfig:
    """Test configuration-related behavior of FastEmbedService."""

    def test_default_model_name_from_settings(self) -> None:
        from app.core.config import settings

        service = FastEmbedService()
        assert service._model_name == settings.embedding_model

    def test_custom_model_name_override(self) -> None:
        service = FastEmbedService(model_name="sentence-transformers/all-MiniLM-L6-v2")
        assert service._model_name == "sentence-transformers/all-MiniLM-L6-v2"

    def test_none_model_name_uses_settings(self) -> None:
        from app.core.config import settings

        service = FastEmbedService(model_name=None)
        assert service._model_name == settings.embedding_model

    def test_dimension_returns_int(self) -> None:
        service = FastEmbedService()
        assert isinstance(service.dimension, int)

    def test_model_not_loaded_at_init(self) -> None:
        """FastEmbedService should defer model loading until first embed call."""
        service = FastEmbedService()
        assert service._model is None


# ---------------------------------------------------------------------------
# Concurrent embed calls
# ---------------------------------------------------------------------------


class TestConcurrentEmbeds:
    """Verify thread/async safety of embed calls."""

    @pytest.mark.asyncio
    async def test_concurrent_noop_embeds(self) -> None:
        """Multiple concurrent noop embeds should all succeed."""
        import asyncio

        service = NoopEmbedService()
        tasks = [service.embed(f"query {i}") for i in range(50)]
        results = await asyncio.gather(*tasks)
        assert all(r is None for r in results)

    @pytest.mark.asyncio
    async def test_concurrent_fastembed_with_mock(self) -> None:
        """Multiple concurrent fastembed calls with a mock model."""
        import asyncio

        import numpy as np

        service = FastEmbedService()
        mock_model = MagicMock()
        mock_model.embed.return_value = iter([np.array([0.1, 0.2, 0.3])])
        service._model = mock_model

        # Each call resets the mock return value since iter is consumed
        async def single_embed(text: str) -> list[float] | None:
            mock_model.embed.return_value = iter([np.array([0.1, 0.2, 0.3])])
            return await service.embed(text)

        tasks = [single_embed(f"query {i}") for i in range(10)]
        results = await asyncio.gather(*tasks)
        assert all(r is not None for r in results)
        assert all(len(r) == 3 for r in results if r is not None)
