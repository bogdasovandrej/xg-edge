"""Exact-identity execution quote and trigger state machine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol


class ExecutionQuoteProvider(Protocol):
    provider_id: str

    def quotes(self) -> list[Mapping[str, Any]]:
        """Return licensed, shadow or manually verified quote records."""


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def evaluate_execution_quote(
    candidate: Mapping[str, Any],
    quote: Mapping[str, Any],
    *,
    now: str,
    near_fraction: float = 0.02,
    large_move_fraction: float = 0.07,
    maximum_quote_age: timedelta = timedelta(minutes=30),
    was_preline_selected: bool = True,
) -> dict[str, Any]:
    """Evaluate a fresh exact quote; trigger hit still requires human audit."""
    required_identity = ("fixture_id", "market", "selection", "line")
    if any(candidate.get(field) != quote.get(field) for field in required_identity):
        raise ValueError("execution quote does not match exact fixture/market identity")
    captured = _utc(quote.get("captured_at_utc"), "captured_at_utc")
    current = _utc(now, "now")
    kickoff = _utc(candidate.get("kickoff_utc"), "kickoff_utc")
    if captured >= kickoff or current >= kickoff:
        raise ValueError("post-kickoff data cannot enter a prematch decision")
    if captured > current or current - captured > maximum_quote_age:
        return {"status": "STALE_PRICE", "requires_human_audit": True}
    odds = float(quote.get("odds"))
    trigger = float(candidate.get("trigger_price"))
    reference = quote.get("reference_odds")
    move = None if reference is None else odds / float(reference) - 1.0
    if move is not None and abs(move) >= large_move_fraction:
        status = "LARGE_MOVE_REAUDIT"
    elif not was_preline_selected and odds >= trigger:
        status = "LATE_WILDCARD"
    elif odds >= trigger:
        status = "TRIGGER_HIT"
    elif odds >= trigger * (1.0 - near_fraction):
        status = "NEAR_TRIGGER"
    else:
        status = "WATCH"
    return {
        "schema_version": "trigger-evaluation/1.0",
        "candidate_id": candidate.get("candidate_id"),
        "fixture_id": candidate.get("fixture_id"),
        "status": status,
        "current_odds": odds,
        "trigger_odds": trigger,
        "price_move_fraction": move,
        "quote": dict(quote),
        "requires_human_audit": True,
        "automatic_approval": False,
    }
