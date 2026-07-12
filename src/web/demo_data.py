"""Deterministic simulated matches/predictions/Tipico odds.

Used by src/web/app.py whenever the real pipeline (Postgres + trained model + live
Tipico/Odds-API data) isn't configured, so the JARVIS UI is fully explorable out of
the box. Every response built from this module is tagged "demo": true so the frontend
can clearly label it as SIMULATED DATA rather than a live prediction.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math

TOURNAMENTS = [
    ("Wimbledon", "grass", "R16"),
    ("ATP Masters Toronto", "hard", "QF"),
    ("WTA Prague Open", "clay", "R32"),
    ("ATP 500 Hamburg", "clay", "SF"),
    ("WTA Washington Open", "hard", "R16"),
    ("ATP Newport", "grass", "QF"),
]

# Fictional-enough pairings for demo purposes; ranks are illustrative, not live data.
PLAYERS = [
    ("J. Kovalenko", 3),
    ("M. Feretti", 7),
    ("A. Dubois", 12),
    ("L. Nakamura", 5),
    ("T. Brandt", 21),
    ("R. Silva", 9),
    ("E. Voss", 15),
    ("K. Amara", 4),
    ("S. Whitmore", 18),
    ("D. Okafor", 6),
    ("P. Lindqvist", 27),
    ("N. Castellano", 11),
]

TIPICO_MARGIN = 1.06  # ~6% overround, typical retail bookmaker vig


def _seeded_fraction(*parts: str) -> float:
    """Deterministic pseudo-random float in [0, 1) derived from the given strings."""
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _win_prob(rank_a: int, rank_b: int, seed: str) -> float:
    """Toy win-probability model: rank gap -> logistic, with a small seeded wobble."""
    gap = rank_b - rank_a
    base = 1 / (1 + math.exp(-gap / 6))
    wobble = (_seeded_fraction(seed) - 0.5) * 0.1
    return min(0.93, max(0.07, base + wobble))


def _tipico_odds(prob: float) -> float:
    """Fair decimal odds for `prob`, inflated by the bookmaker's margin."""
    fair = 1 / max(prob, 0.01)
    return round(fair * TIPICO_MARGIN, 2)


def generate_matches(count: int = 6) -> list[dict]:
    now = dt.datetime.utcnow()
    matches = []
    for i in range(count):
        tournament, surface, round_ = TOURNAMENTS[i % len(TOURNAMENTS)]
        p1_name, p1_rank = PLAYERS[(i * 2) % len(PLAYERS)]
        p2_name, p2_rank = PLAYERS[(i * 2 + 1) % len(PLAYERS)]
        match_id = 1000 + i
        seed = f"{tournament}-{p1_name}-{p2_name}-{i}"

        prob_p1 = _win_prob(p1_rank, p2_rank, seed)
        tipico_p1 = _tipico_odds(prob_p1)
        tipico_p2 = _tipico_odds(1 - prob_p1)

        ev_p1 = round(prob_p1 * tipico_p1 - 1, 3)
        ev_p2 = round((1 - prob_p1) * tipico_p2 - 1, 3)
        favored_p1 = prob_p1 >= 0.5
        edge = max(ev_p1, ev_p2)

        matches.append(
            {
                "match_id": match_id,
                "tournament": tournament,
                "round": round_,
                "surface": surface,
                "start_time": (now + dt.timedelta(hours=3 + i * 5)).isoformat() + "Z",
                "player1": {"name": p1_name, "rank": p1_rank},
                "player2": {"name": p2_name, "rank": p2_rank},
                "player1_win_prob": round(prob_p1, 3),
                "player2_win_prob": round(1 - prob_p1, 3),
                "bookmaker": "Tipico",
                "tipico_player1_odds": tipico_p1,
                "tipico_player2_odds": tipico_p2,
                "expected_value": edge,
                "pick": p1_name if favored_p1 else p2_name,
                "is_value_bet": edge >= 0.05,
                "demo": True,
            }
        )
    return matches


def best_value_bets(matches: list[dict], min_edge: float = 0.05, limit: int = 5) -> list[dict]:
    """Also used against live matches (see app.py's _offline_reply), some of which may
    not have a model prediction yet (has_prediction: False) -- skip those rather than
    KeyError on the missing expected_value."""
    picks = [m for m in matches if m.get("has_prediction", True) and m.get("expected_value", 0) >= min_edge]
    picks.sort(key=lambda m: m["expected_value"], reverse=True)
    return picks[:limit]
