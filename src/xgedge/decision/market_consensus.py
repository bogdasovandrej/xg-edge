"""Free edge source: price one bookmaker against the consensus of the others.

Bookmaker odds already contain the news, the injuries, the confirmed lineups
and the transfer that happened this morning — pricing that in is what a
trading desk does all day. That makes the market an excellent probability
estimate and a terrible one to bet into, because a probability derived from a
book's own price cannot beat that book: de-vig its odds, hand the number back,
and the measured edge is exactly zero minus the margin.

The way to use market prices as a *signal* rather than as a mirror is
leave-one-out: build the fair probability from every OTHER bookmaker, then ask
whether the book being evaluated is offering materially more than that
consensus. A book that is out of line with its competitors is the free,
well-established version of "someone knows something" — and it needs no
language model, no news feed and no paid data.

Two rules this module exists to enforce:

* the evaluated book is never part of its own benchmark. Including it drags
  the consensus toward the very price under test and quietly shrinks the
  edge toward zero;
* a consensus needs enough independent books to mean anything. Below
  ``minimum_books`` the result is reported as unusable rather than as a
  confident zero.

This does not, on its own, demonstrate an edge. The project's holdout CLV is
still negative, and an outlier can simply be a book that is slower, not
wrong. It produces candidates for audit, not bets.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import median
from typing import Any, Mapping, Sequence

import numpy as np

from xgedge.decision.pricing import VALUE_PCT_GATE, fair, min_entry, value_pct
from xgedge.decision.staking import demargin_shin

CONSENSUS_SCHEMA_VERSION = "market-consensus/1.0"


@dataclass(frozen=True, slots=True)
class ConsensusConfig:
    # Two books make a consensus of one once the evaluated book is removed,
    # which is not a consensus. Three is the minimum that can outvote a single
    # stale price.
    minimum_books: int = 3
    value_gate_pct: float = VALUE_PCT_GATE

    def validate(self) -> None:
        if isinstance(self.minimum_books, bool) or not isinstance(self.minimum_books, int):
            raise ValueError("minimum_books must be an integer")
        if self.minimum_books < 3:
            raise ValueError(
                "a leave-one-out consensus needs at least 3 books to be meaningful"
            )
        if not isfinite(self.value_gate_pct):
            raise ValueError("value_gate_pct must be finite")


def devig_book(odds_by_outcome: Mapping[str, float]) -> dict[str, float]:
    """Remove one bookmaker's margin from a complete market.

    Uses Shin de-margining, which models the insider share rather than
    splitting the margin evenly, and falls back to proportional when no root
    can be bracketed. The market must be complete: de-vigging a partial market
    would normalise against missing mass and overstate every probability.
    """
    outcomes = list(odds_by_outcome)
    if len(outcomes) < 2:
        raise ValueError("de-vigging needs a complete market of at least two outcomes")
    prices = []
    for outcome in outcomes:
        price = float(odds_by_outcome[outcome])
        if not isfinite(price) or price <= 1.0:
            raise ValueError(f"odds for {outcome!r} must be finite and above 1")
        prices.append(price)
    probabilities = demargin_shin(np.asarray(prices, dtype=float))
    return {outcome: float(p) for outcome, p in zip(outcomes, probabilities)}


def _book_margin(odds_by_outcome: Mapping[str, float]) -> float:
    return sum(1.0 / float(price) for price in odds_by_outcome.values()) - 1.0


def consensus_probability(
    books: Mapping[str, Mapping[str, float]],
    outcome: str,
    *,
    exclude: str | None = None,
) -> float | None:
    """Median de-vigged probability for one outcome across books.

    ``exclude`` drops the book under evaluation so it cannot vote on its own
    price. The median rather than the mean, so one stale or mispriced book
    cannot drag the benchmark.
    """
    estimates: list[float] = []
    for name, odds in books.items():
        if name == exclude or outcome not in odds:
            continue
        try:
            estimates.append(devig_book(odds)[outcome])
        except (TypeError, ValueError):
            continue
    return median(estimates) if estimates else None


def evaluate_market(
    books: Mapping[str, Mapping[str, float]],
    *,
    config: ConsensusConfig | None = None,
) -> dict[str, Any]:
    """Price every book's every outcome against the other books' consensus."""
    cfg = config or ConsensusConfig()
    cfg.validate()
    usable = {
        name: dict(odds) for name, odds in books.items()
        if isinstance(odds, Mapping) and len(odds) >= 2
    }
    outcomes = sorted({outcome for odds in usable.values() for outcome in odds})

    if len(usable) < cfg.minimum_books:
        return {
            "schema_version": CONSENSUS_SCHEMA_VERSION,
            "status": "INSUFFICIENT_BOOKS",
            "books": len(usable),
            "minimum_books": cfg.minimum_books,
            "reason": (
                f"{len(usable)} book(s) available; a leave-one-out consensus "
                f"needs at least {cfg.minimum_books}"
            ),
            "candidates": [],
        }

    candidates: list[dict[str, Any]] = []
    for name, odds in usable.items():
        for outcome, price in odds.items():
            benchmark = consensus_probability(usable, outcome, exclude=name)
            if benchmark is None or not 0.0 < benchmark < 1.0:
                continue
            win, loss = benchmark, 1.0 - benchmark
            offered = float(price)
            candidates.append({
                "bookmaker": name,
                "outcome": outcome,
                "odds": offered,
                "consensus_probability": benchmark,
                "consensus_books": len(usable) - 1,
                "fair": fair(win, loss),
                "min_entry": min_entry(win, loss),
                "value_pct": value_pct(win, loss, offered),
                "book_margin": _book_margin(odds),
                "status": (
                    "OUTLIER_CANDIDATE"
                    if value_pct(win, loss, offered) >= cfg.value_gate_pct
                    else "BELOW_MIN"
                ),
            })
    candidates.sort(key=lambda row: (-row["value_pct"], row["bookmaker"], row["outcome"]))
    return {
        "schema_version": CONSENSUS_SCHEMA_VERSION,
        "status": "OK",
        "books": len(usable),
        "outcomes": outcomes,
        "gate": {"metric": "value_pct", "threshold": cfg.value_gate_pct},
        "method": "leave_one_out_shin_devig_median",
        "note": (
            "Расхождение с консенсусом — кандидат на разбор, а не доказанный "
            "эдж: контора может быть просто медленнее, а не ошибаться."
        ),
        "candidates": candidates,
    }


def best_outliers(
    evaluations: Sequence[Mapping[str, Any]], *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Collect gate-passing outliers across many markets, best value first."""
    rows = [
        dict(row)
        for evaluation in evaluations
        if isinstance(evaluation, Mapping) and evaluation.get("status") == "OK"
        for row in evaluation.get("candidates", [])
        if isinstance(row, Mapping) and row.get("status") == "OUTLIER_CANDIDATE"
    ]
    rows.sort(key=lambda row: -float(row["value_pct"]))
    return rows[:limit] if limit is not None else rows
