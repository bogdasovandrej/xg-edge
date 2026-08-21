"""Same-match joint probability for score-resolvable market combinations.

Two markets on one fixture share a single scoreline process, so their joint
probability must come from enumerating the score matrix, never from
multiplying the two independent market probabilities. This module is the
generic building block for correlated same-match accumulator legs (e.g.
``Under 2.5`` and ``Away to qualify`` on one fixture).
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

import numpy as np

from xgedge.markets.paper_markets import (
    SUPPORTED_SCORE_MARKETS,
    canonical_market,
    market_probability,
    settle_score_market,
)
from xgedge.markets.settlement import return_multiplier

JOINT_SCHEMA_VERSION = "same-match-joint/1.0"


@dataclass(frozen=True, slots=True)
class JointLeg:
    """One score-resolvable market selection on a shared fixture score matrix."""

    market: str
    selection: str
    line: float | None = None

    def __post_init__(self) -> None:
        if canonical_market(self.market) not in SUPPORTED_SCORE_MARKETS:
            raise ValueError(
                "joint probability requires a score-resolvable market "
                f"(got {self.market!r}); qualification and other non-90-minute "
                "markets are not part of the shared scoreline process"
            )


def _validate_matrix(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=float)
    if values.ndim != 2 or values.shape[0] != values.shape[1]:
        raise ValueError("score matrix must be square")
    if not np.isfinite(values).all() or abs(float(values.sum()) - 1.0) > 1e-6:
        raise ValueError("score matrix must contain normalized finite probabilities")
    return values


def joint_settlement_distribution(
    matrix: np.ndarray, legs: Sequence[JointLeg]
) -> dict[tuple[str, ...], float]:
    """Enumerate the joint settlement-state distribution across every leg.

    Returned keys are tuples of :class:`SettlementOutcome` values, one per
    leg, in ``legs`` order. Probability mass sums to 1.
    """
    if len(legs) < 2:
        raise ValueError("joint probability requires at least two legs")
    values = _validate_matrix(matrix)
    joint: dict[tuple[str, ...], float] = {}
    for home_goals in range(values.shape[0]):
        for away_goals in range(values.shape[1]):
            mass = float(values[home_goals, away_goals])
            if mass <= 0.0:
                continue
            key = tuple(
                settle_score_market(
                    market=leg.market,
                    selection=leg.selection,
                    line=leg.line,
                    home_goals=home_goals,
                    away_goals=away_goals,
                )
                for leg in legs
            )
            joint[key] = joint.get(key, 0.0) + mass
    total = sum(joint.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError("joint settlement distribution failed to normalize")
    return joint


def joint_win_probability(matrix: np.ndarray, legs: Sequence[JointLeg]) -> float:
    """Probability mass where every leg settles as an outright WIN.

    This is the number that must replace a naive ``P(A) * P(B)`` product for
    two markets sharing one score process.
    """
    distribution = joint_settlement_distribution(matrix, legs)
    return sum(
        mass for states, mass in distribution.items() if all(state == "win" for state in states)
    )


def naive_independent_probability(matrix: np.ndarray, legs: Sequence[JointLeg]) -> float:
    """The (usually wrong) independent product, kept only for comparison/audit."""
    values = _validate_matrix(matrix)
    probability = 1.0
    for leg in legs:
        probability *= market_probability(
            values, market=leg.market, selection=leg.selection, line=leg.line
        )
    return probability


def joint_fair_combo_odds(matrix: np.ndarray, legs: Sequence[JointLeg]) -> float:
    """Fair decimal price for an all-win same-match combo, from the true joint mass."""
    probability = joint_win_probability(matrix, legs)
    if not 0.0 < probability < 1.0:
        raise ValueError("joint all-win probability must be strictly between 0 and 1")
    return 1.0 / probability


def joint_combo_expected_value(
    matrix: np.ndarray, legs: Sequence[JointLeg], odds: Sequence[float]
) -> float:
    """Push-aware expected value of a same-match combo at given per-leg prices.

    Each leg's payout multiplier is resolved independently per scoreline
    (WIN/HALF_WIN/PUSH/HALF_LOSS/LOSS) and multiplied through, matching how a
    real accumulator settles a push leg at multiplier 1.0 rather than voiding
    the whole combo.
    """
    if len(odds) != len(legs):
        raise ValueError("odds must have one entry per leg")
    for price in odds:
        if not isfinite(price) or price <= 1.0:
            raise ValueError("odds must be finite and above 1")
    distribution = joint_settlement_distribution(matrix, legs)
    expected_return = 0.0
    for states, mass in distribution.items():
        multiplier = 1.0
        for state, price in zip(states, odds):
            multiplier *= return_multiplier(state, price)
        expected_return += mass * multiplier
    return expected_return - 1.0
