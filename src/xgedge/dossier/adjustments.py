"""Auditable xG adjustments used by match dossiers.

These transformations are deterministic heuristics.  Their constants are
reported with every result and must not be presented as causal estimates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class AdjustmentConfig:
    standard_penalty_xg: float = 0.76
    scored_penalty_credit: float = 0.30
    opponent_elo_scale: float = 800.0
    opponent_factor_min: float = 0.70
    opponent_factor_max: float = 1.40
    opponent_red_inflation: float = 0.35
    own_red_suppression: float = 0.30
    minimum_red_factor: float = 0.55

    def validate(self) -> None:
        values = asdict(self)
        if any(not isfinite(float(value)) for value in values.values()):
            raise ValueError("adjustment configuration must be finite")
        if not 0 <= self.scored_penalty_credit <= self.standard_penalty_xg <= 1:
            raise ValueError("penalty xG constants are inconsistent")
        if self.opponent_elo_scale <= 0:
            raise ValueError("opponent_elo_scale must be positive")
        if not 0 < self.opponent_factor_min <= 1 <= self.opponent_factor_max:
            raise ValueError("opponent factor clamp must contain 1")
        if not 0 < self.minimum_red_factor <= 1:
            raise ValueError("minimum_red_factor must be in (0, 1]")


def _number(value: Any, *, non_negative: bool = True) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result) or (non_negative and result < 0):
        return None
    return result


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def extract_non_penalty_xg(
    match: Mapping[str, Any],
    side: str,
    *,
    config: AdjustmentConfig | None = None,
) -> dict[str, Any]:
    """Return npxG without silently treating raw xG as penalty-free.

    Preferred input is explicit ``npxg_home``/``npxg_away``.  When only raw
    xG is available, the number of penalty attempts must be known; otherwise
    the result remains unknown.  A provider's explicit penalty-xG total wins
    over the documented 0.76-per-attempt modelling constant.
    """
    if side not in {"home", "away"}:
        raise ValueError("side must be home or away")
    cfg = config or AdjustmentConfig()
    cfg.validate()
    explicit = _number(match.get(f"npxg_{side}"))
    if explicit is not None:
        source = str(match.get(f"npxg_{side}_source") or "normalized_record")
        return {
            "status": "available",
            "value": explicit,
            "source": source,
            "method": "provider_non_penalty_xg",
            "penalty_xg_removed": None,
            "assumptions": [],
        }

    raw_xg = _number(match.get(f"xg_{side}"))
    attempts = _integer(match.get(f"penalties_taken_{side}"))
    if raw_xg is None:
        return {
            "status": "unknown",
            "value": None,
            "source": None,
            "method": None,
            "reason": "xg_and_npxg_missing",
            "assumptions": [],
        }
    if attempts is None:
        return {
            "status": "unknown",
            "value": None,
            "source": str(match.get(f"xg_{side}_source") or "normalized_record"),
            "method": None,
            "reason": "penalty_attempt_count_unknown",
            "assumptions": [],
        }
    explicit_penalty_xg = _number(match.get(f"penalty_xg_{side}"))
    removed = (
        explicit_penalty_xg
        if explicit_penalty_xg is not None
        else attempts * cfg.standard_penalty_xg
    )
    assumptions = [] if explicit_penalty_xg is not None else [
        f"standard_penalty_xg={cfg.standard_penalty_xg:.2f}"
    ]
    return {
        "status": "available",
        "value": max(0.0, raw_xg - removed),
        "source": str(match.get(f"xg_{side}_source") or "normalized_record"),
        "method": "raw_xg_minus_penalty_xg",
        "penalty_xg_removed": removed,
        "assumptions": assumptions,
    }


def penalty_credit_signal(
    non_penalty_xg: Mapping[str, Any],
    match: Mapping[str, Any],
    side: str,
    *,
    config: AdjustmentConfig | None = None,
) -> dict[str, Any]:
    """Experimental presentation signal: npxG + 0.30 per scored penalty.

    It is deliberately not called xG and is never substituted for npxG in the
    model.  The signal remains unknown when penalty-goal event data is absent.
    """
    cfg = config or AdjustmentConfig()
    cfg.validate()
    if non_penalty_xg.get("status") != "available":
        return {
            "status": "unknown",
            "value": None,
            "reason": "non_penalty_xg_unknown",
            "method": "npxg_plus_scored_penalty_credit",
        }
    goals = _integer(match.get(f"penalty_goals_{side}"))
    if goals is None:
        return {
            "status": "unknown",
            "value": None,
            "reason": "penalty_goal_count_unknown",
            "method": "npxg_plus_scored_penalty_credit",
        }
    return {
        "status": "available",
        "value": float(non_penalty_xg["value"]) + goals * cfg.scored_penalty_credit,
        "penalty_goals": goals,
        "credit_per_goal": cfg.scored_penalty_credit,
        "method": "npxg_plus_scored_penalty_credit",
        "warning": "experimental_signal_not_expected_goals",
    }


def _score_before(event: Mapping[str, Any]) -> tuple[int, int] | None:
    home = _integer(event.get("score_before_home"))
    away = _integer(event.get("score_before_away"))
    return (home, away) if home is not None and away is not None else None


def red_card_neutralization(
    value: float,
    team_side: str,
    events: Iterable[Mapping[str, Any]] | None,
    *,
    config: AdjustmentConfig | None = None,
) -> dict[str, Any]:
    """Neutralize approximate whole-match red-card distortion.

    Because event-level xG timestamps are not part of this contract, the
    remaining share of regulation time is used.  Incomplete card events make
    the adjustment unknown instead of being guessed.
    """
    if team_side not in {"home", "away"}:
        raise ValueError("team_side must be home or away")
    observed = _number(value)
    if observed is None:
        raise ValueError("value must be a finite non-negative number")
    cfg = config or AdjustmentConfig()
    cfg.validate()
    if events is None:
        return {
            "status": "unknown",
            "value": None,
            "reason": "red_card_events_not_available",
            "method": "event_time_score_state_heuristic_v1",
            "components": [],
        }
    cards = [dict(event) for event in events]
    if not cards:
        return {
            "status": "available",
            "value": observed,
            "observed_value": observed,
            "combined_observation_factor": 1.0,
            "method": "event_time_score_state_heuristic_v1",
            "components": [],
            "warning": "heuristic_not_causal_estimate",
        }

    factor = 1.0
    components: list[dict[str, Any]] = []
    ordered = sorted(
        cards,
        key=lambda card: (
            _integer(card.get("minute"))
            if _integer(card.get("minute")) is not None
            else 10_000
        ),
    )
    for card in ordered:
        minute = _integer(card.get("minute"))
        card_side = str(card.get("red_card_side") or "").lower()
        score = _score_before(card)
        if minute is None or minute > 130 or card_side not in {"home", "away"} or score is None:
            return {
                "status": "unknown",
                "value": None,
                "reason": "incomplete_red_card_event",
                "method": "event_time_score_state_heuristic_v1",
                "components": components,
            }
        remaining = max(0.0, min(1.0, (90.0 - minute) / 90.0))
        team_goals, opponent_goals = score if team_side == "home" else (score[1], score[0])
        state = "losing" if team_goals < opponent_goals else "leading" if team_goals > opponent_goals else "level"
        state_scale = 1.15 if state == "losing" else 0.85 if state == "leading" else 1.0
        relation = "own_red" if card_side == team_side else "opponent_red"
        if relation == "opponent_red":
            event_factor = 1.0 + cfg.opponent_red_inflation * remaining * state_scale
        else:
            event_factor = max(
                cfg.minimum_red_factor,
                1.0 - cfg.own_red_suppression * remaining * state_scale,
            )
        factor *= event_factor
        components.append({
            "event_id": card.get("event_id"),
            "minute": minute,
            "card_side": card_side,
            "relation": relation,
            "score_before": {"home": score[0], "away": score[1]},
            "team_score_state": state,
            "regulation_share_remaining": remaining,
            "observation_factor": event_factor,
        })
    return {
        "status": "available",
        "value": observed / factor,
        "observed_value": observed,
        "combined_observation_factor": factor,
        "method": "event_time_score_state_heuristic_v1",
        "components": components,
        "warning": "heuristic_not_causal_estimate",
    }


def opponent_strength_adjustment(
    value: float,
    opponent_elo: float | None,
    *,
    baseline_elo: float = 1500.0,
    config: AdjustmentConfig | None = None,
) -> dict[str, Any]:
    """Reward npxG produced against stronger opponents and vice versa."""
    observed = _number(value)
    opponent = _number(opponent_elo, non_negative=False)
    baseline = _number(baseline_elo, non_negative=False)
    if observed is None:
        raise ValueError("value must be a finite non-negative number")
    if opponent is None or baseline is None:
        return {
            "status": "unknown",
            "value": None,
            "reason": "opponent_elo_unknown",
            "method": "elo_ratio_clamped_v1",
        }
    cfg = config or AdjustmentConfig()
    cfg.validate()
    raw_factor = 10.0 ** ((opponent - baseline) / cfg.opponent_elo_scale)
    factor = min(cfg.opponent_factor_max, max(cfg.opponent_factor_min, raw_factor))
    return {
        "status": "available",
        "value": observed * factor,
        "input_value": observed,
        "opponent_elo": opponent,
        "baseline_elo": baseline,
        "factor": factor,
        "unclamped_factor": raw_factor,
        "method": "elo_ratio_clamped_v1",
        "clamp": [cfg.opponent_factor_min, cfg.opponent_factor_max],
    }


def adjusted_match_npxg(
    match: Mapping[str, Any],
    side: str,
    opponent_elo: float | None,
    *,
    config: AdjustmentConfig | None = None,
) -> dict[str, Any]:
    """Run penalty removal, red neutralization, then opponent adjustment."""
    cfg = config or AdjustmentConfig()
    base = extract_non_penalty_xg(match, side, config=cfg)
    credit = penalty_credit_signal(base, match, side, config=cfg)
    if base["status"] != "available":
        return {
            "status": "unknown",
            "value": None,
            "reason": base.get("reason"),
            "non_penalty_xg": base,
            "penalty_credit_signal": credit,
            "red_card_adjustment": None,
            "opponent_adjustment": None,
        }
    red = red_card_neutralization(
        float(base["value"]), side, match.get("red_cards"), config=cfg
    )
    if red["status"] != "available":
        return {
            "status": "unknown",
            "value": None,
            "reason": red.get("reason"),
            "non_penalty_xg": base,
            "penalty_credit_signal": credit,
            "red_card_adjustment": red,
            "opponent_adjustment": None,
        }
    opponent = opponent_strength_adjustment(
        float(red["value"]), opponent_elo, config=cfg
    )
    return {
        "status": opponent["status"],
        "value": opponent.get("value"),
        "reason": opponent.get("reason"),
        "non_penalty_xg": base,
        "penalty_credit_signal": credit,
        "red_card_adjustment": red,
        "opponent_adjustment": opponent,
        "method_order": [
            "penalty_removal",
            "red_card_neutralization",
            "opponent_elo_adjustment",
        ],
    }
