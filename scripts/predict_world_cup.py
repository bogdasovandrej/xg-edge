"""Predict World Cup 2026 fixtures from official FIFA data (90 minutes only)."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from xgedge.international.fifa import load_fifa_fixtures, load_fifa_rankings
from xgedge.international.model import WorldCupModel


def _write_csv(path: Path, predictions: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "fixture_id", "stage", "kickoff_utc", "home", "away", "label",
        "lambda_home", "lambda_away", "rho", "p_home", "p_draw", "p_away",
        "p_over_2_5", "p_btts", "top_scores", "training_matches",
        "ranking_publication_utc", "generated_as_of_utc",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in predictions:
            writer.writerow(
                {
                    "fixture_id": row["fixture_id"],
                    "stage": row["stage"],
                    "kickoff_utc": row["kickoff_utc"],
                    "home": row["home"],
                    "away": row["away"],
                    "label": row["label"],
                    "lambda_home": row["lambda_home"],
                    "lambda_away": row["lambda_away"],
                    "rho": row["rho"],
                    "p_home": row["probabilities"]["home"],
                    "p_draw": row["probabilities"]["draw"],
                    "p_away": row["probabilities"]["away"],
                    "p_over_2_5": row["probabilities"]["over_2_5"],
                    "p_btts": row["probabilities"]["btts_yes"],
                    "top_scores": json.dumps(row["top_scores"], ensure_ascii=False),
                    "training_matches": row["data_provenance"]["training_matches"],
                    "ranking_publication_utc": row["data_provenance"]["ranking_publication_utc"],
                    "generated_as_of_utc": row["generated_as_of_utc"],
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures-json", type=Path, help="offline normalized or raw FIFA calendar JSON")
    parser.add_argument("--rankings-json", type=Path, help="offline normalized or raw FIFA rankings JSON")
    parser.add_argument(
        "--stage", default="auto",
        help="stage name, or 'auto' for every future fixture with known teams",
    )
    parser.add_argument("--as-of", help="timezone-aware ISO cutoff (default: current UTC)")
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output-json", type=Path, default=ROOT / "reports" / "world_cup_2026_semifinals.json")
    parser.add_argument("--output-csv", type=Path, default=ROOT / "reports" / "world_cup_2026_semifinals.csv")
    parser.add_argument("--save-fixtures-json", type=Path, help="save normalized FIFA inputs for offline replay")
    parser.add_argument("--save-rankings-json", type=Path, help="save normalized FIFA inputs for offline replay")
    args = parser.parse_args()

    as_of = args.as_of or datetime.now(timezone.utc).isoformat()
    rankings = load_fifa_rankings(args.rankings_json)
    fixtures = load_fifa_fixtures(args.fixtures_json)
    if args.save_rankings_json:
        args.save_rankings_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_rankings_json.write_text(json.dumps(rankings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.save_fixtures_json:
        args.save_fixtures_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_fixtures_json.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    model = WorldCupModel(
        rankings,
        fixtures["matches"],
        uncertainty_draws=args.draws,
        random_seed=args.seed,
    )
    predictions = (
        model.predict_upcoming(as_of=as_of)
        if args.stage.casefold() == "auto"
        else model.predict_stage(stage=args.stage, as_of=as_of)
    )
    document = {
        "label": "experimental",
        "warning": "90-minute probability estimates only; no betting recommendation.",
        "predictions": predictions,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(args.output_csv, predictions)
    if not predictions:
        print(f"no future {args.stage!r} fixtures at cutoff {as_of}; wrote an empty snapshot")
    print(json.dumps(document, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
