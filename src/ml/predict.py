"""Score upcoming matches and combine model probabilities with 1X2 odds into an EV call.

expected_value = model_probability * best_decimal_odds - 1, computed separately for the
home/draw/away outcomes; the highest of the three is stored as the match's overall EV.
A positive EV means the model thinks the market is underpricing that outcome.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

import xgboost as xgb
from sqlalchemy import select

from src.db.models import ComboBet, ComboLeg, Match, Odds, Prediction
from src.db.session import get_session
from src.ml.features import build_features
from src.ml.train import MODEL_DIR, MODEL_PATH, MODEL_VERSION

logger = logging.getLogger(__name__)

VALUE_BET_EV_THRESHOLD = 0.05  # flag bets with >5% edge over the market-implied probability
COMBO_MAX_LEGS = 3
COMBO_SAVE_FRESHNESS = dt.timedelta(hours=20)  # one saved combo per day, not one per run


def load_model() -> tuple[xgb.XGBClassifier, list[str]]:
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"No trained model at {MODEL_PATH}; run `python -m src.ml.train` first.")
    model = xgb.XGBClassifier()
    model.load_model(str(MODEL_PATH))
    feature_columns = json.loads((MODEL_DIR / "feature_columns.json").read_text())
    return model, feature_columns


def _latest_odds(session, match_id: int) -> Odds | None:
    return session.scalar(select(Odds).where(Odds.match_id == match_id).order_by(Odds.fetched_at.desc()).limit(1))


def run_daily_predictions(days_ahead: int = 7) -> int | None:
    try:
        model, feature_columns = load_model()
    except FileNotFoundError as exc:
        # No model yet -- e.g. train() skipped this run for lack of data (off-season).
        # Not worth hard-failing the daily scheduled run over.
        logger.warning("Skipping predictions: %s", exc)
        return None
    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)

    count = 0
    with get_session() as session:
        upcoming = session.scalars(
            select(Match).where(Match.status == "scheduled", Match.start_time.between(now, horizon))
        ).all()

        for match in upcoming:
            features = build_features(session, match.home_team, match.away_team, now)
            X = [[features[col] for col in feature_columns]]
            # Label encoding from features.py: 0=away win, 1=draw, 2=home win.
            away_prob, draw_prob, home_prob = model.predict_proba(X)[0]

            odds = _latest_odds(session, match.id)
            best_home_odds = odds.home_decimal_odds if odds else None
            best_draw_odds = odds.draw_decimal_odds if odds else None
            best_away_odds = odds.away_decimal_odds if odds else None

            candidates: list[tuple[str, float]] = []
            if best_home_odds:
                candidates.append(("home", home_prob * best_home_odds - 1))
            if best_draw_odds:
                candidates.append(("draw", draw_prob * best_draw_odds - 1))
            if best_away_odds:
                candidates.append(("away", away_prob * best_away_odds - 1))

            value_pick, ev = (None, None)
            is_value = False
            if candidates:
                value_pick, ev = max(candidates, key=lambda c: c[1])
                is_value = ev >= VALUE_BET_EV_THRESHOLD

            session.add(
                Prediction(
                    match_id=match.id,
                    model_version=MODEL_VERSION,
                    home_win_prob=float(home_prob),
                    draw_prob=float(draw_prob),
                    away_win_prob=float(away_prob),
                    best_home_odds=best_home_odds,
                    best_draw_odds=best_draw_odds,
                    best_away_odds=best_away_odds,
                    expected_value=ev,
                    value_pick=value_pick,
                    is_value_bet=is_value,
                )
            )
            count += 1

        save_daily_combo(session, days_ahead=days_ahead)

    logger.info("Wrote %d predictions", count)
    return count


def save_daily_combo(session, days_ahead: int = 7, max_legs: int = COMBO_MAX_LEGS, min_edge: float = 0.0) -> ComboBet | None:
    """Persist today's best combo suggestion (the single largest one, built from the
    same ranked value picks as tools.get_combo_suggestions()'s smaller ones) so it can
    be checked against final results later -- see settle_combo_bets() in ingest.py.
    Skips if one was already saved within the last day.
    """
    cutoff = dt.datetime.utcnow() - COMBO_SAVE_FRESHNESS
    if session.scalar(select(ComboBet).where(ComboBet.created_at >= cutoff)) is not None:
        logger.info("Daily combo already saved within the last %s, skipping", COMBO_SAVE_FRESHNESS)
        return None

    now = dt.datetime.utcnow()
    horizon = now + dt.timedelta(days=days_ahead)
    rows = session.execute(
        select(Prediction, Match)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Match.start_time.between(now, horizon),
            Prediction.expected_value.is_not(None),
            Prediction.expected_value >= min_edge,
            Prediction.value_pick.is_not(None),
        )
        .order_by(Prediction.expected_value.desc())
        .limit(max_legs)
    ).all()
    if len(rows) < 2:
        logger.info("Only %d value pick(s) available -- not enough for a combo today", len(rows))
        return None

    odds_field = {"home": "best_home_odds", "draw": "best_draw_odds", "away": "best_away_odds"}
    prob_field = {"home": "home_win_prob", "draw": "draw_prob", "away": "away_win_prob"}

    combo = ComboBet(combined_odds=1.0, combined_prob=1.0, combined_ev=0.0)
    session.add(combo)
    session.flush()  # assign combo.id for the legs' FK

    combined_odds, combined_prob = 1.0, 1.0
    for pred, match in rows:
        side = pred.value_pick
        odds = getattr(pred, odds_field[side])
        prob = getattr(pred, prob_field[side])
        pick_name = {"home": match.home_team.name, "draw": "Unentschieden", "away": match.away_team.name}[side]
        combined_odds *= odds
        combined_prob *= prob
        session.add(ComboLeg(combo_id=combo.id, match_id=match.id, pick_side=side, pick_name=pick_name, odds=odds))

    combo.combined_odds = round(combined_odds, 2)
    combo.combined_prob = round(combined_prob, 3)
    combo.combined_ev = round(combined_prob * combined_odds - 1, 3)
    session.flush()
    logger.info(
        "Saved daily combo: %d legs, odds=%.2f, EV=%.1f%%", len(rows), combo.combined_odds, combo.combined_ev * 100
    )
    return combo


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_daily_predictions())
