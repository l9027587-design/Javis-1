"""Deterministic simulated football matches/predictions/Tipico odds.

Used by src/web/app.py whenever the real pipeline (Postgres + trained model + live
Tipico/Odds-API data) isn't configured, so the JARVIS UI is fully explorable out of
the box. Every response built from this module is tagged "demo": true so the frontend
can clearly label it as SIMULATED DATA rather than a live prediction. Team/league names
here are fictional on purpose -- this is a UI demo, not a claim about real clubs.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math

LEAGUES = [
    ("Premier League", "Matchday 14"),
    ("La Liga", "Matchday 12"),
    ("Bundesliga", "Matchday 11"),
    ("Serie A", "Matchday 13"),
    ("Ligue 1", "Matchday 10"),
    ("UEFA Champions League", "Group Stage MD4"),
]

# Fictional-enough club names for demo purposes; table positions are illustrative,
# not live data.
TEAMS = [
    ("Northbridge FC", 3),
    ("Vale Rovers", 14),
    ("Sterling City", 7),
    ("Ashwood United", 2),
    ("Meridian AC", 9),
    ("Castleport", 16),
    ("Ironmoor FC", 5),
    ("Redgate Athletic", 11),
    ("Solworth Town", 4),
    ("Blackfen United", 18),
    ("Harrow Vale", 8),
    ("Kingsmere FC", 6),
]

TIPICO_MARGIN = 1.06  # ~6% overround, typical retail bookmaker vig


def _seeded_fraction(*parts: str) -> float:
    """Deterministic pseudo-random float in [0, 1) derived from the given strings."""
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _outcome_probs(pos_home: int, pos_away: int, seed: str) -> tuple[float, float, float]:
    """Toy 1X2 model: table-position gap -> logistic home/away split, draw held in a
    plausible band on top, with a small seeded wobble on each."""
    gap = pos_away - pos_home  # positive => home team ranked higher (lower number = better)
    home_share = 1 / (1 + math.exp(-gap / 8))
    home_share = min(0.92, max(0.08, home_share + (_seeded_fraction(seed) - 0.5) * 0.1))

    draw_prob = 0.24 + (_seeded_fraction(seed, "draw") - 0.5) * 0.06
    draw_prob = min(0.32, max(0.16, draw_prob))

    remaining = 1 - draw_prob
    return remaining * home_share, draw_prob, remaining * (1 - home_share)


def _tipico_odds(prob: float) -> float:
    """Fair decimal odds for `prob`, inflated by the bookmaker's margin."""
    fair = 1 / max(prob, 0.01)
    return round(fair * TIPICO_MARGIN, 2)


def _totals_odds(seed: str) -> tuple[float, float, float]:
    """Deterministic Über/Unter 2.5-goals line with plausible odds."""
    over_prob = min(0.75, max(0.35, 0.5 + (_seeded_fraction(seed, "totals") - 0.5) * 0.3))
    return 2.5, _tipico_odds(over_prob), _tipico_odds(1 - over_prob)


def _btts_odds(seed: str) -> tuple[float, float]:
    """Deterministic BTTS (both teams to score) yes/no odds."""
    yes_prob = min(0.75, max(0.35, 0.5 + (_seeded_fraction(seed, "btts") - 0.5) * 0.3))
    return _tipico_odds(yes_prob), _tipico_odds(1 - yes_prob)


def generate_matches(count: int = 6) -> list[dict]:
    now = dt.datetime.utcnow()
    matches = []
    for i in range(count):
        league, round_ = LEAGUES[i % len(LEAGUES)]
        home_name, home_pos = TEAMS[(i * 2) % len(TEAMS)]
        away_name, away_pos = TEAMS[(i * 2 + 1) % len(TEAMS)]
        match_id = 1000 + i
        seed = f"{league}-{home_name}-{away_name}-{i}"

        home_prob, draw_prob, away_prob = _outcome_probs(home_pos, away_pos, seed)
        home_odds = _tipico_odds(home_prob)
        draw_odds = _tipico_odds(draw_prob)
        away_odds = _tipico_odds(away_prob)

        candidates = [
            ("home", round(home_prob * home_odds - 1, 3)),
            ("draw", round(draw_prob * draw_odds - 1, 3)),
            ("away", round(away_prob * away_odds - 1, 3)),
        ]
        value_pick_key, edge = max(candidates, key=lambda c: c[1])
        pick_name = {"home": home_name, "draw": "Unentschieden", "away": away_name}[value_pick_key]
        total_line, over_odds, under_odds = _totals_odds(seed)
        btts_yes_odds, btts_no_odds = _btts_odds(seed)

        matches.append(
            {
                "match_id": match_id,
                "league": league,
                "round": round_,
                "start_time": (now + dt.timedelta(hours=3 + i * 7)).isoformat() + "Z",
                "home_team": {"name": home_name, "position": home_pos},
                "away_team": {"name": away_name, "position": away_pos},
                "home_win_prob": round(home_prob, 3),
                "draw_prob": round(draw_prob, 3),
                "away_win_prob": round(away_prob, 3),
                "bookmaker": "Tipico",
                "home_odds": home_odds,
                "draw_odds": draw_odds,
                "away_odds": away_odds,
                "totals": {"line": total_line, "over_odds": over_odds, "under_odds": under_odds},
                "btts": {"yes_odds": btts_yes_odds, "no_odds": btts_no_odds},
                "expected_value": edge,
                # "pick" mirrors the live-data shape from tools.get_best_value_bets()
                # (used by the ticker); "value_pick" is this module's own field name,
                # used by the match-list/detail views -- kept both to satisfy each caller
                # without reshaping data at the call site. "value_pick_side" is the raw
                # home/draw/away key, used by combo_suggestions() to look up the right
                # odds/probability without re-deriving it from the display name.
                "pick": pick_name,
                "value_pick": pick_name,
                "value_pick_side": value_pick_key,
                "is_value_bet": edge >= 0.05,
                "has_prediction": True,
                "demo": True,
            }
        )
    return matches


def best_value_bets(matches: list[dict], min_edge: float = 0.05, limit: int = 5) -> list[dict]:
    """Also used against live matches (see app.py's _offline_reply), some of which may
    not have a model prediction yet, or a prediction but no odds yet (expected_value is
    present but None in both demo-shaped and live-shaped dicts) -- skip those rather
    than crashing on `None >= min_edge`. Note dict.get(key, default)'s default only
    applies when the key is *missing*, not when it's present with value None, which is
    exactly the case here -- both conditions need an explicit check.
    """
    picks = [
        m
        for m in matches
        if m.get("has_prediction", True) and m.get("expected_value") is not None and m["expected_value"] >= min_edge
    ]
    picks.sort(key=lambda m: m["expected_value"], reverse=True)
    return picks[:limit]


def combo_suggestions(matches: list[dict], min_edge: float = 0.0, max_legs: int = 3) -> list[dict]:
    """Demo-mode mirror of tools.get_combo_suggestions() -- same combine-the-best-picks
    logic, built from generate_matches()'s simulated data instead of Postgres."""
    picks = best_value_bets(matches, min_edge=min_edge, limit=max_legs)
    if len(picks) < 2:
        return []

    side_odds = {"home": "home_odds", "draw": "draw_odds", "away": "away_odds"}
    side_prob = {"home": "home_win_prob", "draw": "draw_prob", "away": "away_win_prob"}

    combos = []
    for n in range(2, len(picks) + 1):
        legs = picks[:n]
        combined_odds = 1.0
        combined_prob = 1.0
        leg_entries = []
        for leg in legs:
            side = leg["value_pick_side"]
            odds = leg[side_odds[side]]
            prob = leg[side_prob[side]]
            combined_odds *= odds
            combined_prob *= prob
            leg_entries.append({"match_id": leg["match_id"], "league": leg["league"], "pick": leg["pick"], "odds": odds})
        combos.append(
            {
                "legs": leg_entries,
                "combined_odds": round(combined_odds, 2),
                "combined_prob": round(combined_prob, 3),
                "combined_ev": round(combined_prob * combined_odds - 1, 3),
            }
        )
    return combos


def combo_history(count: int = 5) -> list[dict]:
    """Deterministic simulated past combo bets (a mix of won/lost) so the "vergangene
    Kombis" panel isn't empty while SIMULATION MODE is active."""
    now = dt.datetime.utcnow()
    history = []
    for i in range(count):
        seed = f"combo-history-{i}"
        n_legs = 2 + i % 2
        legs = []
        combined_odds = 1.0
        for j in range(n_legs):
            team_name, _ = TEAMS[(i * 3 + j * 2) % len(TEAMS)]
            odds = round(1.5 + _seeded_fraction(seed, str(j)) * 2, 2)
            combined_odds *= odds
            hit = _seeded_fraction(seed, str(j), "hit") > 0.4
            legs.append({"pick": team_name, "odds": odds, "status": "won" if hit else "lost"})
        status = "won" if all(leg["status"] == "won" for leg in legs) else "lost"
        history.append(
            {
                "id": 9000 + i,
                "created_at": (now - dt.timedelta(days=i + 1)).isoformat() + "Z",
                "status": status,
                "combined_odds": round(combined_odds, 2),
                "combined_prob": round(1 / combined_odds, 3),
                "combined_ev": round((_seeded_fraction(seed, "ev") - 0.15) * 0.3, 3),
                "legs": legs,
            }
        )
    return history
