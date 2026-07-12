"""Canonical team-id mapping for football-data.co.uk and understat.com.

Canonical ids are snake_case strings (e.g. ``man_city``) used everywhere
downstream of the raw layer. Each source keeps its own name -> id table
because the two sites spell club names differently.
"""
from __future__ import annotations

import re
import unicodedata

from xgedge.data.competitions import Competition, resolve_competition

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

# Cross-source aliases that cannot be reconciled with mechanical slugging.
# The fallback handles promoted/new clubs, while this small explicit layer
# keeps common football-data and Understat spellings aligned.
_MULTILEAGUE_ALIASES = {
    "fd": {
        **_FD_TO_CANONICAL,
        "Ath Madrid": "atletico_madrid",
        "Betis": "real_betis",
        "Celta": "celta_vigo",
        "La Coruna": "deportivo_la_coruna",
        "Sociedad": "real_sociedad",
        "Ath Bilbao": "athletic_bilbao",
        "Espanol": "espanyol",
        "Valladolid": "real_valladolid",
        "Vallecano": "rayo_vallecano",
        "M'gladbach": "borussia_monchengladbach",
        "Dortmund": "borussia_dortmund",
        "Bayern Munich": "bayern_munich",
        "Ein Frankfurt": "eintracht_frankfurt",
        "FC Koln": "koln",
        "Leverkusen": "bayer_leverkusen",
        "Hertha": "hertha_berlin",
        "Inter": "inter_milan",
        "Milan": "ac_milan",
        "Verona": "hellas_verona",
        "Paris SG": "paris_saint_germain",
        "St Etienne": "saint_etienne",
    },
    "understat": {
        **_UNDERSTAT_TO_CANONICAL,
        "Atletico Madrid": "atletico_madrid",
        "Real Betis": "real_betis",
        "Celta Vigo": "celta_vigo",
        "Deportivo La Coruna": "deportivo_la_coruna",
        "Real Sociedad": "real_sociedad",
        "Athletic Club": "athletic_bilbao",
        "Athletic Bilbao": "athletic_bilbao",
        "Espanyol": "espanyol",
        "Real Valladolid": "real_valladolid",
        "Rayo Vallecano": "rayo_vallecano",
        "Borussia M.Gladbach": "borussia_monchengladbach",
        "Borussia Monchengladbach": "borussia_monchengladbach",
        "Borussia Dortmund": "borussia_dortmund",
        "Bayern Munich": "bayern_munich",
        "Eintracht Frankfurt": "eintracht_frankfurt",
        "FC Cologne": "koln",
        "Bayer Leverkusen": "bayer_leverkusen",
        "Hertha Berlin": "hertha_berlin",
        "Internazionale": "inter_milan",
        "Inter": "inter_milan",
        "AC Milan": "ac_milan",
        "Hellas Verona": "hellas_verona",
        "Paris Saint Germain": "paris_saint_germain",
        "Saint-Etienne": "saint_etienne",
    },
}

_TRANSLITERATION = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "SS",
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
    }
)


def slugify_team(name: str) -> str:
    """Create a deterministic ASCII team slug without guessing club identity."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Team name must be a non-empty string")
    folded = unicodedata.normalize("NFKD", name.translate(_TRANSLITERATION))
    ascii_name = folded.encode("ascii", "ignore").decode("ascii").lower()
    ascii_name = ascii_name.replace("&", " and ").replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_name).strip("_")
    if not slug:
        raise ValueError(f"Team name {name!r} cannot be converted to an ASCII slug")
    return slug


def to_canonical(
    name: str,
    source: str,
    league: str | Competition | None = None,
) -> str:
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
    if league is not None:
        comp = resolve_competition(league)
        slug = _MULTILEAGUE_ALIASES[source].get(name)
        if slug is None:
            slug = slugify_team(name)
        return f"{comp.key}:{slug}"
    try:
        return mapping[name]
    except KeyError:
        known = ", ".join(sorted(mapping))
        raise KeyError(
            f"Unknown {source} team name {name!r}. Known names: {known}. "
            "If this is a newly promoted club or a new spelling, add it to "
            "xgedge.data.teams."
        ) from None
