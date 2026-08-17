"""Deterministic JSON storage helpers for large automation state files."""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    """Read UTF-8 JSON, transparently handling a ``.gz`` suffix."""
    try:
        payload = path.read_bytes()
        if path.suffix.casefold() == ".gz":
            payload = gzip.decompress(payload)
        return json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc


def write_json_atomic(path: Path, value: Any) -> bool:
    """Atomically write stable JSON or gzip JSON and report whether it changed.

    Existing gzip content is compared after decompression.  That prevents a
    platform-specific gzip header from creating a meaningless Git diff.
    """
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    if path.exists():
        try:
            existing = path.read_bytes()
            if path.suffix.casefold() == ".gz":
                existing = gzip.decompress(existing)
            if existing == payload:
                return False
        except (OSError, gzip.BadGzipFile):
            pass

    encoded = (
        gzip.compress(payload, compresslevel=9, mtime=0)
        if path.suffix.casefold() == ".gz"
        else payload
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)
    return True
