"""Contract tests for the offline, event-sourced PAPER simulator."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from xgedge.simulation.paper import (
    PAPER_ONLY,
    STARTING_BALANCE_RUB,
    TARGET_BALANCE_RUB,
    BetPlaced,
    ConservativeEdgeStrategy,
    CycleStarted,
    FlatOnePercentStrategy,
    FractionalKellyStrategy,
    PaperSimulator,
    SettlementResult,
    preregistered_score,
    rank_strategies,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sim(strategy=None, **kwargs) -> PaperSimulator:
    return PaperSimulator(
        strategy or FlatOnePercentStrategy(), started_at=T0, **kwargs
    )


def test_contract_is_explicitly_paper_only_with_fixed_balances() -> None:
    sim = _sim()
    assert PAPER_ONLY is True
    assert sim.available_balance_rub == STARTING_BALANCE_RUB == 10_000.0
    assert sim.metrics().diagnostic_target_rub == TARGET_BALANCE_RUB == 1_000_000.0


def test_flat_strategy_places_exactly_one_percent_and_reserves_cash() -> None:
    sim = _sim()
    bet = sim.place_bet(
        bet_id="b1", match_id="m1", odds=2.0, model_probability=0.20,
        timestamp=T0 + timedelta(minutes=1),
    )
    assert bet is not None
    assert bet.stake_rub == 100.0
    assert sim.available_balance_rub == 9_900.0
    assert sim.equity_balance_rub == 10_000.0
    assert sim.active_cycle.open_bets == 1


def test_fractional_kelly_is_quarter_kelly_capped_at_one_percent() -> None:
    sim = _sim(FractionalKellyStrategy())
    capped = sim.quote(odds=2.0, model_probability=0.90)
    assert capped.accepted
    assert capped.stake_fraction == pytest.approx(0.01)
    assert capped.stake_rub == 100.0

    no_edge = sim.quote(odds=2.0, model_probability=0.40)
    assert not no_edge.accepted
    assert no_edge.stake_rub == 0.0
    assert sim.place_bet(
        bet_id="filtered", odds=2.0, model_probability=0.40,
        timestamp=T0 + timedelta(minutes=1),
    ) is None
    assert len(sim.events) == 1


def test_strategy_cannot_be_configured_above_hard_one_percent_cap() -> None:
    with pytest.raises(ValueError, match="0.01"):
        FractionalKellyStrategy(hard_cap=0.011)
    with pytest.raises(ValueError, match="0.01"):
        ConservativeEdgeStrategy(stake_fraction=0.011)


def test_conservative_strategy_requires_strict_probability_edge() -> None:
    sim = _sim(ConservativeEdgeStrategy(min_probability_edge=0.05))
    rejected = sim.quote(
        odds=2.0, model_probability=0.54, bookmaker_probability=0.50
    )
    accepted = sim.quote(
        odds=2.0, model_probability=0.56, bookmaker_probability=0.50
    )
    assert not rejected.accepted
    assert accepted.accepted
    assert accepted.stake_fraction == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("result", "expected_balance", "counter"),
    [
        (SettlementResult.WIN, 10_100.0, "wins"),
        (SettlementResult.LOSS, 9_900.0, "losses"),
        (SettlementResult.PUSH, 10_000.0, "pushes"),
        (SettlementResult.VOID, 10_000.0, "voids"),
    ],
)
def test_settlement_results(result, expected_balance, counter) -> None:
    sim = _sim()
    sim.place_bet(
        bet_id="b1", odds=2.0, model_probability=0.60,
        timestamp=T0 + timedelta(minutes=1),
    )
    sim.settle_bet(
        bet_id="b1", result=result, timestamp=T0 + timedelta(minutes=2)
    )
    metrics = sim.metrics()
    assert sim.equity_balance_rub == expected_balance
    assert getattr(metrics, counter) == 1
    assert metrics.settled_bets == 1


def test_metrics_cover_pnl_roi_drawdown_log_growth_and_clv() -> None:
    sim = _sim()
    sim.place_bet(
        bet_id="win", odds=2.0, model_probability=0.60,
        timestamp=T0 + timedelta(minutes=1),
    )
    sim.settle_bet(
        bet_id="win", result="win", closing_odds=1.8,
        timestamp=T0 + timedelta(minutes=2),
    )
    sim.place_bet(
        bet_id="loss", odds=2.0, model_probability=0.60,
        timestamp=T0 + timedelta(minutes=3),
    )
    sim.settle_bet(
        bet_id="loss", result="loss", closing_odds=2.0,
        timestamp=T0 + timedelta(minutes=4),
    )

    metrics = sim.metrics()
    # +100, then -101 => -1 on 201 total stake.
    assert metrics.pnl_rub == -1.0
    assert metrics.total_staked_rub == 201.0
    assert metrics.roi == pytest.approx(-1.0 / 201.0)
    assert metrics.max_drawdown == pytest.approx(101.0 / 10_100.0)
    assert metrics.log_growth == pytest.approx(__import__("math").log(9_999 / 10_000))
    assert metrics.mean_clv == pytest.approx(((2.0 / 1.8 - 1.0) + 0.0) / 2)


def test_ruin_starts_fresh_10k_cycle_without_erasing_history() -> None:
    sim = _sim(ruin_threshold_rub=9_900.0)
    sim.place_bet(
        bet_id="ruin-me", odds=2.0, model_probability=0.60,
        timestamp=T0 + timedelta(minutes=1), event_id="place-1",
    )
    sim.settle_bet(
        bet_id="ruin-me", result="loss", timestamp=T0 + timedelta(minutes=2),
        event_id="settle-1",
    )

    assert len(sim.cycles) == 2
    assert sim.cycles[0].ruined
    assert sim.cycles[0].equity_balance_rub == 9_900.0
    assert sim.cycles[1].starting_balance_rub == 10_000.0
    assert sim.equity_balance_rub == 10_000.0
    assert [event.kind.value for event in sim.events] == [
        "cycle_started", "bet_placed", "bet_settled", "ruin_observed",
        "cycle_started",
    ]
    metrics = sim.metrics()
    assert metrics.pnl_rub == -100.0
    assert metrics.ruin_count == 1
    assert metrics.ruin_rate == pytest.approx(0.5)


def test_million_target_is_observed_but_does_not_end_cycle() -> None:
    sim = _sim()
    sim.place_bet(
        bet_id="longshot", odds=10_000.0, model_probability=0.01,
        timestamp=T0 + timedelta(minutes=1),
    )
    sim.settle_bet(
        bet_id="longshot", result="win", timestamp=T0 + timedelta(minutes=2)
    )
    assert sim.equity_balance_rub == 1_009_900.0
    assert sim.metrics().target_hit_count == 1
    assert len(sim.cycles) == 1
    assert sim.place_bet(
        bet_id="still-running", odds=2.0, model_probability=0.5,
        timestamp=T0 + timedelta(minutes=3),
    ) is not None


def test_events_are_frozen_and_log_is_exposed_as_tuple() -> None:
    sim = _sim()
    event = sim.events[0]
    assert isinstance(event, CycleStarted)
    assert isinstance(sim.events, tuple)
    with pytest.raises(FrozenInstanceError):
        event.cycle_id = "tampered"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("odds", 1.0),
        ("odds", float("nan")),
        ("model_probability", -0.01),
        ("model_probability", 1.01),
        ("bookmaker_probability", float("inf")),
    ],
)
def test_invalid_odds_and_probabilities_are_rejected(field, value) -> None:
    sim = _sim()
    kwargs = {"odds": 2.0, "model_probability": 0.6, field: value}
    with pytest.raises(ValueError):
        sim.place_bet(bet_id="b1", timestamp=T0 + timedelta(minutes=1), **kwargs)


def test_invalid_stake_event_and_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="stake_rub"):
        BetPlaced(
            event_id="e", timestamp=T0, strategy_id="s", cycle_id="c",
            bet_id="b", match_id="m", odds=2.0, model_probability=0.5,
            stake_rub=0.0,
        )
    sim = _sim()
    with pytest.raises(ValueError, match="timezone-aware"):
        sim.place_bet(
            bet_id="b1", odds=2.0, model_probability=0.5,
            timestamp=datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="precede"):
        sim.place_bet(
            bet_id="b2", odds=2.0, model_probability=0.5,
            timestamp=T0 - timedelta(seconds=1),
        )


def test_event_and_bet_ids_must_be_unique_and_settlement_is_once_only() -> None:
    sim = _sim(first_event_id="start")
    sim.place_bet(
        bet_id="b1", odds=2.0, model_probability=0.5,
        timestamp=T0 + timedelta(minutes=1), event_id="place",
    )
    with pytest.raises(ValueError, match="duplicate event_id"):
        sim.place_bet(
            bet_id="b2", odds=2.0, model_probability=0.5,
            timestamp=T0 + timedelta(minutes=2), event_id="place",
        )
    with pytest.raises(ValueError, match="duplicate bet_id"):
        sim.place_bet(
            bet_id="b1", odds=2.0, model_probability=0.5,
            timestamp=T0 + timedelta(minutes=2),
        )
    sim.settle_bet(
        bet_id="b1", result="void", timestamp=T0 + timedelta(minutes=3)
    )
    with pytest.raises(ValueError, match="already settled"):
        sim.settle_bet(
            bet_id="b1", result="win", timestamp=T0 + timedelta(minutes=4)
        )
    with pytest.raises(ValueError, match="duplicate event_id"):
        PaperSimulator.from_events(
            FlatOnePercentStrategy(), [sim.events[0], sim.events[0]]
        )


def test_restore_from_event_log_reproduces_state() -> None:
    sim = _sim()
    sim.place_bet(
        bet_id="b1", odds=2.2, model_probability=0.6,
        timestamp=T0 + timedelta(minutes=1),
    )
    sim.settle_bet(
        bet_id="b1", result="win", timestamp=T0 + timedelta(minutes=2)
    )
    restored = PaperSimulator.from_events(FlatOnePercentStrategy(), sim.events)
    assert restored.events == sim.events
    assert restored.cycles == sim.cycles
    assert restored.metrics() == sim.metrics()


def test_open_stake_is_not_counted_in_realized_roi_denominator() -> None:
    sim = _sim()
    sim.place_bet(
        bet_id="open", odds=2.0, model_probability=0.6,
        timestamp=T0 + timedelta(minutes=1),
    )
    assert sim.active_cycle.total_staked_rub == 100.0
    assert sim.metrics().total_staked_rub == 0.0
    assert sim.metrics().roi == 0.0


def test_ranking_penalizes_ruin_and_ignores_fastest_to_million() -> None:
    steady = _sim(FlatOnePercentStrategy(strategy_id="steady"))
    steady.place_bet(
        bet_id="s", odds=2.0, model_probability=0.6,
        timestamp=T0 + timedelta(minutes=1),
    )
    steady.settle_bet(
        bet_id="s", result="win", timestamp=T0 + timedelta(minutes=2)
    )

    ruined = _sim(
        FlatOnePercentStrategy(strategy_id="ruined"), ruin_threshold_rub=9_900.0
    )
    ruined.place_bet(
        bet_id="r", odds=2.0, model_probability=0.6,
        timestamp=T0 + timedelta(minutes=1),
    )
    ruined.settle_bet(
        bet_id="r", result="loss", timestamp=T0 + timedelta(minutes=2)
    )

    ranking = rank_strategies([ruined, steady])
    assert [row.strategy_id for row in ranking] == ["steady", "ruined"]
    metrics = steady.metrics()
    headline_changed = replace(metrics, target_hit_count=999)
    assert preregistered_score(metrics) == preregistered_score(headline_changed)
