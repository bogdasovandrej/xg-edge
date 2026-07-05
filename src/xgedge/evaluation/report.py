"""Serialize evaluation results to JSON and a human-readable markdown summary."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _json_default(obj: object) -> object:
    """Convert numpy/pandas/path objects to JSON-serializable equivalents."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (pd.Timestamp, dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, pd.Series):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    # last-resort readable representation instead of failing the whole report
    return str(obj)


def write_metrics_json(results: dict, path: Path) -> None:
    """Write results as indented JSON, converting numpy/pandas scalar types."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(results, indent=2, default=_json_default), encoding="utf-8"
    )


def _fmt(value: object) -> str:
    """Format a cell value: floats rounded to 4 decimals, the rest as str."""
    if isinstance(value, bool) or isinstance(value, np.bool_):
        return str(bool(value))
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.4f}"
    return str(value)


def _metrics_table(block: dict, key_header: str) -> list[str]:
    """Render a dict-of-dicts as a GFM table; columns in first-seen order."""
    columns: list[str] = []
    for row in block.values():
        for key in row:
            if key not in columns:
                columns.append(key)
    lines = [
        "| " + " | ".join([key_header] + columns) + " |",
        "| " + " | ".join(["---"] * (len(columns) + 1)) + " |",
    ]
    for name, row in block.items():
        cells = [_fmt(row[c]) if c in row else "" for c in columns]
        lines.append("| " + " | ".join([str(name)] + cells) + " |")
    return lines


def _kv_table(block: dict, key_header: str, value_header: str) -> list[str]:
    """Render a flat dict as a two-column GFM table."""
    lines = [
        f"| {key_header} | {value_header} |",
        "| --- | --- |",
    ]
    for key, value in block.items():
        lines.append(f"| {key} | {_fmt(value)} |")
    return lines


def write_summary_md(results: dict, path: Path) -> None:
    """Write a GitHub-flavoured markdown summary of an evaluation run.

    Includes a config echo when ``results['config']`` is present, per-model
    1X2 metrics, totals metrics, betting simulation and CLV blocks — each
    section is skipped silently when its key is missing.
    """
    lines: list[str] = ["# xg-edge evaluation summary", ""]

    config = results.get("config")
    if config:
        lines += ["## Config", ""]
        lines += _kv_table(config, "Parameter", "Value")
        lines.append("")

    models_1x2 = results.get("models_1x2")
    if models_1x2:
        lines += ["## 1X2 metrics", ""]
        lines += _metrics_table(models_1x2, "Model")
        lines.append("")

    totals = results.get("totals")
    if totals:
        lines += ["## Over/Under 2.5 metrics", ""]
        lines += _metrics_table(totals, "Model")
        lines.append("")

    bankroll = results.get("bankroll")
    if bankroll:
        lines += ["## Betting simulation", ""]
        if all(isinstance(v, dict) for v in bankroll.values()):
            lines += _metrics_table(bankroll, "Staking")
        else:
            lines += _kv_table(bankroll, "Metric", "Value")
        lines.append("")

    clv = results.get("clv")
    if clv:
        lines += ["## Closing line value", ""]
        lines += _kv_table(clv, "Statistic", "Value")
        lines.append("")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
