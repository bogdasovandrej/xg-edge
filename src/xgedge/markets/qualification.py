"""Qualification/to-advance settlement, decoupled from 90-minute-score guessing.

Probability estimation for the qualification market already exists as a
two-leg Monte Carlo in
``xgedge.experiments.ucl_qualifying.simulate_qualification`` (aggregate plus
simulated extra time plus a 50/50 penalty shootout). This module owns the
other half of the market: a versioned contract documenting which tie-break
rules that simulation assumed, and fail-closed settlement against the
official confirmed advancing team. It never infers who advanced from a bare
90-minute or aggregate scoreline, as ``xgedge.markets.paper_markets`` already
notes qualification requires additional official result fields and must fail
closed elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xgedge.markets.settlement import SettlementDistribution, SettlementOutcome

QUALIFICATION_RULES_SCHEMA = "competition-advance-rules/1.0"


@dataclass(frozen=True, slots=True)
class CompetitionAdvanceRules:
    """Documents the tie-break rules a qualification probability assumed.

    This is a versioned record, not an implementation: the actual extra-time
    and penalty simulation lives in
    ``xgedge.experiments.ucl_qualifying.simulate_qualification``. Keeping the
    assumptions here lets a stored candidate or settlement be audited against
    the rules that were actually in force for its competition/season.
    """

    competition_id: str
    season_id: str
    away_goals_rule: bool = False  # UEFA abolished away goals from 2021/22 onward
    extra_time_enabled: bool = True
    penalty_shootout_enabled: bool = True
    penalty_shootout_model: str = "50/50_after_simulated_extra_time"

    def validate(self) -> None:
        if not self.competition_id or not self.season_id:
            raise ValueError("competition_id and season_id are required")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": QUALIFICATION_RULES_SCHEMA,
            "competition_id": self.competition_id,
            "season_id": self.season_id,
            "away_goals_rule": self.away_goals_rule,
            "extra_time_enabled": self.extra_time_enabled,
            "penalty_shootout_enabled": self.penalty_shootout_enabled,
            "penalty_shootout_model": self.penalty_shootout_model,
        }


def qualification_probability_distribution(
    *, selection: str, home_to_advance: float
) -> SettlementDistribution:
    """Bridge a two-leg advance probability into the generic push-aware payout engine.

    ``home_to_advance`` must come from an already-validated simulation (e.g.
    :func:`xgedge.experiments.ucl_qualifying.simulate_qualification`); this
    function does not compute it from a scoreline.
    """
    side = str(selection or "").strip().casefold()
    if side not in {"home", "away"}:
        raise ValueError("qualification selection must be 'home' or 'away'")
    if not 0.0 < home_to_advance < 1.0:
        raise ValueError("home_to_advance must be strictly between 0 and 1")
    probability = home_to_advance if side == "home" else 1.0 - home_to_advance
    return SettlementDistribution({
        SettlementOutcome.WIN: probability,
        SettlementOutcome.LOSS: 1.0 - probability,
    })


def settle_qualification_market(
    *,
    selection: str,
    home_team_id: str,
    away_team_id: str,
    qualified_team_id: str | None,
) -> str:
    """Settle strictly against the officially confirmed advancing team.

    Returns ``"win"``/``"loss"``. Raises when the outcome is not yet
    resolvable: an empty/unconfirmed ``qualified_team_id`` (including
    deriving it from a bare 90-minute or aggregate score) must never be
    treated as a settlement.
    """
    side = str(selection or "").strip().casefold()
    if side not in {"home", "away"}:
        raise ValueError("qualification selection must be 'home' or 'away'")
    if not home_team_id or not away_team_id or home_team_id == away_team_id:
        raise ValueError("qualification settlement requires two distinct identified teams")
    if not qualified_team_id:
        raise ValueError(
            "qualification is not settleable without an official confirmed "
            "qualified_team_id; a regulation-time or aggregate score is not sufficient"
        )
    if qualified_team_id not in (home_team_id, away_team_id):
        raise ValueError("qualified_team_id does not match either fixture team")
    picked_team = home_team_id if side == "home" else away_team_id
    return "win" if qualified_team_id == picked_team else "loss"
