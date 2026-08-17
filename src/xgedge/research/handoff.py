"""Versioned ChatGPT export/import packets for human research audits."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


PRELINE_AUDIT_SCHEMA = "human_preline_audit/1.0"
DEEP_AUDIT_SCHEMA = "human_deep_audit/1.0"

PRELINE_INSTRUCTION = """Ты проводишь независимый PRE-LINE audit.

Не доверяй автоматически вероятностям xG Edge. Проверь футбольный контекст
самостоятельно. Для каждого матча оцени все candidate market hypotheses,
обязательно проверь team totals, BTTS, qualification и DNB/AH. Меняй fair
probability только при доказуемой причине, задай pre-line triggers, не присваивай
final Value score и верни JSON по schema human_preline_audit/1.0. UNKNOWN не
заменяй догадками.
"""


def build_chat_batches(
    workflow: Mapping[str, Any],
    forecasts: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 5,
) -> list[dict[str, Any]]:
    if workflow.get("schema_version") != "uefa-research-workflow/2.0":
        raise ValueError("unsupported research workflow schema")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    forecast_index = {str(row.get("id")): row for row in forecasts if isinstance(row, Mapping)}
    packets: list[dict[str, Any]] = []
    for fixture_id in workflow.get("selected_fixture_ids", []):
        forecast = forecast_index.get(str(fixture_id))
        market_set = workflow.get("market_sets", {}).get(str(fixture_id))
        if not isinstance(forecast, Mapping) or not isinstance(market_set, Mapping):
            continue
        details = forecast.get("details") if isinstance(forecast.get("details"), Mapping) else {}
        packets.append({
            "fixture_id": fixture_id,
            "identity": {
                key: forecast.get(key) for key in (
                    "competition", "stage", "kickoff_utc", "home", "away", "venue", "first_leg"
                )
            },
            "strength": {"rating_basis": forecast.get("rating_basis")},
            "recent_official_matches": (
                details.get("teams") if isinstance(details, Mapping) else "UNKNOWN"
            ),
            "process_metrics": {
                "expected_goals": forecast.get("expected_goals"),
                "lambda_home": forecast.get("lambda_home"),
                "lambda_away": forecast.get("lambda_away"),
                "npxg_and_events": details.get("teams", "UNKNOWN") if isinstance(details, Mapping) else "UNKNOWN",
            },
            "context": details.get("context_availability", "UNKNOWN") if isinstance(details, Mapping) else "UNKNOWN",
            "probabilities": {
                key: forecast.get(key) for key in (
                    "p_home", "p_draw", "p_away", "p_over25", "p_btts",
                    "p_home_advance", "p_away_advance",
                )
            },
            "score_distribution": forecast.get("score_scenarios", "UNKNOWN"),
            "market_set": deepcopy(market_set),
            "missing_data": next((
                row.get("missing_data", []) for row in workflow.get("records", [])
                if isinstance(row, Mapping) and row.get("fixture_id") == fixture_id
            ), []),
        })
    output: list[dict[str, Any]] = []
    for offset in range(0, len(packets), batch_size):
        batch = packets[offset:offset + batch_size]
        output.append({
            "schema_version": "chat-research-packet/1.0",
            "analysis_stage": "PRELINE",
            "batch_number": len(output) + 1,
            "instruction": PRELINE_INSTRUCTION,
            "fixtures": batch,
            "expected_output_schema": PRELINE_AUDIT_SCHEMA,
        })
    return output


def validate_audit_import(
    source: Mapping[str, Any],
    workflow: Mapping[str, Any],
    *,
    imported_at: str | None = None,
) -> dict[str, Any]:
    """Validate exact fixture/candidate identities and return an immutable snapshot."""
    if source.get("schema_version") not in {PRELINE_AUDIT_SCHEMA, DEEP_AUDIT_SCHEMA}:
        raise ValueError("unsupported human audit schema")
    fixture_id = str(source.get("fixture_id") or "")
    market_set = workflow.get("market_sets", {}).get(fixture_id)
    if not isinstance(market_set, Mapping):
        raise ValueError("audit fixture is not in the research workflow")
    stage = source.get("analysis_stage")
    expected_stage = "PRELINE" if source["schema_version"] == PRELINE_AUDIT_SCHEMA else "DEEP"
    if stage != expected_stage:
        raise ValueError("audit stage does not match its schema")
    valid_ids = {row.get("candidate_id") for row in market_set.get("candidates", []) if isinstance(row, Mapping)}
    updates = source.get("candidate_updates")
    if not isinstance(updates, list):
        raise ValueError("candidate_updates must be a list")
    for row in updates:
        if not isinstance(row, Mapping) or row.get("candidate_id") not in valid_ids:
            raise ValueError("audit contains an unknown candidate_id")
    when = imported_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "human-audit-snapshot/1.0",
        "fixture_id": fixture_id,
        "analysis_stage": stage,
        "imported_at": when,
        "source": "USER_VERIFIED_CHATGPT_IMPORT",
        "audit": deepcopy(dict(source)),
        "immutable": True,
    }
