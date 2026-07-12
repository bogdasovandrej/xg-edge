"""Download and parse football-data.co.uk season CSVs.

Raw layer is immutable: downloads are byte-exact copies of the source CSV
and are never re-fetched when the file already exists.  Calls without a
competition preserve the original EPL API and filenames; explicit
competitions use the top-five registry and collision-free filenames.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import requests

from xgedge.contracts import FD_LEAGUE_CODE, FD_SEASON_CODES, Col
from xgedge.data.competitions import (
    Competition,
    SourceDataUnavailable,
    raw_filename,
    resolve_competition,
    resolve_season,
)
from xgedge.data.teams import to_canonical

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# source header -> canonical odds column
_ODDS_COLUMNS = {
    "B365H": Col.B365H,
    "B365D": Col.B365D,
    "B365A": Col.B365A,
    "PSH": Col.PSH,
    "PSD": Col.PSD,
    "PSA": Col.PSA,
    "B365CH": Col.B365CH,
    "B365CD": Col.B365CD,
    "B365CA": Col.B365CA,
    "PSCH": Col.PSCH,
    "PSCD": Col.PSCD,
    "PSCA": Col.PSCA,
    "B365>2.5": Col.B365_O25,
    "B365<2.5": Col.B365_U25,
    "B365C>2.5": Col.B365C_O25,
    "B365C<2.5": Col.B365C_U25,
    "P>2.5": Col.P_O25,
    "P<2.5": Col.P_U25,
    "PC>2.5": Col.PC_O25,
    "PC<2.5": Col.PC_U25,
}


def _fd_path(
    season: str,
    directory: Path,
    competition: str | Competition | None,
) -> Path:
    if competition is None:
        return directory / f"fd_{FD_SEASON_CODES[season]}.csv"
    return directory / raw_filename("fd", season, competition)


def download_fd_season(
    season: str,
    dest_dir: Path,
    *,
    competition: str | Competition | None = None,
) -> Path:
    """Fetch a football-data CSV and return its path.

    ``competition=None`` is the legacy EPL mode.  An explicit competition
    enables a namespaced top-five raw file.  HTTP 404/410 is reported as
    :class:`SourceDataUnavailable`, allowing scheduled jobs to start before
    a future season has been published.
    """
    if competition is None:
        code = FD_SEASON_CODES[season]
        league_code = FD_LEAGUE_CODE
    else:
        period = resolve_season(season)
        comp = resolve_competition(competition)
        code = period.football_data_code
        league_code = comp.football_data_code
    dest = _fd_path(season, dest_dir, competition)
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = f"https://www.football-data.co.uk/mmz4281/{code}/{league_code}.csv"
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if getattr(resp, "status_code", None) in {404, 410}:
        raise SourceDataUnavailable(
            f"football-data has not published {league_code} for {season}"
        )
    resp.raise_for_status()
    if competition is not None:
        first_line = resp.content.lstrip(b"\xef\xbb\xbf\r\n ").splitlines()[:1]
        if (
            not first_line
            or b"Div" not in first_line[0]
            or b"Date" not in first_line[0]
        ):
            raise SourceDataUnavailable(
                f"football-data returned no CSV dataset for {league_code} {season}"
            )
    dest.write_bytes(resp.content)
    return dest


def _parse_dates(raw: pd.Series) -> pd.Series:
    """Parse fd dates, which use %d/%m/%Y or %d/%m/%y depending on season."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return pd.to_datetime(raw, format=fmt)
        except ValueError:
            continue
    # formats mixed within one file
    return pd.to_datetime(raw, format="mixed", dayfirst=True)


def _odds(raw: pd.DataFrame, src: str) -> pd.Series:
    """Odds column as float; NaN column when absent from the source file."""
    if src in raw.columns:
        return pd.to_numeric(raw[src], errors="coerce")
    return pd.Series(np.nan, index=raw.index, dtype=float)


def load_fd_season(
    season: str,
    raw_dir: Path,
    *,
    competition: str | Competition | None = None,
) -> pd.DataFrame:
    """Parse a saved fd CSV into per-match rows with canonical columns.

    Explicit multi-league mode prefixes team ids with the competition key.
    """
    path = _fd_path(season, raw_dir, competition)
    raw = pd.read_csv(path, encoding="latin-1")
    # trailing rows of bare commas parse as all-NaN
    raw = raw.dropna(how="all")
    raw = raw[raw["Date"].notna()]
    out = pd.DataFrame(
        {
            Col.DATE: _parse_dates(raw["Date"].astype(str).str.strip()),
            Col.HOME: raw["HomeTeam"].map(
                lambda n: to_canonical(n, "fd", league=competition)
            ),
            Col.AWAY: raw["AwayTeam"].map(
                lambda n: to_canonical(n, "fd", league=competition)
            ),
            Col.FTHG: pd.to_numeric(raw["FTHG"]).astype(int),
            Col.FTAG: pd.to_numeric(raw["FTAG"]).astype(int),
            Col.FTR: raw["FTR"].astype(str),
            Col.RED_H: pd.to_numeric(raw["HR"], errors="coerce").fillna(0).astype(int),
            Col.RED_A: pd.to_numeric(raw["AR"], errors="coerce").fillna(0).astype(int),
        }
    )
    for src, dst in _ODDS_COLUMNS.items():
        out[dst] = _odds(raw, src)
    out[Col.SEASON] = season
    return out.reset_index(drop=True)
