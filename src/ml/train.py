"""Train an XGBoost 1X2 (home/draw/away) model from the matches stored in Postgres.

Run manually or on the daily schedule (see .github/workflows/pipeline.yml). Saves the
model plus its feature list as JSON so `predict.py` can load it without re-importing
this module.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import xgboost as xgb
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import train_test_split

from src.db.session import get_session
from src.ml.features import FEATURE_COLUMNS, build_training_dataframe

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
MODEL_PATH = MODEL_DIR / "model.json"
MODEL_VERSION = "xgb-1x2-v1"


def train() -> dict[str, float] | None:
    with get_session() as session:
        df = build_training_dataframe(session)

    if len(df) < 50:
        # Not an error worth hard-failing the daily scheduled run over -- e.g. during
        # the European football off-season (roughly June-August), there simply aren't
        # 50 finished matches to train on yet. Skip quietly; the next run picks up
        # wherever the finished-match count landed once the season resumes.
        logger.warning(
            "Only %d training rows available (need >=50) -- skipping this run.", len(df)
        )
        return None

    X, y = df[FEATURE_COLUMNS], df["label"]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    # This early in a season there are only a couple hundred finished matches to train
    # on -- 300 boosting rounds at depth 4 comfortably overfits that, producing
    # overconfident probabilities (e.g. 97% for an outcome that's really more like
    # 60%) rather than wrong *predictions* per se: log_loss can come out *worse* than
    # a flat 33/33/33 guess even while argmax accuracy looks fine, because the
    # confidently-wrong cases dominate the loss. Those overconfident probabilities are
    # exactly what predict.py's EV math (prob * odds - 1) turns into the absurd
    # "+1000% edge" picks that keep getting chosen for combos and keep losing.
    # min_child_weight/reg_lambda add regularization appropriate for this little data,
    # and early stopping on the held-out split picks the actual best round instead of
    # always running the full 300, which is the main lever against overfitting here.
    model = xgb.XGBClassifier(
        objective="multi:softprob",
        num_class=3,
        n_estimators=300,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        reg_lambda=2.0,
        eval_metric="mlogloss",
        early_stopping_rounds=20,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    proba = model.predict_proba(X_test)
    metrics = {
        "log_loss": log_loss(y_test, proba, labels=[0, 1, 2]),
        "accuracy": accuracy_score(y_test, proba.argmax(axis=1)),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "best_iteration": model.best_iteration,
    }
    logger.info("Training complete: %s", metrics)

    MODEL_DIR.mkdir(exist_ok=True)
    model.save_model(str(MODEL_PATH))
    (MODEL_DIR / "feature_columns.json").write_text(json.dumps(FEATURE_COLUMNS))
    (MODEL_DIR / "metrics.json").write_text(json.dumps(metrics))

    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(train())
