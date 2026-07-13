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
from xgedge.dossier.builder import build_match_dossier


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


def _fixture_index(payload: Any) -> dict[str, dict]:
    rows = payload.get("fixtures", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("fixture snapshot must be a list or contain a fixtures list")
    return {str(row["id"]): row for row in rows if isinstance(row, dict) and row.get("id")}


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


def _build_dossiers(
    world_cup: dict,
    ucl: dict,
    fixtures: dict[str, dict],
    generated_at: str,
    *,
    world_cup_history: dict | None,
    rankings: dict | None,
    context_document: dict | None,
) -> dict[str, dict]:
    output: dict[str, dict] = {}
    history = _world_cup_history(world_cup_history)
    national_priors = _national_priors(rankings)
    supplied = context_document.get("fixtures", {}) if isinstance(context_document, dict) else {}
    for prediction in world_cup.get("predictions", []):
        fixture_id = str(prediction.get("fixture_id"))
        source = fixtures.get(fixture_id)
        if not source or not source.get("home_id") or not source.get("away_id"):
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
        if not source or not source.get("home_id") or not source.get("away_id"):
            continue
        fixture = {**source, "scope": "club", "competition_level": "uefa_champions_league_qualifying"}
        priors = {}
        if home_rating.get("elo") is not None:
            priors[("club", str(fixture["home_id"]))] = float(home_rating["elo"])
        if away_rating.get("elo") is not None:
            priors[("club", str(fixture["away_id"]))] = float(away_rating["elo"])
        dossier = build_match_dossier(
            fixture,
            [],
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
        fixture = fixtures.get(str(prediction["fixture_id"]), {})
        scores = prediction.get("top_scores") or []
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
            "p_home": public_probs["home"],
            "p_draw": public_probs["draw"],
            "p_away": public_probs["away"],
            "probability_basis": "market_anchored" if comparison else "fundamental_only",
            "raw_model_1x2": raw,
            "p_over25": probs["over_2_5"],
            "p_btts": probs["btts_yes"],
            "top_score": scores[0]["score"] if scores else None,
            "uncertainty": _uncertainty(*interval),
            "recommendation": "NO BET",
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
        scores = prediction.get("most_likely_scores_90m") or []
        agg_h, agg_a = fixture.get("aggregate_home_score"), fixture.get("aggregate_away_score")
        first_leg = (
            f"Агрегат {agg_h}:{agg_a}"
            if agg_h is not None and agg_a is not None else None
        )
        rows.append({
            "id": fixture_id,
            "competition": "UEFA Champions League",
            "stage": "1-й квалификационный раунд · ответный матч",
            "kickoff_utc": prediction["kickoff_utc"],
            "home": prediction["home"],
            "away": prediction["away"],
            "venue": fixture.get("venue"),
            "model": "ClubElo–Poisson (experimental)",
            "p_home": probs.get("home_win"),
            "p_draw": probs.get("draw"),
            "p_away": probs.get("away_win"),
            "p_over25": p_over,
            "p_btts": p_btts,
            "p_home_advance": qualification.get("home_to_advance"),
            "p_away_advance": qualification.get("away_to_advance"),
            "top_score": scores[0]["score"] if scores else None,
            "uncertainty": _uncertainty(interval.get("low"), interval.get("high")),
            "recommendation": "NO BET",
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
    )
    rows = _world_cup_rows(world_cup, fixture_by_id, markets, anchor, dossiers) + _ucl_rows(ucl, fixture_by_id, dossiers)
    rows.sort(key=lambda row: (row["kickoff_utc"], row["competition"], row["id"]))
    return {
        "generated_at": generated_at,
        "status": "experimental-no-bet",
        "betting_gate": {
            "allowed": False,
            "reason": "Positive prospective CLV has not been demonstrated.",
        },
        "forecasts": rows,
    }


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
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(payload['forecasts'])} forecasts to {args.output}")


if __name__ == "__main__":
    main()
