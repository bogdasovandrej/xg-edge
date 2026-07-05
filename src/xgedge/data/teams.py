"""Canonical team-id mapping for football-data.co.uk and understat.com.

Canonical ids are snake_case strings (e.g. ``man_city``) used everywhere
downstream of the raw layer. Each source keeps its own name -> id table
because the two sites spell club names differently.
"""
from __future__ import annotations

_FD_TO_CANONICAL = {
    "Arsenal": "arsenal",
    "Aston Villa": "aston_villa",
    "Bournemouth": "bournemouth",
    "Brentford": "brentford",
    "Brighton": "brighton",
    "Burnley": "burnley",
    "Chelsea": "chelsea",
    "Crystal Palace": "crystal_palace",
    "Everton": "everton",
    "Fulham": "fulham",
    "Ipswich": "ipswich",
    "Leeds": "leeds",
    "Leicester": "leicester",
    "Liverpool": "liverpool",
    "Luton": "luton",
    "Man City": "man_city",
    "Man United": "man_united",
    "Newcastle": "newcastle",
    "Norwich": "norwich",
    "Nott'm Forest": "nottm_forest",
    "Sheffield United": "sheffield_united",
    "Southampton": "southampton",
    "Sunderland": "sunderland",
    "Tottenham": "tottenham",
    "Watford": "watford",
    "West Ham": "west_ham",
    "Wolves": "wolves",
}

# Understat is inconsistent across seasons for a few clubs, so plausible
# alternate spellings are accepted alongside the primary titles.
_UNDERSTAT_TO_CANONICAL = {
    "Arsenal": "arsenal",
    "Aston Villa": "aston_villa",
    "Bournemouth": "bournemouth",
    "AFC Bournemouth": "bournemouth",
    "Brentford": "brentford",
    "Brighton": "brighton",
    "Burnley": "burnley",
    "Chelsea": "chelsea",
    "Crystal Palace": "crystal_palace",
    "Everton": "everton",
    "Fulham": "fulham",
    "Ipswich": "ipswich",
    "Ipswich Town": "ipswich",
    "Leeds": "leeds",
    "Leeds United": "leeds",
    "Leicester": "leicester",
    "Leicester City": "leicester",
    "Liverpool": "liverpool",
    "Luton": "luton",
    "Luton Town": "luton",
    "Manchester City": "man_city",
    "Manchester United": "man_united",
    "Newcastle United": "newcastle",
    "Newcastle": "newcastle",
    "Norwich": "norwich",
    "Norwich City": "norwich",
    "Nottingham Forest": "nottm_forest",
    "Sheffield United": "sheffield_united",
    "Southampton": "southampton",
    "Sunderland": "sunderland",
    "Tottenham": "tottenham",
    "Tottenham Hotspur": "tottenham",
    "Watford": "watford",
    "West Ham": "west_ham",
    "West Ham United": "west_ham",
    "Wolverhampton Wanderers": "wolves",
    "Wolves": "wolves",
}

_SOURCES = {"fd": _FD_TO_CANONICAL, "understat": _UNDERSTAT_TO_CANONICAL}


def to_canonical(name: str, source: str) -> str:
    """Map a source-specific team name to its canonical snake_case id.

    ``source`` must be ``"fd"`` (football-data.co.uk) or ``"understat"``.
    Raises ``KeyError`` with a helpful message on an unknown source or name.
    """
    try:
        mapping = _SOURCES[source]
    except KeyError:
        raise KeyError(
            f"Unknown source {source!r}; expected one of {sorted(_SOURCES)}"
        ) from None
    try:
        return mapping[name]
    except KeyError:
        known = ", ".join(sorted(mapping))
        raise KeyError(
            f"Unknown {source} team name {name!r}. Known names: {known}. "
            "If this is a newly promoted club or a new spelling, add it to "
            "xgedge.data.teams."
        ) from None
