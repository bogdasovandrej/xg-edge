"""File-level tests for the autonomous forecast archive and registry CLIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from scripts.update_forecast_archive import update_files as update_archive_files
from scripts.update_model_registry import update_files as update_registry_files
from xgedge.automation.archive import validate_archive
from xgedge.automation.challenger import validate_registry

T0 = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _fixtures(kickoff: datetime) -> dict:
    return {
        "fixtures": [{
            "source": "uefa",
            "id": "m1",
            "competition_id": "1",
            "competition": "UEFA Champions League",
            "season_id": "2027",
            "kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
            "home_id": "home",
            "home": "Home FC",
            "away_id": "away",
            "away": "Away FC",
            "venue": "Test Arena",
            "venue_city": "Test City",
            "latitude": 50.0,
            "longitude": 8.0,
            "round": "Second qualifying round",
            "stage": "QUALIFYING",
            "leg": 1,
            "first_leg_home_score": None,
            "first_leg_away_score": None,
            "aggregate_home_score": None,
            "aggregate_away_score": None,
            "referee": "Jane Ref",
        }],
    }


def _payload(kickoff: datetime) -> dict:
    return {
        "generated_at": T0.isoformat().replace("+00:00", "Z"),
        "forecasts": [{
            "id": "m1",
            "competition": "UEFA Champions League",
            "kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
            "home": "Home FC",
            "away": "Away FC",
            "model": "ClubElo-Poisson",
            "forecast_generated_at": T0.isoformat().replace("+00:00", "Z"),
            "p_home": 0.5,
            "p_draw": 0.3,
            "p_away": 0.2,
            "lambda_home": 1.4,
            "lambda_away": 0.8,
            "top_score": "1-0",
        }],
    }


def test_archive_cli_freezes_forecast_then_appends_official_result(tmp_path: Path) -> None:
    archive_path = tmp_path / "forecast_archive.json"
    fixtures_path = tmp_path / "fixtures.json"
    payload_path = tmp_path / "live.json"
    kickoff = T0 + timedelta(hours=4)
    _write(fixtures_path, _fixtures(kickoff))
    _write(payload_path, _payload(kickoff))

    first = update_archive_files(
        archive_path,
        fixtures_path=fixtures_path,
        live_payload_path=payload_path,
        observed_at=T0 + timedelta(minutes=1),
    )

    assert first["fixture_snapshots_added"] == 1
    assert first["forecasts_added"] == 1
    archive = validate_archive(json.loads(archive_path.read_text(encoding="utf-8")))
    assert len(archive["forecasts"]) == 1

    def fake_fetcher(*args, **kwargs):
        return {
            "status": "available",
            "results": [{
                "source": "uefa",
                "id": "m1",
                "status": "FINISHED",
                "home_goals_90": 2,
                "away_goals_90": 1,
            }],
            "errors": [],
        }

    second = update_archive_files(
        archive_path,
        observed_at=kickoff + timedelta(hours=3),
        fetch_results=True,
        result_fetcher=fake_fetcher,
    )

    archive = validate_archive(json.loads(archive_path.read_text(encoding="utf-8")))
    assert second["results_added"] == 1
    assert archive["results"][0]["outcome"] == "home"
    assert len(archive["events"]) == 3


def test_model_registry_blocks_self_learning_until_fixed_evidence_threshold(tmp_path: Path) -> None:
    archive_path = tmp_path / "forecast_archive.json"
    registry_path = tmp_path / "model_registry.json"
    fixtures_path = tmp_path / "fixtures.json"
    payload_path = tmp_path / "live.json"
    kickoff = T0 + timedelta(hours=4)
    _write(fixtures_path, _fixtures(kickoff))
    _write(payload_path, _payload(kickoff))
    update_archive_files(
        archive_path,
        fixtures_path=fixtures_path,
        live_payload_path=payload_path,
        observed_at=T0 + timedelta(minutes=1),
    )

    result = update_registry_files(
        registry_path,
        archive_path=archive_path,
        evaluated_at=T0 + timedelta(minutes=2),
    )

    registry = validate_registry(json.loads(registry_path.read_text(encoding="utf-8")))
    assert result["status"] == "blocked"
    assert result["paper_promoted"] is False
    assert registry["champion"]["candidate_id"] is None
    assert registry["challengers"][0]["training"]["n_settled"] == 0

    repeated = update_registry_files(
        registry_path,
        archive_path=archive_path,
        evaluated_at=T0 + timedelta(minutes=3),
    )
    registry = validate_registry(json.loads(registry_path.read_text(encoding="utf-8")))
    assert repeated["registry_changed"] is False
    assert len(registry["challengers"]) == 1

    revised_payload = _payload(kickoff)
    revised_at = T0 + timedelta(minutes=4)
    revised_payload["generated_at"] = revised_at.isoformat().replace("+00:00", "Z")
    revised_payload["forecasts"][0]["forecast_generated_at"] = revised_payload[
        "generated_at"
    ]
    revised_payload["forecasts"][0]["p_home"] = 0.51
    revised_payload["forecasts"][0]["p_away"] = 0.19
    _write(payload_path, revised_payload)
    update_archive_files(
        archive_path,
        fixtures_path=fixtures_path,
        live_payload_path=payload_path,
        observed_at=T0 + timedelta(minutes=5),
    )
    update_registry_files(
        registry_path,
        archive_path=archive_path,
        evaluated_at=T0 + timedelta(minutes=6),
    )
    registry = validate_registry(json.loads(registry_path.read_text(encoding="utf-8")))
    assert len(registry["challengers"]) == 2
    assert registry["challengers"][0]["candidate_id"] != registry["challengers"][1][
        "candidate_id"
    ]
