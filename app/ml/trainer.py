"""
ModelTrainer
------------
Trains and cross-validates all GreenPulse ML models:
  - IsolationForest   -- anomaly detection (adaptive contamination)
  - GradientBoosting  -- primary forecast model (adaptive hyperparameters)
  - LinearRegression  -- lightweight baseline / interpretable fallback
  - Ensemble          -- weighted blend of GBR + LR

Cross-validation uses TimeSeriesSplit (respects temporal ordering).
Overfitting is detected by comparing train vs. validation MAE gap.
All artefacts are persisted to MODEL_PATH as a single pickle.
"""

from __future__ import annotations

import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor, IsolationForest
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from app.ml.pipeline import EnergyPreprocessor

logger = logging.getLogger("greenpulse.ml.trainer")

MODEL_PATH = Path(__file__).parent.parent / "ml_models.pkl"
MIN_SAMPLES = 10

# Overfitting alert: if train MAE is less than this fraction of val MAE, warn
OVERFIT_TRAIN_VAL_RATIO = 0.5


def load_bundle() -> dict | None:
    """Return the saved model bundle or None."""
    if not MODEL_PATH.exists():
        return None
    try:
        with open(MODEL_PATH, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.warning("Could not load model bundle: %s", e)
        return None


def save_bundle(bundle: dict) -> None:
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(bundle, f)
    logger.info("Model bundle saved -> %s", MODEL_PATH)


def _adaptive_gbr_params(n_samples: int) -> dict:
    """
    Scale GBR complexity to dataset size to avoid overfitting on small datasets
    and underfitting on large ones.

    Dataset size  | n_estimators | max_depth | min_samples_leaf
    < 100         |     50       |     2     |       5
    100 - 500     |    100       |     3     |       4
    500 - 2000    |    200       |     4     |       3
    > 2000        |    300       |     5     |       3
    """
    if n_samples < 100:
        return dict(n_estimators=50,  max_depth=2, min_samples_leaf=5,  learning_rate=0.1,  subsample=0.9)
    elif n_samples < 500:
        return dict(n_estimators=100, max_depth=3, min_samples_leaf=4,  learning_rate=0.08, subsample=0.85)
    elif n_samples < 2000:
        return dict(n_estimators=200, max_depth=4, min_samples_leaf=3,  learning_rate=0.06, subsample=0.8)
    else:
        return dict(n_estimators=300, max_depth=5, min_samples_leaf=3,  learning_rate=0.05, subsample=0.8)


def train(readings) -> dict:
    """
    Full training pipeline.

    Parameters
    ----------
    readings : list of EnergyReading ORM objects

    Returns
    -------
    dict with keys: status, n_samples, trained_at, metrics, data_quality
    """
    if len(readings) < MIN_SAMPLES:
        raise ValueError(
            f"Need at least {MIN_SAMPLES} readings to train (got {len(readings)})."
        )

    logger.info("Training on %d readings ...", len(readings))

    # ---- Preprocessing (with full data cleaning) ---------------------------
    prep = EnergyPreprocessor()
    df = prep.clean_orm(readings)
    n_clean = len(df)
    y = df["consumption_kwh"].values

    if n_clean < MIN_SAMPLES:
        raise ValueError(
            f"Only {n_clean} clean rows remain after data quality checks "
            f"(minimum {MIN_SAMPLES} required)."
        )

    X_forecast = prep.forecast_X(df, fit=True)
    X_anomaly  = prep.anomaly_X(df, fit=True)

    # ---- Anomaly detection -- IsolationForest (adaptive contamination) -----
    contamination = prep.estimated_contamination
    logger.info("IsolationForest contamination (estimated): %.4f", contamination)

    iso = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        max_features=1.0,
        bootstrap=False,
        random_state=42,
    )
    iso.fit(X_anomaly)
    logger.info("IsolationForest trained.")

    # ---- Forecast -- GradientBoosting (adaptive params) --------------------
    gbr_params = _adaptive_gbr_params(n_clean)
    logger.info("GBR params for n=%d: %s", n_clean, gbr_params)
    gbr = GradientBoostingRegressor(random_state=42, **gbr_params)

    # ---- Forecast -- Linear baseline ---------------------------------------
    lr = LinearRegression()

    # ---- Cross-validation (TimeSeriesSplit) ---------------------------------
    n_splits = min(5, max(2, n_clean // 20))
    tscv = TimeSeriesSplit(n_splits=n_splits)

    gbr_train_maes, gbr_val_maes, gbr_val_rmses = [], [], []
    lr_val_maes,  lr_val_rmses = [], []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_forecast)):
        X_tr, X_val = X_forecast[train_idx], X_forecast[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        if len(X_tr) < 5:
            continue

        gbr.fit(X_tr, y_tr)
        lr.fit(X_tr, y_tr)

        gbr_train_pred = gbr.predict(X_tr)
        gbr_val_pred   = gbr.predict(X_val)
        lr_val_pred    = lr.predict(X_val)

        gbr_train_mae = mean_absolute_error(y_tr, gbr_train_pred)
        gbr_val_mae   = mean_absolute_error(y_val, gbr_val_pred)
        lr_val_mae    = mean_absolute_error(y_val, lr_val_pred)

        gbr_train_maes.append(gbr_train_mae)
        gbr_val_maes.append(gbr_val_mae)
        gbr_val_rmses.append(np.sqrt(mean_squared_error(y_val, gbr_val_pred)))
        lr_val_maes.append(lr_val_mae)
        lr_val_rmses.append(np.sqrt(mean_squared_error(y_val, lr_val_pred)))

        # Overfitting check for this fold
        if gbr_val_mae > 0 and (gbr_train_mae / gbr_val_mae) < OVERFIT_TRAIN_VAL_RATIO:
            logger.warning(
                "Fold %d: potential overfitting detected -- "
                "train MAE=%.3f is less than %.0f%% of val MAE=%.3f",
                fold + 1, gbr_train_mae, OVERFIT_TRAIN_VAL_RATIO * 100, gbr_val_mae,
            )

        logger.info(
            "Fold %d | GBR train_MAE=%.3f val_MAE=%.3f RMSE=%.3f | LR val_MAE=%.3f RMSE=%.3f",
            fold + 1,
            gbr_train_mae, gbr_val_mae, gbr_val_rmses[-1],
            lr_val_mae,    lr_val_rmses[-1],
        )

    # ---- Final fit on all data ---------------------------------------------
    gbr.fit(X_forecast, y)
    lr.fit(X_forecast, y)

    # ---- Ensemble weights (lower val MAE => higher weight) -----------------
    gbr_avg_mae = float(np.mean(gbr_val_maes)) if gbr_val_maes else 1.0
    lr_avg_mae  = float(np.mean(lr_val_maes))  if lr_val_maes  else 1.0
    total = gbr_avg_mae + lr_avg_mae
    w_gbr = 1 - (gbr_avg_mae / total)
    w_lr  = 1 - (lr_avg_mae  / total)
    w_sum = w_gbr + w_lr
    w_gbr /= w_sum
    w_lr  /= w_sum

    logger.info("Ensemble weights -- GBR: %.2f  LR: %.2f", w_gbr, w_lr)

    # ---- Global overfitting summary ----------------------------------------
    overfit_warnings = []
    if gbr_train_maes and gbr_val_maes:
        avg_train_mae = float(np.mean(gbr_train_maes))
        avg_val_mae   = gbr_avg_mae
        gap_ratio     = avg_train_mae / avg_val_mae if avg_val_mae > 0 else 1.0
        if gap_ratio < OVERFIT_TRAIN_VAL_RATIO:
            msg = (
                f"GBR may be overfitting: avg train MAE ({avg_train_mae:.3f}) is only "
                f"{gap_ratio * 100:.0f}% of avg val MAE ({avg_val_mae:.3f}). "
                "Consider reducing n_estimators or max_depth, or collecting more data."
            )
            overfit_warnings.append(msg)
            logger.warning(msg)

    trained_at = datetime.now(timezone.utc).isoformat()

    metrics = {
        "gbr_val_mae":  round(gbr_avg_mae, 4),
        "gbr_val_rmse": round(float(np.mean(gbr_val_rmses)), 4) if gbr_val_rmses else None,
        "gbr_train_mae": round(float(np.mean(gbr_train_maes)), 4) if gbr_train_maes else None,
        "lr_val_mae":   round(lr_avg_mae, 4),
        "lr_val_rmse":  round(float(np.mean(lr_val_rmses)), 4)  if lr_val_rmses  else None,
        "cv_splits":    n_splits,
        "ensemble_w_gbr": round(w_gbr, 3),
        "ensemble_w_lr":  round(w_lr,  3),
        "contamination_used": round(contamination, 4),
        "gbr_params": gbr_params,
        "overfit_warnings": overfit_warnings,
    }

    quality = prep.quality_report.as_dict() if prep.quality_report else {}

    bundle = {
        "version":    3,
        "trained_at": trained_at,
        "n_samples":  len(readings),
        "n_clean":    n_clean,
        "prep":       prep,
        "iso":        iso,
        "gbr":        gbr,
        "lr":         lr,
        "w_gbr":      w_gbr,
        "w_lr":       w_lr,
        "metrics":    metrics,
        "data_quality": quality,
    }

    save_bundle(bundle)

    logger.info(
        "Training complete -- %d samples (%d clean) | GBR val_MAE=%.3f | LR val_MAE=%.3f",
        len(readings), n_clean, gbr_avg_mae, lr_avg_mae,
    )

    return {
        "status":       "trained",
        "n_samples":    len(readings),
        "n_clean":      n_clean,
        "trained_at":   trained_at,
        "metrics":      metrics,
        "data_quality": quality,
    }
