"""Deep-audit state machine: trigger events -> batched ChatGPT audits -> decision.

``xgedge.research.triggers`` already turns a fresh execution quote into
``TRIGGER_HIT`` / ``NEAR_TRIGGER`` / ``LARGE_MOVE_REAUDIT`` / ``LATE_WILDCARD``
and explicitly never approves anything on its own
(``requires_human_audit=True``). This module is the next stage: it collects
those events into a deep-audit queue, batches them for ChatGPT (default 4 per
batch, unlike the fixed 5-per-batch PRELINE export), and turns an imported
``human_deep_audit/1.0`` payload (already validated by
``xgedge.research.handoff.validate_audit_import``) into a per-candidate
decision. Trigger hit never implies approval; only an imported human decision
does.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from xgedge.research.handoff import DEEP_AUDIT_SCHEMA, validate_audit_import

DEEP_AUDIT_INTAKE_STATUSES = frozenset({
    "TRIGGER_HIT", "NEAR_TRIGGER", "LARGE_MOVE_REAUDIT", "LATE_WILDCARD",
})

DEEP_BATCH_SIZE_DEFAULT = 4

# The deep-audit schema (section 29 of the workflow spec) accepts "PASS" as a
# decision label; the fixture/candidate state machine (section 2) instead
# uses "REJECTED". Both mean the same thing and are normalized here.
_DECISION_MAP = {
    "APPROVED": "APPROVED",
    "BORDERLINE": "BORDERLINE",
    "PASS": "REJECTED",
    "REJECTED": "REJECTED",
}


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def collect_deep_audit_queue(
    trigger_evaluations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select trigger evaluations that require deep audit.

    No minimum or maximum queue size is imposed: if three candidates trigger,
    three go to deep audit; if fourteen do, fourteen do.
    """
    queue: list[dict[str, Any]] = []
    for row in trigger_evaluations:
        if not isinstance(row, Mapping) or row.get("status") not in DEEP_AUDIT_INTAKE_STATUSES:
            continue
        queue.append({**dict(row), "deep_audit_status": "DEEP_AUDIT_REQUIRED"})
    return queue


def build_deep_audit_batches(
    queue: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = DEEP_BATCH_SIZE_DEFAULT,
) -> list[dict[str, Any]]:
    """Split the deep-audit queue into fixed-size ChatGPT packets."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    batches: list[dict[str, Any]] = []
    for offset in range(0, len(queue), batch_size):
        chunk = queue[offset : offset + batch_size]
        batches.append({
            "schema_version": "chat-research-packet/1.0",
            "analysis_stage": "DEEP",
            "batch_number": len(batches) + 1,
            "candidates": [dict(row) for row in chunk],
            "expected_output_schema": DEEP_AUDIT_SCHEMA,
        })
    return batches


def apply_deep_audit(
    audit_source: Mapping[str, Any],
    workflow: Mapping[str, Any],
    *,
    imported_at: str | None = None,
) -> dict[str, Any]:
    """Validate an imported deep audit and derive per-candidate decisions.

    Machine-computed probability/EV and human ``value``/``robustness``/
    ``acca_quality`` scores are kept as separate fields, never blended into
    one number.
    """
    snapshot = validate_audit_import(audit_source, workflow, imported_at=imported_at)
    if snapshot["analysis_stage"] != "DEEP":
        raise ValueError("apply_deep_audit requires a DEEP-stage audit")
    decisions: list[dict[str, Any]] = []
    for update in audit_source.get("candidate_updates", []):
        if not isinstance(update, Mapping):
            continue
        raw_decision = str(update.get("decision") or "").strip().upper()
        status = _DECISION_MAP.get(raw_decision)
        if status is None:
            raise ValueError(f"unknown deep-audit decision: {update.get('decision')!r}")
        decisions.append({
            "candidate_id": update.get("candidate_id"),
            "status": status,
            "human_probability_central": update.get("human_probability_central"),
            "human_probability_conservative": (
                update.get("human_probability_conservative")
                or update.get("human_probability_pessimistic")
            ),
            "value": update.get("value_assessment") or update.get("value"),
            "robustness": update.get("robustness"),
            "acca_quality": update.get("acca_quality"),
            "minimum_entry": update.get("minimum_entry"),
            "main_thesis": list(update.get("main_thesis") or []),
            "anti_thesis": list(update.get("anti_thesis") or []),
            "failure_modes": list(update.get("failure_modes") or []),
            "audit_imported_at": snapshot["imported_at"],
        })
    return {
        "schema_version": "deep-audit-decisions/1.0",
        "snapshot": snapshot,
        "decisions": decisions,
    }


def invalidate_if_stale(
    decision: Mapping[str, Any],
    *,
    latest_trigger_status: str,
    latest_trigger_at: str,
) -> dict[str, Any]:
    """Downgrade an already-decided candidate when a later large move arrives.

    A price move after the human audit was captured means the audited
    reasoning may no longer apply; it must be re-audited, not silently kept.
    """
    updated = dict(decision)
    audited_at = decision.get("audit_imported_at")
    if (
        decision.get("status") in {"APPROVED", "BORDERLINE"}
        and audited_at
        and latest_trigger_status == "LARGE_MOVE_REAUDIT"
        and _utc(latest_trigger_at, "latest_trigger_at") > _utc(audited_at, "audit_imported_at")
    ):
        updated["status"] = "STALE_AUDIT"
        updated["stale_reason"] = "large_move_after_deep_audit"
    return updated
