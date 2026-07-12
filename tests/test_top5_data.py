"""Registry and mocked ingestion tests for top-five 2026/27 data."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts import download_top5
from xgedge.contracts import Col
from xgedge.data.competitions import (
    TOP5_COMPETITIONS,
    TOP5_SEASONS,
    SourceDataUnavailable,
    raw_filename,
    resolve_competition,
)
from xgedge.data.football_data import download_fd_season, load_fd_season
from xgedge.data.teams import slugify_team, to_canonical
from xgedge.data.understat import download_understat_season


def test_top5_registry_has_exact_source_identifiers():
    expected = {
        "epl": ("E0", "EPL"),
        "la_liga": ("SP1", "La_liga"),
        "bundesliga": ("D1", "Bundesliga"),
        "serie_a": ("I1", "Serie_A"),
        "ligue_1": ("F1", "Ligue_1"),
    }
    assert {
        key: (value.football_data_code, value.understat_league)
        for key, value in TOP5_COMPETITIONS.items()
    } == expected
    assert TOP5_SEASONS["2026-27"].football_data_code == "2627"
    assert TOP5_SEASONS["2026-27"].understat_year == 2026
    assert resolve_competition("SP1").key == "la_liga"


def test_multileague_team_ids_use_aliases_slug_and_league_prefix():
    assert to_canonical("Ath Madrid", "fd", league="la_liga") == (
        "la_liga:atletico_madrid"
    )
    assert to_canonical("Atletico Madrid", "understat", league="la_liga") == (
        "la_liga:atletico_madrid"
    )
    assert to_canonical("1. FC Köln", "fd", league="bundesliga") == (
        "bundesliga:1_fc_koln"
    )
    assert slugify_team("  Paris FC  ") == "paris_fc"
    # Legacy EPL API remains byte-for-byte compatible downstream.
    assert to_canonical("Man City", "fd") == "man_city"


class _Response:
    def __init__(
        self,
        content: bytes = b"Div,Date\n",
        status_code: int = 200,
        payload: dict | None = None,
    ):
        self.content = content
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))

    def json(self) -> dict:
        assert self.payload is not None
        return self.payload


def test_download_fd_top5_uses_registered_url_and_namespaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(b"Div,Date\nsource-bytes")

    monkeypatch.setattr("xgedge.data.football_data.requests.get", fake_get)
    path = download_fd_season("2026-27", tmp_path, competition="la_liga")

    assert path == tmp_path / "fd_la_liga_2627.csv"
    assert path.read_bytes() == b"Div,Date\nsource-bytes"
    assert calls[0][0].endswith("/2627/SP1.csv")
    assert calls[0][1]["timeout"] == 30


def test_load_fd_top5_prefixes_teams_and_keeps_season(tmp_path: Path):
    path = tmp_path / raw_filename("fd", "2026-27", "serie_a")
    path.write_text(
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HR,AR\n"
        "I1,22/08/2026,Inter,Milan,2,1,H,0,0\n",
        encoding="latin-1",
    )
    frame = load_fd_season("2026-27", tmp_path, competition="serie_a")
    assert frame.loc[0, Col.HOME] == "serie_a:inter_milan"
    assert frame.loc[0, Col.AWAY] == "serie_a:ac_milan"
    assert frame.loc[0, Col.SEASON] == "2026-27"
    assert frame.loc[0, Col.DATE] == pd.Timestamp("2026-08-22")


def test_unpublished_fd_season_does_not_create_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "xgedge.data.football_data.requests.get",
        lambda *args, **kwargs: _Response(status_code=404),
    )
    with pytest.raises(SourceDataUnavailable, match="not published"):
        download_fd_season("2026-27", tmp_path, competition="ligue_1")
    assert not (tmp_path / "fd_ligue_1_2627.csv").exists()


def test_download_understat_top5_uses_registered_url_and_namespaced_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(payload={"dates": [], "teams": {}})

    monkeypatch.setattr("xgedge.data.understat.requests.get", fake_get)
    path = download_understat_season(
        "2026-27", tmp_path, competition="bundesliga"
    )
    assert path == tmp_path / "understat_bundesliga_2026.json"
    assert calls[0][0].endswith("/Bundesliga/2026")
    assert calls[0][1]["timeout"] == 30


def test_download_top5_cli_tolerates_unpublished_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    def unavailable(*args, **kwargs):
        raise SourceDataUnavailable("not available")

    monkeypatch.setattr(download_top5, "download_fd_season", unavailable)
    result = download_top5.main(
        [
            "--competitions",
            "epl",
            "--sources",
            "fd",
            "--dest",
            str(tmp_path),
            "--pause",
            "0",
        ]
    )
    assert result == 0
    assert "not published yet" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
