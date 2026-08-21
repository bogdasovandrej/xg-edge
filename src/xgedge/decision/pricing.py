"""Single source of truth for fair price, minimum entry and value.

Every gate in the project must import from here. The formulas are fixed by
the product contract and are deliberately written in the same form as that
contract, so a reader can diff them by eye:

    fair(W, L)       = 1 + L / W
    min_entry(W, L)  = fair(W, L) * 1.08
    ev_stake(...)    = W * (odds - 1) - L
    ev_at_risk(...)  = ev_stake / (W + L)      # push excluded from the denominator
    value_pct(...)   = ev_at_risk * 100

``fair`` is NOT ``1 / p``. On a market with a push, the money at risk is only
``W + L``, so a push-blind ``1 / p`` prices the bet wrong. (For a market with
no push the two happen to coincide, which is exactly why the mistake survives
review — do not "simplify" this back.)

Two numbers must never be confused:

``value_pct``    the gate. Computed here, threshold ``VALUE_PCT_GATE``.
``value_rating`` a 0-10 human/LLM judgement. Never a gate, never a sort key
                 for candidate selection — a display column only.
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Callable, Mapping

# The gate threshold, in percent of money at risk.
VALUE_PCT_GATE = 8.0

# Entry price must clear the fair price by this factor.
MIN_ENTRY_MARGIN = 1.08

MULTIPLIERS: dict[str, Callable[[float], float]] = {
    "WIN": lambda odds: odds,
    "HALF_WIN": lambda odds: 1 + 0.5 * (odds - 1),
    "PUSH": lambda odds: 1.0,
    "HALF_LOSS": lambda odds: 0.5,
    "LOSS": lambda odds: 0.0,
}


def _checked(W: float, L: float) -> tuple[float, float]:
    win, loss = float(W), float(L)
    if not isfinite(win) or not isfinite(loss) or win <= 0.0 or loss < 0.0:
        raise ValueError("W must be positive and L non-negative and finite")
    return win, loss


def _checked_odds(odds: float) -> float:
    price = float(odds)
    if not isfinite(price) or price <= 1.0:
        raise ValueError("odds must be finite and above 1")
    return price


def fair(W: float, L: float) -> float:
    """Break-even decimal price: 1 + L / W. Not 1 / p."""
    win, loss = _checked(W, L)
    return 1 + loss / win


def min_entry(W: float, L: float) -> float:
    """Lowest acceptable entry price: the fair price plus the required margin."""
    return fair(W, L) * MIN_ENTRY_MARGIN


def ev_stake(W: float, L: float, odds: float) -> float:
    """Expected value per unit staked."""
    win, loss = _checked(W, L)
    return win * (_checked_odds(odds) - 1) - loss


def ev_at_risk(W: float, L: float, odds: float) -> float:
    """Expected value per unit actually at risk; push is excluded below the line."""
    win, loss = _checked(W, L)
    return ev_stake(win, loss, odds) / (win + loss)


def value_pct(W: float, L: float, odds: float) -> float:
    """The gate number, in percent of money at risk."""
    return ev_at_risk(W, L, odds) * 100


def passes_value_gate(W: float, L: float, odds: float, *, threshold: float = VALUE_PCT_GATE) -> bool:
    """True when the price clears the value gate. The only admissible gate."""
    return value_pct(W, L, odds) >= float(threshold)


def calc_mode(push_probability: float) -> str:
    """``WPL_FULL`` when the market can push, ``BINARY`` when it cannot."""
    push = float(push_probability)
    if not isfinite(push) or push < 0.0:
        raise ValueError("push probability must be finite and non-negative")
    return "WPL_FULL" if push > 0.0 else "BINARY"


def states_from_distribution(probabilities: Mapping[Any, float]) -> dict[str, float]:
    """Collapse a settlement distribution into the win/push/loss triple.

    Half-win and half-loss carry half their mass into the push bucket, which
    is what makes ``W + P + L == 1`` hold for quarter Asian lines too while
    keeping ``W`` and ``L`` the mass that is genuinely at risk.
    """
    win = push = loss = 0.0
    for state, mass in probabilities.items():
        name = str(getattr(state, "value", state)).upper()
        amount = float(mass)
        if amount < 0.0 or not isfinite(amount):
            raise ValueError("settlement mass must be finite and non-negative")
        if name == "WIN":
            win += amount
        elif name == "LOSS":
            loss += amount
        elif name in {"PUSH", "VOID"}:
            push += amount
        elif name == "HALF_WIN":
            win += amount / 2.0
            push += amount / 2.0
        elif name == "HALF_LOSS":
            loss += amount / 2.0
            push += amount / 2.0
        else:
            raise ValueError(f"unsupported settlement state: {name}")
    total = win + push + loss
    if abs(total - 1.0) > 1e-3:
        raise ValueError(f"settlement states must sum to one, got {total}")
    return {"win": win, "push": push, "loss": loss}


def price_market(
    states: Mapping[str, float], *, odds: float | None = None
) -> dict[str, Any]:
    """Full pricing record for one market: states, fair, min entry, value."""
    win = float(states["win"])
    push = float(states.get("push", 0.0))
    loss = float(states["loss"])
    record: dict[str, Any] = {
        "win": win,
        "push": push,
        "loss": loss,
        "calc_mode": calc_mode(push),
        "fair": fair(win, loss),
        "min_entry": min_entry(win, loss),
        "value_pct": None,
        "ev_at_risk": None,
        "gate_price": None,
        "odds": None,
    }
    if odds is not None:
        price = _checked_odds(odds)
        record.update({
            "odds": price,
            "ev_at_risk": ev_at_risk(win, loss, price),
            "value_pct": value_pct(win, loss, price),
            "gate_price": passes_value_gate(win, loss, price),
        })
    return record
