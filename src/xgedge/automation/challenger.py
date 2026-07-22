"""Leak-free PAPER challenger retraining and immutable model registry.

The first autonomous challenger is intentionally narrow: a scalar temperature
calibrator for already-frozen 1X2 forecasts.  It cannot invent signal, touch
real-money execution, or train on a result that was not both official and
observed after a forecast had been archived before kickoff.

Training is expanding-window walk-forward.  A PAPER promotion is possible only
when every fixed guardrail passes; the default caller merely registers the
challenger.  This makes scheduled learning useful without silently converting
an experiment into a profitability claim.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite, log
from typing import Any, Iterable, Mapping

import numpy as np
from scipy.optimize import minimize_scalar

from xgedge.automation.archive import validate_archive
from xgedge.data.point_in_time import as_utc, iso_utc

REGISTRY_SCHEMA_VERSION = "paper-model-registry/1.0"
REGISTRY_MODE = "PAPER_ONLY"
_OUTCOMES = ("home", "draw", "away")

# This policy is embedded and hash-locked in every registry.  Changing it after
# seeing results requires a new schema/version, preventing a moving goalpost.
_POLICY: dict[str, Any] = {
    "challenger_family": "temperature_scaling_1x2",
    "validation": "expanding_window_walk_forward",
    "min_total_settled": 200,
    "min_train_per_fold": 80,
    "test_block_size": 20,
    "min_oos_predictions": 100,
    "min_walk_forward_folds": 5,
    "min_logloss_improvement": 0.002,
    "min_brier_improvement": 0.0,
    "max_candidate_ece": 0.08,
    "max_ece_degradation": 0.005,
    "paired_bootstrap_confidence": 0.95,
    "paired_bootstrap_resamples": 5000,
    "required_improvement_ci_low": 0.0,
    "temperature_bounds": [0.5, 3.0],
    "promotion_scope": "PAPER_ONLY",
    "real_money_execution": False,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


POLICY_HASH = _hash(_POLICY)


def empty_registry(*, created_at: str | datetime | None = None) -> dict[str, Any]:
    when = as_utc(created_at or datetime.now(timezone.utc), field="created_at")
    timestamp = iso_utc(when, field="created_at")
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "mode": REGISTRY_MODE,
        "policy_hash": POLICY_HASH,
        "policy": deepcopy(_POLICY),
        "created_at": timestamp,
        "updated_at": timestamp,
        "champion": {
            "candidate_id": None,
            "model_family": "identity_1x2",
            "source_model": None,
            "activated_at": timestamp,
            "deployment_scope": "PAPER_ONLY",
            "real_money_execution": False,
        },
        "challengers": [],
        "promotion_events": [],
    }


def _candidate_hash(candidate: Mapping[str, Any]) -> str:
    return _hash({key: value for key, value in candidate.items() if key != "content_hash"})


def _promotion_hash(event: Mapping[str, Any]) -> str:
    return _hash({key: value for key, value in event.items() if key != "event_hash"})


def validate_registry(document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(document, Mapping):
        raise ValueError("registry must be an object")
    output = deepcopy(dict(document))
    if output.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError("unsupported registry schema")
    if output.get("mode") != REGISTRY_MODE:
        raise ValueError("registry must remain PAPER_ONLY")
    if output.get("policy_hash") != POLICY_HASH or output.get("policy") != _POLICY:
        raise ValueError("registry promotion policy was changed")
    created = as_utc(output.get("created_at"), field="created_at")
    updated = as_utc(output.get("updated_at"), field="updated_at")
    if updated < created:
        raise ValueError("registry updated_at cannot precede created_at")
    champion = output.get("champion")
    if not isinstance(champion, Mapping):
        raise ValueError("registry champion must be an object")
    if champion.get("deployment_scope") != "PAPER_ONLY":
        raise ValueError("champion deployment scope must remain PAPER_ONLY")
    if champion.get("real_money_execution") is not False:
        raise ValueError("real-money execution must remain disabled")

    challengers = output.get("challengers")
    if not isinstance(challengers, list):
        raise ValueError("challengers must be an array")
    by_id: dict[str, Mapping[str, Any]] = {}
    for candidate in challengers:
        if not isinstance(candidate, Mapping):
            raise ValueError("challenger entries must be objects")
        identity = str(candidate.get("candidate_id") or "")
        if not identity or identity in by_id:
            raise ValueError("challenger candidate_id must be unique")
        if candidate.get("content_hash") != _candidate_hash(candidate):
            raise ValueError(f"challenger hash mismatch: {identity}")
        if candidate.get("deployment_scope") != "PAPER_ONLY":
            raise ValueError("challenger deployment scope must remain PAPER_ONLY")
        by_id[identity] = candidate

    events = output.get("promotion_events")
    if not isinstance(events, list):
        raise ValueError("promotion_events must be an array")
    previous = "0" * 64
    for sequence, event in enumerate(events, start=1):
        if not isinstance(event, Mapping) or event.get("sequence") != sequence:
            raise ValueError("promotion event sequence is invalid")
        if event.get("previous_event_hash") != previous:
            raise ValueError("promotion event chain is broken")
        if event.get("event_hash") != _promotion_hash(event):
            raise ValueError("promotion event hash mismatch")
        candidate_id = str(event.get("candidate_id") or "")
        candidate = by_id.get(candidate_id)
        if candidate is None or candidate.get("status") != "eligible":
            raise ValueError("only an eligible registered challenger may be promoted")
        if not all(candidate.get("guardrails", {}).values()):
            raise ValueError("promoted challenger did not pass every guardrail")
        if event.get("scope") != "PAPER_ONLY":
            raise ValueError("promotion scope must remain PAPER_ONLY")
        previous = str(event["event_hash"])
    champion_id = champion.get("candidate_id")
    if champion_id is not None:
        if str(champion_id) not in by_id:
            raise ValueError("champion references an unknown challenger")
        if not events or events[-1].get("candidate_id") != champion_id:
            raise ValueError("champion pointer is not backed by the latest promotion event")
    return output


def _temperature_scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    if not isfinite(float(temperature)) or temperature <= 0:
        raise ValueError("temperature must be positive")
    values = np.asarray(probabilities, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("probabilities must have shape (n, 3)")
    if np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("probabilities must be finite and strictly positive")
    logits = np.log(values) / float(temperature)
    logits -= logits.max(axis=1, keepdims=True)
    scaled = np.exp(logits)
    return scaled / scaled.sum(axis=1, keepdims=True)


def _fit_temperature(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    low, high = map(float, _POLICY["temperature_bounds"])

    def objective(value: float) -> float:
        calibrated = _temperature_scale(probabilities, value)
        return float(-np.log(np.clip(calibrated[np.arange(len(outcomes)), outcomes], 1e-12, 1.0)).mean())

    fitted = minimize_scalar(objective, bounds=(low, high), method="bounded")
    if not fitted.success or not isfinite(float(fitted.x)):
        raise ValueError("temperature optimization failed")
    return float(fitted.x)


def _metrics(probabilities: np.ndarray, outcomes: np.ndarray) -> dict[str, float]:
    selected = np.clip(probabilities[np.arange(len(outcomes)), outcomes], 1e-12, 1.0)
    one_hot = np.eye(3, dtype=float)[outcomes]
    confidence = probabilities.max(axis=1)
    correct = (probabilities.argmax(axis=1) == outcomes).astype(float)
    ece = 0.0
    # Fixed-width bins make the calibration gate preregistered and reproducible.
    for left in np.linspace(0.0, 0.9, 10):
        right = left + 0.1
        mask = (confidence >= left) & (
            (confidence <= right) if right >= 1.0 else (confidence < right)
        )
        if mask.any():
            ece += float(mask.mean()) * abs(float(confidence[mask].mean() - correct[mask].mean()))
    return {
        "logloss": float(-np.log(selected).mean()),
        "brier": float(np.square(probabilities - one_hot).sum(axis=1).mean()),
        "ece_top_label_10bin": float(ece),
    }


def _training_rows(
    archive: Mapping[str, Any], *, as_of: datetime
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    validated = validate_archive(archive)
    results = {
        str(record["fixture_key"]): record
        for record in validated["results"]
        if as_utc(record["observed_at"], field="result.observed_at") <= as_of
        and record.get("settlement_period") == "90M"
        and record.get("provenance", {}).get("official") is True
    }
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    rejected = {
        "post_kickoff_archive": 0,
        "post_kickoff_generation": 0,
        "result_before_kickoff": 0,
        "missing_official_result": 0,
    }
    for forecast in validated["forecasts"]:
        key = str(forecast["fixture_key"])
        result = results.get(key)
        if result is None:
            rejected["missing_official_result"] += 1
            continue
        kickoff = as_utc(forecast["kickoff_utc"], field="forecast.kickoff_utc")
        generated = as_utc(forecast["generated_at"], field="forecast.generated_at")
        archived = as_utc(forecast["archived_at"], field="forecast.archived_at")
        result_observed = as_utc(result["observed_at"], field="result.observed_at")
        if generated >= kickoff:
            rejected["post_kickoff_generation"] += 1
            continue
        if archived >= kickoff:
            rejected["post_kickoff_archive"] += 1
            continue
        if result_observed < kickoff:
            rejected["result_before_kickoff"] += 1
            continue
        model = str(forecast["model"])
        identity = (key, model)
        row = {
            "fixture_key": key,
            "fixture_id": str(forecast["fixture_id"]),
            "kickoff": kickoff,
            "generated_at": generated,
            "archived_at": archived,
            "result_observed_at": result_observed,
            "model": model,
            "probabilities": [
                float(forecast["probabilities"][name]) for name in _OUTCOMES
            ],
            "outcome": _OUTCOMES.index(str(result["outcome"])),
            "forecast_id": str(forecast["forecast_id"]),
            "result_id": str(result["result_id"]),
        }
        previous = candidates.get(identity)
        # Freeze exactly one prediction per match/model: the latest prediction
        # that the append-only archive itself observed before kickoff.
        if previous is None or row["generated_at"] > previous["generated_at"]:
            candidates[identity] = row

    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in candidates.values():
        by_model.setdefault(row["model"], []).append(row)
    if not by_model:
        return [], {"source_model": None, "rejected": rejected, "model_counts": {}}
    source_model = sorted(by_model, key=lambda name: (-len(by_model[name]), name))[0]
    rows = sorted(
        by_model[source_model], key=lambda row: (row["kickoff"], row["fixture_key"])
    )
    return rows, {
        "source_model": source_model,
        "rejected": rejected,
        "model_counts": {key: len(value) for key, value in sorted(by_model.items())},
    }


def _walk_forward_blocks(rows: list[dict[str, Any]]) -> list[tuple[list[int], list[int]]]:
    min_train = int(_POLICY["min_train_per_fold"])
    block_size = int(_POLICY["test_block_size"])
    if len(rows) <= min_train:
        return []
    kickoff_values = [row["kickoff"] for row in rows]
    groups: list[list[int]] = []
    for index, kickoff in enumerate(kickoff_values):
        if not groups or kickoff_values[groups[-1][0]] != kickoff:
            groups.append([])
        groups[-1].append(index)

    first_test_group = None
    count = 0
    for group_index, group in enumerate(groups):
        if count >= min_train:
            first_test_group = group_index
            break
        count += len(group)
    if first_test_group is None:
        return []

    blocks: list[tuple[list[int], list[int]]] = []
    cursor = first_test_group
    while cursor < len(groups):
        test: list[int] = []
        end = cursor
        while end < len(groups) and len(test) < block_size:
            test.extend(groups[end])
            end += 1
        test_start = kickoff_values[test[0]]
        train = [index for index, kickoff in enumerate(kickoff_values) if kickoff < test_start]
        if len(train) >= min_train and test:
            blocks.append((train, test))
        cursor = end
    return blocks


def _paired_ci(values: np.ndarray, *, seed: int) -> tuple[float, float]:
    n_resamples = int(_POLICY["paired_bootstrap_resamples"])
    if values.size == 0:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    estimates = np.empty(n_resamples, dtype=float)
    for index in range(n_resamples):
        estimates[index] = float(rng.choice(values, size=len(values), replace=True).mean())
    return float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def evaluate_temperature_challenger(
    archive: Mapping[str, Any], *, evaluated_at: str | datetime
) -> dict[str, Any]:
    """Train/evaluate one challenger using only evidence known by ``evaluated_at``."""
    as_of = as_utc(evaluated_at, field="evaluated_at")
    validated_archive = validate_archive(archive)
    rows, quality = _training_rows(validated_archive, as_of=as_of)
    archive_event_chain_head = (
        str(validated_archive["events"][-1]["event_hash"])
        if validated_archive["events"] else "0" * 64
    )
    blocks = _walk_forward_blocks(rows)
    champion_oos: list[np.ndarray] = []
    challenger_oos: list[np.ndarray] = []
    outcome_oos: list[np.ndarray] = []
    fold_reports: list[dict[str, Any]] = []
    for fold_number, (train_indices, test_indices) in enumerate(blocks, start=1):
        train_probs = np.asarray([rows[index]["probabilities"] for index in train_indices])
        train_y = np.asarray([rows[index]["outcome"] for index in train_indices], dtype=int)
        test_probs = np.asarray([rows[index]["probabilities"] for index in test_indices])
        test_y = np.asarray([rows[index]["outcome"] for index in test_indices], dtype=int)
        temperature = _fit_temperature(train_probs, train_y)
        calibrated = _temperature_scale(test_probs, temperature)
        champion_oos.append(test_probs)
        challenger_oos.append(calibrated)
        outcome_oos.append(test_y)
        train_end = max(rows[index]["kickoff"] for index in train_indices)
        test_start = min(rows[index]["kickoff"] for index in test_indices)
        if not train_end < test_start:
            raise ValueError("walk-forward leakage: train cutoff is not before test")
        fold_reports.append({
            "fold": fold_number,
            "n_train": len(train_indices),
            "n_test": len(test_indices),
            "train_end_utc": iso_utc(train_end, field="train_end"),
            "test_start_utc": iso_utc(test_start, field="test_start"),
            "test_end_utc": iso_utc(
                max(rows[index]["kickoff"] for index in test_indices),
                field="test_end",
            ),
            "temperature": temperature,
        })

    if champion_oos:
        champion = np.vstack(champion_oos)
        challenger = np.vstack(challenger_oos)
        outcomes = np.concatenate(outcome_oos)
        champion_metrics = _metrics(champion, outcomes)
        challenger_metrics = _metrics(challenger, outcomes)
        champion_loss = -np.log(np.clip(champion[np.arange(len(outcomes)), outcomes], 1e-12, 1.0))
        challenger_loss = -np.log(np.clip(challenger[np.arange(len(outcomes)), outcomes], 1e-12, 1.0))
        improvement = champion_loss - challenger_loss
        seed = int(_hash([row["fixture_key"] for row in rows])[:8], 16)
        ci_low, ci_high = _paired_ci(improvement, seed=seed)
    else:
        champion_metrics = {"logloss": None, "brier": None, "ece_top_label_10bin": None}
        challenger_metrics = deepcopy(champion_metrics)
        outcomes = np.asarray([], dtype=int)
        improvement = np.asarray([], dtype=float)
        ci_low = ci_high = None

    if rows:
        all_probs = np.asarray([row["probabilities"] for row in rows])
        all_y = np.asarray([row["outcome"] for row in rows], dtype=int)
        final_temperature = _fit_temperature(all_probs, all_y)
        training_cutoff = max(row["kickoff"] for row in rows)
    else:
        final_temperature = 1.0
        training_cutoff = None

    n_oos = int(len(outcomes))
    ll_gain = (
        float(champion_metrics["logloss"] - challenger_metrics["logloss"])
        if n_oos else None
    )
    brier_gain = (
        float(champion_metrics["brier"] - challenger_metrics["brier"])
        if n_oos else None
    )
    ece_degradation = (
        float(challenger_metrics["ece_top_label_10bin"] - champion_metrics["ece_top_label_10bin"])
        if n_oos else None
    )
    guardrails = {
        "minimum_total_settled": len(rows) >= int(_POLICY["min_total_settled"]),
        "minimum_oos_predictions": n_oos >= int(_POLICY["min_oos_predictions"]),
        "minimum_walk_forward_folds": len(fold_reports) >= int(_POLICY["min_walk_forward_folds"]),
        "logloss_score_improves": bool(ll_gain is not None and ll_gain >= float(_POLICY["min_logloss_improvement"])),
        "brier_score_not_worse": bool(brier_gain is not None and brier_gain >= float(_POLICY["min_brier_improvement"])),
        "calibration_below_ceiling": bool(
            n_oos and challenger_metrics["ece_top_label_10bin"] <= float(_POLICY["max_candidate_ece"])
        ),
        "calibration_not_materially_worse": bool(
            ece_degradation is not None and ece_degradation <= float(_POLICY["max_ece_degradation"])
        ),
        "paired_improvement_ci_above_zero": bool(
            ci_low is not None and ci_low > float(_POLICY["required_improvement_ci_low"])
        ),
        "point_in_time_integrity": all(
            row["generated_at"] < row["kickoff"]
            and row["archived_at"] < row["kickoff"]
            and row["result_observed_at"] >= row["kickoff"]
            for row in rows
        ),
    }
    eligible = all(guardrails.values())
    fingerprint_rows = [{
        "fixture_key": row["fixture_key"],
        "forecast_id": row["forecast_id"],
        "result_id": row["result_id"],
    } for row in rows]
    data_fingerprint = _hash(fingerprint_rows)
    evaluated_timestamp = iso_utc(as_of, field="evaluated_at")
    identity = {
        "family": _POLICY["challenger_family"],
        "source_model": quality["source_model"],
        "data_fingerprint": data_fingerprint,
        "archive_event_chain_head": archive_event_chain_head,
        "policy_hash": POLICY_HASH,
    }
    candidate = {
        "candidate_id": f"candidate-{_hash(identity)[:24]}",
        "status": "eligible" if eligible else "blocked",
        "deployment_scope": "PAPER_ONLY",
        "real_money_execution": False,
        "evaluated_at": evaluated_timestamp,
        "valid_for_forecasts_after": evaluated_timestamp,
        "source_model": quality["source_model"],
        "model": {
            "family": _POLICY["challenger_family"],
            "temperature": final_temperature,
            "fit_scope": "all eligible completed evidence at evaluation cutoff",
        },
        "training": {
            "n_settled": len(rows),
            "training_cutoff_utc": (
                iso_utc(training_cutoff, field="training_cutoff")
                if training_cutoff is not None else None
            ),
            "data_fingerprint": data_fingerprint,
            "archive_event_chain_head": archive_event_chain_head,
            "source_archive_schema": "match-evidence-archive/1.0",
            "official_results_only": True,
            "regulation_time_only": True,
            "point_in_time_cutoff": evaluated_timestamp,
            "quality": quality,
        },
        "evaluation": {
            "method": "expanding_window_walk_forward",
            "n_oos": n_oos,
            "folds": fold_reports,
            "champion": champion_metrics,
            "challenger": challenger_metrics,
            "mean_logloss_improvement": (
                float(improvement.mean()) if improvement.size else None
            ),
            "mean_brier_improvement": brier_gain,
            "ece_degradation": ece_degradation,
            "paired_logloss_improvement_ci95": {
                "low": ci_low,
                "high": ci_high,
                "bootstrap_unit": "match",
            },
        },
        "guardrails": guardrails,
        "decision_reason": (
            "all_preregistered_paper_guardrails_passed"
            if eligible else "one_or_more_promotion_guardrails_failed"
        ),
    }
    candidate["content_hash"] = _candidate_hash(candidate)
    return candidate


def register_challenger(
    registry: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    *,
    registered_at: str | datetime,
    auto_promote_paper: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append a candidate and optionally promote it inside PAPER_ONLY scope."""
    when = as_utc(registered_at, field="registered_at")
    output = (
        validate_registry(registry)
        if registry is not None else empty_registry(created_at=when)
    )
    candidate_copy = deepcopy(dict(candidate))
    if candidate_copy.get("content_hash") != _candidate_hash(candidate_copy):
        raise ValueError("candidate hash is invalid")
    candidate_id = str(candidate_copy.get("candidate_id") or "")
    existing = {
        str(row["candidate_id"]): row for row in output["challengers"]
    }
    added = False
    if candidate_id in existing:
        if _canonical_json(existing[candidate_id]) != _canonical_json(candidate_copy):
            raise ValueError("attempted rewrite of an immutable challenger")
    else:
        output["challengers"].append(candidate_copy)
        added = True

    promoted = False
    if auto_promote_paper and candidate_copy.get("status") == "eligible":
        if not all(candidate_copy.get("guardrails", {}).values()):
            raise ValueError("eligible candidate is missing a passing guardrail")
        if output["champion"].get("candidate_id") != candidate_id:
            previous = (
                output["promotion_events"][-1]["event_hash"]
                if output["promotion_events"] else "0" * 64
            )
            event = {
                "sequence": len(output["promotion_events"]) + 1,
                "candidate_id": candidate_id,
                "promoted_at": iso_utc(when, field="promoted_at"),
                "scope": "PAPER_ONLY",
                "reason": "all_preregistered_guardrails_passed",
                "previous_event_hash": previous,
            }
            event["event_hash"] = _promotion_hash(event)
            output["promotion_events"].append(event)
            output["champion"] = {
                "candidate_id": candidate_id,
                "model_family": candidate_copy["model"]["family"],
                "source_model": candidate_copy["source_model"],
                "temperature": candidate_copy["model"]["temperature"],
                "activated_at": iso_utc(when, field="activated_at"),
                "valid_for_forecasts_after": candidate_copy["valid_for_forecasts_after"],
                "deployment_scope": "PAPER_ONLY",
                "real_money_execution": False,
            }
            promoted = True
    if added or promoted:
        output["updated_at"] = iso_utc(
            max(when, as_utc(output["updated_at"], field="updated_at")),
            field="updated_at",
        )
    return validate_registry(output), {
        "candidate_added": added,
        "candidate_id": candidate_id,
        "status": candidate_copy.get("status"),
        "paper_promoted": promoted,
        "failed_guardrails": [
            key for key, passed in candidate_copy.get("guardrails", {}).items()
            if not passed
        ],
    }


def apply_active_calibration(
    registry: Mapping[str, Any],
    probabilities: Iterable[float],
    *,
    source_model: str,
    forecast_generated_at: str | datetime,
) -> tuple[float, float, float]:
    """Inference hook; returns identity unless a valid PAPER champion applies."""
    validated = validate_registry(registry)
    values = np.asarray([list(probabilities)], dtype=float)
    if values.shape != (1, 3):
        raise ValueError("exactly three 1X2 probabilities are required")
    champion = validated["champion"]
    if champion.get("candidate_id") is None:
        return tuple(float(value) for value in values[0])
    generated = as_utc(forecast_generated_at, field="forecast_generated_at")
    valid_after = as_utc(
        champion.get("valid_for_forecasts_after"), field="valid_for_forecasts_after"
    )
    if str(champion.get("source_model")) != str(source_model) or generated < valid_after:
        return tuple(float(value) for value in values[0])
    calibrated = _temperature_scale(values, float(champion["temperature"]))[0]
    return tuple(float(value) for value in calibrated)
