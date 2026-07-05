"""Build the cleaned matches table from raw sources, with a QA summary."""
from __future__ import annotations

import argparse

from xgedge.contracts import CLEANED_MATCHES, RAW_DIR, SEASONS, Col
from xgedge.data.assemble import build_cleaned


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seasons", nargs="+", default=SEASONS, choices=SEASONS)
    args = parser.parse_args(argv)

    df = build_cleaned(args.seasons, RAW_DIR, CLEANED_MATCHES)
    print(f"cleaned matches written to {CLEANED_MATCHES} ({len(df)} rows)\n")

    print(f"{'season':<10}{'rows':>6}{'no npxG':>9}{'no PSC':>8}{'no O/U':>8}")
    for season, grp in df.groupby(Col.SEASON):
        print(
            f"{season:<10}{len(grp):>6}"
            f"{int(grp[Col.NPXG_H].isna().sum()):>9}"
            f"{int(grp[Col.PSCH].isna().sum()):>8}"
            f"{int(grp[Col.B365_O25].isna().sum()):>8}"
        )


if __name__ == "__main__":
    main()
