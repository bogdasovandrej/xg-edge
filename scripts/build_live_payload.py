"""Combine World Cup and UCL experiment outputs for the public live site."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _world_cup_rows(document: dict, fixtures: dict[str, dict]) -> list[dict]:
    rows = []
    for prediction in document.get("predictions", []):
        probs = prediction["probabilities"]
        interval = prediction.get("uncertainty", {}).get("p_home", [None, None])
        fixture = fixtures.get(str(prediction["fixture_id"]), {})
        scores = prediction.get("top_scores") or []
        rows.append({
            "id": str(prediction["fixture_id"]),
            "competition": "FIFA World Cup 2026",
            "stage": "Полуфинал" if prediction.get("stage") == "Semi-final" else prediction.get("stage", ""),
            "kickoff_utc": prediction["kickoff_utc"],
            "home": TEAM_RU.get(prediction["home"], prediction["home"]),
            "away": TEAM_RU.get(prediction["away"], prediction["away"]),
            "venue": fixture.get("venue"),
            "model": prediction.get("model"),
            "p_home": probs["home"],
            "p_draw": probs["draw"],
            "p_away": probs["away"],
            "p_over25": probs["over_2_5"],
            "p_btts": probs["btts_yes"],
            "top_score": scores[0]["score"] if scores else None,
            "uncertainty": _uncertainty(*interval),
            "recommendation": "NO BET",
            "first_leg": None,
        })
    return rows


def _ucl_rows(document: dict, fixtures: dict[str, dict]) -> list[dict]:
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
        })
    return rows


def build_payload(world_cup: dict, ucl: dict, fixtures: Any, generated_at: str) -> dict:
    fixture_by_id = _fixture_index(fixtures)
    rows = _world_cup_rows(world_cup, fixture_by_id) + _ucl_rows(ucl, fixture_by_id)
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generated-at")
    args = parser.parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    payload = build_payload(
        _read(args.world_cup), _read(args.ucl), _read(args.fixtures), generated_at
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(payload['forecasts'])} forecasts to {args.output}")


if __name__ == "__main__":
    main()
