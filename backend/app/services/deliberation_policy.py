"""Per-board deliberation policy configuration.

The deliberation policy controls how deliberations behave on a given board:
auto-trigger rules, phase limits, approval requirements, and memory promotion.

Policy values are read from the ``Board.deliberation_config`` JSON column with
fallback to global defaults from ``settings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.boards import Board


# Default entry types allowed in deliberations.
DEFAULT_ENTRY_TYPES: list[str] = [
    "thesis",
    "antithesis",
    "evidence",
    "question",
    "vote",
    "rebuttal",
    "synthesis",
]


@dataclass
class DeliberationPolicy:
    """Per-board deliberation configuration.

    Instances are built from a board's ``deliberation_config`` JSON column.
    Missing keys fall back to sensible defaults derived from global settings.
    """

    # Whether divergent agent positions automatically trigger a deliberation.
    auto_trigger_on_divergence: bool = True

    # Confidence gap between agent positions that qualifies as "divergent".
    divergence_confidence_gap: float = 0.3

    # Maximum entries in the debate phase before auto-advancing.
    max_debate_turns: int = 3

    # Maximum entries in the discussion phase before auto-advancing.
    max_discussion_turns: int = 4

    # Hard cap on total entries across all phases.
    max_total_turns: int = 6

    # Whether synthesis requires an approval before promotion.
    require_synthesis_approval: bool = False

    # Whether concluded syntheses are automatically promoted to board memory.
    auto_promote_to_memory: bool = True

    # Minimum participating agents required for a valid deliberation.
    min_agents_for_deliberation: int = 2

    # Allowed entry type slugs.
    allowed_entry_types: list[str] = field(
        default_factory=lambda: list(DEFAULT_ENTRY_TYPES)
    )

    # Whether task review transitions automatically start a deliberation.
    auto_deliberate_reviews: bool = False


def _safe_bool(value: Any, default: bool) -> bool:
    """Coerce a JSON value to bool, returning *default* on failure."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _safe_int(value: Any, default: int) -> int:
    """Coerce a JSON value to int, returning *default* on failure."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, (float, str)):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    return default


def _safe_float(value: Any, default: float) -> float:
    """Coerce a JSON value to float, returning *default* on failure."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    return default


def _safe_str_list(value: Any, default: list[str]) -> list[str]:
    """Coerce a JSON value to a list of strings, returning *default* on failure."""
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item.strip()]
    return default


def get_deliberation_policy(board: Board) -> DeliberationPolicy:
    """Build a :class:`DeliberationPolicy` from a board's config.

    Values present in ``board.deliberation_config`` override the defaults.
    Global settings are used for divergence threshold and max turns when the
    board config is absent.
    """
    cfg: dict[str, Any] = (
        board.deliberation_config or {} if board.deliberation_config else {}
    )

    return DeliberationPolicy(
        auto_trigger_on_divergence=_safe_bool(
            cfg.get("auto_trigger_on_divergence"),
            default=True,
        ),
        divergence_confidence_gap=_safe_float(
            cfg.get("divergence_confidence_gap"),
            default=settings.deliberation_divergence_threshold,
        ),
        max_debate_turns=_safe_int(
            cfg.get("max_debate_turns"),
            default=3,
        ),
        max_discussion_turns=_safe_int(
            cfg.get("max_discussion_turns"),
            default=4,
        ),
        max_total_turns=_safe_int(
            cfg.get("max_total_turns"),
            default=settings.deliberation_max_turns,
        ),
        require_synthesis_approval=_safe_bool(
            cfg.get("require_synthesis_approval"),
            default=False,
        ),
        auto_promote_to_memory=_safe_bool(
            cfg.get("auto_promote_to_memory"),
            default=True,
        ),
        min_agents_for_deliberation=_safe_int(
            cfg.get("min_agents_for_deliberation"),
            default=2,
        ),
        allowed_entry_types=_safe_str_list(
            cfg.get("allowed_entry_types"),
            default=DEFAULT_ENTRY_TYPES,
        ),
        auto_deliberate_reviews=_safe_bool(
            cfg.get("auto_deliberate_reviews"),
            default=False,
        ),
    )


# Convenience alias used by the deliberation service.
resolve_policy = get_deliberation_policy
