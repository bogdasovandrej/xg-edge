"""Declared competition coverage and the api-football fixture adapter."""
from __future__ import annotations

import pytest

from xgedge.data.api_football import SUPPORTED_KEYS, fetch_fixtures, normalize_fixture
from xgedge.data.coverage import COVERAGE_BY_KEY, coverage_report


def test_every_requested_competition_is_declared() -> None:
    required = {
        "epl", "la_liga", "bundesliga", "serie_a", "ligue_1",     # top-5 leagues
        "fa_cup", "efl_cup", "copa_del_rey", "dfb_pokal",
        "coppa_italia", "coupe_de_france",                        # top-5 cups
        "ucl", "uel", "uecl",                                     # UEFA, all stages
        "rpl",                                                    # Russian Premier League
    }
    assert required <= set(COVERAGE_BY_KEY)


def test_uefa_competitions_need_no_extra_key() -> None:
    for key in ("ucl", "uel", "uecl"):
        assert COVERAGE_BY_KEY[key].availability == "COVERED"
        assert COVERAGE_BY_KEY[key].api_key_env is None


def test_missing_key_is_reported_as_a_gap_not_as_empty() -> None:
    report = coverage_report(available_keys=set())
    statuses = {row["key"]: row["effective_status"] for row in report["competitions"]}
    assert statuses["rpl"] == "MISSING_KEY"
    assert statuses["ucl"] == "ACTIVE"

    with_key = coverage_report(available_keys={"API_FOOTBALL_KEY"})
    statuses_with_key = {row["key"]: row["effective_status"] for row in with_key["competitions"]}
    assert statuses_with_key["rpl"] == "ACTIVE"
    assert statuses_with_key["epl"] == "MISSING_KEY"  # needs the other key


def test_adapter_makes_no_request_without_a_key() -> None:
    with pytest.raises(ValueError):
        fetch_fixtures(api_key="")


def test_adapter_rejects_unsupported_competition() -> None:
    with pytest.raises(KeyError):
        fetch_fixtures(api_key="x", keys=["ucl"])


def _fixture(name: str = "Copa del Rey") -> dict:
    return {
        "fixture": {
            "id": 99, "date": "2026-09-01T18:00:00+00:00",
            "venue": {"name": "Stadium", "city": "Madrid"},
        },
        "league": {"id": 143, "name": name, "season": 2026, "round": "Round of 32"},
        "teams": {"home": {"id": 1, "name": "A"}, "away": {"id": 2, "name": "B"}},
    }


def test_normalize_accepts_the_expected_league() -> None:
    row = normalize_fixture(_fixture(), key="copa_del_rey", expected_name="Copa del Rey")
    assert row["id"] == "apifootball:copa_del_rey:99"
    assert row["competition"] == "Copa del Rey"
    assert row["kickoff_utc"] == "2026-09-01T18:00:00Z"
    assert row["stage"] == "Domestic cup"


def test_normalize_fails_closed_on_the_wrong_league() -> None:
    """A wrong league id must not silently file another country's cup."""
    with pytest.raises(ValueError):
        normalize_fixture(_fixture("Coppa Italia"), key="copa_del_rey",
                          expected_name="Copa del Rey")


def test_normalize_skips_incomplete_rows_without_raising() -> None:
    payload = _fixture()
    payload["teams"]["home"] = {"id": 1, "name": ""}
    assert normalize_fixture(payload, key="copa_del_rey", expected_name="Copa del Rey") is None


def test_rpl_is_marked_as_a_league_not_a_cup() -> None:
    payload = _fixture("Premier League")
    payload["league"]["id"] = 235
    row = normalize_fixture(payload, key="rpl", expected_name="Russian Premier League")
    assert row["stage"] == "Domestic league"


def test_supported_keys_all_exist_in_the_registry() -> None:
    for key in SUPPORTED_KEYS:
        assert key in COVERAGE_BY_KEY
        assert COVERAGE_BY_KEY[key].api_key_env == "API_FOOTBALL_KEY"
