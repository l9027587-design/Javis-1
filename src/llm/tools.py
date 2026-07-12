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


def get_upcoming_matches(days_ahead: int = 3) -> list[dict]:
    """List scheduled matches in the next `days_ahead` days."""
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)
    with get_session() as session:
        matches = session.scalars(
            select(Match).where(Match.status == "scheduled", Match.start_time.between(now, horizon))
        ).all()
        return [
            {
                "match_id": m.id,
                "tournament": m.tournament_name,
                "round": m.round,
                "surface": m.surface,
                "start_time": m.start_time.isoformat(),
                "player1": m.player1.name,
                "player2": m.player2.name,
            }
            for m in matches
        ]


def get_match_prediction(match_id: int) -> dict | None:
    """Latest model prediction + odds/EV for a specific match."""
    with get_session() as session:
        match = session.get(Match, match_id)
        if match is None:
            return None
        pred = session.scalar(
            select(Prediction).where(Prediction.match_id == match_id).order_by(Prediction.created_at.desc()).limit(1)
        )
        if pred is None:
            return {"match_id": match_id, "player1": match.player1.name, "player2": match.player2.name, "prediction": None}
        return {
            "match_id": match_id,
            "tournament": match.tournament_name,
            "player1": match.player1.name,
            "player2": match.player2.name,
            "player1_win_prob": round(pred.player1_win_prob, 3),
            "player2_win_prob": round(1 - pred.player1_win_prob, 3),
            "best_player1_odds": pred.best_player1_odds,
            "best_player2_odds": pred.best_player2_odds,
            "expected_value": round(pred.expected_value, 3) if pred.expected_value is not None else None,
            "is_value_bet": pred.is_value_bet,
        }


def get_matches_with_predictions(days_ahead: int = 3) -> list[dict]:
    """Upcoming matches merged with each one's latest prediction, in a single DB session.

    Used by the web app's /api/matches, which needs all of them at once -- calling
    get_match_prediction() per match there opened one Postgres connection per match
    (e.g. 24 for a full slate), which was slow/flaky enough against Neon's serverless
    connection model to intermittently truncate the response mid-request.
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

        # Field names/shape mirror demo_data.generate_matches() exactly (player1/player2 as
        # {name, rank} objects, tipico_player1_odds/tipico_player2_odds, pick, is_value_bet)
        # since the frontend was built against that shape -- keeping both sources identical
        # means static/js/app.js doesn't need to know which one it's looking at.
        results = []
        for m in matches:
            entry = {
                "match_id": m.id,
                "tournament": m.tournament_name,
                "round": m.round,
                "surface": m.surface,
                "start_time": m.start_time.isoformat(),
                "player1": {"name": m.player1.name, "rank": m.player1.current_rank},
                "player2": {"name": m.player2.name, "rank": m.player2.current_rank},
                "demo": False,
                "has_prediction": False,
            }
            pred = latest_pred_by_match.get(m.id)
            if pred is not None:
                favored_p1 = pred.player1_win_prob >= 0.5
                entry.update(
                    {
                        "has_prediction": True,
                        "player1_win_prob": round(pred.player1_win_prob, 3),
                        "player2_win_prob": round(1 - pred.player1_win_prob, 3),
                        "tipico_player1_odds": pred.best_player1_odds,
                        "tipico_player2_odds": pred.best_player2_odds,
                        "expected_value": round(pred.expected_value, 3) if pred.expected_value is not None else None,
                        "pick": m.player1.name if favored_p1 else m.player2.name,
                        "is_value_bet": pred.is_value_bet,
                    }
                )
            results.append(entry)
        return results


def get_best_value_bets(days_ahead: int = 2, min_edge: float = 0.05, limit: int = 10) -> list[dict]:
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
            favored_p1 = pred.player1_win_prob >= 0.5
            results.append(
                {
                    "match_id": match.id,
                    "tournament": match.tournament_name,
                    "start_time": match.start_time.isoformat(),
                    "pick": match.player1.name if favored_p1 else match.player2.name,
                    "model_win_prob": round(pred.player1_win_prob if favored_p1 else 1 - pred.player1_win_prob, 3),
                    "best_odds": pred.best_player1_odds if favored_p1 else pred.best_player2_odds,
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
            "description": "List upcoming scheduled tennis matches.",
            "parameters": {
                "type": "object",
                "properties": {"days_ahead": {"type": "integer", "description": "How many days ahead to look, default 3"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_match_prediction",
            "description": "Get the model's win-probability prediction and odds/EV for one match by ID.",
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
                    "days_ahead": {"type": "integer", "description": "default 2"},
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
