"""Persistent, fail-closed PAPER-trading ledger.

The ledger is deliberately offline.  It consumes an already-built live payload
and already-fetched official results; it has no HTTP, bookmaker-order, payment,
or real-money execution path.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from xgedge.simulation.paper import (
    STARTING_BALANCE_RUB,
    TARGET_BALANCE_RUB,
    BetPlaced,
    BetSettled,
    ConservativeEdgeStrategy,
    CycleStarted,
    FlatOnePercentStrategy,
    FractionalKellyStrategy,
    PaperEvent,
    PaperSimulator,
    RuinObserved,
    SettlementResult,
    TargetObserved,
    rank_strategies,
)
from xgedge.markets.paper_markets import (
    SUPPORTED_SCORE_MARKETS,
    canonical_market,
    settle_score_market,
    supported_line,
)

LEDGER_SCHEMA_VERSION = "paper-trading-ledger/1.1"
LEGACY_LEDGER_SCHEMA_VERSION = "paper-trading-ledger/1.0"
EVENT_SCHEMA_VERSION = "paper-event/1.0"
SUMMARY_SCHEMA_VERSION = "paper-trading-summary/1.0"
RESULTS_SCHEMA_VERSION = "paper-official-results/1.0"
PROSPECTIVE_SCHEMA_VERSION = "prospective-clv/1.2"
RANKING_SCHEMA_VERSION = "paper-candidate-ranking/1.0"
RUIN_THRESHOLD_RUB = 100.0
MAX_QUOTE_AGE = timedelta(minutes=30)
OUTCOMES = ("home", "draw", "away")

STRATEGY_LABELS = {
    "flat_1pct": "Фиксированные 1%",
    "fractional_kelly_025": "1/4 Kelly, лимит 1%",
    "conservative_edge_5pp": "Только edge от 5 п.п.",
}

_POLICY: dict[str, Any] = {
    "mode": "PAPER_ONLY",
    "real_money_execution": False,
    "starting_balance_rub": STARTING_BALANCE_RUB,
    "target_balance_rub": TARGET_BALANCE_RUB,
    "target_role": "diagnostic_only_not_ranking_input",
    "ruin_threshold_rub": RUIN_THRESHOLD_RUB,
    "maximum_stake_fraction": 0.01,
    "maximum_quote_age_seconds": int(MAX_QUOTE_AGE.total_seconds()),
    "market": "REGULATION_SCORE_MARKETS_V1",
    "supported_markets": sorted(SUPPORTED_SCORE_MARKETS),
    "one_candidate_per_match": True,
    "strategy_ids": list(STRATEGY_LABELS),
    "strategy_elimination": "disabled_until_preregistered_evidence",
    "strategy_ranking_score": {
        "roi_weight": 0.15,
        "log_growth_per_bet_weight": 0.20,
        "clv_weight": 0.65,
        "max_drawdown_penalty": 1.0,
        "ruin_rate_penalty": 2.0,
        "full_evidence_bets": 100,
    },
    "parlays": "disabled_until_singles_are_prospectively_validated",
}

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "event_schema_version",
    "created_at",
    "updated_at",
    "policy",
    "strategies",
    "enrollments",
    "settlements",
    "update_history",
    "paper_trading",
}


def _strategy_objects() -> dict[str, Any]:
    return {
        "flat_1pct": FlatOnePercentStrategy(),
        "fractional_kelly_025": FractionalKellyStrategy(),
        "conservative_edge_5pp": ConservativeEdgeStrategy(),
    }


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = sha256(_canonical([str(part) for part in parts]).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:24]}"


def _timestamp(value: str | datetime, field: str) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: str | datetime, field: str = "timestamp") -> str:
    return _timestamp(value, field).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{field} must be finite")
    if minimum is not None and (
        number <= minimum if strict_minimum else number < minimum
    ):
        operator = ">" if strict_minimum else ">="
        raise ValueError(f"{field} must be {operator} {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return number


def _exact_fields(source: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(source)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{name} fields mismatch; missing={missing}, extra={extra}")


def event_to_json(event: PaperEvent) -> dict[str, Any]:
    """Serialize one known paper event into a versioned strict JSON object."""
    common = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "kind": event.kind.value,
        "event_id": event.event_id,
        "timestamp": _iso(event.timestamp),
        "strategy_id": event.strategy_id,
        "cycle_id": event.cycle_id,
    }
    if isinstance(event, CycleStarted):
        return {**common, "starting_balance_rub": event.starting_balance_rub}
    if isinstance(event, BetPlaced):
        return {
            **common,
            "bet_id": event.bet_id,
            "match_id": event.match_id,
            "odds": event.odds,
            "model_probability": event.model_probability,
            "bookmaker_probability": event.bookmaker_probability,
            "stake_rub": event.stake_rub,
        }
    if isinstance(event, BetSettled):
        return {
            **common,
            "bet_id": event.bet_id,
            "result": event.result.value,
            "closing_odds": event.closing_odds,
        }
    if isinstance(event, TargetObserved):
        return {
            **common,
            "balance_rub": event.balance_rub,
            "target_balance_rub": event.target_balance_rub,
        }
    if isinstance(event, RuinObserved):
        return {**common, "balance_rub": event.balance_rub}
    raise TypeError(f"unsupported paper event: {type(event)!r}")


_EVENT_FIELDS = {
    "cycle_started": {
        "schema_version", "kind", "event_id", "timestamp", "strategy_id",
        "cycle_id", "starting_balance_rub",
    },
    "bet_placed": {
        "schema_version", "kind", "event_id", "timestamp", "strategy_id",
        "cycle_id", "bet_id", "match_id", "odds", "model_probability",
        "bookmaker_probability", "stake_rub",
    },
    "bet_settled": {
        "schema_version", "kind", "event_id", "timestamp", "strategy_id",
        "cycle_id", "bet_id", "result", "closing_odds",
    },
    "target_observed": {
        "schema_version", "kind", "event_id", "timestamp", "strategy_id",
        "cycle_id", "balance_rub", "target_balance_rub",
    },
    "ruin_observed": {
        "schema_version", "kind", "event_id", "timestamp", "strategy_id",
        "cycle_id", "balance_rub",
    },
}


def event_from_json(source: Mapping[str, Any]) -> PaperEvent:
    """Deserialize a paper event and reject unknown schemas or fields."""
    if not isinstance(source, Mapping):
        raise ValueError("paper event must be an object")
    if source.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported paper event schema: {source.get('schema_version')}")
    kind = source.get("kind")
    if kind not in _EVENT_FIELDS:
        raise ValueError(f"unsupported paper event kind: {kind!r}")
    _exact_fields(source, _EVENT_FIELDS[str(kind)], f"{kind} event")
    common = {
        "event_id": _text(source["event_id"], "event_id"),
        "timestamp": _timestamp(source["timestamp"], "timestamp"),
        "strategy_id": _text(source["strategy_id"], "strategy_id"),
        "cycle_id": _text(source["cycle_id"], "cycle_id"),
    }
    if kind == "cycle_started":
        return CycleStarted(
            **common,
            starting_balance_rub=_number(
                source["starting_balance_rub"], "starting_balance_rub", strict_minimum=True,
                minimum=0,
            ),
        )
    if kind == "bet_placed":
        bookmaker_probability = source["bookmaker_probability"]
        return BetPlaced(
            **common,
            bet_id=_text(source["bet_id"], "bet_id"),
            match_id=_text(source["match_id"], "match_id"),
            odds=_number(source["odds"], "odds", minimum=1, strict_minimum=True),
            model_probability=_number(
                source["model_probability"], "model_probability", minimum=0, maximum=1
            ),
            bookmaker_probability=(
                None
                if bookmaker_probability is None
                else _number(
                    bookmaker_probability, "bookmaker_probability", minimum=0, maximum=1
                )
            ),
            stake_rub=_number(
                source["stake_rub"], "stake_rub", minimum=0, strict_minimum=True
            ),
        )
    if kind == "bet_settled":
        closing = source["closing_odds"]
        try:
            result = SettlementResult(source["result"])
        except (TypeError, ValueError) as exc:
            raise ValueError("settlement result must be win, loss, push, or void") from exc
        return BetSettled(
            **common,
            bet_id=_text(source["bet_id"], "bet_id"),
            result=result,
            closing_odds=(
                None
                if closing is None
                else _number(closing, "closing_odds", minimum=1, strict_minimum=True)
            ),
        )
    if kind == "target_observed":
        return TargetObserved(
            **common,
            balance_rub=_number(source["balance_rub"], "balance_rub", minimum=0),
            target_balance_rub=_number(
                source["target_balance_rub"], "target_balance_rub", minimum=0,
                strict_minimum=True,
            ),
        )
    return RuinObserved(
        **common,
        balance_rub=_number(source["balance_rub"], "balance_rub", minimum=0),
    )


def _simulators(document: Mapping[str, Any]) -> dict[str, PaperSimulator]:
    output: dict[str, PaperSimulator] = {}
    strategies = document["strategies"]
    for strategy_id, strategy in _strategy_objects().items():
        row = strategies[strategy_id]
        events = [event_from_json(event) for event in row["events"]]
        output[strategy_id] = PaperSimulator.from_events(
            strategy,
            events,
            ruin_threshold_rub=RUIN_THRESHOLD_RUB,
        )
    return output


def _serialize_simulators(
    document: dict[str, Any], simulators: Mapping[str, PaperSimulator]
) -> None:
    for strategy_id, simulator in simulators.items():
        document["strategies"][strategy_id]["events"] = [
            event_to_json(event) for event in simulator.events
        ]


def _public_summary_unvalidated(
    document: Mapping[str, Any], simulators: Mapping[str, PaperSimulator]
) -> dict[str, Any]:
    rankings = rank_strategies(simulators.values())
    leaderboard = []
    for ranking in rankings:
        simulator = simulators[ranking.strategy_id]
        metrics = ranking.metrics
        active = simulator.active_cycle
        leaderboard.append({
            "rank": ranking.rank,
            "strategy_id": ranking.strategy_id,
            "label": STRATEGY_LABELS[ranking.strategy_id],
            "score": ranking.score,
            "equity_balance_rub": active.equity_balance_rub,
            "available_balance_rub": active.available_balance_rub,
            "pnl_rub": metrics.pnl_rub,
            "roi": metrics.roi,
            "max_drawdown": metrics.max_drawdown,
            "log_growth": metrics.log_growth,
            "mean_clv": metrics.mean_clv,
            "total_staked_rub": metrics.total_staked_rub,
            "settled_bets": metrics.settled_bets,
            "open_bets": active.open_bets,
            "wins": metrics.wins,
            "losses": metrics.losses,
            "pushes": metrics.pushes,
            "voids": metrics.voids,
            "cycle_count": metrics.cycle_count,
            "ruin_count": metrics.ruin_count,
            "ruin_rate": metrics.ruin_rate,
            "target_hit_count": metrics.target_hit_count,
        })
    total_settled = sum(row["settled_bets"] for row in leaderboard)
    total_open = sum(row["open_bets"] for row in leaderboard)
    market_counts: dict[str, dict[str, int]] = {}
    for key, enrollment in document["enrollments"].items():
        market = canonical_market(enrollment.get("market"))
        counts = market_counts.setdefault(market, {"enrolled": 0, "settled": 0, "open": 0})
        counts["enrolled"] += 1
        if key in document["settlements"]:
            counts["settled"] += 1
        else:
            counts["open"] += 1
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "status": "PAPER_ONLY_TRACKING" if document["enrollments"] else "PAPER_ONLY_EMPTY",
        "real_money_execution": False,
        "updated_at": document["updated_at"],
        "starting_balance_rub": STARTING_BALANCE_RUB,
        "target_balance_rub": TARGET_BALANCE_RUB,
        "target_role": "diagnostic_only_not_ranking_input",
        "totals": {
            "strategies": len(leaderboard),
            "enrolled_matches": len(document["enrollments"]),
            "settled_matches": len(document["settlements"]),
            "open_matches": len(document["enrollments"]) - len(document["settlements"]),
            "settled_bets": total_settled,
            "open_bets": total_open,
        },
        "leaderboard": leaderboard,
        "markets": dict(sorted(market_counts.items())),
        "selection_policy": {
            "status": "PREREGISTERED",
            "minimum_settled_bets_for_full_evidence": 100,
            "speed_to_target_used_for_ranking": False,
            "strategy_deletion": False,
        },
        "parlays": {
            "status": "DISABLED",
            "reason": "singles_require_prospective_clv_validation_before_parlay_research",
        },
    }


def new_paper_ledger(*, created_at: str | datetime) -> dict[str, Any]:
    """Create three empty strategy logs, each beginning with 10,000 RUB."""
    when = _timestamp(created_at, "created_at")
    strategies: dict[str, Any] = {}
    simulators: dict[str, PaperSimulator] = {}
    for strategy_id, strategy in _strategy_objects().items():
        simulator = PaperSimulator(
            strategy,
            ruin_threshold_rub=RUIN_THRESHOLD_RUB,
            started_at=when,
            first_event_id=f"event:cycle_started:{strategy_id}:1",
        )
        simulators[strategy_id] = simulator
        strategies[strategy_id] = {
            "strategy_id": strategy_id,
            "label": STRATEGY_LABELS[strategy_id],
            "events": [event_to_json(event) for event in simulator.events],
        }
    document: dict[str, Any] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "created_at": _iso(when),
        "updated_at": _iso(when),
        "policy": deepcopy(_POLICY),
        "strategies": strategies,
        "enrollments": {},
        "settlements": {},
        "update_history": [],
        "paper_trading": {},
    }
    document["paper_trading"] = _public_summary_unvalidated(document, simulators)
    return document


def _validate_action(action: Mapping[str, Any], strategy_id: str) -> None:
    expected = {"accepted", "bet_id", "reason", "stake_rub", "stake_fraction"}
    _exact_fields(action, expected, f"strategy action {strategy_id}")
    if not isinstance(action["accepted"], bool):
        raise ValueError("strategy action accepted must be boolean")
    _text(action["reason"], "strategy action reason")
    _number(action["stake_rub"], "strategy action stake_rub", minimum=0)
    _number(action["stake_fraction"], "strategy action stake_fraction", minimum=0, maximum=.01)
    if action["accepted"]:
        _text(action["bet_id"], "strategy action bet_id")
        if action["stake_rub"] <= 0:
            raise ValueError("accepted strategy action must have positive stake")
    elif action["bet_id"] is not None or action["stake_rub"] != 0:
        raise ValueError("rejected strategy action cannot reserve a bet or stake")


def _validate_enrollment(row: Mapping[str, Any], fixture_id: str) -> None:
    expected = {
        "fixture_id", "competition", "stage", "kickoff_utc", "home", "away",
        "selection", "outcome", "market", "line", "model_probability", "bookmaker_probability",
        "odds", "bookmaker", "bookmaker_key", "quote_source",
        "quote_captured_at", "data_quality_score", "point_edge", "robust_edge",
        "market_period", "enrolled_at", "strategy_actions",
    }
    _exact_fields(row, expected, f"enrollment {fixture_id}")
    if _text(row["fixture_id"], "fixture_id") != fixture_id:
        raise ValueError(f"enrollment key/id mismatch for {fixture_id}")
    market_kind = canonical_market(row["market"])
    if market_kind not in SUPPORTED_SCORE_MARKETS or market_kind != row["market"]:
        raise ValueError(f"enrollment {fixture_id} has unsupported market")
    line = row["line"]
    if market_kind in {"totals", "team_totals", "asian_handicap"}:
        if supported_line(line) is None:
            raise ValueError(f"enrollment {fixture_id} has unsupported line")
    elif line is not None:
        raise ValueError(f"enrollment {fixture_id} must not have a line")
    try:
        settle_score_market(
            market=market_kind,
            selection=_text(row["outcome"], "outcome"),
            line=line,
            home_goals=0,
            away_goals=0,
        )
    except ValueError as exc:
        raise ValueError(f"enrollment {fixture_id} has unsupported selection") from exc
    _timestamp(row["kickoff_utc"], "kickoff_utc")
    _timestamp(row["quote_captured_at"], "quote_captured_at")
    _timestamp(row["enrolled_at"], "enrolled_at")
    odds = _number(row["odds"], "odds", minimum=1, strict_minimum=True)
    model = _number(row["model_probability"], "model_probability", minimum=0, maximum=1)
    market = _number(
        row["bookmaker_probability"], "bookmaker_probability", minimum=0, maximum=1
    )
    if abs(market - 1.0 / odds) > 1e-9 or model <= market:
        raise ValueError(f"enrollment {fixture_id} has inconsistent market edge")
    if row["market_period"] != "REGULATION_90_MINUTES":
        raise ValueError(f"enrollment {fixture_id} is not a regulation-time market")
    actions = row["strategy_actions"]
    if not isinstance(actions, Mapping) or set(actions) != set(STRATEGY_LABELS):
        raise ValueError(f"enrollment {fixture_id} strategy actions are incomplete")
    for strategy_id, action in actions.items():
        if not isinstance(action, Mapping):
            raise ValueError("strategy action must be an object")
        _validate_action(action, strategy_id)


def _validate_settlement(row: Mapping[str, Any], fixture_id: str) -> None:
    expected = {
        "fixture_id", "outcome", "home_goals_90", "away_goals_90",
        "market", "selection", "line", "selection_result", "settled_at", "result_source",
        "closing_benchmark", "closing_odds", "strategy_results",
    }
    _exact_fields(row, expected, f"settlement {fixture_id}")
    if row["fixture_id"] != fixture_id or row["outcome"] not in OUTCOMES:
        raise ValueError(f"settlement {fixture_id} has invalid identity/outcome")
    market_kind = canonical_market(row["market"])
    if market_kind not in SUPPORTED_SCORE_MARKETS or market_kind != row["market"]:
        raise ValueError(f"settlement {fixture_id} has invalid market")
    home, away = row["home_goals_90"], row["away_goals_90"]
    if home is not None or away is not None:
        if (
            isinstance(home, bool) or isinstance(away, bool)
            or not isinstance(home, int) or not isinstance(away, int)
            or home < 0 or away < 0
        ):
            raise ValueError(f"settlement {fixture_id} has invalid goals")
        expected_outcome = "home" if home > away else "away" if away > home else "draw"
        if row["outcome"] != expected_outcome:
            raise ValueError(f"settlement {fixture_id} outcome conflicts with goals")
        expected_result = settle_score_market(
            market=market_kind,
            selection=row["selection"],
            line=row["line"],
            home_goals=home,
            away_goals=away,
        )
        if row["selection_result"] != expected_result:
            raise ValueError(f"settlement {fixture_id} selection result conflicts with goals")
    elif market_kind != "1x2":
        raise ValueError(f"settlement {fixture_id} requires goals for this market")
    else:
        expected_result = "win" if row["selection"] == row["outcome"] else "loss"
        if row["selection_result"] != expected_result:
            raise ValueError(f"settlement {fixture_id} selection result conflicts with outcome")
    if row["selection_result"] not in {"win", "loss", "push", "void"}:
        raise ValueError(f"settlement {fixture_id} has invalid selection result")
    _timestamp(row["settled_at"], "settled_at")
    _text(row["result_source"], "result_source")
    if row["closing_odds"] is None:
        if row["closing_benchmark"] is not None:
            raise ValueError("closing benchmark must be null when closing odds are absent")
    else:
        _number(row["closing_odds"], "closing_odds", minimum=1, strict_minimum=True)
        if row["closing_benchmark"] != "pinnacle_fair_1x2" or market_kind != "1x2":
            raise ValueError("only a validated Pinnacle fair close may be stored")
    results = row["strategy_results"]
    if not isinstance(results, Mapping) or set(results) != set(STRATEGY_LABELS):
        raise ValueError("settlement strategy results are incomplete")
    for result in results.values():
        if result not in {"win", "loss", "push", "void", "not_placed"}:
            raise ValueError("unsupported strategy settlement result")


def validate_paper_ledger(source: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all schemas, replay all events, and return a defensive copy."""
    if not isinstance(source, Mapping):
        raise ValueError("paper ledger must be an object")
    _exact_fields(source, _TOP_LEVEL_FIELDS, "paper ledger")
    if source.get("schema_version") != LEDGER_SCHEMA_VERSION:
        raise ValueError(f"unsupported paper ledger schema: {source.get('schema_version')}")
    if source.get("event_schema_version") != EVENT_SCHEMA_VERSION:
        raise ValueError("paper ledger event schema does not match")
    if source.get("policy") != _POLICY:
        raise ValueError("paper policy is immutable; start a new schema instead")
    _timestamp(source["created_at"], "created_at")
    _timestamp(source["updated_at"], "updated_at")
    strategies = source["strategies"]
    if not isinstance(strategies, Mapping) or set(strategies) != set(STRATEGY_LABELS):
        raise ValueError("paper ledger must contain exactly the three fixed strategies")
    for strategy_id, row in strategies.items():
        if not isinstance(row, Mapping):
            raise ValueError("strategy ledger must be an object")
        _exact_fields(row, {"strategy_id", "label", "events"}, f"strategy {strategy_id}")
        if row["strategy_id"] != strategy_id or row["label"] != STRATEGY_LABELS[strategy_id]:
            raise ValueError(f"strategy metadata mismatch for {strategy_id}")
        if not isinstance(row["events"], list) or not row["events"]:
            raise ValueError(f"strategy {strategy_id} event log cannot be empty")
    enrollments = source["enrollments"]
    settlements = source["settlements"]
    if not isinstance(enrollments, Mapping) or not isinstance(settlements, Mapping):
        raise ValueError("paper enrollments and settlements must be objects")
    for fixture_id, row in enrollments.items():
        if not isinstance(row, Mapping):
            raise ValueError("enrollment must be an object")
        _validate_enrollment(row, str(fixture_id))
    for fixture_id, row in settlements.items():
        if fixture_id not in enrollments or not isinstance(row, Mapping):
            raise ValueError(f"settlement {fixture_id} has no enrollment")
        _validate_settlement(row, str(fixture_id))
        enrollment = enrollments[fixture_id]
        if (
            row["market"] != enrollment["market"]
            or row["selection"] != enrollment["outcome"]
            or row["line"] != enrollment["line"]
        ):
            raise ValueError(f"settlement {fixture_id} does not match enrollment")
    history = source["update_history"]
    if not isinstance(history, list):
        raise ValueError("update_history must be a list")
    history_ids: set[str] = set()
    for row in history:
        if not isinstance(row, Mapping):
            raise ValueError("update history row must be an object")
        expected = {
            "update_id", "updated_at", "enrolled_fixture_ids",
            "settled_fixture_ids", "event_count_after",
        }
        _exact_fields(row, expected, "update history row")
        update_id = _text(row["update_id"], "update_id")
        if update_id in history_ids:
            raise ValueError("duplicate update_id")
        history_ids.add(update_id)
        _timestamp(row["updated_at"], "update_history.updated_at")
        if not isinstance(row["enrolled_fixture_ids"], list) or not isinstance(
            row["settled_fixture_ids"], list
        ):
            raise ValueError("update history fixture ids must be lists")
        if isinstance(row["event_count_after"], bool) or not isinstance(
            row["event_count_after"], int
        ):
            raise ValueError("event_count_after must be an integer")

    document = deepcopy(dict(source))
    simulators = _simulators(document)
    placed: dict[str, dict[str, BetPlaced]] = {}
    settled: dict[str, set[str]] = {}
    for strategy_id, simulator in simulators.items():
        placed[strategy_id] = {
            event.bet_id: event for event in simulator.events if isinstance(event, BetPlaced)
        }
        settled[strategy_id] = {
            event.bet_id for event in simulator.events if isinstance(event, BetSettled)
        }
    referenced: dict[str, set[str]] = {strategy_id: set() for strategy_id in STRATEGY_LABELS}
    for fixture_id, enrollment in enrollments.items():
        for strategy_id, action in enrollment["strategy_actions"].items():
            if not action["accepted"]:
                continue
            bet_id = action["bet_id"]
            event = placed[strategy_id].get(bet_id)
            if event is None or event.match_id != fixture_id:
                raise ValueError(f"enrollment {fixture_id} has no matching placed event")
            referenced[strategy_id].add(bet_id)
            is_settled = bet_id in settled[strategy_id]
            if is_settled != (fixture_id in settlements):
                raise ValueError(f"settlement/event mismatch for {fixture_id}")
    for strategy_id in STRATEGY_LABELS:
        if set(placed[strategy_id]) != referenced[strategy_id]:
            raise ValueError(f"strategy {strategy_id} contains an unreferenced bet")

    expected_summary = _public_summary_unvalidated(document, simulators)
    if source["paper_trading"] != expected_summary:
        raise ValueError("stored paper summary does not match replayed event history")
    return document


def _migrate_legacy_ledger(source: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade the strict 1X2 v1.0 ledger without changing event history."""
    if source.get("schema_version") != LEGACY_LEDGER_SCHEMA_VERSION:
        return deepcopy(dict(source))
    document = deepcopy(dict(source))
    policy = document.get("policy")
    if not isinstance(policy, Mapping) or policy.get("market") != "REGULATION_1X2":
        raise ValueError("unsupported legacy paper policy")
    document["schema_version"] = LEDGER_SCHEMA_VERSION
    document["policy"] = deepcopy(_POLICY)
    enrollments = document.get("enrollments")
    settlements = document.get("settlements")
    if not isinstance(enrollments, dict) or not isinstance(settlements, dict):
        raise ValueError("legacy paper ledger has invalid positions")
    for row in enrollments.values():
        if not isinstance(row, dict):
            raise ValueError("legacy enrollment must be an object")
        row["market"] = "1x2"
        row["line"] = None
    for fixture_id, row in settlements.items():
        if not isinstance(row, dict) or fixture_id not in enrollments:
            raise ValueError("legacy settlement must match an enrollment")
        enrollment = enrollments[fixture_id]
        actual = row.get("outcome")
        selection_result = "win" if enrollment.get("outcome") == actual else "loss"
        row.update({
            "home_goals_90": None,
            "away_goals_90": None,
            "market": "1x2",
            "selection": enrollment.get("outcome"),
            "line": None,
            "selection_result": selection_result,
        })
    simulators = _simulators(document)
    document["paper_trading"] = _public_summary_unvalidated(document, simulators)
    return document


def load_paper_ledger(path: Path) -> dict[str, Any]:
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read paper ledger: {exc}") from exc
    return validate_paper_ledger(_migrate_legacy_ledger(source))


def write_json_atomic(path: Path, document: Mapping[str, Any]) -> bool:
    """Atomically replace a strict-JSON document, avoiding no-op rewrites."""
    rendered = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return True


def _candidate_rows(
    live_payload: Mapping[str, Any], now: datetime
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    ranking = live_payload.get("paper_candidate_ranking")
    if not isinstance(ranking, Mapping):
        raise ValueError("live payload has no paper_candidate_ranking object")
    if ranking.get("schema_version") != RANKING_SCHEMA_VERSION:
        raise ValueError("unsupported paper candidate ranking schema")
    if ranking.get("status") != "PAPER_ONLY" or ranking.get("real_money_execution") is not False:
        raise ValueError("candidate ranking must be PAPER_ONLY")
    rows = ranking.get("candidates")
    if not isinstance(rows, list):
        raise ValueError("paper candidate ranking candidates must be a list")
    raw_ids = [
        str(row.get("fixture_id"))
        for row in rows
        if isinstance(row, Mapping) and row.get("fixture_id") is not None
    ]
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("paper ranking contains more than one candidate for a match")
    accepted: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}

    def reject(reason: str) -> None:
        rejections[reason] = rejections.get(reason, 0) + 1

    for source in rows:
        if not isinstance(source, Mapping):
            reject("invalid_candidate")
            continue
        try:
            fixture_id = _text(source.get("fixture_id"), "fixture_id")
            kickoff = _timestamp(source.get("kickoff_utc"), "kickoff_utc")
            quoted = _timestamp(source.get("quote_captured_at"), "quote_captured_at")
            odds = _number(source.get("odds"), "odds", minimum=1, strict_minimum=True)
            model = _number(
                source.get("model_probability"), "model_probability", minimum=0, maximum=1
            )
            market = _number(
                source.get("break_even_probability"), "break_even_probability",
                minimum=0, maximum=1,
            )
            point_edge = _number(source.get("point_edge"), "point_edge")
            robust_edge = _number(source.get("robust_edge"), "robust_edge")
            quality = _number(
                source.get("data_quality_score"), "data_quality_score", minimum=0,
                maximum=100,
            )
            outcome = _text(source.get("outcome"), "outcome")
            market_kind = canonical_market(source.get("market"))
            line_value = source.get("line")
            if market_kind == "totals" and outcome in {"over_2_5", "under_2_5"}:
                outcome = "over" if outcome.startswith("over") else "under"
                line_value = 2.5
            if market_kind == "btts" and outcome in {"btts_yes", "btts_no"}:
                outcome = "yes" if outcome.endswith("yes") else "no"
            if market_kind not in SUPPORTED_SCORE_MARKETS:
                raise ValueError("unsupported market")
            line = (
                supported_line(line_value)
                if market_kind in {"totals", "team_totals", "asian_handicap"}
                else None
            )
            if market_kind in {"totals", "team_totals", "asian_handicap"} and line is None:
                raise ValueError("unsupported line")
            settle_score_market(
                market=market_kind,
                selection=outcome,
                line=line,
                home_goals=0,
                away_goals=0,
            )
            bookmaker = _text(source.get("bookmaker"), "bookmaker")
            quote_source = _text(source.get("quote_source"), "quote_source")
        except ValueError:
            reject("invalid_candidate")
            continue
        if source.get("status") != "PAPER_ONLY" or source.get("real_money_eligible") is not False:
            reject("not_paper_only")
            continue
        if source.get("market_period") != "REGULATION_90_MINUTES":
            reject("unsupported_market")
            continue
        if abs(market - 1.0 / odds) > 1e-9:
            reject("inconsistent_break_even_probability")
            continue
        if (
            model <= market
            or abs((model * odds - 1.0) - point_edge) > 1e-8
            or point_edge < .03
            or robust_edge <= 0
            or quality < 60
        ):
            reject("strict_edge_or_quality_filter")
            continue
        if quoted > now or quoted >= kickoff:
            reject("invalid_quote_time")
            continue
        if now - quoted > MAX_QUOTE_AGE:
            reject("stale_quote")
            continue
        if now >= kickoff:
            reject("match_already_started")
            continue
        accepted.append({
            "fixture_id": fixture_id,
            "competition": source.get("competition"),
            "stage": source.get("stage"),
            "kickoff_utc": _iso(kickoff),
            "home": source.get("home"),
            "away": source.get("away"),
            "selection": source.get("selection"),
            "outcome": outcome,
            "market": market_kind,
            "line": line,
            "model_probability": model,
            "bookmaker_probability": market,
            "odds": odds,
            "bookmaker": bookmaker,
            "bookmaker_key": source.get("bookmaker_key"),
            "quote_source": quote_source,
            "quote_captured_at": _iso(quoted),
            "data_quality_score": quality,
            "point_edge": point_edge,
            "robust_edge": robust_edge,
            "market_period": "REGULATION_90_MINUTES",
        })
    accepted.sort(key=lambda row: (row["kickoff_utc"], row["fixture_id"]))
    return accepted, dict(sorted(rejections.items()))


def _prospective_results(source: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if source is None:
        return {}
    if source.get("schema_version") != PROSPECTIVE_SCHEMA_VERSION:
        raise ValueError("unsupported prospective CLV ledger schema")
    policy = source.get("policy")
    if not isinstance(policy, Mapping) or policy.get("market") != "1X2":
        raise ValueError("prospective result source is not regulation 1X2")
    fixtures = source.get("fixtures")
    if not isinstance(fixtures, Mapping):
        raise ValueError("prospective CLV fixtures must be an object")
    output: dict[str, dict[str, Any]] = {}
    for fixture_id, entry in fixtures.items():
        if not isinstance(entry, Mapping):
            raise ValueError("prospective fixture must be an object")
        result = entry.get("result")
        if result is None:
            continue
        if not isinstance(result, Mapping):
            raise ValueError("prospective fixture result must be an object")
        home, away, outcome = (
            result.get("home_goals_90"), result.get("away_goals_90"), result.get("outcome")
        )
        if (
            isinstance(home, bool) or isinstance(away, bool)
            or not isinstance(home, int) or not isinstance(away, int)
            or home < 0 or away < 0
        ):
            raise ValueError("prospective result must contain non-negative 90-minute goals")
        expected = "home" if home > away else "away" if away > home else "draw"
        if outcome != expected:
            raise ValueError("prospective result outcome conflicts with its goals")
        output[str(fixture_id)] = {
            "outcome": expected,
            "home_goals_90": home,
            "away_goals_90": away,
            "source": "prospective_clv_official_result",
        }
    return output


def _explicit_results(source: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if source is None:
        return {}
    rows: Mapping[str, Any]
    source_name = "explicit_official_results_map"
    if source.get("schema_version") == RESULTS_SCHEMA_VERSION:
        if source.get("status") != "OFFICIAL_RESULTS" or not isinstance(source.get("results"), Mapping):
            raise ValueError("paper results document must be OFFICIAL_RESULTS")
        rows = source["results"]
        source_name = _text(source.get("source"), "results source")
    elif "schema_version" in source:
        raise ValueError(f"unsupported paper results schema: {source.get('schema_version')}")
    else:
        rows = source
    output: dict[str, dict[str, Any]] = {}
    for fixture_id, value in rows.items():
        home: int | None = None
        away: int | None = None
        if isinstance(value, str):
            outcome = value
        elif isinstance(value, Mapping):
            if value.get("status") not in {None, "FINISHED"}:
                continue
            outcome = value.get("outcome")
            home, away = value.get("home_goals_90"), value.get("away_goals_90")
            if home is not None or away is not None:
                if (
                    isinstance(home, bool) or isinstance(away, bool)
                    or not isinstance(home, int) or not isinstance(away, int)
                    or home < 0 or away < 0
                ):
                    raise ValueError("explicit result goals must be non-negative integers")
                expected = "home" if home > away else "away" if away > home else "draw"
                if outcome != expected:
                    raise ValueError("explicit result outcome conflicts with its goals")
        else:
            raise ValueError("explicit result must be an outcome or result object")
        if outcome not in OUTCOMES:
            raise ValueError("explicit result outcome must be home, draw, or away")
        output[str(fixture_id)] = {
            "outcome": str(outcome),
            "home_goals_90": home,
            "away_goals_90": away,
            "source": source_name,
        }
    return output


def _merge_results(
    prospective: Mapping[str, dict[str, Any]], explicit: Mapping[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    output = dict(prospective)
    for fixture_id, row in explicit.items():
        existing = output.get(fixture_id)
        if existing is not None:
            conflict = existing["outcome"] != row["outcome"]
            for field in ("home_goals_90", "away_goals_90"):
                if existing.get(field) is not None and row.get(field) is not None:
                    conflict = conflict or existing[field] != row[field]
            if conflict:
                raise ValueError(f"conflicting official results for fixture {fixture_id}")
        output[fixture_id] = row if existing is None else existing
    return output


def _closing_odds(
    prospective: Mapping[str, Any] | None,
    enrollment: Mapping[str, Any],
) -> float | None:
    if prospective is None:
        return None
    policy = prospective.get("policy")
    if not isinstance(policy, Mapping) or policy.get("closing_benchmark") != "pinnacle":
        return None
    entry = prospective.get("fixtures", {}).get(enrollment["fixture_id"])
    if not isinstance(entry, Mapping):
        return None
    closing, clv = entry.get("closing"), entry.get("clv")
    if not isinstance(closing, Mapping) or not isinstance(clv, Mapping):
        return None
    if (
        closing.get("benchmark") != "pinnacle"
        or closing.get("method") != "pinnacle_proportional_devig"
        or closing.get("evaluation_tier") != "confirmatory"
        or closing.get("bookmakers") != ["pinnacle"]
        or clv.get("status") != "ready"
        or clv.get("evaluation_tier") not in {None, "confirmatory"}
    ):
        return None
    probabilities = closing.get("probabilities")
    if not isinstance(probabilities, Mapping) or set(probabilities) != set(OUTCOMES):
        return None
    try:
        parsed = {
            outcome: _number(probabilities[outcome], f"closing {outcome}", minimum=0,
                             maximum=1, strict_minimum=True)
            for outcome in OUTCOMES
        }
        captured = _timestamp(closing.get("snapshot_at"), "closing.snapshot_at")
        kickoff = _timestamp(enrollment["kickoff_utc"], "kickoff_utc")
    except ValueError:
        return None
    if abs(sum(parsed.values()) - 1.0) > 1e-6:
        return None
    if captured >= kickoff or kickoff - captured > timedelta(minutes=60):
        return None
    return 1.0 / parsed[enrollment["outcome"]]


def update_paper_ledger(
    ledger: Mapping[str, Any],
    live_payload: Mapping[str, Any],
    *,
    now: str | datetime,
    prospective_ledger: Mapping[str, Any] | None = None,
    official_results: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Settle existing paper bets, then enroll eligible future candidates.

    The update is transactional in memory: every input is validated before the
    caller atomically replaces the on-disk ledger.
    """
    document = validate_paper_ledger(ledger)
    when = _timestamp(now, "now")
    if when < _timestamp(document["updated_at"], "updated_at"):
        raise ValueError("now cannot precede the ledger updated_at")
    candidates, candidate_rejections = _candidate_rows(live_payload, when)
    prospective_results = _prospective_results(prospective_ledger)
    explicit_results = _explicit_results(official_results)
    results = _merge_results(prospective_results, explicit_results)
    simulators = _simulators(document)
    newly_settled: list[str] = []
    newly_enrolled: list[str] = []

    for fixture_id in sorted(document["enrollments"]):
        if fixture_id in document["settlements"] or fixture_id not in results:
            continue
        enrollment = document["enrollments"][fixture_id]
        if when < _timestamp(enrollment["kickoff_utc"], "kickoff_utc"):
            raise ValueError(f"official result for {fixture_id} precedes kickoff")
        match_result = results[fixture_id]
        outcome = match_result["outcome"]
        home_goals = match_result.get("home_goals_90")
        away_goals = match_result.get("away_goals_90")
        if home_goals is None or away_goals is None:
            if enrollment["market"] != "1x2":
                continue
            selection_result = "win" if enrollment["outcome"] == outcome else "loss"
        else:
            selection_result = settle_score_market(
                market=enrollment["market"],
                selection=enrollment["outcome"],
                line=enrollment["line"],
                home_goals=home_goals,
                away_goals=away_goals,
            )
        close = (
            _closing_odds(prospective_ledger, enrollment)
            if enrollment["market"] == "1x2"
            else None
        )
        strategy_results: dict[str, str] = {}
        for strategy_id, action in enrollment["strategy_actions"].items():
            if not action["accepted"]:
                strategy_results[strategy_id] = "not_placed"
                continue
            simulators[strategy_id].settle_bet(
                bet_id=action["bet_id"],
                result=selection_result,
                closing_odds=close,
                timestamp=when,
                event_id=_stable_id("event:settled", strategy_id, fixture_id),
            )
            strategy_results[strategy_id] = selection_result
        document["settlements"][fixture_id] = {
            "fixture_id": fixture_id,
            "outcome": outcome,
            "home_goals_90": home_goals,
            "away_goals_90": away_goals,
            "market": enrollment["market"],
            "selection": enrollment["outcome"],
            "line": enrollment["line"],
            "selection_result": selection_result,
            "settled_at": _iso(when),
            "result_source": results[fixture_id]["source"],
            "closing_benchmark": "pinnacle_fair_1x2" if close is not None else None,
            "closing_odds": close,
            "strategy_results": strategy_results,
        }
        newly_settled.append(fixture_id)

    existing = set(document["enrollments"])
    for candidate in candidates:
        fixture_id = candidate["fixture_id"]
        if fixture_id in existing:
            continue
        actions: dict[str, Any] = {}
        for strategy_id, simulator in simulators.items():
            decision = simulator.quote(
                odds=candidate["odds"],
                model_probability=candidate["model_probability"],
                bookmaker_probability=candidate["bookmaker_probability"],
            )
            bet_id = _stable_id(
                "paper-bet", strategy_id, fixture_id, candidate["market"],
                candidate["outcome"], candidate["line"],
            )
            placed = simulator.place_bet(
                bet_id=bet_id,
                match_id=fixture_id,
                odds=candidate["odds"],
                model_probability=candidate["model_probability"],
                bookmaker_probability=candidate["bookmaker_probability"],
                timestamp=when,
                event_id=_stable_id(
                    "event:placed", strategy_id, fixture_id, candidate["market"],
                    candidate["outcome"], candidate["line"],
                ),
            )
            actions[strategy_id] = {
                "accepted": placed is not None,
                "bet_id": bet_id if placed is not None else None,
                "reason": decision.reason,
                "stake_rub": decision.stake_rub if placed is not None else 0.0,
                "stake_fraction": decision.stake_fraction if placed is not None else 0.0,
            }
        document["enrollments"][fixture_id] = {
            **candidate,
            "enrolled_at": _iso(when),
            "strategy_actions": actions,
        }
        existing.add(fixture_id)
        newly_enrolled.append(fixture_id)

    changed = bool(newly_enrolled or newly_settled)
    if changed:
        document["updated_at"] = _iso(when)
        _serialize_simulators(document, simulators)
        update_id = _stable_id(
            "paper-update", _iso(when), sorted(newly_enrolled), sorted(newly_settled)
        )
        if not any(row["update_id"] == update_id for row in document["update_history"]):
            document["update_history"].append({
                "update_id": update_id,
                "updated_at": _iso(when),
                "enrolled_fixture_ids": sorted(newly_enrolled),
                "settled_fixture_ids": sorted(newly_settled),
                "event_count_after": sum(len(sim.events) for sim in simulators.values()),
            })
        document["paper_trading"] = _public_summary_unvalidated(document, simulators)
    validated = validate_paper_ledger(document)
    return validated, {
        "status": "updated" if changed else "unchanged",
        "enrolled": len(newly_enrolled),
        "settled": len(newly_settled),
        "candidate_rejections": candidate_rejections,
    }


def public_paper_summary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated defensive copy suitable for the public site payload."""
    return deepcopy(validate_paper_ledger(ledger)["paper_trading"])


def read_json_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {name}: {exc}") from exc
    if not isinstance(source, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(source)
