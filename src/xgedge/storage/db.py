"""SQLite store for prices, analyses, value calculations and settlements.

Why SQLite in the repository rather than a hosted database: every workflow
that writes project data already shares the ``xgedge-data-writers``
concurrency group with ``cancel-in-progress: false``, so exactly one writer
runs at a time. That removes the concurrent-write problem this file would
otherwise have, at zero hosting cost. The schema is plain SQL and the access
layer is narrow on purpose, so moving to a hosted engine later is a change of
connection, not of call sites.

Two rules the schema exists to enforce:

* price history is append-only. Without a previous observation there is no
  ``delta_pct``, so no line screener and no CLV.
* ``analysis`` rows are never updated in place. A revised probability
  supersedes its predecessor via ``superseded_by``, so calibration can still
  be checked against what was actually believed at the time.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

SCHEMA_VERSION = "xgedge-store/1.0"

DEFAULT_PATH = Path("reports/live/xgedge.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixtures (
  id TEXT PRIMARY KEY,
  tournament TEXT, round TEXT,
  home TEXT, away TEXT,
  kickoff_utc TEXT,
  leg INTEGER, aggregate_home INTEGER, aggregate_away INTEGER,
  status TEXT
);

CREATE TABLE IF NOT EXISTS markets (
  id TEXT PRIMARY KEY,
  fixture_id TEXT REFERENCES fixtures(id),
  period TEXT,
  family TEXT, selection TEXT, line REAL,
  calc_mode TEXT,
  settlement_rule TEXT
);

CREATE TABLE IF NOT EXISTS analysis (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id TEXT REFERENCES markets(id),
  model_version TEXT,
  central_win REAL, central_push REAL, central_loss REAL,
  cons_win REAL, cons_push REAL, cons_loss REAL,
  data_quality TEXT,
  main_thesis TEXT, anti_thesis TEXT, failure_modes TEXT,
  sources TEXT,
  created_at TEXT,
  superseded_by INTEGER NULL REFERENCES analysis(id)
);

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_id TEXT REFERENCES markets(id),
  bookmaker TEXT, odds REAL,
  checked_at TEXT, executable INTEGER, source TEXT
);

CREATE TABLE IF NOT EXISTS value_calc (
  market_id TEXT REFERENCES markets(id),
  fair REAL, min_entry REAL,
  ev_at_risk REAL, value_pct REAL, value_rating REAL,
  gate_price INTEGER, gate_data INTEGER, gate_ev INTEGER,
  status TEXT,
  computed_at TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY,
  legs TEXT, stake REAL, odds_product REAL,
  bookmaker TEXT, placed_at TEXT
);

CREATE TABLE IF NOT EXISTS settlement (
  market_id TEXT, ticket_id TEXT NULL,
  final_score TEXT, period TEXT,
  state TEXT,
  entry_odds REAL, fair_at_entry REAL, closing_odds REAL,
  clv REAL,
  source_conflict INTEGER,
  error_class TEXT,
  settled_at TEXT
);

CREATE TABLE IF NOT EXISTS bankroll (
  ts TEXT, amount REAL, in_play REAL, reserve REAL
);

CREATE INDEX IF NOT EXISTS quotes_market_time ON quotes(market_id, checked_at);
CREATE INDEX IF NOT EXISTS value_calc_market ON value_calc(market_id, computed_at);
CREATE INDEX IF NOT EXISTS analysis_market ON analysis(market_id, created_at);
"""


def market_id(fixture_id: str, market: str, selection: str, line: Any) -> str:
    """Stable identity for one exact market. Identity must never be fuzzy."""
    return "|".join((str(fixture_id), str(market), str(selection), str(line)))


def connect(path: str | Path = DEFAULT_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the store and apply the schema."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


@contextmanager
def store(path: str | Path = DEFAULT_PATH) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def upsert_fixture(connection: sqlite3.Connection, fixture: Mapping[str, Any]) -> str:
    identity = str(fixture["id"])
    connection.execute(
        """INSERT INTO fixtures (id, tournament, round, home, away, kickoff_utc,
                                 leg, aggregate_home, aggregate_away, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             tournament=excluded.tournament, round=excluded.round,
             home=excluded.home, away=excluded.away,
             kickoff_utc=excluded.kickoff_utc, leg=excluded.leg,
             aggregate_home=excluded.aggregate_home,
             aggregate_away=excluded.aggregate_away, status=excluded.status""",
        (
            identity,
            fixture.get("tournament") or fixture.get("competition"),
            fixture.get("round") or fixture.get("stage"),
            fixture.get("home"), fixture.get("away"),
            fixture.get("kickoff_utc"), fixture.get("leg"),
            fixture.get("aggregate_home"), fixture.get("aggregate_away"),
            fixture.get("status") or "scheduled",
        ),
    )
    return identity


def upsert_market(connection: sqlite3.Connection, market: Mapping[str, Any]) -> str:
    identity = str(market.get("id") or market_id(
        market["fixture_id"], market["family"], market["selection"], market.get("line")
    ))
    connection.execute(
        """INSERT INTO markets (id, fixture_id, period, family, selection, line,
                                calc_mode, settlement_rule)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             period=excluded.period, family=excluded.family,
             selection=excluded.selection, line=excluded.line,
             calc_mode=excluded.calc_mode,
             settlement_rule=COALESCE(excluded.settlement_rule, markets.settlement_rule)""",
        (
            identity, str(market["fixture_id"]),
            market.get("period") or "90M",
            market.get("family"), market.get("selection"), market.get("line"),
            market.get("calc_mode"), market.get("settlement_rule"),
        ),
    )
    return identity


def record_quote(
    connection: sqlite3.Connection,
    *,
    market_key: str,
    bookmaker: str,
    odds: float,
    checked_at: str,
    executable: bool = True,
    source: str | None = None,
) -> int:
    """Append one price observation. Never overwrites an earlier one."""
    cursor = connection.execute(
        """INSERT INTO quotes (market_id, bookmaker, odds, checked_at, executable, source)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (market_key, bookmaker, float(odds), checked_at, int(bool(executable)), source),
    )
    return int(cursor.lastrowid)


def quote_history(
    connection: sqlite3.Connection, market_key: str, *, bookmaker: str | None = None
) -> list[dict[str, Any]]:
    """All observations for one market, oldest first."""
    if bookmaker is None:
        rows = connection.execute(
            "SELECT * FROM quotes WHERE market_id = ? ORDER BY checked_at, id", (market_key,)
        )
    else:
        rows = connection.execute(
            "SELECT * FROM quotes WHERE market_id = ? AND bookmaker = ? ORDER BY checked_at, id",
            (market_key, bookmaker),
        )
    return [dict(row) for row in rows]


def record_analysis(
    connection: sqlite3.Connection,
    *,
    market_key: str,
    model_version: str,
    central: Mapping[str, float],
    conservative: Mapping[str, float],
    created_at: str,
    data_quality: str | None = None,
    main_thesis: str | None = None,
    anti_thesis: str | None = None,
    failure_modes: Sequence[str] | None = None,
    sources: Any = None,
    supersedes: int | None = None,
) -> int:
    """Append an analysis version; a revision supersedes rather than replaces."""
    for name, states in (("central", central), ("conservative", conservative)):
        total = float(states["win"]) + float(states.get("push", 0.0)) + float(states["loss"])
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"{name} states must sum to one, got {total}")
    cursor = connection.execute(
        """INSERT INTO analysis (market_id, model_version,
                                 central_win, central_push, central_loss,
                                 cons_win, cons_push, cons_loss,
                                 data_quality, main_thesis, anti_thesis,
                                 failure_modes, sources, created_at, superseded_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
        (
            market_key, model_version,
            float(central["win"]), float(central.get("push", 0.0)), float(central["loss"]),
            float(conservative["win"]), float(conservative.get("push", 0.0)),
            float(conservative["loss"]),
            data_quality, main_thesis, anti_thesis,
            json.dumps(list(failure_modes or []), ensure_ascii=False),
            json.dumps(sources, ensure_ascii=False) if sources is not None else None,
            created_at,
        ),
    )
    identity = int(cursor.lastrowid)
    if supersedes is not None:
        connection.execute(
            "UPDATE analysis SET superseded_by = ? WHERE id = ?", (identity, int(supersedes))
        )
    return identity


def current_analysis(connection: sqlite3.Connection, market_key: str) -> dict[str, Any] | None:
    """The analysis version nothing has superseded yet."""
    row = connection.execute(
        """SELECT * FROM analysis WHERE market_id = ? AND superseded_by IS NULL
           ORDER BY created_at DESC, id DESC LIMIT 1""",
        (market_key,),
    ).fetchone()
    return dict(row) if row is not None else None


def record_value_calc(
    connection: sqlite3.Connection, *, market_key: str, computed_at: str, **fields: Any
) -> None:
    connection.execute(
        """INSERT INTO value_calc (market_id, fair, min_entry, ev_at_risk, value_pct,
                                   value_rating, gate_price, gate_data, gate_ev,
                                   status, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            market_key, fields.get("fair"), fields.get("min_entry"),
            fields.get("ev_at_risk"), fields.get("value_pct"), fields.get("value_rating"),
            _flag(fields.get("gate_price")), _flag(fields.get("gate_data")),
            _flag(fields.get("gate_ev")), fields.get("status"), computed_at,
        ),
    )


def record_settlement(
    connection: sqlite3.Connection,
    *,
    market_key: str,
    state: str,
    settled_at: str,
    ticket_id: str | None = None,
    final_score: str | None = None,
    period: str = "90M",
    entry_odds: float | None = None,
    fair_at_entry: float | None = None,
    closing_odds: float | None = None,
    source_conflict: bool = False,
    error_class: str | None = None,
) -> None:
    """Store a settlement together with the numbers calibration later needs.

    ``fair_at_entry`` is stored explicitly: recomputing it after the model has
    moved on would measure the new model, not the decision that was made.
    """
    clv = None
    if entry_odds is not None and closing_odds not in (None, 0):
        clv = (float(entry_odds) / float(closing_odds) - 1.0) * 100.0
    connection.execute(
        """INSERT INTO settlement (market_id, ticket_id, final_score, period, state,
                                   entry_odds, fair_at_entry, closing_odds, clv,
                                   source_conflict, error_class, settled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            market_key, ticket_id, final_score, period, state,
            entry_odds, fair_at_entry, closing_odds, clv,
            int(bool(source_conflict)), error_class, settled_at,
        ),
    )


def _flag(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def calibration_buckets(
    connection: sqlite3.Connection, *, edges: Sequence[float] = (0.4, 0.5, 0.6, 0.7, 0.8)
) -> list[dict[str, Any]]:
    """Observed versus predicted win rate per probability bucket.

    Half-win and half-loss count as half a win, which keeps quarter Asian
    settlements from being rounded into the wrong bucket.
    """
    rows = connection.execute(
        """SELECT s.state AS state, a.cons_win AS predicted
           FROM settlement s
           JOIN analysis a ON a.market_id = s.market_id AND a.superseded_by IS NULL
           WHERE s.state IN ('WIN','HALF_WIN','PUSH','HALF_LOSS','LOSS')"""
    ).fetchall()
    scores = {"WIN": 1.0, "HALF_WIN": 0.5, "PUSH": None, "HALF_LOSS": 0.5, "LOSS": 0.0}
    buckets: list[dict[str, Any]] = []
    for low, high in zip(edges, edges[1:]):
        selected = [
            (float(row["predicted"]), scores[row["state"]])
            for row in rows
            if row["predicted"] is not None and low <= float(row["predicted"]) < high
            and scores[row["state"]] is not None
        ]
        buckets.append({
            "bucket": f"{int(low * 100)}-{int(high * 100)}",
            "n": len(selected),
            "predicted": sum(p for p, _ in selected) / len(selected) if selected else None,
            "observed": sum(o for _, o in selected) / len(selected) if selected else None,
        })
    return buckets
