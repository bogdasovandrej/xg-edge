"""Download and parse understat.com league season data.

Since late 2025 understat serves season data as JSON from
``GET /getLeagueData/{league}/{year}`` (keys ``dates`` and ``teams``).
Older pages embedded the same structures as hex-escaped ``JSON.parse``
string literals named ``datesData``/``teamsData``; that path is kept as a
fallback for archived HTML. Both are stored as one JSON file per season.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from xgedge.contracts import LEAGUE, UNDERSTAT_YEARS, Col
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


def decode_blob(blob: str) -> Any:
    """Decode an understat ``JSON.parse('...')`` hex-escaped blob.

    The blob escapes UTF-8 bytes as ``\\xNN``; unicode_escape turns them
    into latin-1 code points, which re-encode to the original UTF-8 bytes.
    """
    text = (
        blob.encode("utf-8")
        .decode("unicode_escape")
        .encode("latin-1")
        .decode("utf-8")
    )
    return json.loads(text)


def _extract_blob(html: str, var: str) -> Any:
    match = re.search(rf"{var}\s*=\s*JSON\.parse\('(.*?)'\)", html, flags=re.DOTALL)
    if match is None:
        raise ValueError(f"Could not find {var} JSON.parse blob in understat page")
    return decode_blob(match.group(1))


def _understat_path(
    season: str,
    directory: Path,
    competition: str | Competition | None,
) -> Path:
    if competition is None:
        return directory / f"understat_{UNDERSTAT_YEARS[season]}.json"
    return directory / raw_filename("understat", season, competition)


def download_understat_season(
    season: str,
    dest_dir: Path,
    *,
    competition: str | Competition | None = None,
) -> Path:
    """Fetch Understat league data for ``season`` and save it as JSON.

    Saves ``{"dates_data": ..., "teams_data": ...}`` as
    a legacy EPL or namespaced multi-league file; skips existing files.
    """
    if competition is None:
        year = UNDERSTAT_YEARS[season]
        league = LEAGUE
    else:
        period = resolve_season(season)
        comp = resolve_competition(competition)
        year = period.understat_year
        league = comp.understat_league
    dest = _understat_path(season, dest_dir, competition)
    if dest.exists():
        return dest
    dest_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest"}
    url = f"https://understat.com/getLeagueData/{league}/{year}"
    resp = requests.get(url, headers=headers, timeout=30)
    if getattr(resp, "status_code", None) in {404, 410}:
        raise SourceDataUnavailable(
            f"Understat has not published {league} for {season}"
        )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or "dates" not in data or "teams" not in data:
        raise SourceDataUnavailable(
            f"Understat returned no league dataset for {league} {season}"
        )
    payload = {"dates_data": data["dates"], "teams_data": data["teams"]}
    dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return dest


def parse_league_page(html: str) -> dict:
    """Fallback parser for pre-2026 understat pages with embedded blobs."""
    return {
        "dates_data": _extract_blob(html, "datesData"),
        "teams_data": _extract_blob(html, "teamsData"),
    }


def _team_history_lookup(
    teams_data: dict,
    competition: str | Competition | None = None,
) -> dict:
    """Index teamsData history rows by (canonical team, date, venue)."""
    lookup: dict = {}
    for team in teams_data.values():
        canon = to_canonical(team["title"], "understat", league=competition)
        for row in team["history"]:
            ppda = row.get("ppda") or {}
            att, deff = ppda.get("att"), ppda.get("def")
            key = (canon, pd.Timestamp(row["date"]).date(), row["h_a"])
            lookup[key] = {
                "npxg": float(row["npxG"]),
                # PPDA undefined when no defensive actions were recorded
                "ppda": float(att) / float(deff) if deff else np.nan,
                "deep": float(row["deep"]),
            }
    return lookup


def load_understat_season(
    season: str,
    raw_dir: Path,
    *,
    competition: str | Competition | None = None,
) -> pd.DataFrame:
    """Parse a saved understat JSON into per-match rows.

    Matches missing from teamsData keep NaN npxG/PPDA/deep; only finished
    matches (``isResult``) are returned. ``Col.DATE`` is the date part of
    the kickoff datetime.
    """
    path = _understat_path(season, raw_dir, competition)
    payload = json.loads(path.read_text(encoding="utf-8"))
    lookup = _team_history_lookup(payload["teams_data"], competition)

    columns = [
        Col.DATE,
        Col.HOME,
        Col.AWAY,
        Col.XG_H,
        Col.XG_A,
        Col.NPXG_H,
        Col.NPXG_A,
        Col.PPDA_H,
        Col.PPDA_A,
        Col.DEEP_H,
        Col.DEEP_A,
        Col.SEASON,
    ]
    rows = []
    for item in payload["dates_data"]:
        if not item.get("isResult"):
            continue
        home = to_canonical(item["h"]["title"], "understat", league=competition)
        away = to_canonical(item["a"]["title"], "understat", league=competition)
        date = pd.Timestamp(item["datetime"]).normalize()
        rec_h = lookup.get((home, date.date(), "h"), {})
        rec_a = lookup.get((away, date.date(), "a"), {})
        rows.append(
            {
                Col.DATE: date,
                Col.HOME: home,
                Col.AWAY: away,
                Col.XG_H: float(item["xG"]["h"]),
                Col.XG_A: float(item["xG"]["a"]),
                Col.NPXG_H: rec_h.get("npxg", np.nan),
                Col.NPXG_A: rec_a.get("npxg", np.nan),
                Col.PPDA_H: rec_h.get("ppda", np.nan),
                Col.PPDA_A: rec_a.get("ppda", np.nan),
                Col.DEEP_H: rec_h.get("deep", np.nan),
                Col.DEEP_A: rec_a.get("deep", np.nan),
                Col.SEASON: season,
            }
        )
    return pd.DataFrame(rows, columns=columns)
