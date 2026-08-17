"""Prospective UEFA research workflow and handoff contracts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from xgedge.research.handoff import build_chat_batches, validate_audit_import
from xgedge.research.preline import build_research_workflow
from xgedge.research.screening import ResearchScreeningConfig, screen_fixtures
from xgedge.research.triggers import evaluate_execution_quote


T0 = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)


def _forecast(index: int, *, quality: float | None = None) -> dict:
    score = quality if quality is not None else 50 + index % 40
    return {
        "id": f"fixture-{index:02d}",
        "competition": f"UEFA Competition {index % 4}",
        "stage": "Qualifying",
        "kickoff_utc": (T0 + timedelta(days=1, minutes=index)).isoformat(),
        "forecast_generated_at": T0.isoformat(),
        "home": f"Home {index}",
        "away": f"Away {index}",
        "venue": "Stadium",
        "model": "test-model",
        "uncertainty": "high" if index % 5 == 0 else "medium",
        "lambda_home": 1.5 + index / 100,
        "lambda_away": 1.0,
        "expected_goals": 2.5 + index / 100,
        "model_market_forecasts": [
            {
                "market": "totals", "selection": "under", "line": 3.0,
                "label": "Under 3", "theoretical_probability": .62,
                "conservative_probability": .57,
            },
            {
                "market": "totals", "selection": "under", "line": 3.5,
                "label": "Under 3.5", "theoretical_probability": .69,
                "conservative_probability": .64,
            },
            {
                "market": "btts", "selection": "no", "line": None,
                "label": "BTTS No", "theoretical_probability": .55,
                "conservative_probability": .50,
            },
            {
                "market": "team_totals", "selection": "away_under", "line": 1.5,
                "label": "Away under 1.5", "theoretical_probability": .67,
                "conservative_probability": .62,
            },
        ],
        "details": {
            "generated_as_of": T0.isoformat(),
            "data_quality": {"score": score, "warnings": []},
            "tail_risk": {"score": 20 + index % 70},
            "context_availability": {
                "lineups": {"status": "unavailable" if index % 3 == 0 else "available"},
                "weather": {"status": "available"},
                "referee": {"status": "available"},
            },
            "teams": {},
        },
    }


def test_53_fixture_scan_selects_17_plus_3_deterministically() -> None:
    forecasts = [_forecast(index) for index in range(53)]
    original = deepcopy(forecasts)
    first = screen_fixtures(forecasts, generated_at=T0.isoformat())
    second = screen_fixtures(forecasts, generated_at=T0.isoformat())
    assert forecasts == original
    assert first == second
    assert first["summary"] == {
        "total_fixtures": 53,
        "machine_scanned": 53,
        "preline_selected": 20,
        "exploitation_slots": 17,
        "exploration_slots": 3,
        "not_selected": 33,
    }
    selected = [row for row in first["records"] if row["status"] == "PRELINE_SELECTED"]
    assert sum(row["selection_lane"] == "EXPLOITATION" for row in selected) == 17
    assert sum(row["selection_lane"] == "EXPLORATION" for row in selected) == 3


def test_missing_data_penalty_lowers_priority() -> None:
    complete = _forecast(1, quality=80)
    missing = _forecast(2, quality=80)
    missing["details"]["context_availability"] = {
        "lineups": {"status": "unavailable"},
        "weather": {"status": "unavailable"},
        "referee": {"status": "unavailable"},
    }
    result = screen_fixtures(
        [complete, missing], generated_at=T0.isoformat(),
        config=ResearchScreeningConfig(preline_pool_size=2, exploration_slots=0),
    )
    scores = {row["fixture_id"]: row["research_priority_score"] for row in result["records"]}
    assert scores[complete["id"]] > scores[missing["id"]]


def test_preline_market_sets_are_diverse_and_make_four_batches() -> None:
    forecasts = [_forecast(index) for index in range(20)]
    workflow = build_research_workflow(forecasts, generated_at=T0.isoformat())
    assert len(workflow["market_sets"]) == 20
    for market_set in workflow["market_sets"].values():
        candidates = market_set["candidates"]
        assert 0 <= len(candidates) <= 3
        assert len({row["market_cluster"] for row in candidates}) == len(candidates)
        assert all(row["trigger_price"] > row["fair_odds_conservative"] > 1 for row in candidates)
    batches = build_chat_batches(workflow, forecasts)
    assert [len(batch["fixtures"]) for batch in batches] == [5, 5, 5, 5]


def test_chat_import_round_trip_rejects_unknown_candidate() -> None:
    forecasts = [_forecast(index) for index in range(20)]
    workflow = build_research_workflow(forecasts, generated_at=T0.isoformat())
    fixture_id = workflow["selected_fixture_ids"][0]
    candidate_id = workflow["market_sets"][fixture_id]["candidates"][0]["candidate_id"]
    source = {
        "schema_version": "human_preline_audit/1.0",
        "fixture_id": fixture_id,
        "analysis_stage": "PRELINE",
        "candidate_updates": [{"candidate_id": candidate_id, "decision": "WATCH"}],
    }
    snapshot = validate_audit_import(source, workflow, imported_at=T0.isoformat())
    assert snapshot["immutable"] is True
    bad = deepcopy(source)
    bad["candidate_updates"][0]["candidate_id"] = "unknown"
    with pytest.raises(ValueError, match="unknown candidate"):
        validate_audit_import(bad, workflow)


def test_trigger_state_machine_and_no_post_kickoff_leakage() -> None:
    kickoff = (T0 + timedelta(hours=2)).isoformat()
    candidate = {
        "candidate_id": "c1", "fixture_id": "f1", "market": "totals",
        "selection": "under", "line": 3.0, "trigger_price": 1.80,
        "kickoff_utc": kickoff,
    }
    quote = {
        "fixture_id": "f1", "market": "totals", "selection": "under", "line": 3.0,
        "odds": 1.82, "reference_odds": 1.80,
        "captured_at_utc": (T0 + timedelta(minutes=5)).isoformat(),
    }
    assert evaluate_execution_quote(
        candidate, quote, now=(T0 + timedelta(minutes=6)).isoformat()
    )["status"] == "TRIGGER_HIT"
    assert evaluate_execution_quote(
        candidate, quote, now=(T0 + timedelta(minutes=6)).isoformat(),
        was_preline_selected=False,
    )["status"] == "LATE_WILDCARD"
    late = dict(quote, captured_at_utc=(T0 + timedelta(hours=2)).isoformat())
    with pytest.raises(ValueError, match="post-kickoff"):
        evaluate_execution_quote(candidate, late, now=(T0 + timedelta(hours=2)).isoformat())
