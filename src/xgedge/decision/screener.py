"""Line screener: classify price movement and rank it separately from value.

The screener answers a different question from the value top. The value top
asks "is this price good against our probability". The screener asks "has
this price moved, and in which direction" — so its ranking key is the size
and direction of the move, never ``value_pct``. Mixing the two into one list
would hide which question a row is answering.

On ``LARGE_MOVE``: a lengthening price is deliberately NOT scored as good or
bad. The project's own sample of such markets is 8 observations, which is far
too small to justify a rule in either direction, so the classifier marks them
``NEEDS_MECHANISM`` and leaves the judgement to a human audit.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

SCREENER_SCHEMA_VERSION = "line-screener/1.0"


@dataclass(frozen=True, slots=True)
class ScreenerConfig:
    frozen_threshold: float = 0.005   # below this the price is unchanged
    large_move_threshold: float = 0.07
    version: str = SCREENER_SCHEMA_VERSION

    def validate(self) -> None:
        if not 0 < self.frozen_threshold < self.large_move_threshold < 1:
            raise ValueError(
                "thresholds must satisfy 0 < frozen < large_move < 1"
            )


def delta_pct(current_odds: float, reference_odds: float) -> float:
    """Relative price change against the reference observation."""
    current, reference = float(current_odds), float(reference_odds)
    if not isfinite(current) or not isfinite(reference) or current <= 1.0 or reference <= 1.0:
        raise ValueError("odds must be finite and above 1")
    return current / reference - 1.0


def classify_move(
    current_odds: float,
    reference_odds: float,
    *,
    config: ScreenerConfig | None = None,
    limit_reported: bool = False,
) -> dict[str, Any]:
    """Classify one price move.

    ``LIMIT_SIGNAL`` is only ever returned when a provider actually reported a
    stake-limit change. It cannot be inferred from price alone, and guessing
    it would invent data.
    """
    cfg = config or ScreenerConfig()
    cfg.validate()
    change = delta_pct(current_odds, reference_odds)
    magnitude = abs(change)

    if limit_reported:
        state = "LIMIT_SIGNAL"
    elif magnitude < cfg.frozen_threshold:
        state = "FROZEN"
    elif magnitude >= cfg.large_move_threshold:
        state = "LARGE_MOVE"
    elif change > 0:
        state = "DRIFT"     # price lengthened
    else:
        state = "STEAM"     # price shortened

    return {
        "state": state,
        "delta_pct": change * 100.0,
        "current_odds": float(current_odds),
        "reference_odds": float(reference_odds),
        "direction": "LENGTHENED" if change > 0 else "SHORTENED" if change < 0 else "FLAT",
        # A large move is a question, not an answer. The sample behind any
        # "lengthening is good" rule is 8 markets; that is not a rule.
        "assessment": "NEEDS_MECHANISM" if state == "LARGE_MOVE" else "NO_ASSESSMENT",
        "requires_human_audit": state in {"LARGE_MOVE", "LIMIT_SIGNAL"},
    }


def screen_quote_history(
    observations: Sequence[Mapping[str, Any]],
    *,
    config: ScreenerConfig | None = None,
) -> dict[str, Any] | None:
    """Compare the newest observation against the oldest stored reference."""
    usable = [
        row for row in observations
        if isinstance(row, Mapping)
        and isinstance(row.get("odds"), (int, float))
        and not isinstance(row.get("odds"), bool)
        and float(row["odds"]) > 1.0
    ]
    if len(usable) < 2:
        return None
    ordered = sorted(usable, key=lambda row: str(row.get("checked_at") or ""))
    reference, current = ordered[0], ordered[-1]
    move = classify_move(
        float(current["odds"]),
        float(reference["odds"]),
        config=config,
        limit_reported=bool(current.get("limit_reported", False)),
    )
    return {
        **move,
        "market_id": current.get("market_id") or reference.get("market_id"),
        "bookmaker": current.get("bookmaker"),
        "reference_checked_at": reference.get("checked_at"),
        "current_checked_at": current.get("checked_at"),
        "observations": len(usable),
    }


def movement_top(
    screened: Sequence[Mapping[str, Any]],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Rank moved markets by how favourably the price moved for a backer.

    Sorted by ``delta_pct`` descending — the size of the move — and explicitly
    NOT by ``value_pct``. This feed is separate from the value top so a reader
    always knows which question produced the ordering.
    """
    moved = [
        dict(row) for row in screened
        if isinstance(row, Mapping) and row.get("state") not in (None, "FROZEN")
    ]
    moved.sort(key=lambda row: (-float(row["delta_pct"]), str(row.get("market_id") or "")))
    if limit is not None:
        moved = moved[: int(limit)]
    return {
        "schema_version": SCREENER_SCHEMA_VERSION,
        "sorted_by": "delta_pct",
        "not_sorted_by": "value_pct",
        "note": (
            "Движение цены — отдельный сигнал. LARGE_MOVE помечен как "
            "NEEDS_MECHANISM и не считается автоматически хорошим или плохим."
        ),
        "rows": moved,
    }
