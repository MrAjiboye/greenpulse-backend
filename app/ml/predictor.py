"""
Predictor
---------
Runs inference using the saved model bundle.

Public functions
----------------
  anomaly_scan(readings, db)             -> dict with anomalies list + counts
  forecast(horizon_hours, last_readings) -> dict with forecast list
  auto_insights(db)                      -> generates Insight + Notification records
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np
import pandas as pd

from app.ml.trainer import load_bundle

logger = logging.getLogger("greenpulse.ml.predictor")

SEVERITY_THRESHOLDS = {"high": -0.15, "medium": -0.05}


def anomaly_scan(readings, db=None) -> dict:
    """
    Run anomaly detection on a list of EnergyReading ORM objects.

    Parameters
    ----------
    readings : list[EnergyReading]
    db       : SQLAlchemy Session (optional -- used for auto-notification)

    Returns
    -------
    {
      anomalies: [...],
      total_checked: int,
      anomaly_count: int,
      anomaly_rate_pct: float,
    }

    NOTE: clean_orm() may drop duplicate rows. We iterate the cleaned df rows,
    not the original readings list, to keep predictions and source data aligned.
    A (timestamp, zone) lookup dict maps back to the original reading for its .id.
    """
    bundle = load_bundle()
    if bundle is None:
        raise RuntimeError("No model trained. Train the model first.")

    prep = bundle["prep"]
    iso  = bundle["iso"]

    # Build lookup: (normalised_ts, normalised_zone) -> original reading
    # Matches the dedup "keep last" behaviour in clean_orm
    reading_lookup: dict = {}
    for r in readings:
        ts = (
            pd.Timestamp(r.timestamp).tz_localize(None)
            if getattr(r.timestamp, "tzinfo", None)
            else pd.Timestamp(r.timestamp)
        )
        key = (ts, str(r.zone).strip().lower())
        reading_lookup[key] = r  # last duplicate wins

    df     = prep.clean_orm(readings)
    X      = prep.anomaly_X(df, fit=False)
    preds  = iso.predict(X)          # -1 = anomaly, 1 = normal
    scores = iso.score_samples(X)

    anomalies = []
    # Iterate df rows (same length as preds/scores) -- never the original list
    for i in range(len(df)):
        if preds[i] != -1:
            continue

        score = float(scores[i])
        if score < SEVERITY_THRESHOLDS["high"]:
            severity = "high"
        elif score < SEVERITY_THRESHOLDS["medium"]:
            severity = "medium"
        else:
            severity = "low"

        row_ts   = df.iloc[i]["timestamp"]
        row_zone = str(df.iloc[i]["zone"])
        original = reading_lookup.get((row_ts, row_zone))

        anomalies.append({
            "id":              original.id if original else None,
            "timestamp":       row_ts.isoformat() if hasattr(row_ts, "isoformat") else str(row_ts),
            "zone":            row_zone,
            "consumption_kwh": round(float(df.iloc[i]["consumption_kwh"]), 2),
            "anomaly_score":   round(score, 4),
            "severity":        severity,
        })

    total = len(readings)
    count = len(anomalies)
    rate  = round(count / total * 100, 1) if total else 0.0

    if db and anomalies:
        _auto_notify_anomalies(anomalies, db)

    return {
        "anomalies":        anomalies,
        "total_checked":    total,
        "anomaly_count":    count,
        "anomaly_rate_pct": rate,
    }


def forecast(horizon_hours: int = 168, last_readings=None) -> dict:
    """
    Generate an N-hour energy forecast using the ensemble model.

    Parameters
    ----------
    horizon_hours : int  (default 168 = 7 days)
    last_readings : list[EnergyReading] ORM objects used to seed lag features

    Returns
    -------
    {
      forecast: [{timestamp, predicted_kwh, lower_kwh, upper_kwh}, ...],
      horizon_hours: int,
      model: "ensemble" | "gbr" | "lr",
    }
    """
    bundle = load_bundle()
    if bundle is None:
        raise RuntimeError("No model trained. Train the model first.")

    prep  = bundle["prep"]
    gbr   = bundle["gbr"]
    lr    = bundle["lr"]
    w_gbr = bundle.get("w_gbr", 0.7)
    w_lr  = bundle.get("w_lr",  0.3)

    # Clean last_readings before using as lag seed
    # Filter out zero-consumption rows so they don't pull the forecast to zero
    last_df = None
    if last_readings:
        last_df = prep.clean_orm(last_readings)
        if len(last_df):
            last_df = last_df[last_df["consumption_kwh"] > 0].reset_index(drop=True)
        if not len(last_df):
            last_df = None

    future_df = prep.future_frame(horizon_hours=horizon_hours, last_df=last_df)
    X = prep.forecast_X(future_df, fit=False)

    gbr_pred = gbr.predict(X)
    lr_pred  = lr.predict(X)
    ensemble = w_gbr * gbr_pred + w_lr * lr_pred

    # Confidence band: use CV residuals stored in bundle if available,
    # otherwise fall back to ±15% placeholder
    cv_mae = bundle.get("metrics", {}).get("gbr_val_mae")
    if cv_mae and cv_mae > 0:
        half_band = cv_mae * 1.64  # ~90% PI assuming normal residuals
    else:
        half_band = None  # use percentage fallback

    result = []
    for i, row in enumerate(future_df.itertuples()):
        val = float(max(ensemble[i], 0.0))
        if half_band is not None:
            lower = round(max(val - half_band, 0.0), 3)
            upper = round(val + half_band, 3)
        else:
            lower = round(max(val * 0.85, 0.0), 3)
            upper = round(val * 1.15, 3)
        result.append({
            "timestamp":     row.timestamp.isoformat(),
            "predicted_kwh": round(val, 3),
            "lower_kwh":     lower,
            "upper_kwh":     upper,
        })

    return {
        "forecast":      result,
        "horizon_hours": horizon_hours,
        "model":         "ensemble",
    }


def auto_insights(db) -> dict:
    """
    Analyse the last 7 days and auto-generate Insight + Notification records.
    Deduplication window is 24 hours -- the same insight can be re-created the
    next day if the condition persists.
    Stats are computed on IQR-capped cleaned data so outliers don't skew them.
    """
    from app.models import (
        EnergyReading, Insight, InsightCategory, InsightStatus,
        Notification, NotificationType,
    )
    from app.database import naive_utc

    bundle = load_bundle()
    if bundle is None:
        return {"created": 0, "skipped": "model not trained"}

    cutoff_7d  = naive_utc(datetime.now(timezone.utc) - timedelta(days=7))
    cutoff_24h = naive_utc(datetime.now(timezone.utc) - timedelta(hours=24))

    readings = db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff_7d).all()
    if not readings:
        return {"created": 0, "skipped": "no recent readings"}

    # ---- Use IQR-cleaned consumption values for all statistics ---------------
    kwh_raw = np.array([r.consumption_kwh for r in readings], dtype=float)
    kwh_raw = np.clip(kwh_raw, 0, None)            # no negatives
    q1, q3  = np.percentile(kwh_raw, [25, 75])
    iqr     = q3 - q1
    if iqr > 0:
        kwh_clean = np.clip(kwh_raw, None, q3 + 3.0 * iqr)
    else:
        kwh_clean = kwh_raw

    avg  = float(np.mean(kwh_clean))
    peak = float(np.max(kwh_clean))
    peak_ratio = peak / avg if avg > 0 else 1.0

    created = 0

    def _insight_exists(title: str) -> bool:
        """Return True if this insight title was created within the last 24 h."""
        return bool(
            db.query(Insight)
            .filter(Insight.title == title, Insight.created_at >= cutoff_24h)
            .first()
        )

    # ---- Insight 1: Peak usage warning --------------------------------------
    if peak_ratio > 2.0:
        title = "Unusually high peak consumption detected"
        if not _insight_exists(title):
            db.add(Insight(
                title=title,
                description=(
                    f"Peak consumption ({peak:.1f} kWh) is {peak_ratio:.1f}x the 7-day average "
                    f"({avg:.1f} kWh). Investigate high-draw equipment during peak windows."
                ),
                category=InsightCategory.ENERGY,
                confidence_score=0.87,
                estimated_savings=round((peak - avg) * 0.18 * 30, 2),
                status=InsightStatus.PENDING,
                facility_id=1,
            ))
            created += 1

    # ---- Insight 2: Night-time waste ----------------------------------------
    night_kwh = np.array(
        [kwh_clean[i] for i, r in enumerate(readings)
         if r.timestamp.hour <= 5 or r.timestamp.hour >= 22],
        dtype=float,
    )
    if len(night_kwh):
        night_avg = float(np.mean(night_kwh))
        if night_avg > avg * 0.5:
            title = "High energy usage during off-hours"
            if not _insight_exists(title):
                db.add(Insight(
                    title=title,
                    description=(
                        f"Average night-time consumption ({night_avg:.1f} kWh) is "
                        f"{night_avg / avg * 100:.0f}% of the daytime average. "
                        "Consider scheduling equipment shutdowns."
                    ),
                    category=InsightCategory.ENERGY,
                    confidence_score=0.82,
                    estimated_savings=round(night_avg * 0.4 * 0.18 * 30, 2),
                    status=InsightStatus.PENDING,
                    facility_id=1,
                ))
                created += 1

    # ---- Insight 3: Weekend vs weekday --------------------------------------
    we_kwh = np.array(
        [kwh_clean[i] for i, r in enumerate(readings) if r.timestamp.weekday() >= 5],
        dtype=float,
    )
    wd_kwh = np.array(
        [kwh_clean[i] for i, r in enumerate(readings) if r.timestamp.weekday() < 5],
        dtype=float,
    )
    if len(we_kwh) and len(wd_kwh):
        we_avg = float(np.mean(we_kwh))
        wd_avg = float(np.mean(wd_kwh))
        if we_avg > wd_avg * 1.3:
            title = "Weekend energy usage exceeds weekday average"
            if not _insight_exists(title):
                db.add(Insight(
                    title=title,
                    description=(
                        f"Weekend average ({we_avg:.1f} kWh) is "
                        f"{we_avg / wd_avg * 100:.0f}% of the weekday average ({wd_avg:.1f} kWh). "
                        "Review weekend staffing and equipment schedules."
                    ),
                    category=InsightCategory.OPERATIONS,
                    confidence_score=0.79,
                    estimated_savings=round((we_avg - wd_avg) * 0.18 * 8, 2),
                    status=InsightStatus.PENDING,
                    facility_id=1,
                ))
                created += 1

    db.commit()

    # ---- Notification for anomalies ----------------------------------------
    try:
        scan = anomaly_scan(readings, db=None)
        high = [a for a in scan["anomalies"] if a["severity"] == "high"]
        if high:
            msg = (
                f"{len(high)} high-severity energy anomal{'y' if len(high) == 1 else 'ies'} "
                f"detected in the last 7 days. Check zone consumption logs."
            )
            db.add(Notification(
                title="Energy anomalies detected",
                message=msg,
                type=NotificationType.ALERT,
                read=False,
            ))
            db.commit()
    except Exception as e:
        logger.warning("Anomaly notification skipped: %s", e)

    return {"created": created, "skipped": None}


# ---- Helpers -----------------------------------------------------------------

def _auto_notify_anomalies(anomalies: list, db) -> None:
    from app.models import Notification, NotificationType

    high_count = sum(1 for a in anomalies if a["severity"] == "high")
    if high_count == 0:
        return

    zones = list({a["zone"] for a in anomalies if a["severity"] == "high"})
    zone_str = ", ".join(zones[:3])

    db.add(Notification(
        title=f"{high_count} high-severity anomal{'y' if high_count == 1 else 'ies'} detected",
        message=f"Zones affected: {zone_str}. Review energy consumption immediately.",
        type=NotificationType.ALERT,
        read=False,
    ))
    db.commit()
