"""Assemble the cleaned matches table from raw fd + understat layers."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from xgedge.contracts import Col
from xgedge.data.football_data import load_fd_season
from xgedge.data.understat import load_understat_season

_JOIN_KEYS = [Col.SEASON, Col.DATE, Col.HOME, Col.AWAY]
_MIN_JOIN_RATE = 0.95

_CLEANED_COLUMNS = [
    Col.MATCH_ID,
    Col.SEASON,
    Col.DATE,
    Col.HOME,
    Col.AWAY,
    Col.FTHG,
    Col.FTAG,
    Col.FTR,
    Col.XG_H,
    Col.XG_A,
    Col.NPXG_H,
    Col.NPXG_A,
    Col.PPDA_H,
    Col.PPDA_A,
    Col.DEEP_H,
    Col.DEEP_A,
    Col.RED_H,
    Col.RED_A,
    Col.B365H,
    Col.B365D,
    Col.B365A,
    Col.PSH,
    Col.PSD,
    Col.PSA,
    Col.B365CH,
    Col.B365CD,
    Col.B365CA,
    Col.PSCH,
    Col.PSCD,
    Col.PSCA,
    Col.B365_O25,
    Col.B365_U25,
    Col.B365C_O25,
    Col.B365C_U25,
]


def build_cleaned(seasons: list[str], raw_dir: Path, out_path: Path) -> pd.DataFrame:
    """Join fd and understat per season into the cleaned matches parquet.

    Inner-joins on (season, date, home, away). Raises ``AssertionError``
    when any season loses 5% or more of its football-data rows in the
    join, listing the dropped matches. Returns the sorted cleaned frame.
    """
    frames = []
    for season in seasons:
        fd = load_fd_season(season, raw_dir)
        us = load_understat_season(season, raw_dir)
        merged = fd.merge(us, on=_JOIN_KEYS, how="inner", validate="one_to_one")
        if len(merged) < _MIN_JOIN_RATE * len(fd):
            probe = fd.merge(us[_JOIN_KEYS], on=_JOIN_KEYS, how="left", indicator=True)
            dropped = probe.loc[probe["_merge"] == "left_only", _JOIN_KEYS]
            lines = [
                f"  {row[Col.DATE]:%Y-%m-%d} {row[Col.HOME]} vs {row[Col.AWAY]}"
                for _, row in dropped.iterrows()
            ]
            raise AssertionError(
                f"Season {season}: join kept {len(merged)}/{len(fd)} "
                f"football-data rows (<{_MIN_JOIN_RATE:.0%}). "
                "Dropped matches:\n" + "\n".join(lines)
            )
        frames.append(merged)

    df = pd.concat(frames, ignore_index=True)
    df[Col.MATCH_ID] = [
        f"{season}_{date:%Y%m%d}_{home}_{away}"
        for season, date, home, away in zip(
            df[Col.SEASON], df[Col.DATE], df[Col.HOME], df[Col.AWAY]
        )
    ]
    df = (
        df.sort_values(Col.DATE, kind="mergesort")
        .reset_index(drop=True)
        .loc[:, _CLEANED_COLUMNS]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return df
