"""Generic payout math for binary, push-aware and quarter-line markets."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Mapping


class SettlementOutcome(str, Enum):
    """All supported returns for one settled market position."""

    WIN = "win"
    HALF_WIN = "half_win"
    PUSH = "push"
    HALF_LOSS = "half_loss"
    LOSS = "loss"
    VOID = "void"


def return_multiplier(outcome: SettlementOutcome | str, odds: float) -> float:
    """Return including stake for one settlement state."""
    try:
        state = outcome if isinstance(outcome, SettlementOutcome) else SettlementOutcome(outcome)
    except (TypeError, ValueError) as exc:
        raise ValueError("unsupported settlement outcome") from exc
    price = float(odds)
    if not isfinite(price) or price <= 1.0:
        raise ValueError("odds must be finite and above 1")
    if state is SettlementOutcome.WIN:
        return price
    if state is SettlementOutcome.HALF_WIN:
        return 1.0 + 0.5 * (price - 1.0)
    if state in {SettlementOutcome.PUSH, SettlementOutcome.VOID}:
        return 1.0
    if state is SettlementOutcome.HALF_LOSS:
        return 0.5
    return 0.0


@dataclass(frozen=True, slots=True)
class SettlementDistribution:
    """Normalized probability mass over payout states."""

    probabilities: Mapping[SettlementOutcome, float]

    def __post_init__(self) -> None:
        parsed: dict[SettlementOutcome, float] = {}
        for raw_state, raw_probability in self.probabilities.items():
            try:
                state = (
                    raw_state
                    if isinstance(raw_state, SettlementOutcome)
                    else SettlementOutcome(raw_state)
                )
                probability = float(raw_probability)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid settlement distribution") from exc
            if not isfinite(probability) or probability < 0.0:
                raise ValueError("settlement probabilities must be finite and non-negative")
            parsed[state] = parsed.get(state, 0.0) + probability
        if abs(sum(parsed.values()) - 1.0) > 1e-9:
            raise ValueError("settlement probabilities must sum to one")
        object.__setattr__(self, "probabilities", parsed)

    def expected_return(self, odds: float) -> float:
        return sum(
            probability * return_multiplier(state, odds)
            for state, probability in self.probabilities.items()
        )

    def expected_value(self, odds: float) -> float:
        return self.expected_return(odds) - 1.0

    def odds_for_target_ev(
        self,
        target_ev: float = 0.0,
        *,
        lower: float = 1.000001,
        upper: float = 1000.0,
        tolerance: float = 1e-10,
    ) -> float:
        """Find the minimum decimal price using deterministic bisection."""
        target = float(target_ev)
        if not isfinite(target) or target < -1.0:
            raise ValueError("target_ev must be finite and at least -1")
        if self.expected_value(upper) < target:
            raise ValueError("target EV is unreachable for this distribution")
        lo, hi = float(lower), float(upper)
        for _ in range(100):
            middle = (lo + hi) / 2.0
            if self.expected_value(middle) >= target:
                hi = middle
            else:
                lo = middle
            if hi - lo <= tolerance:
                break
        return hi

    def fair_odds(self) -> float:
        return self.odds_for_target_ev(0.0)

    def trigger_odds(self, target_ev: float) -> float:
        return self.odds_for_target_ev(target_ev)
