# ruff: noqa
"""Tests for deliberation model constants, state machine, and entry type validation.

Covers:
- DELIBERATION_STATUSES constant completeness and type
- TERMINAL_STATUSES subset relationship and values
- PHASE_ORDER progression correctness
- ENTRY_TYPES constant completeness
- CONSENSUS_LEVELS constant completeness
- Phase order vs status set consistency
- State machine transition rules (valid progressions, terminal blocking)
- Deliberation model field defaults
- DeliberationEntry model field defaults
- DeliberationSynthesis model field defaults
- Cross-model foreign key field naming conventions
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest

from app.models.deliberation import (
    CONSENSUS_LEVELS,
    DELIBERATION_STATUSES,
    ENTRY_TYPES,
    PHASE_ORDER,
    TERMINAL_STATUSES,
    Deliberation,
    DeliberationEntry,
    DeliberationSynthesis,
)


# ---------------------------------------------------------------------------
# DELIBERATION_STATUSES
# ---------------------------------------------------------------------------


class TestDeliberationStatuses:
    """Tests for the DELIBERATION_STATUSES constant."""

    def test_is_frozenset(self) -> None:
        assert isinstance(DELIBERATION_STATUSES, frozenset)

    def test_contains_all_expected_statuses(self) -> None:
        expected = {
            "created",
            "debating",
            "discussing",
            "verifying",
            "synthesizing",
            "concluded",
            "abandoned",
        }
        assert DELIBERATION_STATUSES == expected

    def test_has_seven_statuses(self) -> None:
        assert len(DELIBERATION_STATUSES) == 7

    def test_all_statuses_are_lowercase_strings(self) -> None:
        for status in DELIBERATION_STATUSES:
            assert isinstance(status, str)
            assert status == status.lower()
            assert status.strip() == status

    def test_no_empty_strings(self) -> None:
        for status in DELIBERATION_STATUSES:
            assert len(status) > 0

    def test_created_is_a_status(self) -> None:
        assert "created" in DELIBERATION_STATUSES

    def test_concluded_is_a_status(self) -> None:
        assert "concluded" in DELIBERATION_STATUSES

    def test_abandoned_is_a_status(self) -> None:
        assert "abandoned" in DELIBERATION_STATUSES


# ---------------------------------------------------------------------------
# TERMINAL_STATUSES
# ---------------------------------------------------------------------------


class TestTerminalStatuses:
    """Tests for the TERMINAL_STATUSES constant."""

    def test_is_frozenset(self) -> None:
        assert isinstance(TERMINAL_STATUSES, frozenset)

    def test_is_subset_of_deliberation_statuses(self) -> None:
        assert TERMINAL_STATUSES.issubset(DELIBERATION_STATUSES)

    def test_contains_concluded(self) -> None:
        assert "concluded" in TERMINAL_STATUSES

    def test_contains_abandoned(self) -> None:
        assert "abandoned" in TERMINAL_STATUSES

    def test_has_exactly_two_terminal_statuses(self) -> None:
        assert len(TERMINAL_STATUSES) == 2

    def test_created_is_not_terminal(self) -> None:
        assert "created" not in TERMINAL_STATUSES

    def test_debating_is_not_terminal(self) -> None:
        assert "debating" not in TERMINAL_STATUSES

    def test_discussing_is_not_terminal(self) -> None:
        assert "discussing" not in TERMINAL_STATUSES

    def test_verifying_is_not_terminal(self) -> None:
        assert "verifying" not in TERMINAL_STATUSES

    def test_synthesizing_is_not_terminal(self) -> None:
        assert "synthesizing" not in TERMINAL_STATUSES

    def test_non_terminal_statuses_are_complement(self) -> None:
        non_terminal = DELIBERATION_STATUSES - TERMINAL_STATUSES
        assert non_terminal == {
            "created",
            "debating",
            "discussing",
            "verifying",
            "synthesizing",
        }


# ---------------------------------------------------------------------------
# PHASE_ORDER
# ---------------------------------------------------------------------------


class TestPhaseOrder:
    """Tests for the PHASE_ORDER progression list."""

    def test_is_list(self) -> None:
        assert isinstance(PHASE_ORDER, list)

    def test_has_six_phases(self) -> None:
        assert len(PHASE_ORDER) == 6

    def test_starts_with_created(self) -> None:
        assert PHASE_ORDER[0] == "created"

    def test_ends_with_concluded(self) -> None:
        assert PHASE_ORDER[-1] == "concluded"

    def test_exact_order(self) -> None:
        expected = [
            "created",
            "debating",
            "discussing",
            "verifying",
            "synthesizing",
            "concluded",
        ]
        assert PHASE_ORDER == expected

    def test_debating_follows_created(self) -> None:
        idx_created = PHASE_ORDER.index("created")
        idx_debating = PHASE_ORDER.index("debating")
        assert idx_debating == idx_created + 1

    def test_discussing_follows_debating(self) -> None:
        idx_debating = PHASE_ORDER.index("debating")
        idx_discussing = PHASE_ORDER.index("discussing")
        assert idx_discussing == idx_debating + 1

    def test_verifying_follows_discussing(self) -> None:
        idx_discussing = PHASE_ORDER.index("discussing")
        idx_verifying = PHASE_ORDER.index("verifying")
        assert idx_verifying == idx_discussing + 1

    def test_synthesizing_follows_verifying(self) -> None:
        idx_verifying = PHASE_ORDER.index("verifying")
        idx_synthesizing = PHASE_ORDER.index("synthesizing")
        assert idx_synthesizing == idx_verifying + 1

    def test_concluded_follows_synthesizing(self) -> None:
        idx_synthesizing = PHASE_ORDER.index("synthesizing")
        idx_concluded = PHASE_ORDER.index("concluded")
        assert idx_concluded == idx_synthesizing + 1

    def test_no_duplicate_phases(self) -> None:
        assert len(PHASE_ORDER) == len(set(PHASE_ORDER))

    def test_abandoned_is_not_in_phase_order(self) -> None:
        """Abandoned is a terminal state reachable from any phase, not a linear phase."""
        assert "abandoned" not in PHASE_ORDER

    def test_all_phase_order_entries_are_valid_statuses(self) -> None:
        for phase in PHASE_ORDER:
            assert phase in DELIBERATION_STATUSES

    def test_phase_order_covers_all_non_abandoned_statuses(self) -> None:
        """PHASE_ORDER should include every status except 'abandoned'."""
        expected = DELIBERATION_STATUSES - {"abandoned"}
        assert set(PHASE_ORDER) == expected

    def test_next_phase_lookup(self) -> None:
        """Each phase (except concluded) has a defined successor."""
        for i in range(len(PHASE_ORDER) - 1):
            current = PHASE_ORDER[i]
            next_phase = PHASE_ORDER[i + 1]
            assert next_phase is not None
            assert next_phase != current

    def test_concluded_has_no_successor(self) -> None:
        """The last phase should be concluded, with no further phase."""
        assert PHASE_ORDER.index("concluded") == len(PHASE_ORDER) - 1


# ---------------------------------------------------------------------------
# ENTRY_TYPES
# ---------------------------------------------------------------------------


class TestEntryTypes:
    """Tests for the ENTRY_TYPES constant."""

    def test_is_frozenset(self) -> None:
        assert isinstance(ENTRY_TYPES, frozenset)

    def test_contains_all_expected_types(self) -> None:
        expected = {
            "thesis",
            "antithesis",
            "evidence",
            "question",
            "vote",
            "rebuttal",
            "synthesis",
        }
        assert ENTRY_TYPES == expected

    def test_has_seven_types(self) -> None:
        assert len(ENTRY_TYPES) == 7

    def test_all_types_are_lowercase_strings(self) -> None:
        for entry_type in ENTRY_TYPES:
            assert isinstance(entry_type, str)
            assert entry_type == entry_type.lower()
            assert entry_type.strip() == entry_type

    def test_no_empty_strings(self) -> None:
        for entry_type in ENTRY_TYPES:
            assert len(entry_type) > 0

    def test_thesis_present(self) -> None:
        assert "thesis" in ENTRY_TYPES

    def test_antithesis_present(self) -> None:
        assert "antithesis" in ENTRY_TYPES

    def test_evidence_present(self) -> None:
        assert "evidence" in ENTRY_TYPES

    def test_question_present(self) -> None:
        assert "question" in ENTRY_TYPES

    def test_vote_present(self) -> None:
        assert "vote" in ENTRY_TYPES

    def test_rebuttal_present(self) -> None:
        assert "rebuttal" in ENTRY_TYPES

    def test_synthesis_present(self) -> None:
        assert "synthesis" in ENTRY_TYPES


# ---------------------------------------------------------------------------
# CONSENSUS_LEVELS
# ---------------------------------------------------------------------------


class TestConsensusLevels:
    """Tests for the CONSENSUS_LEVELS constant."""

    def test_is_frozenset(self) -> None:
        assert isinstance(CONSENSUS_LEVELS, frozenset)

    def test_contains_all_expected_levels(self) -> None:
        expected = {"unanimous", "majority", "contested", "split"}
        assert CONSENSUS_LEVELS == expected

    def test_has_four_levels(self) -> None:
        assert len(CONSENSUS_LEVELS) == 4

    def test_all_levels_are_lowercase_strings(self) -> None:
        for level in CONSENSUS_LEVELS:
            assert isinstance(level, str)
            assert level == level.lower()
            assert level.strip() == level

    def test_no_empty_strings(self) -> None:
        for level in CONSENSUS_LEVELS:
            assert len(level) > 0

    def test_unanimous_present(self) -> None:
        assert "unanimous" in CONSENSUS_LEVELS

    def test_majority_present(self) -> None:
        assert "majority" in CONSENSUS_LEVELS

    def test_contested_present(self) -> None:
        assert "contested" in CONSENSUS_LEVELS

    def test_split_present(self) -> None:
        assert "split" in CONSENSUS_LEVELS


# ---------------------------------------------------------------------------
# State machine logic
# ---------------------------------------------------------------------------


class TestStateMachineTransitions:
    """Tests for valid and invalid state transitions derived from PHASE_ORDER."""

    def _next_phase(self, current: str) -> str | None:
        """Return the next valid phase after *current*, or None if terminal."""
        try:
            idx = PHASE_ORDER.index(current)
        except ValueError:
            return None
        if idx >= len(PHASE_ORDER) - 1:
            return None
        return PHASE_ORDER[idx + 1]

    def test_created_advances_to_debating(self) -> None:
        assert self._next_phase("created") == "debating"

    def test_debating_advances_to_discussing(self) -> None:
        assert self._next_phase("debating") == "discussing"

    def test_discussing_advances_to_verifying(self) -> None:
        assert self._next_phase("discussing") == "verifying"

    def test_verifying_advances_to_synthesizing(self) -> None:
        assert self._next_phase("verifying") == "synthesizing"

    def test_synthesizing_advances_to_concluded(self) -> None:
        assert self._next_phase("synthesizing") == "concluded"

    def test_concluded_has_no_next_phase(self) -> None:
        assert self._next_phase("concluded") is None

    def test_abandoned_has_no_next_phase(self) -> None:
        assert self._next_phase("abandoned") is None

    def test_invalid_status_has_no_next_phase(self) -> None:
        assert self._next_phase("nonexistent") is None

    def test_terminal_statuses_cannot_advance(self) -> None:
        for terminal in TERMINAL_STATUSES:
            next_phase = self._next_phase(terminal)
            # concluded is last in PHASE_ORDER, abandoned is not in it at all
            assert next_phase is None

    def test_non_terminal_statuses_can_advance(self) -> None:
        non_terminal = DELIBERATION_STATUSES - TERMINAL_STATUSES
        for status in non_terminal:
            # Every non-terminal status is either in PHASE_ORDER (and can advance)
            # or is abandoned (handled separately).
            if status in PHASE_ORDER:
                idx = PHASE_ORDER.index(status)
                if idx < len(PHASE_ORDER) - 1:
                    assert self._next_phase(status) is not None

    def test_full_forward_progression(self) -> None:
        """Simulate a full forward traversal through the phase order."""
        current = PHASE_ORDER[0]
        visited = [current]
        while True:
            next_status = self._next_phase(current)
            if next_status is None:
                break
            visited.append(next_status)
            current = next_status
        assert visited == PHASE_ORDER

    def test_abandonment_allowed_from_any_non_terminal(self) -> None:
        """Abandoning should be conceptually valid from any non-terminal state."""
        non_terminal = DELIBERATION_STATUSES - TERMINAL_STATUSES
        for status in non_terminal:
            # The model/service enforces this; the constant just defines terminal set
            assert status not in TERMINAL_STATUSES

    def test_skip_phase_is_not_valid(self) -> None:
        """Jumping two phases ahead (e.g. created → discussing) is not a valid
        single-step transition per the PHASE_ORDER definition."""
        for i in range(len(PHASE_ORDER) - 2):
            current = PHASE_ORDER[i]
            skipped = PHASE_ORDER[i + 2]
            next_valid = self._next_phase(current)
            assert next_valid != skipped


# ---------------------------------------------------------------------------
# Deliberation model defaults
# ---------------------------------------------------------------------------


class TestDeliberationModelDefaults:
    """Tests for Deliberation ORM model default field values."""

    def test_status_default(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test topic",
        )
        assert d.status == "created"

    def test_max_turns_default(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test",
        )
        assert d.max_turns == 6

    def test_outcome_changed_default(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test",
        )
        assert d.outcome_changed is False

    def test_optional_fields_default_to_none(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test",
        )
        assert d.initiated_by_agent_id is None
        assert d.synthesizer_agent_id is None
        assert d.trigger_reason is None
        assert d.task_id is None
        assert d.parent_deliberation_id is None
        assert d.confidence_delta is None
        assert d.duration_ms is None
        assert d.approval_id is None
        assert d.concluded_at is None

    def test_id_is_generated(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test",
        )
        assert isinstance(d.id, UUID)

    def test_created_at_is_set(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test",
        )
        assert isinstance(d.created_at, datetime)

    def test_updated_at_is_set(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Test",
        )
        assert isinstance(d.updated_at, datetime)

    def test_tablename(self) -> None:
        assert Deliberation.__tablename__ == "deliberations"

    def test_topic_is_required(self) -> None:
        d = Deliberation(
            board_id=uuid4(),
            topic="Required topic text",
        )
        assert d.topic == "Required topic text"

    def test_board_id_is_required(self) -> None:
        bid = uuid4()
        d = Deliberation(
            board_id=bid,
            topic="Test",
        )
        assert d.board_id == bid


# ---------------------------------------------------------------------------
# DeliberationEntry model defaults
# ---------------------------------------------------------------------------


class TestDeliberationEntryModelDefaults:
    """Tests for DeliberationEntry ORM model default field values."""

    def test_sequence_default(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="Test argument",
        )
        assert e.sequence == 0

    def test_optional_fields_default_to_none(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="Test",
        )
        assert e.agent_id is None
        assert e.user_id is None
        assert e.position is None
        assert e.confidence is None
        assert e.parent_entry_id is None
        assert e.references is None
        assert e.metadata_ is None

    def test_id_is_generated(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="Test",
        )
        assert isinstance(e.id, UUID)

    def test_created_at_is_set(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="Test",
        )
        assert isinstance(e.created_at, datetime)

    def test_tablename(self) -> None:
        assert DeliberationEntry.__tablename__ == "deliberation_entries"

    def test_content_is_required(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="This is required",
        )
        assert e.content == "This is required"

    def test_phase_is_stored(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="discussion",
            entry_type="evidence",
            content="Test",
        )
        assert e.phase == "discussion"

    def test_entry_type_is_stored(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="antithesis",
            content="Test",
        )
        assert e.entry_type == "antithesis"

    def test_confidence_accepts_valid_range(self) -> None:
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="Test",
            confidence=0.85,
        )
        assert e.confidence == pytest.approx(0.85)

    def test_references_accepts_list(self) -> None:
        refs = ["mem:abc-123", "task:def-456", "https://example.com"]
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="evidence",
            content="Test",
            references=refs,
        )
        assert e.references == refs

    def test_metadata_accepts_dict(self) -> None:
        meta = {"rubric_clarity": 4, "rubric_depth": 3}
        e = DeliberationEntry(
            deliberation_id=uuid4(),
            phase="debate",
            entry_type="thesis",
            content="Test",
            metadata_=meta,
        )
        assert e.metadata_ == meta


# ---------------------------------------------------------------------------
# DeliberationSynthesis model defaults
# ---------------------------------------------------------------------------


class TestDeliberationSynthesisModelDefaults:
    """Tests for DeliberationSynthesis ORM model default field values."""

    def test_promoted_to_memory_default(self) -> None:
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis text",
            consensus_level="majority",
            confidence=0.8,
        )
        assert s.promoted_to_memory is False

    def test_optional_fields_default_to_none(self) -> None:
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="unanimous",
            confidence=0.95,
        )
        assert s.synthesized_by_agent_id is None
        assert s.key_points is None
        assert s.dissenting_views is None
        assert s.tags is None
        assert s.board_memory_id is None

    def test_id_is_generated(self) -> None:
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="split",
            confidence=0.4,
        )
        assert isinstance(s.id, UUID)

    def test_created_at_is_set(self) -> None:
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="contested",
            confidence=0.6,
        )
        assert isinstance(s.created_at, datetime)

    def test_tablename(self) -> None:
        assert DeliberationSynthesis.__tablename__ == "deliberation_syntheses"

    def test_content_is_required(self) -> None:
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Required synthesis content",
            consensus_level="majority",
            confidence=0.7,
        )
        assert s.content == "Required synthesis content"

    def test_consensus_level_is_stored(self) -> None:
        for level in CONSENSUS_LEVELS:
            s = DeliberationSynthesis(
                deliberation_id=uuid4(),
                content="Synthesis",
                consensus_level=level,
                confidence=0.5,
            )
            assert s.consensus_level == level

    def test_confidence_is_stored(self) -> None:
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="majority",
            confidence=0.92,
        )
        assert s.confidence == pytest.approx(0.92)

    def test_key_points_accepts_list(self) -> None:
        points = ["Point 1", "Point 2", "Point 3"]
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="majority",
            confidence=0.8,
            key_points=points,
        )
        assert s.key_points == points

    def test_dissenting_views_accepts_list(self) -> None:
        views = ["Agent A disagrees on timing", "Agent B prefers alternative"]
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="contested",
            confidence=0.55,
            dissenting_views=views,
        )
        assert s.dissenting_views == views

    def test_tags_accepts_list(self) -> None:
        tags = ["architecture", "refactoring", "high-priority"]
        s = DeliberationSynthesis(
            deliberation_id=uuid4(),
            content="Synthesis",
            consensus_level="unanimous",
            confidence=0.95,
            tags=tags,
        )
        assert s.tags == tags

    def test_deliberation_id_is_required(self) -> None:
        did = uuid4()
        s = DeliberationSynthesis(
            deliberation_id=did,
            content="Synthesis",
            consensus_level="majority",
            confidence=0.7,
        )
        assert s.deliberation_id == did


# ---------------------------------------------------------------------------
# Cross-constant consistency
# ---------------------------------------------------------------------------


class TestCrossConstantConsistency:
    """Verify that constants are mutually consistent."""

    def test_phase_order_subset_of_statuses(self) -> None:
        """Every phase in PHASE_ORDER must be a valid deliberation status."""
        for phase in PHASE_ORDER:
            assert phase in DELIBERATION_STATUSES

    def test_terminal_statuses_subset_of_statuses(self) -> None:
        assert TERMINAL_STATUSES <= DELIBERATION_STATUSES

    def test_concluded_is_terminal_and_last_phase(self) -> None:
        assert "concluded" in TERMINAL_STATUSES
        assert PHASE_ORDER[-1] == "concluded"

    def test_abandoned_is_terminal_but_not_in_phase_order(self) -> None:
        assert "abandoned" in TERMINAL_STATUSES
        assert "abandoned" not in PHASE_ORDER

    def test_non_terminal_non_abandoned_all_in_phase_order(self) -> None:
        """Every status that is neither terminal nor 'abandoned' must
        appear in the linear phase progression."""
        non_terminal = DELIBERATION_STATUSES - TERMINAL_STATUSES
        for status in non_terminal:
            assert status in PHASE_ORDER

    def test_phase_order_plus_abandoned_equals_all_statuses(self) -> None:
        """PHASE_ORDER ∪ {abandoned} should cover all deliberation statuses."""
        combined = set(PHASE_ORDER) | {"abandoned"}
        assert combined == DELIBERATION_STATUSES

    def test_entry_types_includes_synthesis(self) -> None:
        """The 'synthesis' entry type should exist for the synthesizing phase."""
        assert "synthesis" in ENTRY_TYPES

    def test_entry_types_all_unique(self) -> None:
        assert len(ENTRY_TYPES) == len(set(ENTRY_TYPES))

    def test_consensus_levels_all_unique(self) -> None:
        assert len(CONSENSUS_LEVELS) == len(set(CONSENSUS_LEVELS))


# ---------------------------------------------------------------------------
# Table name conventions
# ---------------------------------------------------------------------------


class TestTableNameConventions:
    """Verify ORM table names follow the project's snake_case convention."""

    def test_deliberation_table_name(self) -> None:
        assert Deliberation.__tablename__ == "deliberations"

    def test_deliberation_entry_table_name(self) -> None:
        assert DeliberationEntry.__tablename__ == "deliberation_entries"

    def test_deliberation_synthesis_table_name(self) -> None:
        assert DeliberationSynthesis.__tablename__ == "deliberation_syntheses"

    def test_all_table_names_are_snake_case(self) -> None:
        for model in (Deliberation, DeliberationEntry, DeliberationSynthesis):
            name = model.__tablename__
            assert name == name.lower()
            assert " " not in name
            assert "-" not in name


# ---------------------------------------------------------------------------
# Model index definitions
# ---------------------------------------------------------------------------


class TestModelIndexes:
    """Verify that expected index definitions are present via __table_args__."""

    def test_deliberation_has_table_args(self) -> None:
        assert hasattr(Deliberation, "__table_args__")
        args = Deliberation.__table_args__
        assert isinstance(args, tuple)
        assert len(args) >= 2

    def test_deliberation_entry_has_table_args(self) -> None:
        assert hasattr(DeliberationEntry, "__table_args__")
        args = DeliberationEntry.__table_args__
        assert isinstance(args, tuple)
        assert len(args) >= 3

    def test_deliberation_synthesis_has_table_args(self) -> None:
        assert hasattr(DeliberationSynthesis, "__table_args__")
        args = DeliberationSynthesis.__table_args__
        assert isinstance(args, tuple)
        assert len(args) >= 1

    def test_deliberation_index_names(self) -> None:
        """Verify key index names appear in the table args."""
        args = Deliberation.__table_args__
        index_names = {idx.name for idx in args if hasattr(idx, "name")}
        assert "ix_deliberations_board_status" in index_names
        assert "ix_deliberations_board_created" in index_names

    def test_entry_index_names(self) -> None:
        args = DeliberationEntry.__table_args__
        index_names = {idx.name for idx in args if hasattr(idx, "name")}
        assert "ix_delib_entries_delib_seq" in index_names
        assert "ix_delib_entries_delib_phase" in index_names
        assert "ix_delib_entries_agent_created" in index_names

    def test_synthesis_index_names(self) -> None:
        args = DeliberationSynthesis.__table_args__
        index_names = {idx.name for idx in args if hasattr(idx, "name")}
        assert "ix_delib_synth_promoted" in index_names
