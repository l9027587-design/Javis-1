"""Score upcoming matches and combine model probability with odds into an EV/value-bet call.

expected_value = model_probability * best_decimal_odds - 1
A positive EV means the model thinks the market is underpricing that player.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import xgboost as xgb
from sqlalchemy import select

from src.db.models import Match, Odds, Prediction
from src.db.session import get_session
from src.ml.features import build_features
from src.ml.train import MODEL_DIR, MODEL_PATH, MODEL_VERSION

logger = logging.getLogger(__name__)

VALUE_BET_EV_THRESHOLD = 0.05  # flag bets with >5% edge over the market-implied probability


def load_model() -> tuple[xgb.XGBClassifier, list[str]]:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"No trained model at {MODEL_PATH}; run `python -m src.ml.train` first.")
    model = xgb.XGBClassifier()
    model.load_model(str(MODEL_PATH))
    feature_columns = json.loads((MODEL_DIR / "feature_columns.json").read_text())
    return model, feature_columns


def _latest_odds(session, match_id: int) -> Odds | None:
    return session.scalar(select(Odds).where(Odds.match_id == match_id).order_by(Odds.fetched_at.desc()).limit(1))


def run_daily_predictions(days_ahead: int = 3) -> int:
    model, feature_columns = load_model()
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)

    count = 0
    with get_session() as session:
        upcoming = session.scalars(
            select(Match).where(Match.status == "scheduled", Match.start_time.between(now, horizon))
        ).all()

        for match in upcoming:
            features = build_features(session, match.player1, match.player2, match.surface, now)
            X = [[features[col] for col in feature_columns]]
            prob_p1 = float(model.predict_proba(X)[0, 1])

            odds = _latest_odds(session, match.id)
            best_p1_odds = odds.player1_decimal_odds if odds else None
            best_p2_odds = odds.player2_decimal_odds if odds else None

            ev = None
            is_value = False
            if best_p1_odds and best_p2_odds:
                ev_p1 = prob_p1 * best_p1_odds - 1
                ev_p2 = (1 - prob_p1) * best_p2_odds - 1
                ev = max(ev_p1, ev_p2)
                is_value = ev >= VALUE_BET_EV_THRESHOLD

            session.add(
                Prediction(
                    match_id=match.id,
                    model_version=MODEL_VERSION,
                    player1_win_prob=prob_p1,
                    best_player1_odds=best_p1_odds,
                    best_player2_odds=best_p2_odds,
                    expected_value=ev,
                    is_value_bet=is_value,
                )
            )
            count += 1

    logger.info("Wrote %d predictions", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_daily_predictions())
