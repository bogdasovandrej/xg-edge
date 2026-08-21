"""Mirror value calculations and settled positions into the durable journal.

The append-only JSON ledgers stay the source of truth for the prospective
protocol. This script copies the parts calibration later needs into the
SQLite store, where they can be queried by probability bucket:

* every value calculation, gate-passing or not, with the fair price and
  minimum entry as computed at that moment;
* every PAPER position with the price actually taken AND the fair price
  believed at entry;
* every settlement, with the closing price and CLV where one exists.

Storing ``fair_at_entry`` explicitly is the point. Recomputing it later would
measure whatever the model believes then, not the decision that was actually
made, and calibration of a decision you can no longer reconstruct is not
calibration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from xgedge.decision.pricing import min_entry as pricing_min_entry
from xgedge.storage.db import (
    DEFAULT_PATH,
    calibration_buckets,
    market_id,
    record_analysis,
    record_settlement,
    record_value_calc,
    store,
    upsert_fixture,
    upsert_market,
)


def _read(path: Path) -> Any | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def journal_value_calcs(connection: Any, payload: Mapping[str, Any]) -> int:
    """Record every priced candidate, including the ones that failed the gate.

    Rejected rows matter as much as accepted ones: without them there is no
    way to tell later whether the gate was too tight or the prices were.
    """
    computed_at = str(payload.get("generated_at") or "")
    written = 0
    for forecast in payload.get("forecasts", []) or []:
        if not isinstance(forecast, Mapping):
            continue
        fixture_id = str(forecast.get("id") or "")
        details = forecast.get("details")
        if not fixture_id or not isinstance(details, Mapping):
            continue
        upsert_fixture(connection, {
            "id": fixture_id,
            "competition": forecast.get("competition"),
            "round": forecast.get("stage"),
            "home": forecast.get("home"),
            "away": forecast.get("away"),
            "kickoff_utc": forecast.get("kickoff_utc"),
            "status": "scheduled",
        })
        rows: list[Mapping[str, Any]] = []
        for key in ("market_candidates", "expanded_market_candidates"):
            source = details.get(key)
            if isinstance(source, list):
                rows.extend(row for row in source if isinstance(row, Mapping))
        for row in rows:
            fair = _number(row.get("fair"))
            value = _number(row.get("value_pct"))
            if fair is None or value is None:
                continue
            market = str(row.get("market") or "1x2")
            selection = str(row.get("outcome") or row.get("selection") or "")
            key = market_id(fixture_id, market, selection, row.get("line"))
            upsert_market(connection, {
                "fixture_id": fixture_id, "family": market,
                "selection": selection, "line": row.get("line"),
                "period": forecast.get("market_period") or "90M",
                "calc_mode": row.get("calc_mode"),
            })
            record_value_calc(
                connection,
                market_key=key,
                computed_at=computed_at,
                fair=fair,
                min_entry=_number(row.get("min_entry")),
                ev_at_risk=value / 100.0,
                value_pct=value,
                # A human rating, when one exists, is stored beside the
                # arithmetic and never substituted for it.
                value_rating=_number(row.get("value_rating")),
                gate_price=row.get("gate_price"),
                gate_data=None,
                gate_ev=value > 0,
                status=row.get("value_status") or "CANDIDATE",
            )
            written += 1
    return written


def journal_positions(connection: Any, ledger: Mapping[str, Any]) -> int:
    """Record each PAPER enrollment's taken price and its fair price at entry."""
    enrollments = ledger.get("enrollments")
    if not isinstance(enrollments, Mapping):
        return 0
    written = 0
    for enrollment in enrollments.values():
        if not isinstance(enrollment, Mapping):
            continue
        fixture_id = str(enrollment.get("fixture_id") or "")
        probability = _number(enrollment.get("model_probability"))
        odds = _number(enrollment.get("odds"))
        if not fixture_id or probability is None or odds is None:
            continue
        if not 0.0 < probability < 1.0:
            continue
        market = str(enrollment.get("market") or "1x2")
        selection = str(enrollment.get("outcome") or "")
        key = market_id(fixture_id, market, selection, enrollment.get("line"))
        upsert_fixture(connection, {
            "id": fixture_id,
            "competition": enrollment.get("competition"),
            "round": enrollment.get("stage"),
            "home": enrollment.get("home"),
            "away": enrollment.get("away"),
            "kickoff_utc": enrollment.get("kickoff_utc"),
            "status": "scheduled",
        })
        upsert_market(connection, {
            "fixture_id": fixture_id, "family": market,
            "selection": selection, "line": enrollment.get("line"),
            "period": enrollment.get("market_period") or "90M",
        })
        # model_probability is conditional on no push, so 1/p is exactly the
        # contract's 1 + L/W. Both are stored so neither has to be re-derived.
        loss = 1.0 - probability
        record_analysis(
            connection,
            market_key=key,
            model_version=str(enrollment.get("quote_source") or "paper-ledger"),
            central={"win": probability, "push": 0.0, "loss": loss},
            conservative={"win": probability, "push": 0.0, "loss": loss},
            created_at=str(enrollment.get("enrolled_at") or ""),
            data_quality=str(enrollment.get("data_quality_score") or ""),
            sources={"bookmaker": enrollment.get("bookmaker"), "odds": odds},
        )
        record_value_calc(
            connection,
            market_key=key,
            computed_at=str(enrollment.get("enrolled_at") or ""),
            fair=1.0 / probability,
            min_entry=pricing_min_entry(probability, loss),
            ev_at_risk=probability * odds - 1.0,
            value_pct=(probability * odds - 1.0) * 100.0,
            gate_price=None,
            status="ENROLLED",
        )
        written += 1
    return written


def journal_settlements(
    connection: Any, ledger: Mapping[str, Any], prospective: Mapping[str, Any] | None
) -> int:
    """Record settled positions with closing price and CLV where available."""
    settlements = ledger.get("settlements")
    enrollments = ledger.get("enrollments")
    if not isinstance(settlements, Mapping) or not isinstance(enrollments, Mapping):
        return 0
    fixtures = (
        prospective.get("fixtures") if isinstance(prospective, Mapping) else None
    ) or {}
    written = 0
    for candidate_id, settlement in settlements.items():
        if not isinstance(settlement, Mapping):
            continue
        enrollment = enrollments.get(candidate_id)
        if not isinstance(enrollment, Mapping):
            continue
        fixture_id = str(enrollment.get("fixture_id") or "")
        probability = _number(enrollment.get("model_probability"))
        entry_odds = _number(enrollment.get("odds"))
        if not fixture_id or probability is None or not 0.0 < probability < 1.0:
            continue
        key = market_id(
            fixture_id,
            str(enrollment.get("market") or "1x2"),
            str(enrollment.get("outcome") or ""),
            enrollment.get("line"),
        )
        record = fixtures.get(fixture_id) if isinstance(fixtures, Mapping) else None
        closing = None
        if isinstance(record, Mapping) and isinstance(record.get("closing"), Mapping):
            closing = _number(record["closing"].get("odds"))
        result = settlement.get("result") if isinstance(settlement, Mapping) else None
        record_settlement(
            connection,
            market_key=key,
            state=str(settlement.get("state") or settlement.get("outcome") or "PENDING").upper(),
            settled_at=str(settlement.get("settled_at") or ""),
            final_score=(
                f"{result.get('home_goals_90')}:{result.get('away_goals_90')}"
                if isinstance(result, Mapping) else None
            ),
            period=str(enrollment.get("market_period") or "90M"),
            entry_odds=entry_odds,
            fair_at_entry=1.0 / probability,
            closing_odds=closing,
            source_conflict=bool(settlement.get("source_conflict", False)),
        )
        written += 1
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-payload", type=Path, default=Path("reports/live_predictions.json"))
    parser.add_argument("--paper-ledger", type=Path, default=Path("reports/live/paper_trading.json"))
    parser.add_argument(
        "--prospective-ledger", type=Path, default=Path("reports/live/prospective_clv.json")
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_PATH)
    parser.add_argument(
        "--calibration-output", type=Path, default=Path("reports/live/calibration.json")
    )
    args = parser.parse_args(argv)

    payload = _read(args.live_payload)
    ledger = _read(args.paper_ledger)
    prospective = _read(args.prospective_ledger)

    with store(args.database) as connection:
        values = journal_value_calcs(connection, payload) if isinstance(payload, Mapping) else 0
        positions = journal_positions(connection, ledger) if isinstance(ledger, Mapping) else 0
        settled = (
            journal_settlements(connection, ledger, prospective)
            if isinstance(ledger, Mapping) else 0
        )
        buckets = calibration_buckets(connection)

    args.calibration_output.parent.mkdir(parents=True, exist_ok=True)
    args.calibration_output.write_text(
        json.dumps(
            {
                "schema_version": "calibration-buckets/1.0",
                "note": (
                    "Наблюдение, а не основание менять пороги: для вывода нужна "
                    "достаточная выборка в каждой корзине."
                ),
                "buckets": buckets,
            },
            ensure_ascii=False, indent=2, allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(
        f"journal: {values} value calcs, {positions} positions, {settled} settlements"
    )


if __name__ == "__main__":
    main()
