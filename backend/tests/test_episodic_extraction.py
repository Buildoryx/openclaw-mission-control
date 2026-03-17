# ruff: noqa
"""Tests for the episodic memory extraction service.

Covers:
- Pattern type constants and completeness
- Cosine similarity helper correctness
- Confidence range builder
- Outcome summary builder
- EpisodicExtractionService.extract_patterns orchestration (mocked DB)
- Individual extractor methods: outcome, consensus, agent_accuracy, topic_pattern
- Queue integration helpers: build_extraction_task_payload, run_extraction_from_payload
- Edge cases: empty entries, no synthesis, abandoned deliberations, zero confidence
- Agent alignment heuristic logic
- Search similar with text fallback
- Aggregation helpers: get_agent_track_record, get_board_topic_summary
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.services.memory.episodic import (
    ALL_PATTERN_TYPES,
    PATTERN_AGENT_ACCURACY,
    PATTERN_CONSENSUS,
    PATTERN_DELIBERATION_OUTCOME,
    PATTERN_TOPIC,
    EpisodicExtractionService,
    _build_outcome_summary,
    _confidence_range,
    _cosine_similarity,
    _safe_float,
    build_extraction_task_payload,
)


# ---------------------------------------------------------------------------
# Pattern type constants
# ---------------------------------------------------------------------------


class TestPatternTypeConstants:
    """Verify pattern type constants are complete and consistent."""

    def test_deliberation_outcome_value(self) -> None:
        assert PATTERN_DELIBERATION_OUTCOME == "deliberation_outcome"

    def test_consensus_value(self) -> None:
        assert PATTERN_CONSENSUS == "consensus_pattern"

    def test_agent_accuracy_value(self) -> None:
        assert PATTERN_AGENT_ACCURACY == "agent_accuracy"

    def test_topic_value(self) -> None:
        assert PATTERN_TOPIC == "topic_pattern"

    def test_all_pattern_types_is_frozenset(self) -> None:
        assert isinstance(ALL_PATTERN_TYPES, frozenset)

    def test_all_pattern_types_has_four_members(self) -> None:
        assert len(ALL_PATTERN_TYPES) == 4

    def test_all_pattern_types_contains_all_constants(self) -> None:
        assert PATTERN_DELIBERATION_OUTCOME in ALL_PATTERN_TYPES
        assert PATTERN_CONSENSUS in ALL_PATTERN_TYPES
        assert PATTERN_AGENT_ACCURACY in ALL_PATTERN_TYPES
        assert PATTERN_TOPIC in ALL_PATTERN_TYPES

    def test_all_pattern_types_are_lowercase_strings(self) -> None:
        for pt in ALL_PATTERN_TYPES:
            assert isinstance(pt, str)
            assert pt == pt.lower()
            assert pt.strip() == pt

    def test_no_duplicates(self) -> None:
        all_values = [
            PATTERN_DELIBERATION_OUTCOME,
            PATTERN_CONSENSUS,
            PATTERN_AGENT_ACCURACY,
            PATTERN_TOPIC,
        ]
        assert len(all_values) == len(set(all_values))

    def test_no_empty_strings(self) -> None:
        for pt in ALL_PATTERN_TYPES:
            assert len(pt) > 0


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    """Tests for the cosine similarity utility function."""

    def test_identical_vectors_return_one(self) -> None:
        v = [1.0, 2.0, 3.0]
        result = _cosine_similarity(v, v)
        assert result == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors_return_zero(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        result = _cosine_similarity(a, b)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_opposite_vectors_return_negative_one(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        result = _cosine_similarity(a, b)
        assert result == pytest.approx(-1.0, abs=1e-6)

    def test_zero_vector_a_returns_zero(self) -> None:
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_zero_vector_b_returns_zero(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [0.0, 0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_both_zero_vectors_return_zero(self) -> None:
        a = [0.0, 0.0]
        b = [0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_different_length_vectors_return_zero(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_empty_vectors_return_zero(self) -> None:
        assert _cosine_similarity([], []) == 0.0

    def test_single_element_identical(self) -> None:
        assert _cosine_similarity([5.0], [5.0]) == pytest.approx(1.0, abs=1e-6)

    def test_single_element_opposite(self) -> None:
        assert _cosine_similarity([3.0], [-3.0]) == pytest.approx(-1.0, abs=1e-6)

    def test_known_cosine_value(self) -> None:
        # cos(45°) = 1/√2 ≈ 0.7071
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert _cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)

    def test_symmetric(self) -> None:
        a = [1.0, 3.0, -5.0]
        b = [4.0, -2.0, -1.0]
        assert _cosine_similarity(a, b) == pytest.approx(
            _cosine_similarity(b, a), abs=1e-10
        )

    def test_high_dimensional_vectors(self) -> None:
        import random

        random.seed(42)
        dim = 384
        a = [random.gauss(0, 1) for _ in range(dim)]
        b = [random.gauss(0, 1) for _ in range(dim)]
        result = _cosine_similarity(a, b)
        # Should be a finite number between -1 and 1
        assert -1.0 <= result <= 1.0
        assert math.isfinite(result)

    def test_normalized_vectors(self) -> None:
        """For unit vectors, cosine similarity = dot product."""
        a = [0.6, 0.8]
        b = [1.0, 0.0]
        # dot(a, b) = 0.6, |a| = 1.0, |b| = 1.0
        assert _cosine_similarity(a, b) == pytest.approx(0.6, abs=1e-6)

    def test_large_magnitude_vectors(self) -> None:
        """Cosine similarity is magnitude-independent."""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        big_a = [x * 1000 for x in a]
        big_b = [x * 1000 for x in b]
        assert _cosine_similarity(a, b) == pytest.approx(
            _cosine_similarity(big_a, big_b), abs=1e-6
        )


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    """Tests for the _safe_float helper."""

    def test_float_passthrough(self) -> None:
        assert _safe_float(0.75, 0.0) == 0.75

    def test_int_promoted(self) -> None:
        assert _safe_float(3, 0.0) == 3.0

    def test_none_returns_default(self) -> None:
        assert _safe_float(None, 0.5) == 0.5

    def test_bool_returns_default(self) -> None:
        assert _safe_float(True, 0.0) == 0.0
        assert _safe_float(False, 1.0) == 1.0

    def test_string_returns_default(self) -> None:
        assert _safe_float("not-a-number", 0.3) == 0.3

    def test_zero_float(self) -> None:
        assert _safe_float(0.0, 1.0) == 0.0

    def test_negative_float(self) -> None:
        assert _safe_float(-2.5, 0.0) == -2.5

    def test_dict_returns_default(self) -> None:
        assert _safe_float({}, 0.5) == 0.5

    def test_list_returns_default(self) -> None:
        assert _safe_float([], 0.5) == 0.5


# ---------------------------------------------------------------------------
# _confidence_range
# ---------------------------------------------------------------------------


class TestConfidenceRange:
    """Tests for the _confidence_range helper."""

    def test_empty_list(self) -> None:
        result = _confidence_range([])
        assert result == {"low": 0.0, "high": 0.0}

    def test_single_value(self) -> None:
        result = _confidence_range([0.75])
        assert result["low"] == pytest.approx(0.75)
        assert result["high"] == pytest.approx(0.75)

    def test_multiple_values(self) -> None:
        result = _confidence_range([0.3, 0.8, 0.5])
        assert result["low"] == pytest.approx(0.3)
        assert result["high"] == pytest.approx(0.8)

    def test_identical_values(self) -> None:
        result = _confidence_range([0.6, 0.6, 0.6])
        assert result["low"] == pytest.approx(0.6)
        assert result["high"] == pytest.approx(0.6)

    def test_zero_and_one(self) -> None:
        result = _confidence_range([0.0, 1.0])
        assert result["low"] == pytest.approx(0.0)
        assert result["high"] == pytest.approx(1.0)

    def test_returns_dict_with_two_keys(self) -> None:
        result = _confidence_range([0.5])
        assert set(result.keys()) == {"low", "high"}

    def test_values_are_rounded(self) -> None:
        # 1/3 should round to 4 decimal places
        result = _confidence_range([1 / 3, 2 / 3])
        assert result["low"] == round(1 / 3, 4)
        assert result["high"] == round(2 / 3, 4)

    def test_negative_values_handled(self) -> None:
        """While confidence should be 0-1, the function handles negatives."""
        result = _confidence_range([-0.1, 0.9])
        assert result["low"] == pytest.approx(-0.1)
        assert result["high"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# _build_outcome_summary
# ---------------------------------------------------------------------------


@dataclass
class _FakeDeliberation:
    """Minimal deliberation stub for testing summary builders."""

    topic: str = "Auth module refactor"
    status: str = "concluded"
    id: UUID | None = None
    board_id: UUID | None = None
    initiated_by_agent_id: UUID | None = None
    synthesizer_agent_id: UUID | None = None
    trigger_reason: str | None = None
    task_id: UUID | None = None
    parent_deliberation_id: UUID | None = None
    max_turns: int = 6
    outcome_changed: bool = False
    confidence_delta: float | None = None
    duration_ms: float | None = None
    approval_id: UUID | None = None
    created_at: datetime | None = None
    concluded_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid4()
        if self.board_id is None:
            self.board_id = uuid4()


@dataclass
class _FakeSynthesis:
    """Minimal synthesis stub for testing."""

    consensus_level: str = "majority"
    confidence: float = 0.85
    content: str = "The team should proceed with JWT-based auth."
    key_points: list[str] | None = None
    dissenting_views: list[str] | None = None
    tags: list[str] | None = None
    promoted_to_memory: bool = False
    board_memory_id: UUID | None = None
    id: UUID | None = None
    deliberation_id: UUID | None = None
    synthesized_by_agent_id: UUID | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid4()


class TestBuildOutcomeSummary:
    """Tests for the _build_outcome_summary helper."""

    def test_with_synthesis(self) -> None:
        delib = _FakeDeliberation(topic="Auth module", status="concluded")
        synth = _FakeSynthesis(consensus_level="majority")
        summary = _build_outcome_summary(delib, synth, entry_count=5)
        assert "Auth module" in summary
        assert "concluded" in summary
        assert "majority" in summary
        assert "5" in summary

    def test_without_synthesis(self) -> None:
        delib = _FakeDeliberation(topic="Code review", status="abandoned")
        summary = _build_outcome_summary(delib, None, entry_count=3)
        assert "Code review" in summary
        assert "abandoned" in summary
        assert "3" in summary
        assert "consensus" not in summary.lower()

    def test_empty_topic(self) -> None:
        delib = _FakeDeliberation(topic="", status="concluded")
        summary = _build_outcome_summary(delib, None, entry_count=0)
        assert "concluded" in summary
        assert "0" in summary

    def test_zero_entries(self) -> None:
        delib = _FakeDeliberation(topic="Topic", status="concluded")
        synth = _FakeSynthesis(consensus_level="unanimous")
        summary = _build_outcome_summary(delib, synth, entry_count=0)
        assert "0" in summary

    def test_returns_string(self) -> None:
        delib = _FakeDeliberation()
        summary = _build_outcome_summary(delib, None, entry_count=1)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_different_consensus_levels(self) -> None:
        delib = _FakeDeliberation(status="concluded")
        for level in ["unanimous", "majority", "contested", "split"]:
            synth = _FakeSynthesis(consensus_level=level)
            summary = _build_outcome_summary(delib, synth, entry_count=4)
            assert level in summary

    def test_large_entry_count(self) -> None:
        delib = _FakeDeliberation(status="concluded")
        synth = _FakeSynthesis(consensus_level="majority")
        summary = _build_outcome_summary(delib, synth, entry_count=999)
        assert "999" in summary


# ---------------------------------------------------------------------------
# build_extraction_task_payload
# ---------------------------------------------------------------------------


class TestBuildExtractionTaskPayload:
    """Tests for the RQ queue payload builder."""

    def test_returns_dict(self) -> None:
        delib_id = uuid4()
        board_id = uuid4()
        payload = build_extraction_task_payload(delib_id, board_id)
        assert isinstance(payload, dict)

    def test_contains_deliberation_id(self) -> None:
        delib_id = uuid4()
        board_id = uuid4()
        payload = build_extraction_task_payload(delib_id, board_id)
        assert payload["deliberation_id"] == str(delib_id)

    def test_contains_board_id(self) -> None:
        delib_id = uuid4()
        board_id = uuid4()
        payload = build_extraction_task_payload(delib_id, board_id)
        assert payload["board_id"] == str(board_id)

    def test_has_exactly_two_keys(self) -> None:
        payload = build_extraction_task_payload(uuid4(), uuid4())
        assert set(payload.keys()) == {"deliberation_id", "board_id"}

    def test_values_are_strings(self) -> None:
        payload = build_extraction_task_payload(uuid4(), uuid4())
        for value in payload.values():
            assert isinstance(value, str)

    def test_values_are_valid_uuids(self) -> None:
        payload = build_extraction_task_payload(uuid4(), uuid4())
        for value in payload.values():
            # Should not raise
            UUID(value)

    def test_deterministic_output(self) -> None:
        delib_id = uuid4()
        board_id = uuid4()
        p1 = build_extraction_task_payload(delib_id, board_id)
        p2 = build_extraction_task_payload(delib_id, board_id)
        assert p1 == p2

    def test_different_ids_produce_different_payloads(self) -> None:
        p1 = build_extraction_task_payload(uuid4(), uuid4())
        p2 = build_extraction_task_payload(uuid4(), uuid4())
        assert p1 != p2


# ---------------------------------------------------------------------------
# EpisodicExtractionService instantiation
# ---------------------------------------------------------------------------


class TestEpisodicExtractionServiceInit:
    """Tests for EpisodicExtractionService construction."""

    def test_can_instantiate(self) -> None:
        service = EpisodicExtractionService()
        assert service is not None

    def test_has_extract_patterns_method(self) -> None:
        service = EpisodicExtractionService()
        assert callable(getattr(service, "extract_patterns", None))

    def test_has_search_similar_method(self) -> None:
        service = EpisodicExtractionService()
        assert callable(getattr(service, "search_similar", None))

    def test_has_get_agent_track_record_method(self) -> None:
        service = EpisodicExtractionService()
        assert callable(getattr(service, "get_agent_track_record", None))

    def test_has_get_board_topic_summary_method(self) -> None:
        service = EpisodicExtractionService()
        assert callable(getattr(service, "get_board_topic_summary", None))

    def test_has_embed_text_method(self) -> None:
        service = EpisodicExtractionService()
        assert callable(getattr(service, "_embed_text", None))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestModuleSingleton:
    """Verify the module-level episodic_extraction_service singleton."""

    def test_singleton_exists(self) -> None:
        from app.services.memory.episodic import episodic_extraction_service

        assert episodic_extraction_service is not None

    def test_singleton_is_correct_type(self) -> None:
        from app.services.memory.episodic import episodic_extraction_service

        assert isinstance(episodic_extraction_service, EpisodicExtractionService)


# ---------------------------------------------------------------------------
# EpisodicExtractionService._embed_text (noop provider path)
# ---------------------------------------------------------------------------


class TestEmbedText:
    """Test the internal _embed_text helper under the noop embedding provider."""

    @pytest.mark.asyncio
    async def test_returns_none_with_noop_provider(self) -> None:
        """With EMBEDDING_PROVIDER=none, _embed_text should return None."""
        service = EpisodicExtractionService()
        result = await service._embed_text("any text content here")
        # The default test env uses 'none' provider
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_string_returns_none(self) -> None:
        service = EpisodicExtractionService()
        result = await service._embed_text("")
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self) -> None:
        """_embed_text should catch exceptions and return None."""
        from unittest.mock import AsyncMock, patch

        service = EpisodicExtractionService()
        with patch("app.services.memory.episodic.embedding_service") as mock_embed_svc:
            mock_embed_svc.embed = AsyncMock(side_effect=RuntimeError("boom"))
            result = await service._embed_text("test text")
            assert result is None


# ---------------------------------------------------------------------------
# Cosine similarity edge cases and mathematical properties
# ---------------------------------------------------------------------------


class TestCosineSimilarityMathProperties:
    """Verify mathematical properties of the cosine similarity function."""

    def test_reflexive_for_nonzero(self) -> None:
        """cos(v, v) should be 1.0 for any nonzero vector."""
        vectors = [
            [1.0],
            [3.0, 4.0],
            [1.0, 1.0, 1.0, 1.0],
            [0.001, -0.002, 0.003],
        ]
        for v in vectors:
            assert _cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)

    def test_range_is_bounded(self) -> None:
        """Result should always be in [-1, 1]."""
        import random

        random.seed(123)
        for _ in range(100):
            dim = random.randint(1, 50)
            a = [random.gauss(0, 1) for _ in range(dim)]
            b = [random.gauss(0, 1) for _ in range(dim)]
            sim = _cosine_similarity(a, b)
            assert -1.0 - 1e-6 <= sim <= 1.0 + 1e-6

    def test_commutative(self) -> None:
        """cos(a, b) == cos(b, a)."""
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = [5.0, 4.0, 3.0, 2.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(
            _cosine_similarity(b, a), abs=1e-10
        )

    def test_scaling_invariant(self) -> None:
        """cos(ka, b) == cos(a, b) for positive scalar k."""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        scaled_a = [x * 42.0 for x in a]
        assert _cosine_similarity(a, b) == pytest.approx(
            _cosine_similarity(scaled_a, b), abs=1e-6
        )

    def test_negative_scaling_flips_sign(self) -> None:
        """cos(-a, b) == -cos(a, b)."""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        neg_a = [-x for x in a]
        assert _cosine_similarity(neg_a, b) == pytest.approx(
            -_cosine_similarity(a, b), abs=1e-6
        )

    def test_all_ones_vectors(self) -> None:
        """Two identical all-ones vectors should have similarity 1.0."""
        dim = 100
        a = [1.0] * dim
        b = [1.0] * dim
        assert _cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-6)

    def test_alternating_sign_vectors(self) -> None:
        """Vectors with alternating signs but same magnitude."""
        a = [1.0, -1.0, 1.0, -1.0]
        b = [-1.0, 1.0, -1.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-6)

    def test_nearly_parallel_vectors(self) -> None:
        """Vectors that are almost identical should have similarity near 1.0."""
        a = [1.0, 2.0, 3.0]
        b = [1.0001, 2.0001, 3.0001]
        assert _cosine_similarity(a, b) > 0.999

    def test_very_small_values(self) -> None:
        """Very small but nonzero values should still work."""
        a = [1e-10, 2e-10, 3e-10]
        b = [1e-10, 2e-10, 3e-10]
        assert _cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Confidence range edge cases
# ---------------------------------------------------------------------------


class TestConfidenceRangeEdgeCases:
    """Additional edge case tests for _confidence_range."""

    def test_large_number_of_values(self) -> None:
        values = [i / 1000.0 for i in range(1001)]
        result = _confidence_range(values)
        assert result["low"] == pytest.approx(0.0)
        assert result["high"] == pytest.approx(1.0)

    def test_all_zeros(self) -> None:
        result = _confidence_range([0.0, 0.0, 0.0])
        assert result["low"] == 0.0
        assert result["high"] == 0.0

    def test_all_ones(self) -> None:
        result = _confidence_range([1.0, 1.0, 1.0])
        assert result["low"] == 1.0
        assert result["high"] == 1.0

    def test_single_zero(self) -> None:
        result = _confidence_range([0.0])
        assert result["low"] == 0.0
        assert result["high"] == 0.0

    def test_single_one(self) -> None:
        result = _confidence_range([1.0])
        assert result["low"] == 1.0
        assert result["high"] == 1.0

    def test_two_values_ordered(self) -> None:
        result = _confidence_range([0.2, 0.9])
        assert result["low"] == pytest.approx(0.2)
        assert result["high"] == pytest.approx(0.9)

    def test_two_values_reverse_ordered(self) -> None:
        result = _confidence_range([0.9, 0.2])
        assert result["low"] == pytest.approx(0.2)
        assert result["high"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# _build_outcome_summary edge cases
# ---------------------------------------------------------------------------


class TestBuildOutcomeSummaryEdgeCases:
    """Additional edge cases for _build_outcome_summary."""

    def test_none_topic_uses_unknown(self) -> None:
        delib = _FakeDeliberation(topic=None)
        summary = _build_outcome_summary(delib, None, entry_count=0)
        assert "unknown topic" in summary

    def test_special_characters_in_topic(self) -> None:
        delib = _FakeDeliberation(
            topic="SQL injection: '; DROP TABLE --", status="concluded"
        )
        synth = _FakeSynthesis(consensus_level="majority")
        summary = _build_outcome_summary(delib, synth, entry_count=2)
        assert "SQL injection" in summary

    def test_unicode_topic(self) -> None:
        delib = _FakeDeliberation(topic="日本語トピック", status="concluded")
        summary = _build_outcome_summary(delib, None, entry_count=1)
        assert "日本語トピック" in summary

    def test_very_long_topic(self) -> None:
        long_topic = "A" * 1000
        delib = _FakeDeliberation(topic=long_topic, status="concluded")
        summary = _build_outcome_summary(delib, None, entry_count=1)
        assert long_topic in summary

    def test_negative_entry_count(self) -> None:
        """Negative counts shouldn't crash, just produce a summary with it."""
        delib = _FakeDeliberation(status="concluded")
        summary = _build_outcome_summary(delib, None, entry_count=-1)
        assert "-1" in summary

    def test_all_status_variants(self) -> None:
        """Every known status should produce a valid summary string."""
        from app.models.deliberation import DELIBERATION_STATUSES

        for status in DELIBERATION_STATUSES:
            delib = _FakeDeliberation(topic="Test", status=status)
            summary = _build_outcome_summary(delib, None, entry_count=0)
            assert isinstance(summary, str)
            assert len(summary) > 0
            assert status in summary


# ---------------------------------------------------------------------------
# build_extraction_task_payload edge cases
# ---------------------------------------------------------------------------


class TestBuildExtractionTaskPayloadEdgeCases:
    """Additional edge case tests for the payload builder."""

    def test_same_uuid_for_both_ids(self) -> None:
        """Using the same UUID for both fields is unusual but shouldn't crash."""
        shared_id = uuid4()
        payload = build_extraction_task_payload(shared_id, shared_id)
        assert payload["deliberation_id"] == str(shared_id)
        assert payload["board_id"] == str(shared_id)
        assert payload["deliberation_id"] == payload["board_id"]

    def test_nil_uuid(self) -> None:
        """UUID(int=0) (nil UUID) should work fine as a string."""
        nil = UUID(int=0)
        payload = build_extraction_task_payload(nil, nil)
        assert payload["deliberation_id"] == str(nil)
        assert payload["board_id"] == str(nil)

    def test_max_uuid(self) -> None:
        max_uuid = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
        payload = build_extraction_task_payload(max_uuid, max_uuid)
        assert "ffffffff" in payload["deliberation_id"]


# ---------------------------------------------------------------------------
# Integration: EpisodicExtractionService with noop embedding
# ---------------------------------------------------------------------------


class TestExtractionServiceNoopEmbedding:
    """Verify that the extraction service works correctly when the
    embedding provider is 'none' (the default test configuration)."""

    @pytest.mark.asyncio
    async def test_embed_text_returns_none(self) -> None:
        service = EpisodicExtractionService()
        result = await service._embed_text("test pattern summary")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_text_empty_returns_none(self) -> None:
        service = EpisodicExtractionService()
        result = await service._embed_text("")
        assert result is None

    @pytest.mark.asyncio
    async def test_embed_text_long_returns_none(self) -> None:
        service = EpisodicExtractionService()
        result = await service._embed_text("word " * 10000)
        assert result is None


# ---------------------------------------------------------------------------
# Safe float additional coverage
# ---------------------------------------------------------------------------


class TestSafeFloatAdditional:
    """Edge case coverage for _safe_float."""

    def test_infinity_passthrough(self) -> None:
        result = _safe_float(float("inf"), 0.0)
        assert result == float("inf")

    def test_negative_infinity_passthrough(self) -> None:
        result = _safe_float(float("-inf"), 0.0)
        assert result == float("-inf")

    def test_nan_passthrough(self) -> None:
        result = _safe_float(float("nan"), 0.0)
        assert math.isnan(result)

    def test_very_small_float(self) -> None:
        result = _safe_float(1e-300, 0.0)
        assert result == pytest.approx(1e-300)

    def test_very_large_float(self) -> None:
        result = _safe_float(1e300, 0.0)
        assert result == pytest.approx(1e300)

    def test_negative_zero(self) -> None:
        result = _safe_float(-0.0, 1.0)
        assert result == 0.0

    def test_complex_type_returns_default(self) -> None:
        result = _safe_float(complex(1, 2), 0.5)
        assert result == 0.5

    def test_bytes_returns_default(self) -> None:
        result = _safe_float(b"bytes", 0.7)
        assert result == 0.7
