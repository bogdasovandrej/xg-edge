"""Re-derive the published numbers and report where they disagree.

The golden tests prove the formulas are right on fixed inputs. This checks the
opposite direction: that the numbers actually published for real fixtures are
consistent with those formulas. A refactor that quietly changes how a field is
populated passes every unit test and still ships a wrong price.

Each check re-computes a published value from its own inputs and compares. The
audit never repairs a row: a silent correction would hide the defect that
produced it, and the same defect would keep shipping. It reports, and a
CRITICAL finding suppresses the betting surfaces for the affected fixture.

Severity:

``CRITICAL``  the arithmetic behind a price is wrong, or data from after
              kickoff reached a pre-match decision. Bets must not be shown.
``WARNING``   internally consistent but suspicious — worth a human look.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

SELF_AUDIT_SCHEMA = "payload-self-audit/1.0"

# Absolute tolerances. Generous enough for float noise, tight enough that a
# real formula error cannot hide underneath.
STATE_SUM_TOLERANCE = 1e-3
PRICE_TOLERANCE = 1e-6
VALUE_PCT_TOLERANCE = 1e-4


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) else None


def _finding(
    severity: str, check: str, fixture_id: str, detail: str, **extra: Any
) -> dict[str, Any]:
    return {
        "severity": severity,
        "check": check,
        "fixture_id": fixture_id,
        "detail": detail,
        **extra,
    }


def _audit_market_row(
    row: Mapping[str, Any], fixture_id: str
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    label = str(row.get("label") or row.get("market") or "?")
    for name in ("central", "conservative"):
        states = row.get(name)
        if not isinstance(states, Mapping):
            continue
        win = _number(states.get("win"))
        push = _number(states.get("push"))
        loss = _number(states.get("loss"))
        if win is None or push is None or loss is None:
            findings.append(_finding(
                "CRITICAL", "states_incomplete", fixture_id,
                f"{label}: {name} is missing a win/push/loss value",
            ))
            continue
        total = win + push + loss
        if abs(total - 1.0) > STATE_SUM_TOLERANCE:
            findings.append(_finding(
                "CRITICAL", "states_do_not_sum_to_one", fixture_id,
                f"{label}: {name} sums to {total:.6f}", market=label, sum=total,
            ))
        if win <= 0.0 or loss < 0.0 or push < 0.0:
            findings.append(_finding(
                "CRITICAL", "state_out_of_range", fixture_id,
                f"{label}: {name} has a non-positive win or negative mass",
            ))

    conservative = row.get("conservative")
    published_fair = _number(row.get("fair"))
    if isinstance(conservative, Mapping) and published_fair is not None:
        win = _number(conservative.get("win"))
        loss = _number(conservative.get("loss"))
        if win and win > 0 and loss is not None:
            # The contract's fair price, re-derived from the states themselves.
            expected = 1.0 + loss / win
            if abs(expected - published_fair) > PRICE_TOLERANCE * max(1.0, expected):
                findings.append(_finding(
                    "CRITICAL", "fair_disagrees_with_states", fixture_id,
                    f"{label}: published fair {published_fair:.6f}, "
                    f"1 + L/W gives {expected:.6f}",
                    market=label, published=published_fair, expected=expected,
                ))

    minimum = _number(row.get("min_entry"))
    if minimum is not None and published_fair is not None and minimum <= published_fair:
        findings.append(_finding(
            "CRITICAL", "min_entry_not_above_fair", fixture_id,
            f"{label}: min_entry {minimum:.4f} does not exceed fair {published_fair:.4f}",
            market=label,
        ))

    central = _number(row.get("theoretical_probability"))
    cautious = _number(row.get("conservative_probability"))
    if central is not None and cautious is not None and cautious > central:
        findings.append(_finding(
            "WARNING", "conservative_above_central", fixture_id,
            f"{label}: conservative {cautious:.4f} exceeds central {central:.4f}",
            market=label,
        ))
    return findings


def _audit_candidate(
    candidate: Mapping[str, Any], fixture_id: str, kickoff: datetime | None
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    label = str(candidate.get("selection") or candidate.get("outcome") or "?")
    probability = _number(candidate.get("probability"))
    odds = _number(candidate.get("market_odds"))
    value = _number(candidate.get("value_pct"))

    if probability is not None and not 0.0 < probability < 1.0:
        findings.append(_finding(
            "CRITICAL", "probability_out_of_range", fixture_id,
            f"{label}: probability {probability}",
        ))
    if odds is not None and odds <= 1.0:
        findings.append(_finding(
            "CRITICAL", "odds_not_above_one", fixture_id, f"{label}: odds {odds}",
        ))
    if probability is not None and odds is not None and value is not None:
        # value_pct must be the same number the price implies, not a copy of
        # some other field that happened to be nearby.
        expected = (probability * odds - 1.0) * 100.0
        if abs(expected - value) > VALUE_PCT_TOLERANCE * max(1.0, abs(expected)):
            findings.append(_finding(
                "CRITICAL", "value_pct_disagrees_with_price", fixture_id,
                f"{label}: published value {value:.6f}%, price implies {expected:.6f}%",
                published=value, expected=expected,
            ))

    captured = _utc(candidate.get("quote_captured_at"))
    if kickoff is not None and captured is not None and captured >= kickoff:
        findings.append(_finding(
            "CRITICAL", "quote_captured_after_kickoff", fixture_id,
            f"{label}: quote captured {candidate.get('quote_captured_at')} "
            f"at or after kickoff {kickoff.isoformat()}",
        ))
    return findings


def audit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive published prices and timings; report every disagreement."""
    findings: list[dict[str, Any]] = []
    checked_markets = 0
    checked_candidates = 0

    for forecast in payload.get("forecasts", []) or []:
        if not isinstance(forecast, Mapping):
            continue
        fixture_id = str(forecast.get("id") or "?")
        kickoff = _utc(forecast.get("kickoff_utc"))

        generated = _utc(forecast.get("forecast_generated_at"))
        if kickoff is not None and generated is not None and generated >= kickoff:
            findings.append(_finding(
                "CRITICAL", "forecast_generated_after_kickoff", fixture_id,
                f"forecast generated {forecast.get('forecast_generated_at')} "
                f"at or after kickoff {forecast.get('kickoff_utc')}",
            ))

        for row in forecast.get("model_market_forecasts", []) or []:
            if not isinstance(row, Mapping) or row.get("status") == "SOURCE_GAP":
                continue
            checked_markets += 1
            findings.extend(_audit_market_row(row, fixture_id))

        details = forecast.get("details")
        if isinstance(details, Mapping):
            for key in ("market_candidates", "expanded_market_candidates"):
                for candidate in details.get(key) or []:
                    if not isinstance(candidate, Mapping):
                        continue
                    checked_candidates += 1
                    findings.extend(_audit_candidate(candidate, fixture_id, kickoff))

    critical = [row for row in findings if row["severity"] == "CRITICAL"]
    affected = sorted({row["fixture_id"] for row in critical})
    by_check: dict[str, int] = {}
    for row in findings:
        by_check[row["check"]] = by_check.get(row["check"], 0) + 1

    return {
        "schema_version": SELF_AUDIT_SCHEMA,
        "status": "FAILED" if critical else "PASSED",
        "checked_markets": checked_markets,
        "checked_candidates": checked_candidates,
        "findings": findings,
        "findings_by_check": dict(sorted(by_check.items())),
        "critical_count": len(critical),
        "fixtures_with_critical_findings": affected,
        "policy": (
            "Расхождение не исправляется автоматически: тихая правка скрыла бы "
            "дефект, который его породил. Матч с критичной находкой теряет "
            "ставочные поверхности до разбора."
        ),
    }


def suppress_unsafe_fixtures(payload: dict[str, Any], audit: Mapping[str, Any]) -> dict[str, Any]:
    """Strip betting surfaces from fixtures the audit flagged as critical."""
    blocked = set(audit.get("fixtures_with_critical_findings") or ())
    if not blocked:
        return payload
    for forecast in payload.get("forecasts", []) or []:
        if not isinstance(forecast, dict) or str(forecast.get("id")) not in blocked:
            continue
        forecast["betting_eligible"] = False
        forecast["value_verdict"] = {
            "status": "NO_BET_FAILED_SELF_AUDIT",
            "text": (
                "Не рекомендую ставить: расчёт по этому матчу не прошёл "
                "самопроверку. Числа показаны, но ставки по ним закрыты."
            ),
        }
        details = forecast.get("details")
        if isinstance(details, dict):
            for key in ("market_candidates", "expanded_market_candidates"):
                if key in details:
                    details[key] = []
    top = payload.get("value_top")
    if isinstance(top, dict) and isinstance(top.get("candidates"), list):
        top["candidates"] = [
            row for row in top["candidates"]
            if str(row.get("fixture_id")) not in blocked
        ]
    return payload
