"""Compare Monte Carlo market estimates with the analytical score matrix."""
from __future__ import annotations

import argparse

from xgedge.markets.markets import prob_btts, prob_over, probs_1x2
from xgedge.models.dixon_coles import score_matrix
from xgedge.models.monte_carlo import monte_carlo_markets


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lambda-home", type=float, required=True)
    parser.add_argument("--lambda-away", type=float, required=True)
    parser.add_argument("--rho", type=float, default=0.0)
    parser.add_argument("--simulations", type=int, default=100_000)
    parser.add_argument("--max-goals", type=int, default=10)
    parser.add_argument("--total-line", type=float, default=2.5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    matrix = score_matrix(
        args.lambda_home,
        args.lambda_away,
        rho=args.rho,
        max_goals=args.max_goals,
    )
    p_home, p_draw, p_away = probs_1x2(matrix)
    exact = {
        "home": p_home,
        "draw": p_draw,
        "away": p_away,
        "over": prob_over(matrix, args.total_line),
        "btts": prob_btts(matrix),
    }
    estimated = monte_carlo_markets(
        args.lambda_home,
        args.lambda_away,
        rho=args.rho,
        n_simulations=args.simulations,
        max_goals=args.max_goals,
        total_line=args.total_line,
        seed=args.seed,
    )

    print(f"simulations: {estimated['n_simulations']} | seed: {args.seed}")
    print(f"{'market':<8}{'analytic':>12}{'monte_carlo':>15}{'mc_se':>12}")
    for market, analytic in exact.items():
        mc = estimated[f"p_{market}"]
        se = estimated[f"se_{market}"]
        print(f"{market:<8}{analytic:>12.6f}{mc:>15.6f}{se:>12.6f}")


if __name__ == "__main__":
    main()
