"""Download registered top-five league data without requiring it to exist yet."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from xgedge.contracts import RAW_DIR
from xgedge.data.competitions import (
    TOP5_COMPETITIONS,
    TOP5_SEASONS,
    SourceDataUnavailable,
    raw_filename,
)
from xgedge.data.football_data import download_fd_season
from xgedge.data.understat import download_understat_season


def _download_one(
    source: str,
    season: str,
    competition: str,
    dest: Path,
    force: bool,
) -> tuple[Path | None, str]:
    target = dest / raw_filename(source, season, competition)
    existed = target.exists()
    if force:
        target.unlink(missing_ok=True)
        existed = False
    if source == "fd":
        path = download_fd_season(season, dest, competition=competition)
    else:
        path = download_understat_season(season, dest, competition=competition)
    return path, "skipped" if existed else "downloaded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--competitions",
        nargs="+",
        default=list(TOP5_COMPETITIONS),
        choices=list(TOP5_COMPETITIONS),
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=["2026-27"],
        choices=list(TOP5_SEASONS),
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["fd", "understat"],
        choices=["fd", "understat"],
    )
    parser.add_argument("--dest", type=Path, default=RAW_DIR)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when an upstream source has not published the season",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.5,
        help="seconds between successful new Understat downloads",
    )
    args = parser.parse_args(argv)

    args.dest.mkdir(parents=True, exist_ok=True)
    unavailable = 0
    for season in args.seasons:
        for competition in args.competitions:
            for source in args.sources:
                try:
                    _, state = _download_one(
                        source, season, competition, args.dest, args.force
                    )
                except SourceDataUnavailable as exc:
                    unavailable += 1
                    print(f"{season} {competition} {source}: unavailable ({exc})")
                    if args.strict:
                        raise
                    continue
                print(f"{season} {competition} {source}: {state}")
                if source == "understat" and state == "downloaded" and args.pause > 0:
                    time.sleep(args.pause)

    if unavailable:
        print(
            f"{unavailable} source dataset(s) are not published yet; "
            "rerun the command later."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
