"""Portfolio engine: turn FINAL_CHECK_PASSED exact markets into a bet slip.

Everything upstream (research screening, PRELINE market sets, trigger engine,
deep audit, Final XI gate) stays advisory. This module is the first place in
the pipeline that proposes actual stakes, and it stays strictly PAPER_ONLY:
no order is placed, and the module only accepts candidates the caller has
already marked ``APPROVED`` + ``FINAL_CHECK_PASSED`` upstream — it does not
re-derive approval from probabilities.

Design choices carried over verbatim from the workflow spec:

* singles first, doubles from genuinely diverse ideas, triples rare, no 4+
  leg accumulators;
* a same-match multi-leg ticket is illegal unless the caller supplies an
  actual joint probability (``xgedge.markets.joint``) for that pair — never
  a naive product of two markets sharing one score process;
* every stake is the minimum of quarter-Kelly and the hard caps, so the edge
  sizes the bet and the caps bound it;
* a human ``value`` rating never gates or orders a funding decision — the
  arithmetic does;
* the portfolio must never force itself to spend the whole bankroll.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, prod
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    version: str = "portfolio-engine/2.0"
    bankroll_rub: float = 6000.0
    unit_rub: float = 250.0
    double_stake_rub: float = 500.0
    minimum_cash_reserve_rub: float = 1000.0
    accumulator_stake_rub: float = 250.0
    # Staking caps. The stake is the MINIMUM of quarter-Kelly and every cap,
    # never whichever happens to be convenient.
    kelly_fraction: float = 0.25
    max_stake_fraction_per_bet: float = 0.03
    max_in_play_fraction: float = 0.10
    max_archetype_fraction_per_day: float = 0.15
    # How many tickets the user asked for. None means "as many as qualify".
    max_singles: int | None = None
    max_accumulators: int | None = None
    max_distinct_markets_per_match: int = 2
    max_ticket_uses_per_exact_leg: int = 2
    max_acca_legs: int = 3
    preferred_acca_legs: int = 2
    short_odds_glue_threshold: float = 1.45
    archetype_exposure_cap: float = 0.30
    archetype_limit_mode: str = "warn"  # "warn" | "reject"

    def validate(self) -> None:
        positives = (
            self.bankroll_rub,
            self.unit_rub,
            self.double_stake_rub,
            self.minimum_cash_reserve_rub,
            self.accumulator_stake_rub,
            self.short_odds_glue_threshold,
        )
        if not all(isfinite(v) and v > 0 for v in positives):
            raise ValueError("portfolio stake and price parameters must be finite and positive")
        if self.unit_rub > self.bankroll_rub:
            raise ValueError("unit_rub cannot exceed bankroll_rub")
        if self.minimum_cash_reserve_rub >= self.bankroll_rub:
            raise ValueError("minimum_cash_reserve_rub must leave room to stake anything")
        for field in ("max_distinct_markets_per_match", "max_ticket_uses_per_exact_leg", "max_acca_legs"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field} must be a positive integer")
        if self.preferred_acca_legs > self.max_acca_legs:
            raise ValueError("preferred_acca_legs cannot exceed max_acca_legs")
        if self.max_acca_legs > 3:
            raise ValueError("4+ leg accumulators are disabled by policy")
        if not 0.0 < self.archetype_exposure_cap <= 1.0:
            raise ValueError("archetype_exposure_cap must be in (0, 1]")
        if self.archetype_limit_mode not in {"warn", "reject"}:
            raise ValueError("archetype_limit_mode must be 'warn' or 'reject'")
        fractions = (
            self.kelly_fraction,
            self.max_stake_fraction_per_bet,
            self.max_in_play_fraction,
            self.max_archetype_fraction_per_day,
        )
        if any(not isfinite(v) or not 0 < v <= 1 for v in fractions):
            raise ValueError("Kelly fraction and caps must be in (0, 1]")
        if self.max_stake_fraction_per_bet > self.max_in_play_fraction:
            raise ValueError("a single bet cannot exceed the total in-play cap")
        for field in ("max_singles", "max_accumulators"):
            value = getattr(self, field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer or None")


def kelly_quarter(p: float, odds: float, *, fraction: float = 0.25) -> float:
    """Fractional Kelly stake as a share of bankroll; zero when there is no edge."""
    price = float(odds)
    probability = float(p)
    if not isfinite(price) or price <= 1.0:
        raise ValueError("odds must be finite and above 1")
    if not isfinite(probability) or not 0.0 < probability < 1.0:
        raise ValueError("probability must be in (0, 1)")
    b = price - 1.0
    full = (probability * b - (1.0 - probability)) / b
    return max(full * float(fraction), 0.0)


def stake_for(
    candidate: Mapping[str, Any],
    *,
    config: PortfolioConfig,
    in_play_rub: float,
    archetype_exposure_rub: Mapping[str, float],
) -> dict[str, Any]:
    """Size one stake as the minimum of quarter-Kelly and every hard cap.

    Taking the minimum is the whole point: Kelly alone will happily stake far
    more than a research bankroll should carry on one correlated idea, and a
    flat cap alone ignores how thin the edge is.
    """
    bankroll = config.bankroll_rub
    kelly_fraction = kelly_quarter(
        float(candidate["conservative_probability"]),
        float(candidate["odds"]),
        fraction=config.kelly_fraction,
    )
    caps = {
        "quarter_kelly": kelly_fraction * bankroll,
        "per_bet_cap": config.max_stake_fraction_per_bet * bankroll,
        "in_play_cap": max(0.0, config.max_in_play_fraction * bankroll - in_play_rub),
        "reserve": max(
            0.0, bankroll - config.minimum_cash_reserve_rub - in_play_rub
        ),
    }
    for archetype in candidate.get("archetypes", []) or []:
        used = float(archetype_exposure_rub.get(archetype, 0.0))
        caps[f"archetype:{archetype}"] = max(
            0.0, config.max_archetype_fraction_per_day * bankroll - used
        )
    binding = min(caps, key=lambda name: caps[name])
    return {
        "stake_rub": round(max(0.0, caps[binding]), 2),
        "binding_constraint": binding,
        "caps_rub": {name: round(value, 2) for name, value in caps.items()},
        "quarter_kelly_fraction": kelly_fraction,
    }


def _conservative_ev(candidate: Mapping[str, Any]) -> float:
    probability = float(candidate["conservative_probability"])
    odds = float(candidate["odds"])
    if not 0.0 < probability < 1.0 or not isfinite(odds) or odds <= 1.0:
        raise ValueError("conservative_probability must be in (0,1) and odds must exceed 1")
    return probability * odds - 1.0


def _validate_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fail-closed intake: only APPROVED + FINAL_CHECK_PASSED, positive-EV candidates pass."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in candidates:
        candidate_id = row.get("candidate_id")
        if row.get("status") != "APPROVED" or row.get("final_check_status") != "FINAL_CHECK_PASSED":
            rejected.append({"candidate_id": candidate_id, "reason": "not_approved_and_final_checked"})
            continue
        try:
            ev = _conservative_ev(row)
        except (KeyError, ValueError):
            rejected.append({"candidate_id": candidate_id, "reason": "invalid_probability_or_odds"})
            continue
        if ev <= 0.0:
            rejected.append({"candidate_id": candidate_id, "reason": "conservative_ev_not_positive"})
            continue
        accepted.append({**dict(row), "conservative_ev": ev})
    return accepted, rejected


def _apply_match_cluster_cap(
    candidates: list[dict[str, Any]], *, config: PortfolioConfig
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_fixture: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        by_fixture.setdefault(str(row.get("fixture_id")), []).append(row)
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for fixture_id, rows in by_fixture.items():
        rows.sort(key=lambda r: (-r["conservative_ev"], str(r["candidate_id"])))
        kept.extend(rows[: config.max_distinct_markets_per_match])
        for row in rows[config.max_distinct_markets_per_match :]:
            rejected.append({"candidate_id": row["candidate_id"], "reason": "match_market_cap_exceeded"})
    return kept, rejected


def build_singles(
    candidates: Sequence[Mapping[str, Any]], *, config: PortfolioConfig
) -> dict[str, Any]:
    """Build singles, sized by quarter-Kelly under every hard cap.

    Ordering is by conservative EV — the arithmetic — never by the human
    ``value`` rating. A rating of 8.1 on a bet worth +0.45% must not be funded
    ahead of a genuinely better-priced one.
    """
    ordered = sorted(
        candidates,
        key=lambda r: (-r["conservative_ev"], str(r["candidate_id"])),
    )
    if config.max_singles is not None:
        wanted, surplus = ordered[: config.max_singles], ordered[config.max_singles :]
    else:
        wanted, surplus = ordered, []

    singles: list[dict[str, Any]] = []
    skipped = [
        {"candidate_id": row["candidate_id"], "reason": "SKIPPED_USER_SINGLES_LIMIT"}
        for row in surplus
    ]
    in_play = 0.0
    archetype_exposure_rub: dict[str, float] = {}
    for row in wanted:
        sizing = stake_for(
            row,
            config=config,
            in_play_rub=in_play,
            archetype_exposure_rub=archetype_exposure_rub,
        )
        stake = sizing["stake_rub"]
        if stake <= 0.0:
            skipped.append({
                "candidate_id": row["candidate_id"],
                "reason": f"NO_ROOM:{sizing['binding_constraint']}",
            })
            continue
        in_play += stake
        for archetype in row.get("archetypes", []) or []:
            archetype_exposure_rub[archetype] = (
                archetype_exposure_rub.get(archetype, 0.0) + stake
            )
        singles.append({
            "ticket_type": "single",
            "candidate_id": row["candidate_id"],
            "fixture_id": row.get("fixture_id"),
            "market_family": row.get("market_family"),
            "odds": row.get("odds"),
            "stake_rub": stake,
            "binding_constraint": sizing["binding_constraint"],
            "quarter_kelly_fraction": sizing["quarter_kelly_fraction"],
            "conservative_ev": row["conservative_ev"],
            # Human judgement travels alongside the number; it never sizes it.
            "value_rating": row.get("value"),
            "archetypes": list(row.get("archetypes", [])),
        })
    return {
        "singles": singles,
        "skipped": skipped,
        "staked_rub": round(in_play, 2),
        # The old "one 500 RUB single per day, gated on a human value >= 8.4"
        # rule is gone: quarter-Kelly under the caps sizes every stake from the
        # edge instead, and a human rating no longer gates any money.
        "staking_method": "min(quarter_kelly, per_bet_cap, in_play_cap, archetype_cap, reserve)",
    }


def evaluate_ticket(
    legs: Sequence[Mapping[str, Any]],
    *,
    config: PortfolioConfig,
    same_match_joint_probability: float | None = None,
) -> dict[str, Any]:
    """Validate and price one accumulator ticket of 2 or 3 legs."""
    if len(legs) < 2:
        raise ValueError("a ticket requires at least two legs")
    leg_ids = [leg["candidate_id"] for leg in legs]
    if len(legs) > config.max_acca_legs:
        return {"status": "REJECTED", "reason": "too_many_legs", "legs": leg_ids}
    for leg in legs:
        if not leg.get("independently_approved", True):
            return {
                "status": "REJECTED",
                "reason": "leg_not_independently_approved",
                "legs": leg_ids,
            }
    fixture_ids = [str(leg["fixture_id"]) for leg in legs]
    same_match_pair = len(set(fixture_ids)) != len(fixture_ids)
    if same_match_pair and same_match_joint_probability is None:
        return {"status": "REJECTED", "reason": "REJECT_CORRELATED_SAME_MATCH_LEGS", "legs": leg_ids}
    combined_price = prod(float(leg["odds"]) for leg in legs)
    if same_match_pair:
        probability = float(same_match_joint_probability)
        if not 0.0 < probability < 1.0:
            raise ValueError("same_match_joint_probability must be in (0, 1)")
    else:
        probability = prod(float(leg["conservative_probability"]) for leg in legs)
    ev = probability * combined_price - 1.0
    if ev <= 0.0:
        return {"status": "REJECTED", "reason": "conservative_ev_not_positive", "legs": leg_ids}
    return {
        "status": "VALID",
        "ticket_type": {2: "double", 3: "triple"}[len(legs)],
        "legs": leg_ids,
        "fixture_ids": fixture_ids,
        "combined_price": combined_price,
        "joint_probability": probability,
        "conservative_ev": ev,
        "archetypes": sorted({tag for leg in legs for tag in leg.get("archetypes", [])}),
        "short_odds_glue_warning": [
            leg["candidate_id"] for leg in legs if float(leg["odds"]) < config.short_odds_glue_threshold
        ],
        "same_match_joint_probability_used": same_match_pair,
    }


def build_accumulators(
    eligible_legs: Sequence[Mapping[str, Any]],
    *,
    config: PortfolioConfig,
    leg_usage: dict[str, int],
    budget_remaining: float,
    same_match_joint_probabilities: Mapping[frozenset[str], float] | None = None,
    extra_ticket_leg_ids: Sequence[Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Auto-generate cross-fixture doubles, plus any caller-specified extra tickets."""
    by_id = {str(leg["candidate_id"]): leg for leg in eligible_legs}
    joint_map = same_match_joint_probabilities or {}
    ordered = sorted(
        eligible_legs, key=lambda r: (-r["conservative_ev"], str(r["candidate_id"]))
    )
    tickets: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    spent = 0.0

    def _try_add(leg_group: Sequence[Mapping[str, Any]]) -> None:
        nonlocal spent
        ids = [leg["candidate_id"] for leg in leg_group]
        if any(leg_usage.get(cid, 0) >= config.max_ticket_uses_per_exact_leg for cid in ids):
            rejected.append({"legs": ids, "reason": "max_leg_uses_exceeded"})
            return
        key = frozenset(ids) if len(ids) == 2 else None
        joint_probability = joint_map.get(key) if key is not None else None
        evaluated = evaluate_ticket(leg_group, config=config, same_match_joint_probability=joint_probability)
        if evaluated["status"] != "VALID":
            rejected.append(evaluated)
            return
        stake = config.accumulator_stake_rub
        if spent + stake > budget_remaining:
            rejected.append({"legs": ids, "reason": "SKIPPED_RESERVE_LIMIT"})
            return
        spent += stake
        for cid in ids:
            leg_usage[cid] = leg_usage.get(cid, 0) + 1
        tickets.append({**evaluated, "ticket_type": evaluated["ticket_type"], "stake_rub": stake})

    pool = list(ordered)
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            # evaluate_ticket rejects a same-fixture pair with an explicit
            # REJECT_CORRELATED_SAME_MATCH_LEGS reason unless the caller
            # supplied that exact pair's joint probability; either way the
            # attempt is recorded rather than silently dropped.
            _try_add((pool[i], pool[j]))

    for leg_ids in extra_ticket_leg_ids or ():
        missing = [cid for cid in leg_ids if cid not in by_id]
        if missing:
            rejected.append({"legs": list(leg_ids), "reason": "unknown_candidate_id"})
            continue
        _try_add([by_id[cid] for cid in leg_ids])

    return {"accumulators": tickets, "rejected": rejected, "staked_rub": spent}


def archetype_exposure(
    tickets: Sequence[Mapping[str, Any]], *, config: PortfolioConfig
) -> dict[str, Any]:
    """Share of total portfolio stake carried by each archetype tag."""
    total = sum(float(t["stake_rub"]) for t in tickets)
    if total <= 0.0:
        return {"total_staked_rub": 0.0, "by_archetype": {}, "warnings": []}
    exposure: dict[str, float] = {}
    for ticket in tickets:
        for tag in ticket.get("archetypes", []):
            exposure[tag] = exposure.get(tag, 0.0) + float(ticket["stake_rub"])
    shares = {tag: amount / total for tag, amount in exposure.items()}
    warnings = sorted(tag for tag, share in shares.items() if share > config.archetype_exposure_cap)
    return {"total_staked_rub": total, "by_archetype": shares, "warnings": warnings}


def build_portfolio(
    candidates: Sequence[Mapping[str, Any]],
    *,
    config: PortfolioConfig | None = None,
    same_match_joint_probabilities: Mapping[frozenset[str], float] | None = None,
    extra_ticket_leg_ids: Sequence[Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Build the full PAPER_ONLY portfolio from APPROVED + FINAL_CHECK_PASSED candidates."""
    cfg = config or PortfolioConfig()
    cfg.validate()

    accepted, intake_rejections = _validate_candidates(candidates)
    eligible, cap_rejections = _apply_match_cluster_cap(accepted, config=cfg)

    singles_result = build_singles(eligible, config=cfg)
    leg_usage = {row["candidate_id"]: 1 for row in singles_result["singles"]}

    budget = cfg.bankroll_rub - cfg.minimum_cash_reserve_rub
    accumulators_result = build_accumulators(
        eligible,
        config=cfg,
        leg_usage=leg_usage,
        budget_remaining=budget - singles_result["staked_rub"],
        same_match_joint_probabilities=same_match_joint_probabilities,
        extra_ticket_leg_ids=extra_ticket_leg_ids,
    )

    all_tickets = [
        *singles_result["singles"],
        *accumulators_result["accumulators"],
    ]
    exposure = archetype_exposure(all_tickets, config=cfg)
    if cfg.archetype_limit_mode == "reject" and exposure["warnings"]:
        blocked_archetypes = set(exposure["warnings"])
        all_tickets = [
            t for t in all_tickets if not (set(t.get("archetypes", [])) & blocked_archetypes)
        ]
        exposure = archetype_exposure(all_tickets, config=cfg)

    total_staked = singles_result["staked_rub"] + accumulators_result["staked_rub"]
    return {
        "schema_version": "portfolio/1.0",
        "status": "PAPER_ONLY",
        "real_money_execution": False,
        "policy": asdict(cfg),
        "bankroll": {
            "bankroll_rub": cfg.bankroll_rub,
            "minimum_cash_reserve_rub": cfg.minimum_cash_reserve_rub,
            "staked_rub": total_staked,
            "unused_rub": cfg.bankroll_rub - total_staked,
        },
        "singles": singles_result["singles"],
        "accumulators": accumulators_result["accumulators"],
        "exposure": exposure,
        "rejections": {
            "intake": intake_rejections,
            "match_cluster_cap": cap_rejections,
            "singles_skipped": singles_result["skipped"],
            "accumulators_rejected": accumulators_result["rejected"],
        },
    }
