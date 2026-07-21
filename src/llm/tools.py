"""Read-only query functions exposed to the LLM as tools.

Each function returns plain JSON-serializable data pulled straight from Postgres —
the LLM only ever reasons over these, never over raw scraped text, so its answers stay
grounded in the pipeline's actual numbers.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from src.db.models import Match, Prediction
from src.db.session import get_session


def get_upcoming_matches(days_ahead: int = 7) -> list[dict]:
    """List scheduled football matches in the next `days_ahead` days."""
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)
    with get_session() as session:
        matches = session.scalars(
            select(Match).where(Match.status == "scheduled", Match.start_time.between(now, horizon))
        ).all()
        return [
            {
                "match_id": m.id,
                "league": m.league_name,
                "round": m.round,
                "start_time": m.start_time.isoformat(),
                "home_team": m.home_team.name,
                "away_team": m.away_team.name,
            }
            for m in matches
        ]


def get_match_prediction(match_id: int) -> dict | None:
    """Latest 1X2 model prediction + odds/EV for a specific match."""
    with get_session() as session:
        match = session.get(Match, match_id)
        if match is None:
            return None
        pred = session.scalar(
            select(Prediction).where(Prediction.match_id == match_id).order_by(Prediction.created_at.desc()).limit(1)
        )
        if pred is None:
            return {
                "match_id": match_id,
                "home_team": match.home_team.name,
                "away_team": match.away_team.name,
                "prediction": None,
            }
        return {
            "match_id": match_id,
            "league": match.league_name,
            "home_team": match.home_team.name,
            "away_team": match.away_team.name,
            "home_win_prob": round(pred.home_win_prob, 3),
            "draw_prob": round(pred.draw_prob, 3),
            "away_win_prob": round(pred.away_win_prob, 3),
            "best_home_odds": pred.best_home_odds,
            "best_draw_odds": pred.best_draw_odds,
            "best_away_odds": pred.best_away_odds,
            "expected_value": round(pred.expected_value, 3) if pred.expected_value is not None else None,
            "value_pick": pred.value_pick,
            "is_value_bet": pred.is_value_bet,
        }


def get_matches_with_predictions(days_ahead: int = 7) -> list[dict]:
    """Upcoming matches merged with each one's latest prediction, in a single DB session.

    Used by the web app's /api/matches, which needs all of them at once -- calling
    get_match_prediction() per match there would open one Postgres connection per
    match, which is slow/flaky enough against Neon's serverless connection model to
    intermittently truncate the response mid-request. One session for the whole batch
    avoids that.
    """
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)
    with get_session() as session:
        matches = session.scalars(
            select(Match).where(Match.status == "scheduled", Match.start_time.between(now, horizon))
        ).all()
        if not matches:
            return []
        match_ids = [m.id for m in matches]
        preds = session.scalars(
            select(Prediction)
            .where(Prediction.match_id.in_(match_ids))
            .order_by(Prediction.match_id, Prediction.created_at.desc())
        ).all()
        latest_pred_by_match: dict[int, Prediction] = {}
        for pred in preds:
            latest_pred_by_match.setdefault(pred.match_id, pred)

        results = []
        for m in matches:
            entry = {
                "match_id": m.id,
                "league": m.league_name,
                "round": m.round,
                "start_time": m.start_time.isoformat(),
                "home_team": {"name": m.home_team.name, "position": m.home_team.league_position},
                "away_team": {"name": m.away_team.name, "position": m.away_team.league_position},
                "demo": False,
                "has_prediction": False,
            }
            pred = latest_pred_by_match.get(m.id)
            if pred is not None:
                pick_name = {"home": m.home_team.name, "draw": "Unentschieden", "away": m.away_team.name}.get(
                    pred.value_pick
                )
                entry.update(
                    {
                        "has_prediction": True,
                        "home_win_prob": round(pred.home_win_prob, 3),
                        "draw_prob": round(pred.draw_prob, 3),
                        "away_win_prob": round(pred.away_win_prob, 3),
                        "home_odds": pred.best_home_odds,
                        "draw_odds": pred.best_draw_odds,
                        "away_odds": pred.best_away_odds,
                        "expected_value": round(pred.expected_value, 3) if pred.expected_value is not None else None,
                        "value_pick": pick_name,
                        "is_value_bet": pred.is_value_bet,
                    }
                )
            results.append(entry)
        return results


def get_best_value_bets(days_ahead: int = 3, min_edge: float = 0.05, limit: int = 10) -> list[dict]:
    """Upcoming matches where the model's edge over the market (EV) is >= min_edge, sorted best-first."""
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)
    with get_session() as session:
        rows = session.execute(
            select(Prediction, Match)
            .join(Match, Prediction.match_id == Match.id)
            .where(
                Match.start_time.between(now, horizon),
                Prediction.expected_value.is_not(None),
                Prediction.expected_value >= min_edge,
            )
            .order_by(Prediction.expected_value.desc())
            .limit(limit)
        ).all()
        results = []
        for pred, match in rows:
            pick_map = {"home": match.home_team.name, "draw": "Unentschieden", "away": match.away_team.name}
            prob_map = {"home": pred.home_win_prob, "draw": pred.draw_prob, "away": pred.away_win_prob}
            odds_map = {"home": pred.best_home_odds, "draw": pred.best_draw_odds, "away": pred.best_away_odds}
            results.append(
                {
                    "match_id": match.id,
                    "league": match.league_name,
                    "start_time": match.start_time.isoformat(),
                    "pick": pick_map.get(pred.value_pick),
                    "model_win_prob": round(prob_map.get(pred.value_pick, 0.0), 3),
                    "best_odds": odds_map.get(pred.value_pick),
                    "expected_value": round(pred.expected_value, 3),
                }
            )
        return results


# OpenAI function-calling tool schemas, paired with the callables above.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_matches",
            "description": "List upcoming scheduled football matches.",
            "parameters": {
                "type": "object",
                "properties": {"days_ahead": {"type": "integer", "description": "How many days ahead to look, default 7"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_match_prediction",
            "description": "Get the model's 1X2 (home/draw/away) win-probability prediction and odds/EV for one match by ID.",
            "parameters": {
                "type": "object",
                "properties": {"match_id": {"type": "integer"}},
                "required": ["match_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_best_value_bets",
            "description": "Get upcoming matches where the model's win probability implies positive expected value against the best available odds, sorted by edge.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "description": "default 3"},
                    "min_edge": {"type": "number", "description": "minimum expected value to include, default 0.05"},
                    "limit": {"type": "integer", "description": "max results, default 10"},
                },
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "get_upcoming_matches": get_upcoming_matches,
    "get_match_prediction": get_match_prediction,
    "get_best_value_bets": get_best_value_bets,
}
