"""Data contracts: canonical column names, seasons, and data-layer paths.

Every module communicates through these constants. No module may invent its
own string literal for a column that already has a name here — this file is
the single source of truth for the shape of data flowing through the
pipeline (raw -> cleaned -> features -> model -> markets -> decision ->
evaluation).
"""
from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------
# Data layers (repo-root anchored; the package is installed editable)
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CLEANED_DIR = DATA_DIR / "cleaned"
FEATURES_DIR = DATA_DIR / "features"
REPORTS_DIR = ROOT / "reports"

CLEANED_MATCHES = CLEANED_DIR / "matches.parquet"

# --------------------------------------------------------------------------
# Scope: league and seasons
# --------------------------------------------------------------------------
LEAGUE = "EPL"
SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]

# football-data.co.uk URL season codes, e.g. mmz4281/2122/E0.csv
FD_SEASON_CODES = {
    "2021-22": "2122",
    "2022-23": "2223",
    "2023-24": "2324",
    "2024-25": "2425",
    "2025-26": "2526",
}
FD_LEAGUE_CODE = "E0"

# understat.com season start years, e.g. understat.com/league/EPL/2021
UNDERSTAT_YEARS = {
    "2021-22": 2021,
    "2022-23": 2022,
    "2023-24": 2023,
    "2024-25": 2024,
    "2025-26": 2025,
}


class Col:
    """Canonical columns of the cleaned matches table (one row per match)."""

    MATCH_ID = "match_id"
    SEASON = "season"
    DATE = "date"          # pandas datetime64[ns], kickoff date
    HOME = "home"          # canonical team id (see data/teams.py)
    AWAY = "away"

    # full-time result
    FTHG = "fthg"          # home goals, int
    FTAG = "ftag"          # away goals, int
    FTR = "ftr"            # 'H' / 'D' / 'A'

    # understat per-match metrics
    XG_H = "xg_h"
    XG_A = "xg_a"
    NPXG_H = "npxg_h"      # non-penalty xG
    NPXG_A = "npxg_a"
    PPDA_H = "ppda_h"      # passes per defensive action (lower = more press)
    PPDA_A = "ppda_a"
    DEEP_H = "deep_h"      # deep completions
    DEEP_A = "deep_a"

    # football-data.co.uk discipline
    RED_H = "red_h"        # red cards, int
    RED_A = "red_a"

    # football-data.co.uk odds: pre-closing (collected days before kickoff)
    B365H = "b365h"
    B365D = "b365d"
    B365A = "b365a"
    PSH = "psh"            # Pinnacle
    PSD = "psd"
    PSA = "psa"

    # closing odds (the benchmark for CLV)
    B365CH = "b365ch"
    B365CD = "b365cd"
    B365CA = "b365ca"
    PSCH = "psch"          # Pinnacle closing — sharpest available benchmark
    PSCD = "pscd"
    PSCA = "psca"

    # totals odds (over/under 2.5), pre-closing and closing
    B365_O25 = "b365_o25"
    B365_U25 = "b365_u25"
    B365C_O25 = "b365c_o25"
    B365C_U25 = "b365c_u25"
    P_O25 = "p_o25"          # Pinnacle pre-closing total 2.5
    P_U25 = "p_u25"
    PC_O25 = "pc_o25"        # Pinnacle closing total 2.5
    PC_U25 = "pc_u25"


class Feat:
    """Canonical columns of the features table (one row per match).

    Rates are decayed weighted means of per-match npxG (or xG, depending on
    builder params) strictly over matches finished BEFORE the row's date.
    """

    ATT_H = "f_att_h"      # home team attacking rate
    DEF_H = "f_def_h"      # home team conceding rate
    ATT_A = "f_att_a"
    DEF_A = "f_def_a"

    N_HIST_H = "n_hist_h"  # matches of history available for home team
    N_HIST_A = "n_hist_a"
    IS_VALID = "is_valid"  # both teams have >= min_history matches


OUTCOMES = ["H", "D", "A"]
