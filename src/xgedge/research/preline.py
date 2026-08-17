"""Build diverse pre-line market sets without pretending they are bets."""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence

from xgedge.markets.paper_markets import (
    SUPPORTED_SCORE_MARKETS,
    market_settlement_distribution,
    score_matrix,
)
from xgedge.markets.taxonomy import TAXONOMY_VERSION, market_cluster, market_family
from xgedge.markets.settlement import SettlementDistribution, SettlementOutcome
from xgedge.research.screening import ResearchScreeningConfig, screen_fixtures


TARGET_EV = 0.03


def _candidate_id(fixture_id: str, market: object, selection: object, line: object) -> str:
    raw = f"{fixture_id}|{market}|{selection}|{line}|PRELINE"
    return f"preline:{sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _conservative_distribution(
    distribution: SettlementDistribution,
    *,
    central_probability: float,
    conservative_probability: float,
) -> SettlementDistribution:
    probabilities = dict(distribution.probabilities)
    push = probabilities.get(SettlementOutcome.PUSH, 0.0)
    quarter_states = {
        SettlementOutcome.HALF_WIN,
        SettlementOutcome.HALF_LOSS,
    } & set(probabilities)
    if not quarter_states:
        active = 1.0 - push
        probabilities[SettlementOutcome.WIN] = active * conservative_probability
        probabilities[SettlementOutcome.LOSS] = active * (1.0 - conservative_probability)
        return SettlementDistribution(probabilities)

    shift = max(0.0, central_probability - conservative_probability)
    for favorable in (SettlementOutcome.WIN, SettlementOutcome.HALF_WIN):
        available = probabilities.get(favorable, 0.0)
        moved = min(available, shift)
        probabilities[favorable] = available - moved
        probabilities[SettlementOutcome.LOSS] = (
            probabilities.get(SettlementOutcome.LOSS, 0.0) + moved
        )
        shift -= moved
        if shift <= 1e-12:
            break
    return SettlementDistribution(probabilities)


def _trigger_odds(
    forecast: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[float, float, str]:
    market = str(row.get("market") or "")
    selection = str(row.get("selection") or "")
    probability = float(row.get("conservative_probability") or 0.0)
    central = float(row.get("theoretical_probability") or 0.0)
    if market in SUPPORTED_SCORE_MARKETS:
        try:
            matrix = score_matrix(float(forecast["lambda_home"]), float(forecast["lambda_away"]))
            distribution = market_settlement_distribution(
                matrix, market=market, selection=selection, line=row.get("line")
            )
            conservative_distribution = _conservative_distribution(
                distribution,
                central_probability=central,
                conservative_probability=probability,
            )
            return (
                conservative_distribution.trigger_odds(TARGET_EV),
                conservative_distribution.fair_odds(),
                "push_aware_conservative_score_distribution",
            )
        except (KeyError, TypeError, ValueError):
            pass
    if not 0.0 < probability < 1.0:
        raise ValueError("candidate has no usable conservative probability")
    return (
        (1.0 + TARGET_EV) / probability,
        1.0 / probability,
        "binary_conservative_probability",
    )


def build_market_set(forecast: Mapping[str, Any], *, maximum: int = 3) -> dict[str, Any]:
    fixture_id = str(forecast.get("id") or "")
    source = forecast.get("model_market_forecasts")
    rows = source if isinstance(source, list) else []
    hypotheses: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            central = float(row.get("theoretical_probability"))
            conservative = float(row.get("conservative_probability"))
            trigger, conservative_fair, trigger_method = _trigger_odds(forecast, row)
        except (TypeError, ValueError):
            continue
        if not 0 < conservative <= central < 1:
            continue
        market = str(row.get("market") or "")
        selection = str(row.get("selection") or "")
        family = market_family(market)
        cluster = market_cluster(market, selection)
        details = forecast.get("details") if isinstance(forecast.get("details"), Mapping) else {}
        quality_source = details.get("data_quality") if isinstance(details, Mapping) else None
        quality = quality_source if isinstance(quality_source, Mapping) else {}
        warnings = quality.get("warnings") if isinstance(quality, Mapping) else []
        hypotheses.append({
            "candidate_id": _candidate_id(fixture_id, market, selection, row.get("line")),
            "fixture_id": fixture_id,
            "market": market,
            "selection": selection,
            "label": row.get("label"),
            "line": row.get("line"),
            "market_family": family,
            "market_cluster": cluster,
            "market_taxonomy_version": TAXONOMY_VERSION,
            "archetype": None,
            "central_probability": central,
            "pessimistic_probability": conservative,
            "optimistic_probability": min(0.999, central + (central - conservative) / 2),
            "fair_odds_central": 1.0 / central,
            "fair_odds_conservative": conservative_fair,
            "trigger_price": trigger,
            "preferred_entry_price": trigger,
            "trigger_method": trigger_method,
            "target_ev": TARGET_EV,
            "robustness_preline": max(0.0, min(10.0, float(quality.get("score", 0)) / 10)),
            "data_quality": quality.get("score", 0),
            "main_thesis": ["model_score_distribution_supports_this_market"],
            "anti_thesis": list(warnings or []),
            "failure_modes": ["lineup_change", "price_move", "model_miscalibration"],
            "status": "WATCH",
            "executable_quote": None,
            "created_at": forecast.get("forecast_generated_at"),
            "model_version": forecast.get("model"),
            "context_version": details.get("generated_as_of") if isinstance(details, Mapping) else None,
        })

    priority = {
        "QUALIFICATION": 0,
        "TEAM_TOTALS": 1,
        "BTTS": 2,
        "ASIAN_HANDICAP": 3,
        "TOTALS": 4,
        "DRAW_NO_BET": 5,
        "DOUBLE_CHANCE": 6,
        "MATCH_RESULT": 7,
    }
    hypotheses.sort(key=lambda row: (
        priority.get(row["market_family"], 99),
        -row["pessimistic_probability"],
        row["market_cluster"],
    ))
    selected: list[dict[str, Any]] = []
    clusters: set[str] = set()
    for row in hypotheses:
        if row["market_cluster"] in clusters:
            continue
        selected.append(row)
        clusters.add(row["market_cluster"])
        if len(selected) >= maximum:
            break
    checked = sorted({row["market_family"] for row in hypotheses})
    return {
        "schema_version": "preline-market-set/1.0",
        "fixture_id": fixture_id,
        "maximum_candidates": maximum,
        "checked_market_families": checked,
        "coverage_notes": {
            family: ("candidate_selected" if any(row["market_family"] == family for row in selected)
                     else "checked_no_candidate_in_top_diverse_set")
            for family in checked
        },
        "candidates": selected,
    }


def build_research_workflow(
    forecasts: Sequence[Mapping[str, Any]],
    *,
    generated_at: str,
    config: ResearchScreeningConfig | None = None,
) -> dict[str, Any]:
    queue = screen_fixtures(forecasts, generated_at=generated_at, config=config)
    by_id = {str(row.get("id")): row for row in forecasts if isinstance(row, Mapping)}
    market_sets = {
        fixture_id: build_market_set(by_id[fixture_id])
        for fixture_id in queue["selected_fixture_ids"] if fixture_id in by_id
    }
    coverage: dict[str, dict[str, int]] = {}
    for market_set in market_sets.values():
        selected_families = {row["market_family"] for row in market_set["candidates"]}
        for family in market_set["checked_market_families"]:
            row = coverage.setdefault(family, {"matches_checked": 0, "hypotheses": 0})
            row["matches_checked"] += 1
            row["hypotheses"] += int(family in selected_families)
    return {
        **queue,
        "schema_version": "uefa-research-workflow/2.0",
        "market_sets": market_sets,
        "market_coverage": dict(sorted(coverage.items())),
        "workflow_status": "PAPER_ONLY_MODEL_IN_QUARANTINE",
    }
