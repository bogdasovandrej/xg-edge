"""Combine World Cup and UCL experiment outputs for the public live site."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xgedge.decision.live_market import (
    anchor_from_audit,
    anchor_live_1x2,
    market_index,
)
from xgedge.data.point_in_time import available_snapshot
from xgedge.data.bookmaker_odds import apply_odds_snapshot_to_live_payload
from xgedge.decision.ranking import rank_paper_candidates
from xgedge.dossier.builder import build_match_dossier
from xgedge.evaluation.prospective import apply_summary_to_live_payload, prospective_summary
from xgedge.markets.markets import prob_over
from xgedge.markets.paper_markets import market_probability
from xgedge.models.dixon_coles import score_matrix
from xgedge.simulation.ledger import public_paper_summary


TEAM_RU = {
    "France": "Франция",
    "Spain": "Испания",
    "England": "Англия",
    "Argentina": "Аргентина",
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _uncertainty(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "не оценена"
    width = float(high) - float(low)
    if width >= 0.25:
        return "высокая"
    if width >= 0.12:
        return "средняя"
    return "низкая"


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _future_forecasts(rows: list[dict], generated_at: str) -> list[dict]:
    """Keep only fixtures whose regulation kickoff is strictly in the future."""
    cutoff = _as_utc(generated_at)
    if cutoff is None:
        raise ValueError("generated_at must be an ISO-8601 timestamp")
    return [
        row
        for row in rows
        if (kickoff := _as_utc(row.get("kickoff_utc"))) is not None
        and kickoff > cutoff
    ]


def _uefa_stage_label(fixture: dict, prediction: dict) -> str:
    round_name = fixture.get("round") or prediction.get("round")
    translated = {
        "First qualifying round": "1-й квалификационный раунд",
        "Second qualifying round": "2-й квалификационный раунд",
        "Third qualifying round": "3-й квалификационный раунд",
        "Play-offs": "Раунд плей-офф",
    }.get(str(round_name), str(round_name or fixture.get("stage") or "Квалификация"))
    leg = fixture.get("leg") if fixture.get("leg") is not None else prediction.get("leg")
    leg_label = {1: "первый матч", 2: "ответный матч"}.get(leg)
    return f"{translated} · {leg_label}" if leg_label else translated


def _fixture_index(payload: Any) -> dict[str, dict]:
    rows = payload.get("fixtures", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("fixture snapshot must be a list or contain a fixtures list")
    return {str(row["id"]): row for row in rows if isinstance(row, dict) and row.get("id")}


def _top_five_rows(document: dict | None) -> list[dict]:
    if not isinstance(document, dict) or document.get("schema_version") != "top-five-fixtures/1.0":
        return []
    fixtures = document.get("fixtures")
    if not isinstance(fixtures, list):
        return []
    rows = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        fixture_id = str(fixture.get("id") or "").strip()
        home = str(fixture.get("home") or "").strip()
        away = str(fixture.get("away") or "").strip()
        kickoff = fixture.get("kickoff_utc")
        if not fixture_id or not home or not away or _as_utc(kickoff) is None:
            continue
        rows.append({
            "id": fixture_id,
            "competition": fixture.get("competition") or "Top-5 league",
            "stage": fixture.get("round") or fixture.get("stage") or "Domestic league",
            "kickoff_utc": kickoff,
            "home": home,
            "away": away,
            "venue": fixture.get("venue"),
            "model": "Pending top-five model",
            "forecast_generated_at": document.get("generated_at"),
            "p_home": None,
            "p_draw": None,
            "p_away": None,
            "p_over25": None,
            "p_btts": None,
            "score_distribution": None,
            "uncertainty": "не оценена",
            "recommendation": "NO BET",
            "first_leg": None,
            "probability_basis": "calendar_only_no_validated_top5_features",
            "details": {
                "data_quality": {
                    "score": 0,
                    "label": "low",
                    "sources": ["football-data.org"],
                    "warnings": [
                        "Top-five fixture loaded, but no validated point-in-time xG feature set is attached yet."
                    ],
                },
                "candidate_bets": [],
                "betting_gate": {
                    "allowed": False,
                    "reason": "top_five_model_not_validated",
                },
            },
        })
    return rows


def _world_cup_history(document: dict | None) -> list[dict]:
    if not isinstance(document, dict) or not isinstance(document.get("matches"), list):
        return []
    rows = []
    for source in document["matches"]:
        if not isinstance(source, dict):
            continue
        row = dict(source)
        row.update({
            "official": True,
            "scope": "national",
            "neutral_venue": True,
            "competition": "FIFA World Cup 2026",
            "competition_level": "international_major",
            "provenance": {"source": "official_fifa_api"},
        })
        rows.append(row)
    return rows


def _national_priors(rankings: dict | None) -> dict[tuple[str, str], float]:
    rows = rankings.get("rankings", []) if isinstance(rankings, dict) else []
    return {
        ("national", str(row["team_id"])): float(row["rating"])
        for row in rows
        if isinstance(row, dict) and row.get("team_id") is not None and row.get("rating") is not None
    }


def _uefa_history(document: dict | None) -> list[dict]:
    if not isinstance(document, dict) or document.get("schema_version") != "uefa-club-history/1.0":
        return []
    rows = document.get("matches")
    if not isinstance(rows, list):
        raise ValueError("UEFA history document must contain a matches list")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _fixture_context(fixture: dict, generated_at: str, supplied: dict | None) -> dict:
    context = dict(supplied or {})
    if "referee" not in context and fixture.get("referee"):
        context["referee"] = available_snapshot(
            "official_fixture",
            [{
                "match_id": str(fixture["id"]),
                "referee_name": fixture["referee"],
                "role": "referee",
                "yellow_cards_per_match": None,
                "red_cards_per_match": None,
            }],
            snapshot_at=generated_at,
        )
    return context


def _fair_candidates(probabilities: dict) -> list[dict]:
    labels = (("home_win", "П1"), ("draw", "X"), ("away_win", "П2"))
    rows = []
    for key, label in labels:
        value = probabilities.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        rows.append({
            "selection": label,
            "probability": float(value),
            "fair_odds": 1.0 / float(value),
            "market_odds": None,
            "point_edge": None,
            "status": "NO_VERIFIED_MARKET_PRICE",
        })
    rows.sort(key=lambda row: (-row["probability"], row["selection"]))
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


def _model_market_forecasts(
    lambda_home: Any,
    lambda_away: Any,
    *,
    uncertainty: str,
    rho: Any = 0.0,
) -> list[dict[str, Any]]:
    """Price the full score-resolvable line without pretending a bookmaker quote exists."""
    try:
        home_xg = float(lambda_home)
        away_xg = float(lambda_away)
        fitted_rho = float(rho or 0.0)
        if not math.isfinite(home_xg + away_xg + fitted_rho) or home_xg <= 0 or away_xg <= 0:
            raise ValueError
        matrix = score_matrix(home_xg, away_xg, fitted_rho, max_goals=12)
    except (TypeError, ValueError):
        return []

    haircut = {
        "низкая": 0.03,
        "средняя": 0.04,
        "высокая": 0.06,
    }.get(uncertainty, 0.05)
    definitions: list[tuple[str, str, float | None, str, str]] = [
        ("1x2", "home", None, "П1", "исход"),
        ("1x2", "draw", None, "X", "исход"),
        ("1x2", "away", None, "П2", "исход"),
        ("double_chance", "home_draw", None, "1X", "исход"),
        ("double_chance", "home_away", None, "12", "исход"),
        ("double_chance", "draw_away", None, "X2", "исход"),
        ("draw_no_bet", "home", None, "П1 с возвратом", "исход"),
        ("draw_no_bet", "away", None, "П2 с возвратом", "исход"),
        ("btts", "yes", None, "Обе забьют — да", "обе забьют"),
        ("btts", "no", None, "Обе забьют — нет", "обе забьют"),
    ]
    for line in (1.5, 2.5, 3.5, 4.5):
        definitions.extend([
            ("totals", "over", line, f"ТБ {line:.1f}", "тотал"),
            ("totals", "under", line, f"ТМ {line:.1f}", "тотал"),
        ])
    for line in (0.5, 1.5, 2.5):
        definitions.extend([
            ("team_totals", "home_over", line, f"ИТБ1 {line:.1f}", "инд. тотал хозяев"),
            ("team_totals", "home_under", line, f"ИТМ1 {line:.1f}", "инд. тотал хозяев"),
            ("team_totals", "away_over", line, f"ИТБ2 {line:.1f}", "инд. тотал гостей"),
            ("team_totals", "away_under", line, f"ИТМ2 {line:.1f}", "инд. тотал гостей"),
        ])
    for line in (-1.5, -0.5, 0.0, 0.5, 1.5):
        definitions.extend([
            ("asian_handicap", "home", line, f"Ф1({line:+.1f})", "фора"),
            ("asian_handicap", "away", line, f"Ф2({line:+.1f})", "фора"),
        ])

    rows: list[dict[str, Any]] = []
    for market, selection, line, label, recommendation_group in definitions:
        try:
            probability = market_probability(
                matrix, market=market, selection=selection, line=line
            )
        except ValueError:
            continue
        # A fixed percentage-point haircut cannot be subtracted from a tail
        # probability smaller than the haircut.  Reduce such rare outcomes
        # proportionally so the conservative estimate can never exceed the
        # theoretical one or become non-positive.
        applied_haircut = min(haircut, probability * 0.5)
        conservative = probability - applied_haircut
        rows.append({
            "market": market,
            "selection": selection,
            "line": line,
            "label": label,
            "theoretical_probability": probability,
            "reliability_haircut": applied_haircut,
            "conservative_probability": conservative,
            "theoretical_fair_odds": 1.0 / probability,
            "conservative_fair_odds": 1.0 / conservative,
            "recommendation_group": recommendation_group,
            "recommendation_rank": None,
            "status": "MODEL_ONLY_NO_BOOKMAKER_PRICE",
            "settlement_period": "REGULATION_90_MINUTES",
        })

    # Select diverse scenarios instead of filling the top three with correlated
    # variants of the same market.  They are forecasts, not bookmaker value bets.
    group_best: dict[str, dict[str, Any]] = {}
    for row in rows:
        group = str(row["recommendation_group"])
        current = group_best.get(group)
        if current is None or row["conservative_probability"] > current["conservative_probability"]:
            group_best[group] = row
    recommended = sorted(
        group_best.values(),
        key=lambda row: (-row["conservative_probability"], row["conservative_fair_odds"]),
    )[:3]
    for rank, row in enumerate(recommended, start=1):
        row["recommendation_rank"] = rank
    return rows


def _score_distribution(
    scores: Any,
    *,
    lambda_home: Any,
    lambda_away: Any,
    rho: Any = 0.0,
) -> dict[str, Any]:
    scenarios = []
    for source in scores if isinstance(scores, list) else []:
        if not isinstance(source, dict) or not isinstance(source.get("score"), str):
            continue
        probability = source.get("probability")
        if (
            isinstance(probability, (int, float))
            and not isinstance(probability, bool)
            and math.isfinite(float(probability))
            and 0.0 <= float(probability) <= 1.0
        ):
            scenarios.append({
                "score": source["score"],
                "probability": float(probability),
            })
    scenarios = scenarios[:5]
    coverage = min(1.0, sum(row["probability"] for row in scenarios))
    try:
        lam_h, lam_a, fitted_rho = (
            float(lambda_home), float(lambda_away), float(rho or 0.0)
        )
        if not math.isfinite(lam_h + lam_a + fitted_rho) or lam_h < 0 or lam_a < 0:
            raise ValueError
        matrix = score_matrix(lam_h, lam_a, fitted_rho, max_goals=10)
        expected = {"home": lam_h, "away": lam_a, "total": lam_h + lam_a}
        over35, over45 = prob_over(matrix, 3.5), prob_over(matrix, 4.5)
    except (TypeError, ValueError):
        expected, over35, over45 = None, None, None
    return {
        "top_score": scenarios[0]["score"] if scenarios else None,
        "top_score_probability": scenarios[0]["probability"] if scenarios else None,
        "score_scenarios": scenarios,
        "score_scenarios_coverage": coverage if scenarios else None,
        "other_score_probability": 1.0 - coverage if scenarios else None,
        "score_display": "distribution_not_exact_score_prediction",
        "expected_goals": expected,
        "p_over35": over35,
        "p_over45": over45,
        "tail_probability_status": "RAW_POISSON_UNCALIBRATED_NO_BET",
    }


def _build_dossiers(
    world_cup: dict,
    ucl: dict,
    fixtures: dict[str, dict],
    generated_at: str,
    *,
    world_cup_history: dict | None,
    rankings: dict | None,
    context_document: dict | None,
    uefa_history: dict | None,
) -> dict[str, dict]:
    output: dict[str, dict] = {}
    cutoff = _as_utc(generated_at)
    if cutoff is None:
        raise ValueError("generated_at must be an ISO-8601 timestamp")
    history = _world_cup_history(world_cup_history)
    national_priors = _national_priors(rankings)
    club_history = _uefa_history(uefa_history)
    supplied = context_document.get("fixtures", {}) if isinstance(context_document, dict) else {}
    for prediction in world_cup.get("predictions", []):
        fixture_id = str(prediction.get("fixture_id"))
        source = fixtures.get(fixture_id)
        kickoff = _as_utc(source.get("kickoff_utc")) if source else None
        if (
            not source
            or not source.get("home_id")
            or not source.get("away_id")
            or kickoff is None
            or kickoff <= cutoff
        ):
            continue
        fixture = {**source, "scope": "national", "competition_level": "international_major"}
        output[fixture_id] = build_match_dossier(
            fixture,
            history,
            cutoff=generated_at,
            contexts=_fixture_context(fixture, generated_at, supplied.get(fixture_id)),
            forecast_probabilities=prediction.get("probabilities"),
            elo_priors=national_priors,
        )
    for prediction in ucl.get("predictions", []):
        fixture_id = str(prediction.get("fixture_id"))
        source = fixtures.get(fixture_id)
        ratings = prediction.get("ratings") or {}
        home_rating, away_rating = ratings.get("home") or {}, ratings.get("away") or {}
        kickoff = _as_utc(source.get("kickoff_utc")) if source else None
        if (
            not source
            or not source.get("home_id")
            or not source.get("away_id")
            or kickoff is None
            or kickoff <= cutoff
        ):
            continue
        fixture = {
            **source,
            "scope": "club",
            "competition_level": (
                "uefa_champions_league" if str(source.get("competition_id")) == "1"
                else "uefa_europa_league" if str(source.get("competition_id")) == "14"
                else "uefa_conference_league" if str(source.get("competition_id")) == "2019"
                else "uefa_club"
            ),
        }
        priors = {}
        if home_rating.get("elo") is not None:
            priors[("club", str(fixture["home_id"]))] = float(home_rating["elo"])
        if away_rating.get("elo") is not None:
            priors[("club", str(fixture["away_id"]))] = float(away_rating["elo"])
        dossier = build_match_dossier(
            fixture,
            club_history,
            cutoff=generated_at,
            contexts=_fixture_context(fixture, generated_at, supplied.get(fixture_id)),
            forecast_probabilities=prediction.get("probabilities_90m"),
            elo_priors=priors,
        )
        dossier["candidate_bets"] = _fair_candidates(prediction.get("probabilities_90m") or {})
        output[fixture_id] = dossier
    return output


def _world_cup_rows(
    document: dict,
    fixtures: dict[str, dict],
    markets: dict[str, dict],
    anchor: Any | None,
    dossiers: dict[str, dict],
) -> list[dict]:
    rows = []
    for prediction in document.get("predictions", []):
        probs = prediction["probabilities"]
        interval = prediction.get("uncertainty", {}).get("p_home", [None, None])
        uncertainty_label = _uncertainty(*interval)
        fixture = fixtures.get(str(prediction["fixture_id"]), {})
        scores = prediction.get("top_scores") or []
        score_distribution = _score_distribution(
            scores,
            lambda_home=prediction.get("lambda_home"),
            lambda_away=prediction.get("lambda_away"),
            rho=prediction.get("rho"),
        )
        raw = {"home": probs["home"], "draw": probs["draw"], "away": probs["away"]}
        market = markets.get(str(prediction["fixture_id"]))
        comparison = anchor_live_1x2(raw, market, anchor) if market and anchor else None
        public_probs = comparison["anchored"] if comparison else raw
        details = dict(dossiers.get(str(prediction["fixture_id"]), {}))
        if comparison:
            details.update({
                "market": {
                    key: comparison[key]
                    for key in (
                        "basis", "bookmaker", "captured_at_utc", "source_url",
                        "calibration_scope", "calibration_warning",
                        "raw_model", "market_fair", "anchored",
                    )
                },
                "candidate_bets": comparison["candidate_bets"],
                "betting_gate": comparison["betting_gate"],
            })
        rows.append({
            "id": str(prediction["fixture_id"]),
            "competition": "FIFA World Cup 2026",
            "stage": "Полуфинал" if prediction.get("stage") == "Semi-final" else prediction.get("stage", ""),
            "kickoff_utc": prediction["kickoff_utc"],
            "home": TEAM_RU.get(prediction["home"], prediction["home"]),
            "away": TEAM_RU.get(prediction["away"], prediction["away"]),
            "venue": fixture.get("venue"),
            "model": prediction.get("model"),
            "forecast_generated_at": (
                prediction.get("generated_as_of_utc")
                or document.get("as_of_utc")
                or document.get("generated_at_utc")
            ),
            "p_home": public_probs["home"],
            "p_draw": public_probs["draw"],
            "p_away": public_probs["away"],
            "probability_basis": "market_anchored" if comparison else "fundamental_only",
            "raw_model_1x2": raw,
            "p_over25": probs["over_2_5"],
            "p_btts": probs["btts_yes"],
            "lambda_home": prediction.get("lambda_home"),
            "lambda_away": prediction.get("lambda_away"),
            "model_market_forecasts": _model_market_forecasts(
                prediction.get("lambda_home"),
                prediction.get("lambda_away"),
                uncertainty=uncertainty_label,
                rho=prediction.get("rho"),
            ),
            **score_distribution,
            "uncertainty": uncertainty_label,
            "recommendation": "MODEL FORECAST",
            "first_leg": None,
            "details": details or None,
        })
    return rows


def _ucl_rows(document: dict, fixtures: dict[str, dict], dossiers: dict[str, dict]) -> list[dict]:
    rows = []
    for prediction in document.get("predictions", []):
        fixture_id = str(prediction["fixture_id"])
        fixture = fixtures.get(fixture_id, {})
        probs = prediction.get("probabilities_90m") or {}
        expected = prediction.get("expected_goals_90m") or {}
        qualification = prediction.get("qualification") or {}
        lam_h, lam_a = expected.get("home"), expected.get("away")
        total = float(lam_h) + float(lam_a) if lam_h is not None and lam_a is not None else None
        p_over = (
            1.0 - math.exp(-total) * (1.0 + total + total * total / 2.0)
            if total is not None else None
        )
        p_btts = (
            (1.0 - math.exp(-float(lam_h))) * (1.0 - math.exp(-float(lam_a)))
            if total is not None else None
        )
        interval = (
            prediction.get("uncertainty_90m", {})
            .get("intervals", {})
            .get("home_win", {})
        )
        uncertainty_label = _uncertainty(interval.get("low"), interval.get("high"))
        scores = prediction.get("most_likely_scores_90m") or []
        score_distribution = _score_distribution(
            scores,
            lambda_home=lam_h,
            lambda_away=lam_a,
        )
        agg_h, agg_a = fixture.get("aggregate_home_score"), fixture.get("aggregate_away_score")
        first_leg = (
            f"Агрегат {agg_h}:{agg_a}"
            if agg_h is not None and agg_a is not None else None
        )
        rows.append({
            "id": fixture_id,
            "competition": fixture.get("competition") or "UEFA Champions League",
            "stage": _uefa_stage_label(fixture, prediction),
            "kickoff_utc": prediction["kickoff_utc"],
            "home": prediction["home"],
            "away": prediction["away"],
            "venue": fixture.get("venue"),
            "model": "ClubElo–Poisson (experimental)",
            "forecast_generated_at": (
                prediction.get("generated_as_of_utc")
                or document.get("as_of_utc")
                or document.get("generated_at_utc")
            ),
            "p_home": probs.get("home_win"),
            "p_draw": probs.get("draw"),
            "p_away": probs.get("away_win"),
            "p_over25": p_over,
            "p_btts": p_btts,
            "p_home_advance": qualification.get("home_to_advance"),
            "p_away_advance": qualification.get("away_to_advance"),
            "lambda_home": lam_h,
            "lambda_away": lam_a,
            "model_market_forecasts": _model_market_forecasts(
                lam_h,
                lam_a,
                uncertainty=uncertainty_label,
            ),
            **score_distribution,
            "uncertainty": uncertainty_label,
            "recommendation": "MODEL FORECAST",
            "first_leg": first_leg,
            "details": dossiers.get(fixture_id),
        })
    return rows


def build_payload(
    world_cup: dict,
    ucl: dict,
    fixtures: Any,
    generated_at: str,
    *,
    market_document: dict | None = None,
    anchor_audit: dict | None = None,
    world_cup_history: dict | None = None,
    rankings: dict | None = None,
    context_document: dict | None = None,
    prospective_ledger: dict | None = None,
    odds_snapshot: dict | None = None,
    paper_ledger: dict | None = None,
    uefa_history: dict | None = None,
    top_five_fixtures: dict | None = None,
) -> dict:
    fixture_by_id = _fixture_index(fixtures)
    markets = market_index(market_document)
    # The historical intercept includes domestic home advantage. World Cup
    # fixtures are neutral, so only the development-selected residual shrinkage
    # transfers; the fitted home/draw/away intercept is deliberately zeroed.
    anchor = (
        anchor_from_audit(anchor_audit, use_fitted_bias=False)
        if anchor_audit is not None else None
    )
    dossiers = _build_dossiers(
        world_cup, ucl, fixture_by_id, generated_at,
        world_cup_history=world_cup_history,
        rankings=rankings,
        context_document=context_document,
        uefa_history=uefa_history,
    )
    rows = (
        _world_cup_rows(world_cup, fixture_by_id, markets, anchor, dossiers)
        + _ucl_rows(ucl, fixture_by_id, dossiers)
        + _top_five_rows(top_five_fixtures)
    )
    rows = _future_forecasts(rows, generated_at)
    rows.sort(key=lambda row: (row["kickoff_utc"], row["competition"], row["id"]))
    for row in rows:
        row.update({
            "decision_status": "MODEL_FORECAST_AVAILABLE",
            "model_status": "EXPERIMENTAL_BACKGROUND_AUDIT",
            "market_period": "REGULATION_90_MINUTES",
            "betting_eligible": False,
        })
        if _as_utc(row.get("forecast_generated_at")) is None:
            row["forecast_generated_at"] = generated_at
    payload = {
        "generated_at": generated_at,
        "status": "MODEL_FORECASTS_ACTIVE_CLV_BACKGROUND_AUDIT",
        "validation_protocol": {
            "mode": "MODEL_FORECAST_WITH_BACKGROUND_CLV_AUDIT",
            "model_status": "EXPERIMENTAL_BACKGROUND_AUDIT",
            "primary_confirmatory_market": "SCORE_RESOLVABLE_FULL_LINE",
            "settlement_period": "REGULATION_90_MINUTES",
            "real_money_execution": False,
            "parlays": "SIMULATION_ONLY_DISABLED_PENDING_INDIVIDUAL_EDGE",
            "governance": "docs/model-governance.md",
        },
        "betting_gate": {
            "allowed": False,
            "reason": "Real-money gate remains closed; model forecasts stay visible.",
        },
        "forecasts": rows,
    }
    if odds_snapshot is not None:
        payload = apply_odds_snapshot_to_live_payload(
            payload, odds_snapshot, now=generated_at
        )
    if prospective_ledger is not None:
        payload = apply_summary_to_live_payload(payload, prospective_summary(prospective_ledger))
    payload["paper_candidate_ranking"] = rank_paper_candidates(payload)
    if paper_ledger is not None:
        payload["paper_trading"] = public_paper_summary(paper_ledger)
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-cup", type=Path, required=True)
    parser.add_argument("--ucl", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--markets", type=Path)
    parser.add_argument("--anchor-audit", type=Path)
    parser.add_argument("--world-cup-history", type=Path)
    parser.add_argument("--rankings", type=Path)
    parser.add_argument("--contexts", type=Path)
    parser.add_argument("--prospective-ledger", type=Path)
    parser.add_argument("--odds-snapshot", type=Path)
    parser.add_argument("--paper-ledger", type=Path)
    parser.add_argument("--uefa-history", type=Path)
    parser.add_argument("--top-five-fixtures", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated-at")
    args = parser.parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    payload = build_payload(
        _read(args.world_cup), _read(args.ucl), _read(args.fixtures), generated_at,
        market_document=_read(args.markets) if args.markets else None,
        anchor_audit=_read(args.anchor_audit) if args.anchor_audit else None,
        world_cup_history=_read(args.world_cup_history) if args.world_cup_history else None,
        rankings=_read(args.rankings) if args.rankings else None,
        context_document=_read(args.contexts) if args.contexts else None,
        prospective_ledger=_read(args.prospective_ledger) if args.prospective_ledger else None,
        odds_snapshot=(
            _read(args.odds_snapshot)
            if args.odds_snapshot and args.odds_snapshot.exists()
            else None
        ),
        paper_ledger=(
            _read(args.paper_ledger)
            if args.paper_ledger and args.paper_ledger.exists()
            else None
        ),
        uefa_history=(
            _read(args.uefa_history)
            if args.uefa_history and args.uefa_history.exists()
            else None
        ),
        top_five_fixtures=(
            _read(args.top_five_fixtures)
            if args.top_five_fixtures and args.top_five_fixtures.exists()
            else None
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(payload['forecasts'])} forecasts to {args.output}")


if __name__ == "__main__":
    main()
