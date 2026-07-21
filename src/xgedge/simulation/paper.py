"""Append-only PAPER betting simulation with conservative risk controls.

This module deliberately has no bookmaker adapter, payment API, HTTP client, or
other route to a real-money transaction.  It records immutable paper events and
derives every balance and metric by replaying that event log.

The RUB 1,000,000 target is diagnostic only.  It never changes stake sizing,
cycle lifetime, or strategy ranking.  Ranking uses the fixed, documented score
in :class:`PreregisteredScorePolicy`, not speed to a headline balance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable, Protocol, TypeAlias
from uuid import uuid4

STARTING_BALANCE_RUB = 10_000.0
TARGET_BALANCE_RUB = 1_000_000.0
PAPER_ONLY = True
_MAX_STAKE_FRACTION = 0.01
_MIN_LOG_BALANCE_RUB = 0.01


def _finite_number(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _positive(value: float, name: str) -> float:
    number = _finite_number(value, name)
    if number <= 0.0:
        raise ValueError(f"{name} must be > 0")
    return number


def _probability(value: float, name: str) -> float:
    number = _finite_number(value, name)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return number


def _odds(value: float, name: str = "odds") -> float:
    number = _finite_number(value, name)
    if number <= 1.0:
        raise ValueError(f"{name} must be > 1.0")
    return number


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _aware_timestamp(value: datetime, name: str = "timestamp") -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _money(value: float) -> float:
    """Round a paper amount to kopecks, avoiding binary negative zero."""
    rounded = round(float(value) + 1e-12, 2)
    return 0.0 if rounded == 0.0 else rounded


class EventKind(str, Enum):
    CYCLE_STARTED = "cycle_started"
    BET_PLACED = "bet_placed"
    BET_SETTLED = "bet_settled"
    TARGET_OBSERVED = "target_observed"
    RUIN_OBSERVED = "ruin_observed"


class SettlementResult(str, Enum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


@dataclass(frozen=True, slots=True, kw_only=True)
class CycleStarted:
    event_id: str
    timestamp: datetime
    strategy_id: str
    cycle_id: str
    starting_balance_rub: float = STARTING_BALANCE_RUB
    kind: EventKind = field(default=EventKind.CYCLE_STARTED, init=False)

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _aware_timestamp(self.timestamp)
        _identifier(self.strategy_id, "strategy_id")
        _identifier(self.cycle_id, "cycle_id")
        amount = _positive(self.starting_balance_rub, "starting_balance_rub")
        if amount != STARTING_BALANCE_RUB:
            raise ValueError(
                f"every paper cycle must start at {STARTING_BALANCE_RUB:.2f} RUB"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class BetPlaced:
    event_id: str
    timestamp: datetime
    strategy_id: str
    cycle_id: str
    bet_id: str
    match_id: str
    odds: float
    model_probability: float
    stake_rub: float
    bookmaker_probability: float | None = None
    kind: EventKind = field(default=EventKind.BET_PLACED, init=False)

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _aware_timestamp(self.timestamp)
        _identifier(self.strategy_id, "strategy_id")
        _identifier(self.cycle_id, "cycle_id")
        _identifier(self.bet_id, "bet_id")
        _identifier(self.match_id, "match_id")
        _odds(self.odds)
        _probability(self.model_probability, "model_probability")
        _positive(self.stake_rub, "stake_rub")
        if self.bookmaker_probability is not None:
            _probability(self.bookmaker_probability, "bookmaker_probability")


@dataclass(frozen=True, slots=True, kw_only=True)
class BetSettled:
    event_id: str
    timestamp: datetime
    strategy_id: str
    cycle_id: str
    bet_id: str
    result: SettlementResult
    closing_odds: float | None = None
    kind: EventKind = field(default=EventKind.BET_SETTLED, init=False)

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _aware_timestamp(self.timestamp)
        _identifier(self.strategy_id, "strategy_id")
        _identifier(self.cycle_id, "cycle_id")
        _identifier(self.bet_id, "bet_id")
        if not isinstance(self.result, SettlementResult):
            raise ValueError("result must be a SettlementResult")
        if self.closing_odds is not None:
            _odds(self.closing_odds, "closing_odds")


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetObserved:
    event_id: str
    timestamp: datetime
    strategy_id: str
    cycle_id: str
    balance_rub: float
    target_balance_rub: float = TARGET_BALANCE_RUB
    kind: EventKind = field(default=EventKind.TARGET_OBSERVED, init=False)

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _aware_timestamp(self.timestamp)
        _identifier(self.strategy_id, "strategy_id")
        _identifier(self.cycle_id, "cycle_id")
        balance = _finite_number(self.balance_rub, "balance_rub")
        if balance < TARGET_BALANCE_RUB:
            raise ValueError("target event balance must be at least 1,000,000 RUB")
        if self.target_balance_rub != TARGET_BALANCE_RUB:
            raise ValueError("the diagnostic target is fixed at 1,000,000 RUB")


@dataclass(frozen=True, slots=True, kw_only=True)
class RuinObserved:
    event_id: str
    timestamp: datetime
    strategy_id: str
    cycle_id: str
    balance_rub: float
    kind: EventKind = field(default=EventKind.RUIN_OBSERVED, init=False)

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _aware_timestamp(self.timestamp)
        _identifier(self.strategy_id, "strategy_id")
        _identifier(self.cycle_id, "cycle_id")
        balance = _finite_number(self.balance_rub, "balance_rub")
        if balance < 0.0:
            raise ValueError("balance_rub cannot be negative")


PaperEvent: TypeAlias = (
    CycleStarted | BetPlaced | BetSettled | TargetObserved | RuinObserved
)


@dataclass(frozen=True, slots=True)
class StakeDecision:
    strategy_id: str
    accepted: bool
    stake_rub: float
    stake_fraction: float
    probability_edge: float
    expected_value: float
    reason: str


class PaperStrategy(Protocol):
    strategy_id: str

    def recommended_fraction(
        self,
        model_probability: float,
        odds: float,
        market_probability: float,
    ) -> float:
        """Return a bankroll fraction; zero means no paper bet."""


@dataclass(frozen=True, slots=True)
class FlatOnePercentStrategy:
    """Stake exactly 1% of available paper bankroll on every input signal."""

    strategy_id: str = "flat_1pct"

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, "strategy_id")

    def recommended_fraction(
        self,
        model_probability: float,
        odds: float,
        market_probability: float,
    ) -> float:
        _probability(model_probability, "model_probability")
        _odds(odds)
        _probability(market_probability, "market_probability")
        return _MAX_STAKE_FRACTION


@dataclass(frozen=True, slots=True)
class FractionalKellyStrategy:
    """Quarter Kelly, with an inviolable maximum of 1% of bankroll."""

    strategy_id: str = "fractional_kelly_025"
    kelly_fraction: float = 0.25
    hard_cap: float = _MAX_STAKE_FRACTION

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, "strategy_id")
        fraction = _finite_number(self.kelly_fraction, "kelly_fraction")
        cap = _finite_number(self.hard_cap, "hard_cap")
        if not 0.0 < fraction <= 1.0:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if not 0.0 < cap <= _MAX_STAKE_FRACTION:
            raise ValueError("hard_cap must be in (0, 0.01]")

    def recommended_fraction(
        self,
        model_probability: float,
        odds: float,
        market_probability: float,
    ) -> float:
        probability = _probability(model_probability, "model_probability")
        decimal_odds = _odds(odds)
        _probability(market_probability, "market_probability")
        expected_value = probability * decimal_odds - 1.0
        if expected_value <= 0.0:
            return 0.0
        full_kelly = expected_value / (decimal_odds - 1.0)
        return min(self.kelly_fraction * full_kelly, self.hard_cap)


@dataclass(frozen=True, slots=True)
class ConservativeEdgeStrategy:
    """Only flat-bet signals clearing a strict probability-edge threshold."""

    strategy_id: str = "conservative_edge_5pp"
    min_probability_edge: float = 0.05
    stake_fraction: float = _MAX_STAKE_FRACTION

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, "strategy_id")
        threshold = _finite_number(
            self.min_probability_edge, "min_probability_edge"
        )
        fraction = _finite_number(self.stake_fraction, "stake_fraction")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("min_probability_edge must be in (0, 1]")
        if not 0.0 < fraction <= _MAX_STAKE_FRACTION:
            raise ValueError("stake_fraction must be in (0, 0.01]")

    def recommended_fraction(
        self,
        model_probability: float,
        odds: float,
        market_probability: float,
    ) -> float:
        probability = _probability(model_probability, "model_probability")
        decimal_odds = _odds(odds)
        market = _probability(market_probability, "market_probability")
        probability_edge = probability - market
        expected_value = probability * decimal_odds - 1.0
        if (
            probability_edge + 1e-12 < self.min_probability_edge
            or expected_value <= 0.0
        ):
            return 0.0
        return self.stake_fraction


@dataclass(frozen=True, slots=True)
class CycleSnapshot:
    cycle_id: str
    cycle_number: int
    starting_balance_rub: float
    available_balance_rub: float
    equity_balance_rub: float
    realized_pnl_rub: float
    total_staked_rub: float
    settled_bets: int
    open_bets: int
    max_drawdown: float
    ruined: bool
    target_observed: bool


@dataclass(frozen=True, slots=True)
class PaperMetrics:
    strategy_id: str
    pnl_rub: float
    roi: float
    max_drawdown: float
    log_growth: float
    total_staked_rub: float
    settled_bets: int
    wins: int
    losses: int
    pushes: int
    voids: int
    cycle_count: int
    ruin_count: int
    ruin_rate: float
    mean_clv: float | None
    target_hit_count: int
    diagnostic_target_rub: float = TARGET_BALANCE_RUB


@dataclass(frozen=True, slots=True)
class PreregisteredScorePolicy:
    """Fixed score declared before results are observed.

    Positive performance is shrunk until 100 settled bets.  Drawdown and ruin
    penalties are not shrunk, preventing a lucky tiny sample from outranking a
    demonstrably safer strategy.  Diagnostic target hits are intentionally not
    an input.
    """

    roi_weight: float = 0.15
    log_growth_per_bet_weight: float = 0.20
    clv_weight: float = 0.65
    max_drawdown_penalty: float = 1.00
    ruin_rate_penalty: float = 2.00
    full_evidence_bets: int = 100

    def __post_init__(self) -> None:
        for name in (
            "roi_weight",
            "log_growth_per_bet_weight",
            "clv_weight",
            "max_drawdown_penalty",
            "ruin_rate_penalty",
        ):
            if _finite_number(getattr(self, name), name) < 0.0:
                raise ValueError(f"{name} cannot be negative")
        if (
            isinstance(self.full_evidence_bets, bool)
            or not isinstance(self.full_evidence_bets, int)
            or self.full_evidence_bets <= 0
        ):
            raise ValueError("full_evidence_bets must be a positive integer")


DEFAULT_SCORE_POLICY = PreregisteredScorePolicy()


@dataclass(frozen=True, slots=True)
class StrategyRanking:
    rank: int
    strategy_id: str
    score: float
    metrics: PaperMetrics


@dataclass(slots=True)
class _CycleState:
    cycle_id: str
    cycle_number: int
    starting_balance: float
    cash: float
    peak_equity: float
    max_drawdown: float = 0.0
    realized_pnl: float = 0.0
    total_staked: float = 0.0
    settled_stake: float = 0.0
    settled_bets: int = 0
    ruined: bool = False
    target_observed: bool = False
    open_bets: dict[str, BetPlaced] = field(default_factory=dict)

    @property
    def equity(self) -> float:
        return _money(self.cash + math.fsum(b.stake_rub for b in self.open_bets.values()))

    def observe_equity(self) -> None:
        equity = self.equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0.0:
            self.max_drawdown = max(
                self.max_drawdown,
                (self.peak_equity - equity) / self.peak_equity,
            )


@dataclass(slots=True)
class _ReplayState:
    cycles: list[_CycleState] = field(default_factory=list)
    placed_bet_ids: set[str] = field(default_factory=set)
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    voids: int = 0
    clvs: list[float] = field(default_factory=list)

    @property
    def current(self) -> _CycleState:
        if not self.cycles:
            raise ValueError("event log must start with CycleStarted")
        return self.cycles[-1]


def _settlement_pnl(bet: BetPlaced, result: SettlementResult) -> float:
    if result is SettlementResult.WIN:
        return _money(bet.stake_rub * (bet.odds - 1.0))
    if result is SettlementResult.LOSS:
        return -bet.stake_rub
    return 0.0


def _replay(events: Iterable[PaperEvent]) -> _ReplayState:
    state = _ReplayState()
    event_ids: set[str] = set()
    last_timestamp: datetime | None = None
    strategy_id: str | None = None

    for event in events:
        if event.event_id in event_ids:
            raise ValueError(f"duplicate event_id: {event.event_id!r}")
        event_ids.add(event.event_id)
        _aware_timestamp(event.timestamp)
        if last_timestamp is not None and event.timestamp < last_timestamp:
            raise ValueError("event timestamps must be non-decreasing")
        last_timestamp = event.timestamp
        if strategy_id is None:
            strategy_id = event.strategy_id
        elif event.strategy_id != strategy_id:
            raise ValueError("one event log cannot mix strategy ids")

        if isinstance(event, CycleStarted):
            if state.cycles and not state.current.ruined:
                raise ValueError("a new cycle may only follow a recorded ruin")
            expected_number = len(state.cycles) + 1
            state.cycles.append(
                _CycleState(
                    cycle_id=event.cycle_id,
                    cycle_number=expected_number,
                    starting_balance=event.starting_balance_rub,
                    cash=event.starting_balance_rub,
                    peak_equity=event.starting_balance_rub,
                )
            )
            continue

        cycle = state.current
        if event.cycle_id != cycle.cycle_id:
            raise ValueError("event cycle_id does not match the active cycle")
        if cycle.ruined:
            raise ValueError("no bet event may follow ruin before a new cycle")

        if isinstance(event, BetPlaced):
            if event.bet_id in state.placed_bet_ids:
                raise ValueError(f"duplicate bet_id: {event.bet_id!r}")
            if event.stake_rub > cycle.cash + 1e-9:
                raise ValueError("stake exceeds available paper balance")
            maximum_stake = _money(cycle.cash * _MAX_STAKE_FRACTION)
            if event.stake_rub > maximum_stake + 1e-9:
                raise ValueError("stake exceeds the hard 1% paper cap")
            state.placed_bet_ids.add(event.bet_id)
            cycle.cash = _money(cycle.cash - event.stake_rub)
            cycle.total_staked = _money(cycle.total_staked + event.stake_rub)
            cycle.open_bets[event.bet_id] = event
        elif isinstance(event, BetSettled):
            if event.bet_id not in cycle.open_bets:
                raise ValueError("bet is unknown or already settled")
            bet = cycle.open_bets.pop(event.bet_id)
            pnl = _settlement_pnl(bet, event.result)
            cycle.settled_stake = _money(cycle.settled_stake + bet.stake_rub)
            if event.result is SettlementResult.WIN:
                cycle.cash = _money(cycle.cash + bet.stake_rub * bet.odds)
                state.wins += 1
            elif event.result is SettlementResult.LOSS:
                state.losses += 1
            else:
                cycle.cash = _money(cycle.cash + bet.stake_rub)
                if event.result is SettlementResult.PUSH:
                    state.pushes += 1
                else:
                    state.voids += 1
            cycle.realized_pnl = _money(cycle.realized_pnl + pnl)
            cycle.settled_bets += 1
            cycle.observe_equity()
            if event.closing_odds is not None:
                state.clvs.append(bet.odds / event.closing_odds - 1.0)
        elif isinstance(event, TargetObserved):
            if cycle.target_observed:
                raise ValueError("target can only be observed once per cycle")
            if abs(event.balance_rub - cycle.equity) > 0.011:
                raise ValueError("target event balance does not match replayed equity")
            cycle.target_observed = True
        elif isinstance(event, RuinObserved):
            if cycle.open_bets:
                raise ValueError("cannot close a cycle while paper bets are open")
            if abs(event.balance_rub - cycle.equity) > 0.011:
                raise ValueError("ruin event balance does not match replayed equity")
            cycle.ruined = True
        else:  # pragma: no cover - the type alias is closed, guard corrupted input
            raise TypeError(f"unsupported paper event: {type(event)!r}")
    return state


class PaperSimulator:
    """Offline event-sourced simulator for one staking strategy."""

    def __init__(
        self,
        strategy: PaperStrategy,
        *,
        ruin_threshold_rub: float = 0.0,
        started_at: datetime | None = None,
        first_event_id: str | None = None,
    ) -> None:
        _identifier(strategy.strategy_id, "strategy.strategy_id")
        threshold = _finite_number(ruin_threshold_rub, "ruin_threshold_rub")
        if not 0.0 <= threshold < STARTING_BALANCE_RUB:
            raise ValueError("ruin_threshold_rub must be in [0, 10,000)")
        self.strategy = strategy
        self.ruin_threshold_rub = threshold
        self._events: list[PaperEvent] = []
        timestamp = _aware_timestamp(started_at or datetime.now(timezone.utc))
        self._append(
            CycleStarted(
                event_id=first_event_id or self._new_id(),
                timestamp=timestamp,
                strategy_id=strategy.strategy_id,
                cycle_id=self._cycle_id(1),
            )
        )

    @classmethod
    def from_events(
        cls,
        strategy: PaperStrategy,
        events: Iterable[PaperEvent],
        *,
        ruin_threshold_rub: float = 0.0,
    ) -> "PaperSimulator":
        """Restore a simulator by validating and replaying an existing log."""
        threshold = _finite_number(ruin_threshold_rub, "ruin_threshold_rub")
        if not 0.0 <= threshold < STARTING_BALANCE_RUB:
            raise ValueError("ruin_threshold_rub must be in [0, 10,000)")
        copied = list(events)
        if not copied:
            raise ValueError("event log cannot be empty")
        state = _replay(copied)
        if copied[0].strategy_id != strategy.strategy_id:
            raise ValueError("strategy does not match the event log")
        if state.current.ruined:
            raise ValueError("a ruined event log must include its replacement cycle")
        if any(
            isinstance(event, RuinObserved)
            and event.balance_rub > threshold + 1e-9
            for event in copied
        ):
            raise ValueError("ruin event balance exceeds ruin_threshold_rub")
        simulator = cls.__new__(cls)
        simulator.strategy = strategy
        simulator.ruin_threshold_rub = threshold
        simulator._events = copied
        return simulator

    @staticmethod
    def _new_id() -> str:
        return uuid4().hex

    def _cycle_id(self, cycle_number: int) -> str:
        return f"{self.strategy.strategy_id}:cycle:{cycle_number}"

    def _event_timestamp(self, timestamp: datetime | None) -> datetime:
        if timestamp is not None:
            value = _aware_timestamp(timestamp)
        else:
            value = datetime.now(timezone.utc)
            if self._events and value < self._events[-1].timestamp:
                value = self._events[-1].timestamp
        if self._events and value < self._events[-1].timestamp:
            raise ValueError("event timestamp cannot precede the previous event")
        return value

    def _append(self, event: PaperEvent) -> None:
        if any(existing.event_id == event.event_id for existing in self._events):
            raise ValueError(f"duplicate event_id: {event.event_id!r}")
        self._events.append(event)
        try:
            _replay(self._events)
        except Exception:
            self._events.pop()
            raise

    @property
    def events(self) -> tuple[PaperEvent, ...]:
        return tuple(self._events)

    @property
    def active_cycle(self) -> CycleSnapshot:
        return self.cycles[-1]

    @property
    def cycles(self) -> tuple[CycleSnapshot, ...]:
        state = _replay(self._events)
        return tuple(
            CycleSnapshot(
                cycle_id=cycle.cycle_id,
                cycle_number=cycle.cycle_number,
                starting_balance_rub=cycle.starting_balance,
                available_balance_rub=cycle.cash,
                equity_balance_rub=cycle.equity,
                realized_pnl_rub=cycle.realized_pnl,
                total_staked_rub=cycle.total_staked,
                settled_bets=cycle.settled_bets,
                open_bets=len(cycle.open_bets),
                max_drawdown=cycle.max_drawdown,
                ruined=cycle.ruined,
                target_observed=cycle.target_observed,
            )
            for cycle in state.cycles
        )

    @property
    def available_balance_rub(self) -> float:
        return self.active_cycle.available_balance_rub

    @property
    def equity_balance_rub(self) -> float:
        return self.active_cycle.equity_balance_rub

    def quote(
        self,
        *,
        odds: float,
        model_probability: float,
        bookmaker_probability: float | None = None,
    ) -> StakeDecision:
        decimal_odds = _odds(odds)
        probability = _probability(model_probability, "model_probability")
        market_probability = (
            1.0 / decimal_odds
            if bookmaker_probability is None
            else _probability(bookmaker_probability, "bookmaker_probability")
        )
        fraction = _finite_number(
            self.strategy.recommended_fraction(
                probability, decimal_odds, market_probability
            ),
            "recommended stake fraction",
        )
        if not 0.0 <= fraction <= _MAX_STAKE_FRACTION + 1e-12:
            raise ValueError("paper strategy stake must remain in [0, 0.01]")
        stake = _money(self.available_balance_rub * fraction)
        accepted = fraction > 0.0 and stake > 0.0
        reason = "accepted" if accepted else "strategy_filter"
        if fraction > 0.0 and stake == 0.0:
            reason = "below_one_kopeck"
        return StakeDecision(
            strategy_id=self.strategy.strategy_id,
            accepted=accepted,
            stake_rub=stake if accepted else 0.0,
            stake_fraction=fraction,
            probability_edge=probability - market_probability,
            expected_value=probability * decimal_odds - 1.0,
            reason=reason,
        )

    def place_bet(
        self,
        *,
        bet_id: str,
        odds: float,
        model_probability: float,
        match_id: str | None = None,
        bookmaker_probability: float | None = None,
        timestamp: datetime | None = None,
        event_id: str | None = None,
    ) -> BetPlaced | None:
        """Append a simulated bet, or return ``None`` when strategy filters it."""
        _identifier(bet_id, "bet_id")
        actual_match_id = match_id if match_id is not None else bet_id
        _identifier(actual_match_id, "match_id")
        state = _replay(self._events)
        if bet_id in state.placed_bet_ids:
            raise ValueError(f"duplicate bet_id: {bet_id!r}")
        decision = self.quote(
            odds=odds,
            model_probability=model_probability,
            bookmaker_probability=bookmaker_probability,
        )
        if not decision.accepted:
            return None
        event = BetPlaced(
            event_id=event_id or self._new_id(),
            timestamp=self._event_timestamp(timestamp),
            strategy_id=self.strategy.strategy_id,
            cycle_id=state.current.cycle_id,
            bet_id=bet_id,
            match_id=actual_match_id,
            odds=float(odds),
            model_probability=float(model_probability),
            bookmaker_probability=(
                None
                if bookmaker_probability is None
                else float(bookmaker_probability)
            ),
            stake_rub=decision.stake_rub,
        )
        self._append(event)
        return event

    def settle_bet(
        self,
        *,
        bet_id: str,
        result: SettlementResult | str,
        closing_odds: float | None = None,
        timestamp: datetime | None = None,
        event_id: str | None = None,
    ) -> BetSettled:
        """Settle one paper bet and apply diagnostic target/ruin observations."""
        _identifier(bet_id, "bet_id")
        try:
            normalized_result = (
                result
                if isinstance(result, SettlementResult)
                else SettlementResult(result)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("result must be win, loss, push, or void") from exc
        if closing_odds is not None:
            _odds(closing_odds, "closing_odds")
        state = _replay(self._events)
        cycle = state.current
        if bet_id not in cycle.open_bets:
            raise ValueError("bet is unknown or already settled")
        event_time = self._event_timestamp(timestamp)
        event = BetSettled(
            event_id=event_id or self._new_id(),
            timestamp=event_time,
            strategy_id=self.strategy.strategy_id,
            cycle_id=cycle.cycle_id,
            bet_id=bet_id,
            result=normalized_result,
            closing_odds=None if closing_odds is None else float(closing_odds),
        )
        self._append(event)

        state = _replay(self._events)
        cycle = state.current
        if cycle.equity >= TARGET_BALANCE_RUB and not cycle.target_observed:
            self._append(
                TargetObserved(
                    event_id=self._new_id(),
                    timestamp=event_time,
                    strategy_id=self.strategy.strategy_id,
                    cycle_id=cycle.cycle_id,
                    balance_rub=cycle.equity,
                )
            )
            state = _replay(self._events)
            cycle = state.current

        if (
            not cycle.open_bets
            and cycle.equity <= self.ruin_threshold_rub + 1e-9
        ):
            self._append(
                RuinObserved(
                    event_id=self._new_id(),
                    timestamp=event_time,
                    strategy_id=self.strategy.strategy_id,
                    cycle_id=cycle.cycle_id,
                    balance_rub=cycle.equity,
                )
            )
            self._append(
                CycleStarted(
                    event_id=self._new_id(),
                    timestamp=event_time,
                    strategy_id=self.strategy.strategy_id,
                    cycle_id=self._cycle_id(len(state.cycles) + 1),
                )
            )
        return event

    def metrics(self) -> PaperMetrics:
        state = _replay(self._events)
        cycles = state.cycles
        pnl = _money(math.fsum(c.realized_pnl for c in cycles))
        # ROI only uses resolved risk.  Open stakes remain visible in cycle
        # snapshots but cannot dilute or inflate realized return.
        total_staked = _money(math.fsum(c.settled_stake for c in cycles))
        roi = pnl / total_staked if total_staked > 0.0 else 0.0
        max_drawdown = max((c.max_drawdown for c in cycles), default=0.0)
        log_growth = math.fsum(
            math.log(max(c.equity, _MIN_LOG_BALANCE_RUB) / c.starting_balance)
            for c in cycles
        )
        settled = sum(c.settled_bets for c in cycles)
        ruin_count = sum(c.ruined for c in cycles)
        mean_clv = (
            math.fsum(state.clvs) / len(state.clvs) if state.clvs else None
        )
        return PaperMetrics(
            strategy_id=self.strategy.strategy_id,
            pnl_rub=pnl,
            roi=float(roi),
            max_drawdown=float(max_drawdown),
            log_growth=float(log_growth),
            total_staked_rub=total_staked,
            settled_bets=settled,
            wins=state.wins,
            losses=state.losses,
            pushes=state.pushes,
            voids=state.voids,
            cycle_count=len(cycles),
            ruin_count=ruin_count,
            ruin_rate=ruin_count / len(cycles),
            mean_clv=None if mean_clv is None else float(mean_clv),
            target_hit_count=sum(c.target_observed for c in cycles),
        )


def preregistered_score(
    metrics: PaperMetrics,
    policy: PreregisteredScorePolicy = DEFAULT_SCORE_POLICY,
) -> float:
    """Calculate the fixed risk-adjusted score; target hits are not consulted."""
    n_bets = max(metrics.settled_bets, 0)
    evidence = min(n_bets / policy.full_evidence_bets, 1.0)
    log_growth_per_bet = metrics.log_growth / n_bets if n_bets else 0.0
    clv = metrics.mean_clv if metrics.mean_clv is not None else 0.0
    performance = (
        policy.roi_weight * metrics.roi
        + policy.log_growth_per_bet_weight * log_growth_per_bet
        + policy.clv_weight * clv
    )
    score = (
        evidence * performance
        - policy.max_drawdown_penalty * metrics.max_drawdown
        - policy.ruin_rate_penalty * metrics.ruin_rate
    )
    return float(score)


def rank_strategies(
    simulators: Iterable[PaperSimulator],
    policy: PreregisteredScorePolicy = DEFAULT_SCORE_POLICY,
) -> tuple[StrategyRanking, ...]:
    """Rank distinct strategies without deleting their event histories."""
    rows: list[tuple[str, float, PaperMetrics]] = []
    seen: set[str] = set()
    for simulator in simulators:
        metrics = simulator.metrics()
        if metrics.strategy_id in seen:
            raise ValueError(f"duplicate strategy_id: {metrics.strategy_id!r}")
        seen.add(metrics.strategy_id)
        rows.append(
            (metrics.strategy_id, preregistered_score(metrics, policy), metrics)
        )
    rows.sort(key=lambda row: (-row[1], row[0]))
    return tuple(
        StrategyRanking(rank=index, strategy_id=name, score=score, metrics=metrics)
        for index, (name, score, metrics) in enumerate(rows, start=1)
    )
