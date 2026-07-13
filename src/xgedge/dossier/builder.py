"""Build the JSON-ready ``forecast.details`` match dossier."""
from __future__ import annotations

from math import log
from typing import Any, Iterable, Mapping, Sequence

from xgedge.data.point_in_time import as_utc, iso_utc, unavailable_snapshot
from xgedge.dossier.adjustments import AdjustmentConfig, adjusted_match_npxg
from xgedge.dossier.elo import PointInTimeElo, rating_level

CONTEXT_KEYS = ("lineups", "absences", "referee", "weather")


def _match_id(row: Mapping[str, Any]) -> str:
    value = row.get("id", row.get("match_id"))
    return str(value).strip() if value is not None else ""


def _team_id(row: Mapping[str, Any], side: str) -> str:
    value = row.get(f"{side}_id")
    return str(value).strip() if value is not None else ""


def _scope(row: Mapping[str, Any]) -> str:
    return str(row.get("scope") or row.get("team_type") or "").lower()


def _is_history_match(row: Mapping[str, Any], cutoff: Any, scope: str) -> bool:
    if row.get("official") is not True:
        return False
    if str(row.get("status", "")).upper() != "FINISHED":
        return False
    if _scope(row) != scope:
        return False
    home_goals = row.get("home_goals_90", row.get("home_goals"))
    away_goals = row.get("away_goals_90", row.get("away_goals"))
    if (
        isinstance(home_goals, bool)
        or isinstance(away_goals, bool)
        or not isinstance(home_goals, int)
        or not isinstance(away_goals, int)
        or min(home_goals, away_goals) < 0
    ):
        return False
    try:
        return as_utc(row.get("kickoff_utc"), field="kickoff_utc") < cutoff
    except (TypeError, ValueError):
        return False


def _context_candidates(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not all(isinstance(item, Mapping) for item in value):
            raise ValueError("context snapshots must be mappings")
        return list(value)
    raise ValueError("context snapshot must be a mapping or sequence")


def _select_context(
    kind: str,
    value: Any,
    *,
    cutoff: Any,
    kickoff: Any,
) -> dict[str, Any]:
    candidates: list[tuple[Any, dict[str, Any]]] = []
    for raw in _context_candidates(value):
        snapshot = dict(raw)
        status, records = snapshot.get("status"), snapshot.get("records")
        if status == "available" and not isinstance(records, list):
            raise ValueError(f"available {kind} snapshot must contain a records list")
        if status == "unavailable" and records is not None:
            raise ValueError(f"unavailable {kind} snapshot records must be None")
        if status not in {"available", "unavailable"}:
            raise ValueError(f"invalid {kind} snapshot status")
        captured = as_utc(snapshot.get("snapshot_at"), field="snapshot_at")
        if captured > kickoff:
            raise ValueError(f"post-kickoff {kind} snapshot cannot enter a prematch dossier")
        if captured <= cutoff:
            candidates.append((captured, snapshot))
    if not candidates:
        return unavailable_snapshot(
            str(kind),
            "not_known_by_cutoff" if value is not None else "provider_not_configured",
            snapshot_at=cutoff,
        )
    captured, selected = max(candidates, key=lambda item: item[0])
    selected["snapshot_at"] = iso_utc(captured, field="snapshot_at")
    return selected


def _records_for(
    snapshot: Mapping[str, Any],
    *,
    fixture_id: str,
    team_id: str | None = None,
) -> list[dict[str, Any]] | None:
    if snapshot.get("status") != "available":
        return None
    output = []
    for source in snapshot.get("records", []):
        record = dict(source)
        record_match = record.get("match_id")
        if record_match is not None and str(record_match) != fixture_id:
            continue
        record_team = record.get("team_id")
        if team_id is not None:
            if record_team is None or str(record_team) != team_id:
                continue
        output.append(record)
    return output


def _compact_player(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "player_id": record.get("player_id"),
        "player_name": record.get("player_name"),
        "status": record.get("lineup_status") or record.get("availability_status"),
        "is_confirmed": record.get("is_confirmed"),
        "expected_minutes": record.get("expected_minutes"),
        "source": record.get("provider"),
    }


def _result_for(goals_for: int, goals_against: int) -> str:
    return "win" if goals_for > goals_against else "loss" if goals_for < goals_against else "draw"


def _history_row(
    row: Mapping[str, Any],
    team_id: str,
    elo: PointInTimeElo,
    adjustment_config: AdjustmentConfig,
) -> dict[str, Any]:
    match_id = _match_id(row)
    home = _team_id(row, "home") == team_id
    side, opponent_side = ("home", "away") if home else ("away", "home")
    goals_for = int(row.get(f"{side}_goals_90", row.get(f"{side}_goals")))
    goals_against = int(row.get(f"{opponent_side}_goals_90", row.get(f"{opponent_side}_goals")))
    prematch = elo.before_match(match_id)
    team_elo = prematch[side] if prematch else None
    opponent_elo = prematch[opponent_side] if prematch else None
    adjusted = adjusted_match_npxg(
        row,
        side,
        opponent_elo.get("rating") if opponent_elo else None,
        config=adjustment_config,
    )
    expected_score = None
    if team_elo is not None and opponent_elo is not None:
        # This display value excludes venue advantage; it measures relative
        # team strength and is not the exact internal Elo match expectation.
        expected_score = 1.0 / (
            1.0 + 10.0 ** (-(team_elo["rating"] - opponent_elo["rating"]) / 400.0)
        )
    return {
        "match_id": match_id,
        "kickoff_utc": iso_utc(row["kickoff_utc"], field="kickoff_utc"),
        "competition": row.get("competition"),
        "competition_level": row.get("competition_level"),
        "venue": side,
        "opponent_id": _team_id(row, opponent_side),
        "opponent": row.get(opponent_side),
        "score_90": {"for": goals_for, "against": goals_against},
        "result_90": _result_for(goals_for, goals_against),
        "team_elo_before": team_elo,
        "opponent_elo_before": opponent_elo,
        "opponent_level": rating_level(opponent_elo["rating"]) if opponent_elo else None,
        "elo_expected_team_score_neutral": expected_score,
        "actual_team_score": 1.0 if goals_for > goals_against else 0.0 if goals_for < goals_against else 0.5,
        "xg": {
            "raw": row.get(f"xg_{side}"),
            "non_penalty": adjusted["non_penalty_xg"],
            "penalty_credit_signal": adjusted["penalty_credit_signal"],
            "red_and_opponent_adjusted_npxg": {
                "status": adjusted["status"],
                "value": adjusted.get("value"),
                "reason": adjusted.get("reason"),
            },
            "audit": {
                "red_card_adjustment": adjusted["red_card_adjustment"],
                "opponent_adjustment": adjusted["opponent_adjustment"],
                "method_order": adjusted.get("method_order"),
            },
        },
        "red_cards": row.get("red_cards"),
        "provenance": row.get("provenance"),
    }


def _team_history(
    team_id: str,
    scope: str,
    matches: Iterable[Mapping[str, Any]],
    cutoff: Any,
    elo: PointInTimeElo,
    adjustment_config: AdjustmentConfig,
    limit: int,
) -> list[dict[str, Any]]:
    candidates = [
        dict(row)
        for row in matches
        if _is_history_match(row, cutoff, scope)
        and team_id in {_team_id(row, "home"), _team_id(row, "away")}
    ]
    candidates.sort(
        key=lambda row: (as_utc(row["kickoff_utc"], field="kickoff_utc"), _match_id(row)),
        reverse=True,
    )
    return [
        _history_row(row, team_id, elo, adjustment_config)
        for row in candidates[:limit]
    ]


def _referee_payload(snapshot: Mapping[str, Any], fixture_id: str) -> dict[str, Any] | None:
    records = _records_for(snapshot, fixture_id=fixture_id)
    if records is None:
        return None
    if not records:
        return {"status": "available", "referee": None, "reason": "no_referee_in_snapshot"}
    referee = next((row for row in records if row.get("role") in {None, "referee"}), records[0])
    average = referee.get("yellow_cards_per_match")
    baseline = referee.get("competition_yellow_cards_per_match")
    comparison = None
    if isinstance(average, (int, float)) and isinstance(baseline, (int, float)):
        difference = float(average) - float(baseline)
        comparison = {
            "difference": difference,
            "label": "above_competition_average" if difference > 0.15 else (
                "below_competition_average" if difference < -0.15 else "near_competition_average"
            ),
        }
    return {
        "status": "available",
        "referee_id": referee.get("referee_id"),
        "name": referee.get("referee_name"),
        "season": referee.get("season"),
        "matches": referee.get("matches"),
        "yellow_cards_per_match": average,
        "red_cards_per_match": referee.get("red_cards_per_match"),
        "comparison": comparison,
        "source": snapshot.get("provider"),
        "snapshot_at": snapshot.get("snapshot_at"),
    }


def _weather_payload(snapshot: Mapping[str, Any], fixture_id: str) -> dict[str, Any] | None:
    records = _records_for(snapshot, fixture_id=fixture_id)
    if records is None:
        return None
    if not records:
        return {"status": "available", "forecast": None, "reason": "no_weather_in_snapshot"}
    weather = records[0]
    return {
        "status": "available",
        "temperature_c": weather.get("temperature_c"),
        "wind_kph": weather.get("wind_kph"),
        "precipitation_mm": weather.get("precipitation_mm"),
        "condition": weather.get("condition"),
        "forecast_for": weather.get("forecast_for"),
        "source": snapshot.get("provider"),
        "snapshot_at": snapshot.get("snapshot_at"),
    }


def _probabilities(probabilities: Mapping[str, Any] | None) -> list[float] | None:
    if not probabilities:
        return None
    variants = (
        ("home_win", "draw", "away_win"),
        ("p_home", "p_draw", "p_away"),
        ("home", "draw", "away"),
    )
    for keys in variants:
        values = [probabilities.get(key) for key in keys]
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            parsed = [float(value) for value in values]
            if all(0 <= value <= 1 for value in parsed) and abs(sum(parsed) - 1.0) <= 1e-6:
                return parsed
    return None


def _tail_risk(
    histories: Sequence[Sequence[Mapping[str, Any]]],
    probabilities: Mapping[str, Any] | None,
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    drivers: list[dict[str, Any]] = []
    score = 0.0
    available_weight = 0.0
    probs = _probabilities(probabilities)
    if probs is None:
        drivers.append({"name": "outcome_entropy", "status": "unknown", "weight": 25.0, "contribution": 0.0})
    else:
        entropy = -sum(value * log(value) for value in probs if value > 0) / log(3.0)
        contribution = 25.0 * entropy
        score += contribution
        available_weight += 25.0
        drivers.append({"name": "outcome_entropy", "status": "available", "weight": 25.0, "raw": entropy, "contribution": contribution})

    surprises = [
        abs(float(row["actual_team_score"]) - float(row["elo_expected_team_score_neutral"]))
        for history in histories
        for row in history
        if row.get("elo_expected_team_score_neutral") is not None
    ]
    if surprises:
        surprise = sum(surprises) / len(surprises)
        contribution = 25.0 * surprise
        score += contribution
        available_weight += 25.0
        drivers.append({"name": "recent_elo_surprise", "status": "available", "weight": 25.0, "raw": surprise, "sample": len(surprises), "contribution": contribution})
    else:
        drivers.append({"name": "recent_elo_surprise", "status": "unknown", "weight": 25.0, "contribution": 0.0})

    minimum_history = min((len(history) for history in histories), default=0)
    sparse = 1.0 - min(minimum_history, 10) / 10.0
    contribution = 20.0 * sparse
    score += contribution
    available_weight += 20.0
    drivers.append({"name": "history_sparsity", "status": "available", "weight": 20.0, "raw": sparse, "minimum_team_matches": minimum_history, "contribution": contribution})

    missing = sum(contexts[key].get("status") != "available" for key in CONTEXT_KEYS)
    missing_ratio = missing / len(CONTEXT_KEYS)
    contribution = 20.0 * missing_ratio
    score += contribution
    available_weight += 20.0
    drivers.append({"name": "missing_prematch_context", "status": "available", "weight": 20.0, "raw": missing_ratio, "missing": missing, "total": len(CONTEXT_KEYS), "contribution": contribution})

    known_card_matches = [
        row for history in histories for row in history if isinstance(row.get("red_cards"), list)
    ]
    if known_card_matches:
        rate = sum(bool(row["red_cards"]) for row in known_card_matches) / len(known_card_matches)
        contribution = 10.0 * min(1.0, rate / 0.20)
        score += contribution
        available_weight += 10.0
        drivers.append({"name": "recent_red_card_incidence", "status": "available", "weight": 10.0, "raw": rate, "sample": len(known_card_matches), "contribution": contribution})
    else:
        drivers.append({"name": "recent_red_card_incidence", "status": "unknown", "weight": 10.0, "contribution": 0.0})

    bounded = min(100.0, max(0.0, score))
    label = "high" if bounded >= 60 else "medium" if bounded >= 30 else "low"
    return {
        "label": label,
        "score": round(bounded, 2),
        "coverage_weight": available_weight,
        "drivers": drivers,
        "interpretation": "forecast_fragility_and_tail_exposure_not_black_swan_prediction",
    }


def _sources(histories: Sequence[Sequence[Mapping[str, Any]]], contexts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    found: set[str] = set()
    for history in histories:
        for row in history:
            provenance = row.get("provenance")
            if isinstance(provenance, Mapping) and provenance.get("source"):
                found.add(str(provenance["source"]))
            elif isinstance(provenance, list):
                for item in provenance:
                    if isinstance(item, Mapping) and item.get("source"):
                        found.add(str(item["source"]))
    for snapshot in contexts.values():
        if snapshot.get("status") == "available" and snapshot.get("provider"):
            found.add(str(snapshot["provider"]))
    return sorted(found)


def _data_quality(histories: Sequence[Sequence[Mapping[str, Any]]], contexts: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    history_rows = [row for history in histories for row in history]
    history_score = 30.0 * sum(min(len(history), 10) / 10.0 for history in histories) / max(len(histories), 1)
    xg_known = sum(row["xg"]["red_and_opponent_adjusted_npxg"]["status"] == "available" for row in history_rows)
    xg_score = 30.0 * xg_known / len(history_rows) if history_rows else 0.0
    context_score = sum(10.0 for key in CONTEXT_KEYS if contexts[key].get("status") == "available")
    score = history_score + xg_score + context_score
    warnings = []
    if any(len(history) < 10 for history in histories):
        warnings.append("fewer_than_10_official_matches_for_at_least_one_team")
    if xg_known < len(history_rows):
        warnings.append("some_adjusted_npxg_is_unknown")
    for key in CONTEXT_KEYS:
        if contexts[key].get("status") != "available":
            warnings.append(f"{key}_unavailable")
    if not _sources(histories, contexts):
        warnings.append("provenance_sources_missing")
    return {
        "score": round(score, 2),
        "label": "high" if score >= 85 else "medium" if score >= 60 else "low",
        "sources": _sources(histories, contexts),
        "warnings": warnings,
        "scoring": {
            "official_history_max": 30,
            "adjusted_npxg_coverage_max": 30,
            "lineup_absence_referee_weather_max": 40,
        },
    }


def build_match_dossier(
    fixture: Mapping[str, Any],
    matches: Iterable[Mapping[str, Any]],
    *,
    cutoff: Any,
    contexts: Mapping[str, Any] | None = None,
    forecast_probabilities: Mapping[str, Any] | None = None,
    elo_priors: Mapping[tuple[str, str], float] | None = None,
    history_limit: int = 10,
    adjustment_config: AdjustmentConfig | None = None,
    betting_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize an auditable dossier matching the site's optional details API."""
    if not isinstance(history_limit, int) or isinstance(history_limit, bool) or history_limit != 10:
        raise ValueError("history_limit is fixed at 10 official matches")
    fixture_id = _match_id(fixture)
    home_id, away_id = _team_id(fixture, "home"), _team_id(fixture, "away")
    scope = _scope(fixture)
    if not fixture_id or not home_id or not away_id or scope not in {"club", "national"}:
        raise ValueError("fixture needs id, distinct home/away ids and club/national scope")
    if home_id == away_id:
        raise ValueError("fixture teams must differ")
    kickoff = as_utc(fixture.get("kickoff_utc"), field="kickoff_utc")
    boundary = as_utc(cutoff, field="cutoff")
    if boundary > kickoff:
        raise ValueError("dossier cutoff cannot be after kickoff")
    rows = [dict(row) for row in matches]
    elo = PointInTimeElo(rows, priors=elo_priors)
    adjustment_cfg = adjustment_config or AdjustmentConfig()
    selected_contexts = {
        key: _select_context(
            key,
            (contexts or {}).get(key),
            cutoff=boundary,
            kickoff=kickoff,
        )
        for key in CONTEXT_KEYS
    }
    histories = {
        "home": _team_history(home_id, scope, rows, kickoff, elo, adjustment_cfg, 10),
        "away": _team_history(away_id, scope, rows, kickoff, elo, adjustment_cfg, 10),
    }
    lineup_records = {
        side: _records_for(
            selected_contexts["lineups"], fixture_id=fixture_id, team_id=team_id
        )
        for side, team_id in (("home", home_id), ("away", away_id))
    }
    absence_records = {
        side: _records_for(
            selected_contexts["absences"], fixture_id=fixture_id, team_id=team_id
        )
        for side, team_id in (("home", home_id), ("away", away_id))
    }
    teams: dict[str, Any] = {}
    for side, team_id in (("home", home_id), ("away", away_id)):
        snapshot = elo.rating_at(team_id, scope, kickoff)
        teams[side] = {
            "team_id": team_id,
            "name": fixture.get(side),
            "elo": snapshot["rating"],
            "elo_meta": snapshot,
            "level": rating_level(snapshot["rating"]),
            "level_basis": "xgedge_point_in_time_elo_tier",
            "competition_level": fixture.get("competition_level"),
            "recent_matches": histories[side],
            "likely_lineup": (
                [_compact_player(row) for row in lineup_records[side]]
                if lineup_records[side] is not None
                else None
            ),
            "absences": (
                [_compact_player(row) for row in absence_records[side]]
                if absence_records[side] is not None
                else None
            ),
            "availability": {
                "lineup": selected_contexts["lineups"]["status"],
                "absences": selected_contexts["absences"]["status"],
            },
        }
    history_values = [histories["home"], histories["away"]]
    adjustments = [
        {
            "name": "penalty_removal",
            "method": "provider_npxg_or_raw_xg_minus_known_penalty_xg",
            "standard_penalty_xg": adjustment_cfg.standard_penalty_xg,
        },
        {
            "name": "red_card_neutralization",
            "method": "event_time_score_state_heuristic_v1",
            "warning": "heuristic_not_causal_estimate",
        },
        {
            "name": "opponent_strength",
            "method": "point_in_time_elo_ratio_clamped_v1",
            "factor_clamp": [adjustment_cfg.opponent_factor_min, adjustment_cfg.opponent_factor_max],
        },
    ]
    gate = dict(betting_gate or {
        "allowed": False,
        "reason": "no_validated_positive_clv_edge",
    })
    if gate.get("allowed") is not False:
        raise ValueError("dossier cannot enable betting without an external validated gate")
    return {
        "schema_version": "match-dossier/1.0",
        "fixture_id": fixture_id,
        "generated_as_of": iso_utc(boundary, field="cutoff"),
        "teams": teams,
        "referee": _referee_payload(selected_contexts["referee"], fixture_id),
        "weather": _weather_payload(selected_contexts["weather"], fixture_id),
        "context_availability": {
            key: {
                "status": snapshot.get("status"),
                "reason": snapshot.get("reason"),
                "provider": snapshot.get("provider"),
                "snapshot_at": snapshot.get("snapshot_at"),
            }
            for key, snapshot in selected_contexts.items()
        },
        "adjustments": adjustments,
        "data_quality": _data_quality(history_values, selected_contexts),
        "tail_risk": _tail_risk(history_values, forecast_probabilities, selected_contexts),
        "candidate_bets": [],
        "betting_gate": gate,
        "disclaimer": "Dossier context is descriptive; it does not establish a betting edge.",
    }
