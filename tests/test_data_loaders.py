"""Tests for the xgedge data layer: teams, fd/understat loaders, assemble."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from xgedge.contracts import Col
from xgedge.data.assemble import build_cleaned
from xgedge.data.football_data import download_fd_season, load_fd_season
from xgedge.data.teams import to_canonical
from xgedge.data.understat import (
    decode_blob,
    download_understat_season,
    load_understat_season,
)

# ---------------------------------------------------------------------------
# fixture builders (inline, no shared conftest)
# ---------------------------------------------------------------------------

FD_HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,Referee,HY,AY,HR,AR,"
    "B365H,B365D,B365A,PSH,PSD,PSA,B365>2.5,B365<2.5,"
    "B365CH,B365CD,B365CA,PSCH,PSCD,PSCA,B365C>2.5,B365C<2.5"
)


def write_fd_csv(raw_dir: Path, code: str, rows: list[str]) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"fd_{code}.csv"
    body = "\n".join([FD_HEADER, *rows]) + "\n"
    path.write_bytes(body.encode("latin-1"))
    return path


FD_ROWS_2122 = [
    # latin-1 referee name; full 4-digit-year dates
    "E0,13/08/2021,20:00,Brentford,Arsenal,2,0,H,Jos\xe9 Mu\xf1oz,1,2,0,0,"
    "3.4,3.3,2.2,3.51,3.4,2.23,2.1,1.8,3.6,3.4,2.1,3.7,3.45,2.15,2.05,1.85",
    # empty PSH cell -> NaN; one red card each side
    "E0,14/08/2021,15:00,Man United,Leeds,5,1,H,Referee,1,1,1,1,"
    "1.5,4.5,6.0,,4.6,6.2,1.7,2.2,1.55,4.4,6.5,1.52,4.75,6.4,1.65,2.3",
    # fully-empty trailing rows as football-data ships them
    "," * (FD_HEADER.count(",")),
    "," * (FD_HEADER.count(",")),
]


def understat_payload_2021(include_second_match: bool = True) -> dict:
    dates_data = [
        {
            "isResult": True,
            "h": {"title": "Brentford"},
            "a": {"title": "Arsenal"},
            "goals": {"h": "2", "a": "0"},
            "xG": {"h": "1.31", "a": "1.12"},
            "datetime": "2021-08-13 20:00:00",
        },
        {
            # future fixture: must be skipped
            "isResult": False,
            "h": {"title": "Chelsea"},
            "a": {"title": "Liverpool"},
            "goals": {"h": None, "a": None},
            "xG": {"h": None, "a": None},
            "datetime": "2021-08-28 17:30:00",
        },
    ]
    if include_second_match:
        dates_data.append(
            {
                "isResult": True,
                "h": {"title": "Manchester United"},
                "a": {"title": "Leeds United"},
                "goals": {"h": "5", "a": "1"},
                "xG": {"h": "2.91", "a": "1.19"},
                "datetime": "2021-08-14 15:00:00",
            }
        )
    teams_data = {
        "83": {
            "title": "Brentford",
            "history": [
                {
                    "h_a": "h",
                    "xG": 1.31,
                    "npxG": 1.20,
                    "npxGA": 1.05,
                    "ppda": {"att": 250, "def": 25},
                    "deep": 5,
                    "deep_allowed": 7,
                    "scored": 2,
                    "missed": 0,
                    "date": "2021-08-13 20:00:00",
                }
            ],
        },
        "71": {
            "title": "Arsenal",
            "history": [
                {
                    "h_a": "a",
                    "xG": 1.12,
                    "npxG": 1.05,
                    "npxGA": 1.20,
                    "ppda": {"att": 300, "def": 20},
                    "deep": 8,
                    "deep_allowed": 5,
                    "scored": 0,
                    "missed": 2,
                    "date": "2021-08-13 20:00:00",
                }
            ],
        },
        "89": {
            "title": "Manchester United",
            "history": [
                {
                    "h_a": "h",
                    "xG": 2.91,
                    "npxG": 2.50,
                    "npxGA": 1.19,
                    "ppda": {"att": 200, "def": 40},
                    "deep": 10,
                    "deep_allowed": 3,
                    "scored": 5,
                    "missed": 1,
                    "date": "2021-08-14 15:00:00",
                }
            ],
        },
        # Leeds intentionally absent -> away metrics stay NaN for match 2
    }
    return {"dates_data": dates_data, "teams_data": teams_data}


def write_understat_json(raw_dir: Path, year: int, payload: dict) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"understat_{year}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# teams
# ---------------------------------------------------------------------------


def test_to_canonical_fd_names():
    assert to_canonical("Man City", "fd") == "man_city"
    assert to_canonical("Nott'm Forest", "fd") == "nottm_forest"
    assert to_canonical("Sheffield United", "fd") == "sheffield_united"


def test_to_canonical_understat_names_and_alternates():
    assert to_canonical("Manchester City", "understat") == "man_city"
    assert to_canonical("Wolverhampton Wanderers", "understat") == "wolves"
    assert to_canonical("Newcastle United", "understat") == "newcastle"
    # alternate spellings understat has used
    assert to_canonical("Leeds", "understat") == "leeds"
    assert to_canonical("Leeds United", "understat") == "leeds"


def test_to_canonical_unknown_name_raises_helpful_keyerror():
    with pytest.raises(KeyError, match="Real Madrid"):
        to_canonical("Real Madrid", "fd")
    with pytest.raises(KeyError, match="understat"):
        to_canonical("Nope FC", "understat")


def test_to_canonical_unknown_source_raises():
    with pytest.raises(KeyError, match="source"):
        to_canonical("Arsenal", "espn")


# ---------------------------------------------------------------------------
# football-data loader
# ---------------------------------------------------------------------------


def test_load_fd_season_parses_matches(tmp_path: Path):
    write_fd_csv(tmp_path, "2122", FD_ROWS_2122)
    df = load_fd_season("2021-22", tmp_path)

    assert len(df) == 2  # trailing empty rows dropped
    row0 = df.iloc[0]
    assert row0[Col.DATE] == pd.Timestamp("2021-08-13")
    assert row0[Col.HOME] == "brentford"
    assert row0[Col.AWAY] == "arsenal"
    assert row0[Col.FTHG] == 2 and row0[Col.FTAG] == 0
    assert row0[Col.FTR] == "H"
    assert row0[Col.RED_H] == 0 and row0[Col.RED_A] == 0
    assert row0[Col.B365H] == pytest.approx(3.4)
    assert row0[Col.B365_O25] == pytest.approx(2.1)
    assert row0[Col.B365C_U25] == pytest.approx(1.85)
    assert row0[Col.SEASON] == "2021-22"

    row1 = df.iloc[1]
    assert row1[Col.HOME] == "man_united" and row1[Col.AWAY] == "leeds"
    assert np.isnan(row1[Col.PSH])  # empty odds cell -> NaN
    assert row1[Col.PSD] == pytest.approx(4.6)
    assert row1[Col.RED_H] == 1 and row1[Col.RED_A] == 1


def test_load_fd_season_two_digit_year_dates(tmp_path: Path):
    rows = [
        "E0,05/08/22,20:00,Crystal Palace,Arsenal,0,2,A,Referee,2,1,0,0,"
        "4.0,3.5,1.95,4.1,3.6,1.97,2.0,1.9,4.2,3.5,1.9,4.3,3.65,1.92,1.98,1.95"
    ]
    write_fd_csv(tmp_path, "2223", rows)
    df = load_fd_season("2022-23", tmp_path)
    assert df.loc[0, Col.DATE] == pd.Timestamp("2022-08-05")


def test_load_fd_season_missing_odds_column_is_nan(tmp_path: Path):
    header = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HR,AR,B365H,B365D,B365A"
    body = header + "\nE0,16/08/2024,Man United,Fulham,1,0,H,0,0,1.6,4.2,5.5\n"
    (tmp_path / "fd_2425.csv").write_bytes(body.encode("latin-1"))
    df = load_fd_season("2024-25", tmp_path)
    assert df.loc[0, Col.B365H] == pytest.approx(1.6)
    for col in (Col.PSH, Col.PSCH, Col.B365_O25, Col.B365C_U25):
        assert np.isnan(df.loc[0, col])


def test_download_fd_season_skips_existing_file(tmp_path: Path, monkeypatch):
    existing = tmp_path / "fd_2122.csv"
    existing.write_bytes(b"raw-bytes")

    def boom(*args, **kwargs):
        raise AssertionError("network must not be touched when file exists")

    monkeypatch.setattr("xgedge.data.football_data.requests.get", boom)
    path = download_fd_season("2021-22", tmp_path)
    assert path == existing
    assert path.read_bytes() == b"raw-bytes"  # raw layer untouched


# ---------------------------------------------------------------------------
# understat blob decoder + loader
# ---------------------------------------------------------------------------


def hex_escape(text: str) -> str:
    """Escape like understat: quotes and non-ASCII UTF-8 bytes as \\xNN."""
    out = []
    for byte in text.encode("utf-8"):
        if byte == 0x22 or byte >= 0x80:
            out.append(f"\\x{byte:02x}")
        else:
            out.append(chr(byte))
    return "".join(out)


def test_decode_blob_roundtrips_hex_escapes():
    original = {"title": "Тоттенхэм café", "xG": "1.25"}
    blob = hex_escape(json.dumps(original, ensure_ascii=False))
    assert "\\x22" in blob  # quotes really are escaped
    assert decode_blob(blob) == original


def test_load_understat_season(tmp_path: Path):
    write_understat_json(tmp_path, 2021, understat_payload_2021())
    df = load_understat_season("2021-22", tmp_path)

    assert len(df) == 2  # isResult=False fixture skipped
    row0 = df.loc[df[Col.HOME] == "brentford"].iloc[0]
    assert row0[Col.DATE] == pd.Timestamp("2021-08-13")  # time stripped
    assert row0[Col.AWAY] == "arsenal"
    assert row0[Col.XG_H] == pytest.approx(1.31)
    assert row0[Col.NPXG_H] == pytest.approx(1.20)
    assert row0[Col.NPXG_A] == pytest.approx(1.05)
    assert row0[Col.PPDA_H] == pytest.approx(250 / 25)
    assert row0[Col.PPDA_A] == pytest.approx(300 / 20)
    assert row0[Col.DEEP_H] == pytest.approx(5)
    assert row0[Col.DEEP_A] == pytest.approx(8)
    assert row0[Col.SEASON] == "2021-22"

    # Leeds missing from teamsData: match kept, away metrics NaN
    row1 = df.loc[df[Col.HOME] == "man_united"].iloc[0]
    assert row1[Col.AWAY] == "leeds"
    assert row1[Col.NPXG_H] == pytest.approx(2.50)
    assert np.isnan(row1[Col.NPXG_A])
    assert np.isnan(row1[Col.PPDA_A])
    assert np.isnan(row1[Col.DEEP_A])
    assert row1[Col.XG_A] == pytest.approx(1.19)


def test_download_understat_season_skips_existing_file(tmp_path: Path, monkeypatch):
    existing = tmp_path / "understat_2021.json"
    existing.write_text("{}", encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("network must not be touched when file exists")

    monkeypatch.setattr("xgedge.data.understat.requests.get", boom)
    assert download_understat_season("2021-22", tmp_path) == existing


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


def test_build_cleaned_joins_and_writes_parquet(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    write_fd_csv(raw_dir, "2122", FD_ROWS_2122)
    write_understat_json(raw_dir, 2021, understat_payload_2021())
    out_path = tmp_path / "cleaned" / "matches.parquet"

    df = build_cleaned(["2021-22"], raw_dir, out_path)

    assert len(df) == 2
    assert list(df[Col.DATE]) == sorted(df[Col.DATE])
    assert df.loc[0, Col.MATCH_ID] == "2021-22_20210813_brentford_arsenal"
    assert df.loc[1, Col.MATCH_ID] == "2021-22_20210814_man_united_leeds"
    # both sources' columns present on the joined row
    assert df.loc[0, Col.FTHG] == 2
    assert df.loc[0, Col.XG_H] == pytest.approx(1.31)
    assert df.loc[0, Col.NPXG_H] == pytest.approx(1.20)
    assert df.loc[0, Col.B365H] == pytest.approx(3.4)

    assert out_path.exists()
    roundtrip = pd.read_parquet(out_path)
    assert list(roundtrip[Col.MATCH_ID]) == list(df[Col.MATCH_ID])


def test_build_cleaned_raises_when_join_drops_too_many(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    write_fd_csv(raw_dir, "2122", FD_ROWS_2122)
    # understat is missing the man_united match -> join keeps 1/2 fd rows
    write_understat_json(
        raw_dir, 2021, understat_payload_2021(include_second_match=False)
    )
    out_path = tmp_path / "cleaned" / "matches.parquet"

    with pytest.raises(AssertionError, match=r"man_united vs leeds"):
        build_cleaned(["2021-22"], raw_dir, out_path)
    assert not out_path.exists()
