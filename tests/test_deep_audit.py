"""Deep-audit queue intake, batching and decision derivation."""
from __future__ import annotations

import pytest

from xgedge.research.deep_audit import (
    apply_deep_audit,
    build_deep_audit_batches,
    collect_deep_audit_queue,
    invalidate_if_stale,
)
from xgedge.research.handoff import DEEP_AUDIT_SCHEMA


def _workflow() -> dict:
    return {
        "market_sets": {
            "fixture-01": {"candidates": [{"candidate_id": "candidate:aaa"}]},
            "fixture-02": {"candidates": [{"candidate_id": "candidate:bbb"}]},
        }
    }


def _trigger_rows() -> list[dict]:
    return [
        {"candidate_id": "candidate:aaa", "fixture_id": "fixture-01", "status": "TRIGGER_HIT"},
        {"candidate_id": "candidate:bbb", "fixture_id": "fixture-02", "status": "WATCH"},
        {"candidate_id": "candidate:ccc", "fixture_id": "fixture-03", "status": "NEAR_TRIGGER"},
        {"candidate_id": "candidate:ddd", "fixture_id": "fixture-04", "status": "LARGE_MOVE_REAUDIT"},
        {"candidate_id": "candidate:eee", "fixture_id": "fixture-05", "status": "LATE_WILDCARD"},
    ]


def test_queue_only_collects_actionable_trigger_states() -> None:
    queue = collect_deep_audit_queue(_trigger_rows())
    statuses = {row["candidate_id"]: row["status"] for row in queue}
    assert statuses == {
        "candidate:aaa": "TRIGGER_HIT",
        "candidate:ccc": "NEAR_TRIGGER",
        "candidate:ddd": "LARGE_MOVE_REAUDIT",
        "candidate:eee": "LATE_WILDCARD",
    }
    assert all(row["deep_audit_status"] == "DEEP_AUDIT_REQUIRED" for row in queue)


def test_batches_of_four_by_default_no_forced_minimum() -> None:
    queue = collect_deep_audit_queue(_trigger_rows())
    assert len(queue) == 4
    batches = build_deep_audit_batches(queue)
    assert [len(b["candidates"]) for b in batches] == [4]
    assert batches[0]["expected_output_schema"] == DEEP_AUDIT_SCHEMA

    small_queue = queue[:1]
    small_batches = build_deep_audit_batches(small_queue)
    assert len(small_batches) == 1
    assert len(small_batches[0]["candidates"]) == 1

    with pytest.raises(ValueError):
        build_deep_audit_batches(queue, batch_size=0)


def test_apply_deep_audit_normalizes_pass_to_rejected_and_keeps_scores_separate() -> None:
    audit = {
        "schema_version": DEEP_AUDIT_SCHEMA,
        "fixture_id": "fixture-01",
        "analysis_stage": "DEEP",
        "candidate_updates": [
            {
                "candidate_id": "candidate:aaa",
                "decision": "PASS",
                "human_probability_central": 0.55,
                "value_assessment": 6.1,
                "robustness": 7.0,
            }
        ],
    }
    result = apply_deep_audit(audit, _workflow(), imported_at="2026-08-20T10:00:00Z")
    assert result["snapshot"]["immutable"] is True
    decision = result["decisions"][0]
    assert decision["status"] == "REJECTED"
    assert decision["value"] == 6.1
    assert decision["human_probability_central"] == 0.55


def test_apply_deep_audit_rejects_unknown_decision_label() -> None:
    audit = {
        "schema_version": DEEP_AUDIT_SCHEMA,
        "fixture_id": "fixture-01",
        "analysis_stage": "DEEP",
        "candidate_updates": [
            {"candidate_id": "candidate:aaa", "decision": "SUPER_BET"}
        ],
    }
    with pytest.raises(ValueError):
        apply_deep_audit(audit, _workflow())


def test_stale_audit_invalidation_requires_move_after_audit() -> None:
    approved = {"candidate_id": "candidate:aaa", "status": "APPROVED", "audit_imported_at": "2026-08-20T10:00:00Z"}
    stale = invalidate_if_stale(
        approved,
        latest_trigger_status="LARGE_MOVE_REAUDIT",
        latest_trigger_at="2026-08-20T12:00:00Z",
    )
    assert stale["status"] == "STALE_AUDIT"

    unchanged = invalidate_if_stale(
        approved,
        latest_trigger_status="LARGE_MOVE_REAUDIT",
        latest_trigger_at="2026-08-20T09:00:00Z",
    )
    assert unchanged["status"] == "APPROVED"

    not_a_move = invalidate_if_stale(
        approved,
        latest_trigger_status="TRIGGER_HIT",
        latest_trigger_at="2026-08-20T12:00:00Z",
    )
    assert not_a_move["status"] == "APPROVED"
