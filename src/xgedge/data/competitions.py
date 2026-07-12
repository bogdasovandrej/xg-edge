"""Competition and season registry for the top-five European leagues.

The historical EPL pipeline predates this registry and intentionally keeps
its old filenames and identifiers.  New multi-league callers pass an
explicit :class:`Competition` and receive namespaced raw files/team ids.
"""
from __future__ import annotations

from dataclasses import dataclass

from xgedge.contracts import FD_SEASON_CODES, UNDERSTAT_YEARS


@dataclass(frozen=True)
class Competition:
    """Source identifiers for one domestic league."""

    key: str
    name: str
    football_data_code: str
    understat_league: str


@dataclass(frozen=True)
class Season:
    """Source identifiers for one season."""

    label: str
    football_data_code: str
    understat_year: int


TOP5_COMPETITIONS: dict[str, Competition] = {
    "epl": Competition("epl", "EPL", "E0", "EPL"),
    "la_liga": Competition("la_liga", "La Liga", "SP1", "La_liga"),
    "bundesliga": Competition("bundesliga", "Bundesliga", "D1", "Bundesliga"),
    "serie_a": Competition("serie_a", "Serie A", "I1", "Serie_A"),
    "ligue_1": Competition("ligue_1", "Ligue 1", "F1", "Ligue_1"),
}

TOP5_SEASONS: dict[str, Season] = {
    "2026-27": Season("2026-27", "2627", 2026),
}

_COMPETITION_ALIASES = {
    "e0": "epl",
    "premier_league": "epl",
    "sp1": "la_liga",
    "laliga": "la_liga",
    "d1": "bundesliga",
    "i1": "serie_a",
    "seriea": "serie_a",
    "f1": "ligue_1",
    "ligue1": "ligue_1",
}


class SourceDataUnavailable(RuntimeError):
    """The upstream source has not published a requested season yet."""


def resolve_competition(value: str | Competition) -> Competition:
    """Resolve a registry key/name/source code to a competition."""
    if isinstance(value, Competition):
        return value
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _COMPETITION_ALIASES.get(normalized, normalized)
    try:
        return TOP5_COMPETITIONS[normalized]
    except KeyError:
        choices = ", ".join(TOP5_COMPETITIONS)
        raise KeyError(f"Unknown competition {value!r}; expected one of: {choices}") from None


def resolve_season(label: str) -> Season:
    """Resolve a registered top-five or legacy EPL season."""
    if label in TOP5_SEASONS:
        return TOP5_SEASONS[label]
    if label in FD_SEASON_CODES and label in UNDERSTAT_YEARS:
        return Season(label, FD_SEASON_CODES[label], UNDERSTAT_YEARS[label])
    choices = sorted({*TOP5_SEASONS, *FD_SEASON_CODES})
    raise KeyError(f"Unknown season {label!r}; expected one of: {', '.join(choices)}")


def raw_filename(source: str, season: str, competition: str | Competition) -> str:
    """Return the collision-free raw filename used by multi-league jobs."""
    comp = resolve_competition(competition)
    period = resolve_season(season)
    if source == "fd":
        return f"fd_{comp.key}_{period.football_data_code}.csv"
    if source == "understat":
        return f"understat_{comp.key}_{period.understat_year}.json"
    raise KeyError(f"Unknown source {source!r}; expected 'fd' or 'understat'")
