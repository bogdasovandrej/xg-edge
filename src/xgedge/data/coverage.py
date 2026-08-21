"""Declared competition coverage: what the site tracks, and from which feed.

The point of this registry is that a competition the project cannot reach is
visible as a gap rather than as an empty list. Every entry names its feed and
its availability, so "no fixtures for the Russian Premier League" is always
distinguishable from "the Russian Premier League is not configured".

Availability values:

``COVERED``       a feed is wired and needs no key, or its key is already used
                  elsewhere in the pipeline.
``NEEDS_KEY``     the adapter exists; set the named environment variable.
``NOT_FREE``      no free feed carries it; listed so it is not silently
                  forgotten.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True, slots=True)
class CompetitionCoverage:
    key: str
    name: str
    name_ru: str
    group: str
    feed: str
    availability: str
    api_key_env: str | None = None
    provider_league_id: str | None = None
    note: str | None = None


# football-data.org's free tier carries the top-five leagues and the Champions
# League, but not the Europa/Conference League, the Russian Premier League or
# any domestic cup. The UEFA club feed already covers all three UEFA
# competitions across qualifying, league phase and knockout, so it is used for
# those instead of spending football-data.org quota.
COVERAGE: tuple[CompetitionCoverage, ...] = (
    # --- Top-five domestic leagues -------------------------------------
    CompetitionCoverage("epl", "Premier League", "АПЛ", "top5_league",
                        "football-data.org", "NEEDS_KEY", "FOOTBALL_DATA_API_KEY", "PL"),
    CompetitionCoverage("la_liga", "La Liga", "Ла Лига", "top5_league",
                        "football-data.org", "NEEDS_KEY", "FOOTBALL_DATA_API_KEY", "PD"),
    CompetitionCoverage("bundesliga", "Bundesliga", "Бундеслига", "top5_league",
                        "football-data.org", "NEEDS_KEY", "FOOTBALL_DATA_API_KEY", "BL1"),
    CompetitionCoverage("serie_a", "Serie A", "Серия А", "top5_league",
                        "football-data.org", "NEEDS_KEY", "FOOTBALL_DATA_API_KEY", "SA"),
    CompetitionCoverage("ligue_1", "Ligue 1", "Лига 1", "top5_league",
                        "football-data.org", "NEEDS_KEY", "FOOTBALL_DATA_API_KEY", "FL1"),

    # --- Russian Premier League ----------------------------------------
    CompetitionCoverage("rpl", "Russian Premier League", "РПЛ", "domestic_league",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "235",
                        "Not in the football-data.org free tier; api-football's "
                        "free plan allows 100 requests per day."),

    # --- Top-five domestic cups ----------------------------------------
    CompetitionCoverage("fa_cup", "FA Cup", "Кубок Англии", "top5_cup",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "45"),
    CompetitionCoverage("efl_cup", "EFL Cup", "Кубок лиги", "top5_cup",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "48"),
    CompetitionCoverage("copa_del_rey", "Copa del Rey", "Кубок Испании", "top5_cup",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "143"),
    CompetitionCoverage("dfb_pokal", "DFB-Pokal", "Кубок Германии", "top5_cup",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "81"),
    CompetitionCoverage("coppa_italia", "Coppa Italia", "Кубок Италии", "top5_cup",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "137"),
    CompetitionCoverage("coupe_de_france", "Coupe de France", "Кубок Франции", "top5_cup",
                        "api-football", "NEEDS_KEY", "API_FOOTBALL_KEY", "66"),

    # --- UEFA club competitions, all stages ----------------------------
    CompetitionCoverage("ucl", "UEFA Champions League", "Лига чемпионов", "uefa",
                        "official UEFA feed", "COVERED", None, "1",
                        "Qualifying, league phase and knockout."),
    CompetitionCoverage("uel", "UEFA Europa League", "Лига Европы", "uefa",
                        "official UEFA feed", "COVERED", None, "14",
                        "Qualifying, league phase and knockout."),
    CompetitionCoverage("uecl", "UEFA Conference League", "Лига конференций", "uefa",
                        "official UEFA feed", "COVERED", None, "2019",
                        "Qualifying, league phase and knockout."),

    # --- International --------------------------------------------------
    CompetitionCoverage("world_cup", "FIFA World Cup 2026", "ЧМ-2026", "international",
                        "official FIFA feed", "COVERED", None, "17"),
)

COVERAGE_BY_KEY = {row.key: row for row in COVERAGE}


def coverage_report(available_keys: set[str] | None = None) -> dict[str, Any]:
    """Summarise declared coverage, optionally marking which env keys are set.

    ``available_keys`` is the set of environment variable names that actually
    hold a value. Anything needing a key that is absent reports
    ``MISSING_KEY`` so the site can say why a competition is empty.
    """
    present = available_keys or set()
    rows: list[dict[str, Any]] = []
    for entry in COVERAGE:
        effective = entry.availability
        if entry.availability == "NEEDS_KEY":
            effective = "ACTIVE" if entry.api_key_env in present else "MISSING_KEY"
        elif entry.availability == "COVERED":
            effective = "ACTIVE"
        rows.append({**asdict(entry), "effective_status": effective})
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["effective_status"]] = by_status.get(row["effective_status"], 0) + 1
    return {
        "schema_version": "competition-coverage/1.0",
        "total": len(rows),
        "by_status": dict(sorted(by_status.items())),
        "competitions": rows,
    }
