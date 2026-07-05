"""Download raw data: football-data.co.uk CSVs and understat season JSONs."""
from __future__ import annotations

import argparse
import time

from xgedge.contracts import FD_SEASON_CODES, RAW_DIR, SEASONS, UNDERSTAT_YEARS
from xgedge.data.football_data import download_fd_season
from xgedge.data.understat import download_understat_season


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", default=SEASONS, choices=SEASONS)
    parser.add_argument("--force", action="store_true",
                        help="re-download even when the raw file exists")
    args = parser.parse_args(argv)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for season in args.seasons:
        fd_path = RAW_DIR / f"fd_{FD_SEASON_CODES[season]}.csv"
        us_path = RAW_DIR / f"understat_{UNDERSTAT_YEARS[season]}.json"
        if args.force:
            fd_path.unlink(missing_ok=True)
            us_path.unlink(missing_ok=True)

        existed = fd_path.exists()
        download_fd_season(season, RAW_DIR)
        print(f"{season}: {fd_path.name} {'skipped' if existed else 'downloaded'}")

        existed = us_path.exists()
        download_understat_season(season, RAW_DIR)
        print(f"{season}: {us_path.name} {'skipped' if existed else 'downloaded'}")
        if not existed:
            time.sleep(1.5)  # be polite to understat


if __name__ == "__main__":
    main()
