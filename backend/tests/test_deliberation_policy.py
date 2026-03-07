# ruff: noqa
"""Tests for the deliberation policy configuration builder.

Covers:
- Default policy values when board has no deliberation_config
- Policy override from board.deliberation_config JSON
- Safe type coercion for bool, int, float, str-list values
- Edge cases: empty config, None config, partial overrides
- Integration with global settings fallbacks
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

from app.services.deliberation_policy import (
    DEFAULT_ENTRY_TYPES,
    DeliberationPolicy,
    _safe_bool,
    _safe_float,
    _safe_int,
    _safe_str_list,
    get_deliberation_policy,
    resolve_policy,
)


# ---------------------------------------------------------------------------
# Fake Board stub (avoids importing the real model + DB setup)
# ---------------------------------------------------------------------------


@dataclass
class _FakeBoard:
    """Minimal Board-like object for policy resolution tests."""

    id: Any = None
    deliberation_config: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = uuid4()


# ---------------------------------------------------------------------------
# _safe_bool
# ---------------------------------------------------------------------------


class TestSafeBool:
    def test_bool_true(self) -> None:
        assert _safe_bool(True, default=False) is True

    def test_bool_false(self) -> None:
        assert _safe_bool(False, default=True) is False

    def test_string_true_variants(self) -> None:
        for val in ("true", "True", "TRUE", "1", "yes", "Yes"):
            assert _safe_bool(val, default=False) is True

    def test_string_false_variants(self) -> None:
        for val in ("false", "False", "0", "no", ""):
            assert _safe_bool(val, default=True) is False

    def test_int_truthy(self) -> None:
        assert _safe_bool(1, default=False) is True

    def test_int_falsy(self) -> None:
        assert _safe_bool(0, default=True) is False

    def test_float_truthy(self) -> None:
        assert _safe_bool(1.0, default=False) is True

    def test_none_returns_default(self) -> None:
        assert _safe_bool(None, default=True) is True
        assert _safe_bool(None, default=False) is False

    def test_dict_returns_default(self) -> None:
        assert _safe_bool({}, default=True) is True

    def test_list_returns_default(self) -> None:
        assert _safe_bool([], default=False) is False


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_int_passthrough(self) -> None:
        assert _safe_int(42, default=0) == 42

    def test_negative_int(self) -> None:
        assert _safe_int(-5, default=0) == -5

    def test_zero(self) -> None:
        assert _safe_int(0, default=99) == 0

    def test_float_truncates(self) -> None:
        assert _safe_int(3.7, default=0) == 3

    def test_string_numeric(self) -> None:
        assert _safe_int("10", default=0) == 10

    def test_string_non_numeric_returns_default(self) -> None:
        assert _safe_int("abc", default=5) == 5

    def test_none_returns_default(self) -> None:
        assert _safe_int(None, default=7) == 7

    def test_bool_returns_default(self) -> None:
        # bool is a subclass of int in Python, but the function
        # explicitly rejects bool values.
        assert _safe_int(True, default=0) == 0
        assert _safe_int(False, default=99) == 99

    def test_dict_returns_default(self) -> None:
        assert _safe_int({}, default=3) == 3

    def test_empty_string_returns_default(self) -> None:
        assert _safe_int("", default=1) == 1


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_float_passthrough(self) -> None:
        assert _safe_float(0.3, default=0.0) == 0.3

    def test_int_promoted(self) -> None:
        assert _safe_float(2, default=0.0) == 2.0

    def test_string_numeric(self) -> None:
        assert _safe_float("0.75", default=0.0) == 0.75

    def test_string_non_numeric_returns_default(self) -> None:
        assert _safe_float("nope", default=0.5) == 0.5

    def test_none_returns_default(self) -> None:
        assert _safe_float(None, default=1.5) == 1.5

    def test_bool_returns_default(self) -> None:
        assert _safe_float(True, default=0.0) == 0.0

    def test_negative_float(self) -> None:
        assert _safe_float(-3.14, default=0.0) == -3.14


# ---------------------------------------------------------------------------
# _safe_str_list
# ---------------------------------------------------------------------------


class TestSafeStrList:
    def test_list_of_strings(self) -> None:
        assert _safe_str_list(["a", "b", "c"], default=[]) == ["a", "b", "c"]

    def test_filters_non_strings(self) -> None:
        result = _safe_str_list(["a", 123, None, "b", "", True], default=[])
        assert result == ["a", "b"]

    def test_empty_list(self) -> None:
        assert _safe_str_list([], default=["fallback"]) == []

    def test_none_returns_default(self) -> None:
        assert _safe_str_list(None, default=["x"]) == ["x"]

    def test_non_list_returns_default(self) -> None:
        assert _safe_str_list("not-a-list", default=["y"]) == ["y"]

    def test_dict_returns_default(self) -> None:
        assert _safe_str_list({"key": "val"}, default=["z"]) == ["z"]

    def test_whitespace_only_strings_filtered(self) -> None:
        result = _safe_str_list(["ok", "  ", "fine"], default=[])
        assert result == ["ok", "fine"]


# ---------------------------------------------------------------------------
# Default policy (no config)
# ---------------------------------------------------------------------------


class TestDefaultPolicy:
    def test_default_policy_from_none_config(self) -> None:
        board = _FakeBoard(deliberation_config=None)
        policy = get_deliberation_policy(board)

        assert isinstance(policy, DeliberationPolicy)
        assert policy.auto_trigger_on_divergence is True
        assert policy.max_debate_turns == 3
        assert policy.max_discussion_turns == 4
        assert policy.require_synthesis_approval is False
        assert policy.auto_promote_to_memory is True
        assert policy.min_agents_for_deliberation == 2
        assert policy.auto_deliberate_reviews is False

    def test_default_policy_from_empty_config(self) -> None:
        board = _FakeBoard(deliberation_config={})
        policy = get_deliberation_policy(board)

        assert policy.auto_trigger_on_divergence is True
        assert policy.max_debate_turns == 3
        assert policy.max_discussion_turns == 4
        assert policy.max_total_turns == 6
        assert policy.require_synthesis_approval is False
        assert policy.auto_promote_to_memory is True
        assert policy.min_agents_for_deliberation == 2
        assert policy.allowed_entry_types == list(DEFAULT_ENTRY_TYPES)
        assert policy.auto_deliberate_reviews is False

    def test_default_divergence_threshold_from_settings(self) -> None:
        board = _FakeBoard(deliberation_config={})
        policy = get_deliberation_policy(board)
        # Default should match settings.deliberation_divergence_threshold (0.3)
        assert policy.divergence_confidence_gap == pytest.approx(0.3)

    def test_default_max_turns_from_settings(self) -> None:
        board = _FakeBoard(deliberation_config={})
        policy = get_deliberation_policy(board)
        # Default should match settings.deliberation_max_turns (6)
        assert policy.max_total_turns == 6


# ---------------------------------------------------------------------------
# Override via deliberation_config
# ---------------------------------------------------------------------------


class TestPolicyOverrides:
    def test_override_auto_trigger(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "auto_trigger_on_divergence": False,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.auto_trigger_on_divergence is False

    def test_override_divergence_gap(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "divergence_confidence_gap": 0.5,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.divergence_confidence_gap == pytest.approx(0.5)

    def test_override_max_debate_turns(self) -> None:
        board = _FakeBoard(deliberation_config={"max_debate_turns": 10})
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == 10

    def test_override_max_discussion_turns(self) -> None:
        board = _FakeBoard(deliberation_config={"max_discussion_turns": 8})
        policy = get_deliberation_policy(board)
        assert policy.max_discussion_turns == 8

    def test_override_max_total_turns(self) -> None:
        board = _FakeBoard(deliberation_config={"max_total_turns": 20})
        policy = get_deliberation_policy(board)
        assert policy.max_total_turns == 20

    def test_override_require_synthesis_approval(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "require_synthesis_approval": True,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.require_synthesis_approval is True

    def test_override_auto_promote_to_memory(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "auto_promote_to_memory": False,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.auto_promote_to_memory is False

    def test_override_min_agents(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "min_agents_for_deliberation": 5,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.min_agents_for_deliberation == 5

    def test_override_allowed_entry_types(self) -> None:
        custom_types = ["thesis", "evidence", "vote"]
        board = _FakeBoard(
            deliberation_config={
                "allowed_entry_types": custom_types,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.allowed_entry_types == custom_types

    def test_override_auto_deliberate_reviews(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "auto_deliberate_reviews": True,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.auto_deliberate_reviews is True

    def test_partial_override_preserves_defaults(self) -> None:
        """Only overridden keys should change; the rest stay at defaults."""
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": 7,
                "require_synthesis_approval": True,
            }
        )
        policy = get_deliberation_policy(board)
        # Overridden
        assert policy.max_debate_turns == 7
        assert policy.require_synthesis_approval is True
        # Defaults
        assert policy.auto_trigger_on_divergence is True
        assert policy.max_discussion_turns == 4
        assert policy.auto_promote_to_memory is True
        assert policy.min_agents_for_deliberation == 2


# ---------------------------------------------------------------------------
# Type coercion in config values
# ---------------------------------------------------------------------------


class TestPolicyTypeCoercion:
    def test_string_bool_coercion(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "auto_trigger_on_divergence": "false",
                "require_synthesis_approval": "true",
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.auto_trigger_on_divergence is False
        assert policy.require_synthesis_approval is True

    def test_string_int_coercion(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": "5",
                "max_discussion_turns": "3",
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == 5
        assert policy.max_discussion_turns == 3

    def test_string_float_coercion(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "divergence_confidence_gap": "0.45",
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.divergence_confidence_gap == pytest.approx(0.45)

    def test_invalid_type_falls_back_to_default(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": "not-a-number",
                "divergence_confidence_gap": "invalid",
                "auto_trigger_on_divergence": {},
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == 3  # default
        assert policy.divergence_confidence_gap == pytest.approx(
            0.3
        )  # settings default
        assert policy.auto_trigger_on_divergence is True  # default

    def test_none_values_in_config_use_defaults(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": None,
                "divergence_confidence_gap": None,
                "auto_trigger_on_divergence": None,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == 3
        assert policy.auto_trigger_on_divergence is True

    def test_int_for_bool_field_coerces(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "auto_trigger_on_divergence": 1,
                "require_synthesis_approval": 0,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.auto_trigger_on_divergence is True
        assert policy.require_synthesis_approval is False


# ---------------------------------------------------------------------------
# resolve_policy alias
# ---------------------------------------------------------------------------


class TestResolvePolicyAlias:
    def test_resolve_policy_is_same_function(self) -> None:
        assert resolve_policy is get_deliberation_policy

    def test_resolve_policy_returns_policy(self) -> None:
        board = _FakeBoard(deliberation_config=None)
        policy = resolve_policy(board)
        assert isinstance(policy, DeliberationPolicy)


# ---------------------------------------------------------------------------
# DeliberationPolicy defaults (dataclass direct instantiation)
# ---------------------------------------------------------------------------


class TestDeliberationPolicyDefaults:
    def test_dataclass_defaults(self) -> None:
        policy = DeliberationPolicy()
        assert policy.auto_trigger_on_divergence is True
        assert policy.divergence_confidence_gap == 0.3
        assert policy.max_debate_turns == 3
        assert policy.max_discussion_turns == 4
        assert policy.max_total_turns == 6
        assert policy.require_synthesis_approval is False
        assert policy.auto_promote_to_memory is True
        assert policy.min_agents_for_deliberation == 2
        assert policy.allowed_entry_types == list(DEFAULT_ENTRY_TYPES)
        assert policy.auto_deliberate_reviews is False

    def test_custom_instantiation(self) -> None:
        policy = DeliberationPolicy(
            auto_trigger_on_divergence=False,
            divergence_confidence_gap=0.6,
            max_debate_turns=10,
            max_discussion_turns=12,
            max_total_turns=30,
            require_synthesis_approval=True,
            auto_promote_to_memory=False,
            min_agents_for_deliberation=4,
            allowed_entry_types=["thesis", "vote"],
            auto_deliberate_reviews=True,
        )
        assert policy.auto_trigger_on_divergence is False
        assert policy.divergence_confidence_gap == 0.6
        assert policy.max_debate_turns == 10
        assert policy.max_discussion_turns == 12
        assert policy.max_total_turns == 30
        assert policy.require_synthesis_approval is True
        assert policy.auto_promote_to_memory is False
        assert policy.min_agents_for_deliberation == 4
        assert policy.allowed_entry_types == ["thesis", "vote"]
        assert policy.auto_deliberate_reviews is True


# ---------------------------------------------------------------------------
# DEFAULT_ENTRY_TYPES constant
# ---------------------------------------------------------------------------


class TestDefaultEntryTypes:
    def test_contains_expected_types(self) -> None:
        expected = {
            "thesis",
            "antithesis",
            "evidence",
            "question",
            "vote",
            "rebuttal",
            "synthesis",
        }
        assert set(DEFAULT_ENTRY_TYPES) == expected

    def test_is_list(self) -> None:
        assert isinstance(DEFAULT_ENTRY_TYPES, list)

    def test_no_duplicates(self) -> None:
        assert len(DEFAULT_ENTRY_TYPES) == len(set(DEFAULT_ENTRY_TYPES))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_unknown_config_keys_are_ignored(self) -> None:
        """Extra keys in deliberation_config should not cause errors."""
        board = _FakeBoard(
            deliberation_config={
                "unknown_key": "some_value",
                "another_random_key": 42,
                "max_debate_turns": 5,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == 5
        # Other fields at default
        assert policy.auto_trigger_on_divergence is True

    def test_deeply_nested_config_values_ignored(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": {"nested": "object"},
            }
        )
        policy = get_deliberation_policy(board)
        # Should fall back to default since dict is not valid for int
        assert policy.max_debate_turns == 3

    def test_allowed_entry_types_with_mixed_types(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "allowed_entry_types": ["thesis", 123, None, "vote", True, ""],
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.allowed_entry_types == ["thesis", "vote"]

    def test_all_overrides_at_once(self) -> None:
        """Full config override with all keys set."""
        board = _FakeBoard(
            deliberation_config={
                "auto_trigger_on_divergence": False,
                "divergence_confidence_gap": 0.8,
                "max_debate_turns": 15,
                "max_discussion_turns": 20,
                "max_total_turns": 50,
                "require_synthesis_approval": True,
                "auto_promote_to_memory": False,
                "min_agents_for_deliberation": 10,
                "allowed_entry_types": ["thesis"],
                "auto_deliberate_reviews": True,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.auto_trigger_on_divergence is False
        assert policy.divergence_confidence_gap == pytest.approx(0.8)
        assert policy.max_debate_turns == 15
        assert policy.max_discussion_turns == 20
        assert policy.max_total_turns == 50
        assert policy.require_synthesis_approval is True
        assert policy.auto_promote_to_memory is False
        assert policy.min_agents_for_deliberation == 10
        assert policy.allowed_entry_types == ["thesis"]
        assert policy.auto_deliberate_reviews is True

    def test_zero_turns_allowed(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": 0,
                "max_discussion_turns": 0,
                "max_total_turns": 0,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == 0
        assert policy.max_discussion_turns == 0
        assert policy.max_total_turns == 0

    def test_negative_turns_passthrough(self) -> None:
        """Negative values are accepted by _safe_int — validation is elsewhere."""
        board = _FakeBoard(
            deliberation_config={
                "max_debate_turns": -1,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.max_debate_turns == -1

    def test_float_divergence_gap_zero(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "divergence_confidence_gap": 0.0,
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.divergence_confidence_gap == 0.0

    def test_empty_allowed_entry_types(self) -> None:
        board = _FakeBoard(
            deliberation_config={
                "allowed_entry_types": [],
            }
        )
        policy = get_deliberation_policy(board)
        assert policy.allowed_entry_types == []
