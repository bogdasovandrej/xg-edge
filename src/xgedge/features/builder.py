"""Leak-free chronological feature builder.

Turns the cleaned matches table into per-match pre-match team ratings
(attack / defence expected-goals rates) with exponential time decay,
red-card down-weighting, venue blending and iterative opponent
adjustment. Strictly causal at the available date resolution: every match on
a date is scored from state frozen at the end of the previous date. Only after
all same-date features have been recorded are those matches appended to the
team histories and league running list. This makes results invariant to source
row order and prevents same-day outcomes leaking into one another.
"""
from __future__ import annotations

import math

import pandas as pd

from xgedge.contracts import Col, Feat

_LN2 = math.log(2.0)
# Venue-specific rating requires this many matches at the venue,
# otherwise the overall rating is used unblended.
_MIN_VENUE_MATCHES = 3

_ODDS_COLS = [
    Col.B365H, Col.B365D, Col.B365A,
    Col.PSH, Col.PSD, Col.PSA,
    Col.B365CH, Col.B365CD, Col.B365CA,
    Col.PSCH, Col.PSCD, Col.PSCA,
    Col.B365_O25, Col.B365_U25, Col.B365C_O25, Col.B365C_U25,
    Col.P_O25, Col.P_U25, Col.PC_O25, Col.PC_U25,
]


def _weight(now: pd.Timestamp, then: pd.Timestamp, half_life_days: float,
            decay: bool) -> float:
    """Exponential half-life decay weight for a past observation."""
    if not decay:
        return 1.0
    age_days = (now - then) / pd.Timedelta(days=1)
    return math.exp(-_LN2 * age_days / half_life_days)


def _team_ratings(hist: list, now: pd.Timestamp, venue: str,
                  half_life_days: float, red_card_weight: float, decay: bool,
                  venue_blend: float) -> tuple:
    """Pre-match ratings from a team's stored history, in one scan.

    Returns (att, def, att_overall, def_overall): the first pair is
    venue-blended (used as the feature), the second is the unblended
    overall rating (used for opponent adjustment). All NaN when the
    history is empty (or total weight degenerates to zero).
    """
    o_att = o_def = o_w = 0.0
    v_att = v_def = v_w = 0.0
    n_venue = 0
    for e in hist:
        w = _weight(now, e["date"], half_life_days, decay)
        if e["red"]:
            w *= red_card_weight
        o_att += w * e["att"]
        o_def += w * e["def"]
        o_w += w
        if e["venue"] == venue:
            v_att += w * e["att"]
            v_def += w * e["def"]
            v_w += w
            n_venue += 1
    if o_w <= 0.0:
        nan = float("nan")
        return nan, nan, nan, nan
    att_overall = o_att / o_w
    def_overall = o_def / o_w
    if n_venue >= _MIN_VENUE_MATCHES and v_w > 0.0:
        att = (1.0 - venue_blend) * att_overall + venue_blend * (v_att / v_w)
        deff = (1.0 - venue_blend) * def_overall + venue_blend * (v_def / v_w)
    else:
        att, deff = att_overall, def_overall
    return att, deff, att_overall, def_overall


def _league_avg(league: list, now: pd.Timestamp, half_life_days: float,
                decay: bool) -> float:
    """Decayed running mean of raw attack values across all teams."""
    num = den = 0.0
    for then, value in league:
        w = _weight(now, then, half_life_days, decay)
        num += w * value
        den += w
    return num / den if den > 0.0 else float("nan")


def _opponent_ratio(opp_rating: float, opp_n: int, league_avg: float,
                    min_history: int, clamp: tuple) -> float:
    """Clamped opponent-strength ratio; 1.0 when it cannot be estimated."""
    if (opp_n < min_history or not math.isfinite(league_avg)
            or league_avg <= 0.0 or not math.isfinite(opp_rating)):
        return 1.0
    return min(max(opp_rating / league_avg, clamp[0]), clamp[1])


def build_features(
    matches: pd.DataFrame,
    half_life_days: float = 180.0,
    red_card_weight: float = 0.5,
    adjust_opponent: bool = False,
    use_npxg: bool = False,
    decay: bool = True,
    min_history: int = 5,
    venue_blend: float = 0.3,
    clamp: tuple = (0.5, 2.0),
) -> pd.DataFrame:
    """Build leak-free pre-match features from cleaned matches.

    One output row per input match, sorted chronologically, containing
    match meta (id, season, date, teams, result), the Feat.* rating
    columns and any odds columns present in the input.

    Rating = weighted mean of per-past-match attack values (npxG if
    ``use_npxg`` else xG; opponent's value for defence). Weight is the
    half-life decay factor (1.0 when ``decay`` is False) times
    ``red_card_weight`` when the past match had any red card. The
    feature rating blends overall and same-venue means via
    ``venue_blend`` once the team has >= 3 matches at that venue.

    With ``adjust_opponent``, values are stored divided by the clamped
    ratio of the opponent's pre-match overall rating (unblended, to stay
    venue-agnostic) to the decayed league average of raw attack values;
    the ratio is 1.0 while the opponent has fewer than ``min_history``
    matches or the league average is unavailable.
    """
    df = matches.sort_values(Col.DATE, kind="mergesort").reset_index(drop=True)
    att_h_col, att_a_col = (Col.NPXG_H, Col.NPXG_A) if use_npxg else (Col.XG_H, Col.XG_A)
    odds_present = [c for c in _ODDS_COLS if c in df.columns]

    hist: dict = {}      # team -> list of {date, venue, red, att, def}
    league: list = []    # (date, raw attack value), two entries per match
    rows: list = []

    for date, same_date in df.groupby(Col.DATE, sort=False):
        # Freeze every piece of state for the whole date. football-data and
        # the cleaned contract contain a date but not a dependable kickoff
        # timestamp, so using an earlier row from the same date would impose a
        # fictional result order.
        league_avg = _league_avg(league, date, half_life_days, decay)
        pending_updates: list[tuple] = []

        for _, m in same_date.iterrows():
            home, away = m[Col.HOME], m[Col.AWAY]
            h_hist = hist.setdefault(home, [])
            a_hist = hist.setdefault(away, [])
            n_h, n_a = len(h_hist), len(a_hist)

            h_att, h_def, h_att_o, h_def_o = _team_ratings(
                h_hist, date, "H", half_life_days, red_card_weight, decay,
                venue_blend,
            )
            a_att, a_def, a_att_o, a_def_o = _team_ratings(
                a_hist, date, "A", half_life_days, red_card_weight, decay,
                venue_blend,
            )

            row = {
                Col.MATCH_ID: m[Col.MATCH_ID],
                Col.SEASON: m[Col.SEASON],
                Col.DATE: date,
                Col.HOME: home,
                Col.AWAY: away,
                Col.FTHG: m[Col.FTHG],
                Col.FTAG: m[Col.FTAG],
                Col.FTR: m[Col.FTR],
                Feat.ATT_H: h_att,
                Feat.DEF_H: h_def,
                Feat.ATT_A: a_att,
                Feat.DEF_A: a_def,
                Feat.N_HIST_H: n_h,
                Feat.N_HIST_A: n_a,
                Feat.IS_VALID: (n_h >= min_history) and (n_a >= min_history),
            }
            for c in odds_present:
                row[c] = m[c]
            rows.append(row)

            raw_att_h = float(m[att_h_col])
            raw_att_a = float(m[att_a_col])

            # A match with missing xG metrics carries no form signal;
            # appending NaN would poison all later ratings.
            if not (math.isfinite(raw_att_h) and math.isfinite(raw_att_a)):
                continue

            if adjust_opponent:
                r_a_att = _opponent_ratio(
                    a_att_o, n_a, league_avg, min_history, clamp
                )
                r_a_def = _opponent_ratio(
                    a_def_o, n_a, league_avg, min_history, clamp
                )
                r_h_att = _opponent_ratio(
                    h_att_o, n_h, league_avg, min_history, clamp
                )
                r_h_def = _opponent_ratio(
                    h_def_o, n_h, league_avg, min_history, clamp
                )
            else:
                r_a_att = r_a_def = r_h_att = r_h_def = 1.0

            red = bool(m[Col.RED_H] + m[Col.RED_A] > 0)
            pending_updates.append(
                (
                    h_hist,
                    a_hist,
                    {"date": date, "venue": "H", "red": red,
                     "att": raw_att_h / r_a_def,
                     "def": raw_att_a / r_a_att},
                    {"date": date, "venue": "A", "red": red,
                     "att": raw_att_a / r_h_def,
                     "def": raw_att_h / r_h_att},
                    raw_att_h,
                    raw_att_a,
                )
            )

        # Commit the date as one atomic observation batch.
        for h_hist, a_hist, h_entry, a_entry, raw_att_h, raw_att_a in pending_updates:
            h_hist.append(h_entry)
            a_hist.append(a_entry)
            league.append((date, raw_att_h))
            league.append((date, raw_att_a))

    cols = [
        Col.MATCH_ID, Col.SEASON, Col.DATE, Col.HOME, Col.AWAY,
        Col.FTHG, Col.FTAG, Col.FTR,
        Feat.ATT_H, Feat.DEF_H, Feat.ATT_A, Feat.DEF_A,
        Feat.N_HIST_H, Feat.N_HIST_A, Feat.IS_VALID,
    ] + odds_present
    out = pd.DataFrame(rows, columns=cols)
    out[Feat.N_HIST_H] = out[Feat.N_HIST_H].astype(int)
    out[Feat.N_HIST_A] = out[Feat.N_HIST_A].astype(int)
    out[Feat.IS_VALID] = out[Feat.IS_VALID].astype(bool)
    return out
