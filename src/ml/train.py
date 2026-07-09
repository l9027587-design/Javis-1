"""Train an XGBoost win-probability model from the matches stored in Postgres.

Run manually or on a weekly schedule (see cloud/template.yaml). Saves the model plus
its feature list as JSON so `predict.py` can load it without re-importing this module.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import xgboost as xgb
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import train_test_split

from src.db.session import get_session
from src.ml.features import FEATURE_COLUMNS, build_training_dataframe

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models"
MODEL_PATH = MODEL_DIR / "model.json"
MODEL_VERSION = "xgb-v1"


def train() -> dict[str, float]:
    with get_session() as session:
        df = build_training_dataframe(session)

    if len(df) < 50:
        raise RuntimeError(
            f"Only {len(df)} training rows available; ingest more finished matches before training."
        )

    X, y = df[FEATURE_COLUMNS], df["label"]
    # Chronological data: a plain random split is fine here only because rows are already
    # duplicated symmetrically; for a more rigorous eval, sort by match date and split by time.
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    metrics = {
        "log_loss": log_loss(y_test, proba),
        "roc_auc": roc_auc_score(y_test, proba),
        "n_train": len(X_train),
        "n_test": len(X_test),
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
