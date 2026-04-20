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

from app.ml.llm import describe_night_usage, describe_peak_usage, describe_weekend_usage
from app.ml.trainer import load_bundle

logger = logging.getLogger("greenpulse.ml.predictor")

SEVERITY_THRESHOLDS = {"high": -0.15, "medium": -0.05}


def anomaly_scan(readings, db=None, org_id: int | None = None) -> dict:
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
    bundle = load_bundle(org_id)
    if bundle is None:
        raise RuntimeError(
            f"No model trained for org {org_id}. Train the model first."
            if org_id else "No model trained. Train the model first."
        )

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

    total = len(df)   # use cleaned row count — anomalies are detected on df, not raw readings
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


def forecast(horizon_hours: int = 168, last_readings=None, org_id: int | None = None) -> dict:
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
    bundle = load_bundle(org_id)
    if bundle is None:
        raise RuntimeError(
            f"No model trained for org {org_id}. Train the model first."
            if org_id else "No model trained. Train the model first."
        )

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

    # Confidence band: use CV RMSE as proxy for σ; 90% PI = ±1.645σ.
    # RMSE is preferred over MAE here because RMSE ≈ σ for zero-mean errors,
    # whereas MAE ≈ 0.798σ — using MAE * 1.64 underestimates the band by ~20%.
    # Fall back to ±15% placeholder if metrics are unavailable.
    cv_rmse = bundle.get("metrics", {}).get("gbr_val_rmse")
    if cv_rmse and cv_rmse > 0:
        half_band = cv_rmse * 1.645  # 90% PI: 1.645 * σ
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


def auto_insights(db, organization_id: int | None = None) -> dict:
    """
    Analyse the last 7 days and auto-generate Insight + Notification records.
    Deduplication window is 24 hours -- the same insight can be re-created the
    next day if the condition persists.
    Stats are computed on IQR-capped cleaned data so outliers don't skew them.

    Pass organization_id to scope the analysis and tag generated records to
    that organisation. Without it, all readings are used and records have no org.
    """
    from app.models import (
        EnergyReading, Insight, InsightCategory, InsightStatus,
        Notification, NotificationType,
    )
    from app.database import naive_utc

    bundle = load_bundle(organization_id)
    if bundle is None:
        return {"created": 0, "skipped": "model not trained for this organisation"}

    cutoff_7d  = naive_utc(datetime.now(timezone.utc) - timedelta(days=7))
    cutoff_24h = naive_utc(datetime.now(timezone.utc) - timedelta(hours=24))

    q = db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff_7d)
    if organization_id is not None:
        q = q.filter(EnergyReading.organization_id == organization_id)
    readings = q.all()
    if not readings:
        return {"created": 0, "skipped": "no recent readings for this organisation"}

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
        """Return True if this insight title was created within the last 24 h for this org."""
        q = db.query(Insight).filter(Insight.title == title, Insight.created_at >= cutoff_24h)
        if organization_id is not None:
            q = q.filter(Insight.organization_id == organization_id)
        return bool(q.first())

    # Only call the LLM for real registered organisations — not for demo/global runs
    use_llm = organization_id is not None

    # ---- Insight 1: Peak usage warning --------------------------------------
    if peak_ratio > 2.0:
        title = "Unusually high peak consumption detected"
        if not _insight_exists(title):
            description = (use_llm and describe_peak_usage(peak, avg, peak_ratio)) or (
                f"Peak consumption ({peak:.1f} kWh) is {peak_ratio:.1f}x the 7-day average "
                f"({avg:.1f} kWh). Investigate high-draw equipment during peak windows."
            )
            # Confidence scales with how extreme the ratio is: 0.70 at 2×, capped at 0.94 above 5×
            peak_confidence = round(min(0.94, 0.70 + (peak_ratio - 2.0) * 0.08), 2)
            db.add(Insight(
                title=title,
                description=description,
                category=InsightCategory.ENERGY,
                confidence_score=peak_confidence,
                estimated_savings=round((peak - avg) * 0.28 * 30, 2),
                status=InsightStatus.PENDING,
                facility_id=1,
                organization_id=organization_id,
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
                description = (use_llm and describe_night_usage(night_avg, avg, night_avg / avg * 100)) or (
                    f"Average night-time consumption ({night_avg:.1f} kWh) is "
                    f"{night_avg / avg * 100:.0f}% of the daytime average. "
                    "Consider scheduling equipment shutdowns."
                )
                # Confidence scales with night/day ratio: 0.65 at 50%, capped at 0.92 above 120%
                night_confidence = round(min(0.92, 0.65 + (night_avg / avg - 0.5) * 0.40), 2)
                db.add(Insight(
                    title=title,
                    description=description,
                    category=InsightCategory.ENERGY,
                    confidence_score=night_confidence,
                    estimated_savings=round(night_avg * 0.4 * 0.28 * 30, 2),
                    status=InsightStatus.PENDING,
                    facility_id=1,
                    organization_id=organization_id,
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
                description = (use_llm and describe_weekend_usage(we_avg, wd_avg, we_avg / wd_avg * 100)) or (
                    f"Weekend average ({we_avg:.1f} kWh) is "
                    f"{we_avg / wd_avg * 100:.0f}% of the weekday average ({wd_avg:.1f} kWh). "
                    "Review weekend staffing and equipment schedules."
                )
                # Confidence scales with weekend/weekday ratio: 0.65 at 1.3×, capped at 0.90 above 2.1×
                we_confidence = round(min(0.90, 0.65 + (we_avg / wd_avg - 1.3) * 0.35), 2)
                db.add(Insight(
                    title=title,
                    description=description,
                    category=InsightCategory.OPERATIONS,
                    confidence_score=we_confidence,
                    estimated_savings=round((we_avg - wd_avg) * 0.28 * 8, 2),
                    status=InsightStatus.PENDING,
                    facility_id=1,
                    organization_id=organization_id,
                ))
                created += 1

    db.commit()

    # ---- Email managers about new insights (notify_new_insights=True) --------
    if created > 0 and organization_id is not None:
        try:
            from app.models import Organization, User, UserRole
            from app.email import send_insight_digest_email
            from app.config import settings

            org = db.query(Organization).filter(Organization.id == organization_id).first()
            org_name = org.name if org else "your organisation"
            dashboard_url = f"{settings.FRONTEND_URL}/insights"

            # Sum estimated savings from all pending insights in this org
            from app.models import Insight as InsightModel, InsightStatus as IS
            total_savings = (
                db.query(InsightModel)
                .filter(
                    InsightModel.organization_id == organization_id,
                    InsightModel.status == IS.PENDING,
                )
                .with_entities(InsightModel.estimated_savings)
                .all()
            )
            total_savings_val = sum(r[0] or 0 for r in total_savings)

            managers = db.query(User).filter(
                User.organization_id == organization_id,
                User.role == UserRole.MANAGER,
                User.is_active == True,
                User.notify_new_insights == True,
            ).all()

            for manager in managers:
                try:
                    send_insight_digest_email(
                        to_email=manager.email,
                        first_name=manager.first_name,
                        org_name=org_name,
                        insight_count=created,
                        total_estimated_savings=total_savings_val,
                        dashboard_url=dashboard_url,
                    )
                except Exception as mail_err:
                    logger.warning("Insight email to %s failed: %s", manager.email, mail_err)
        except Exception as e:
            logger.warning("Insight email setup failed: %s", e)

    # ---- Notification + email alert for anomalies ---------------------------
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
                organization_id=organization_id,
            ))
            db.commit()

            # Email every active MANAGER in the org with anomaly alerts enabled
            if organization_id is not None:
                try:
                    from app.models import Organization, User, UserRole
                    from app.email import send_alert_email
                    from app.config import settings

                    org = db.query(Organization).filter(Organization.id == organization_id).first()
                    org_name = org.name if org else "your organisation"
                    dashboard_url = f"{settings.FRONTEND_URL}/notifications"

                    managers = db.query(User).filter(
                        User.organization_id == organization_id,
                        User.role == UserRole.MANAGER,
                        User.is_active == True,
                        User.notify_anomaly_alerts == True,
                    ).all()

                    for manager in managers:
                        try:
                            send_alert_email(
                                to_email=manager.email,
                                first_name=manager.first_name,
                                alert_title="Energy anomalies detected",
                                alert_message=msg,
                                org_name=org_name,
                                anomaly_count=len(high),
                                dashboard_url=dashboard_url,
                            )
                        except Exception as mail_err:
                            logger.warning("Alert email to %s failed: %s", manager.email, mail_err)
                except Exception as e:
                    logger.warning("Alert email setup failed: %s", e)
    except Exception as e:
        logger.warning("Anomaly notification skipped: %s", e)

    return {"created": created, "skipped": None}


# ---- Signature analysis -------------------------------------------------------

def ghost_load_analysis(readings, electricity_rate: float = 0.28) -> dict:
    """
    Identify ghost load (energy wasted during off-hours) by decomposing
    whole-site consumption into base load, operational load, and ghost load.

    Base load = rolling 24h minimum per zone (always-on equipment floor).
    Ghost load = consumption above base load during off-hours (22:00–06:00).

    Does NOT require a trained ML bundle — pure statistical analysis.
    """
    empty = {
        "total_kwh": 0.0, "base_load_kwh": 0.0, "operational_kwh": 0.0,
        "ghost_load_kwh": 0.0, "ghost_cost_per_hr": 0.0,
        "estimated_monthly_waste_cost": 0.0, "savings_potential_pct": 0.0,
        "hourly_heatmap": [],
    }
    if not readings:
        return empty

    rows = []
    for r in readings:
        ts = (
            pd.Timestamp(r.timestamp).tz_localize(None)
            if getattr(r.timestamp, "tzinfo", None)
            else pd.Timestamp(r.timestamp)
        )
        rows.append({
            "timestamp": ts,
            "consumption_kwh": max(0.0, float(r.consumption_kwh)),
            "zone": str(r.zone).strip().lower(),
        })

    df = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return empty

    # Rolling 24h minimum per zone = base load estimate
    df = df.set_index("timestamp")
    zone_frames = []
    for _, zone_df in df.groupby("zone", sort=False):
        zdf = zone_df.copy()
        zdf["base_load"] = zdf["consumption_kwh"].rolling("24h", min_periods=1).min()
        zone_frames.append(zdf)
    df = pd.concat(zone_frames).sort_index().reset_index()

    df["hour"]         = df["timestamp"].dt.hour
    df["day_of_week"]  = df["timestamp"].dt.dayofweek
    df["is_ghost_hour"] = (df["hour"] <= 5) | (df["hour"] >= 22)

    total_kwh    = float(df["consumption_kwh"].sum())
    base_load_kwh = float(df["base_load"].sum())

    # Ghost load = excess above base load specifically during ghost hours
    ghost_mask  = df["is_ghost_hour"]
    ghost_excess = (
        df.loc[ghost_mask, "consumption_kwh"] - df.loc[ghost_mask, "base_load"]
    ).clip(lower=0)
    ghost_load_kwh   = float(ghost_excess.sum())
    operational_kwh  = max(0.0, total_kwh - base_load_kwh - ghost_load_kwh)

    ghost_hours_count   = int(ghost_mask.sum())
    ghost_cost_per_hr   = (
        ghost_load_kwh / ghost_hours_count * electricity_rate
    ) if ghost_hours_count > 0 else 0.0
    # 8 ghost hours per night × 30 days
    estimated_monthly_waste_cost = ghost_cost_per_hr * 8 * 30
    savings_potential_pct = (ghost_load_kwh / total_kwh * 100) if total_kwh > 0 else 0.0

    # Heatmap: avg consumption per (day_of_week, hour) cell
    hm = (
        df.groupby(["day_of_week", "hour"])["consumption_kwh"]
        .mean()
        .reset_index()
    )
    hm["is_ghost_hour"] = (hm["hour"] <= 5) | (hm["hour"] >= 22)
    hm = hm.sort_values(["day_of_week", "hour"]).reset_index(drop=True)

    hourly_heatmap = [
        {
            "hour":         int(row["hour"]),
            "day_of_week":  int(row["day_of_week"]),
            "avg_kwh":      round(float(row["consumption_kwh"]), 3),
            "is_ghost_hour": bool(row["is_ghost_hour"]),
        }
        for _, row in hm.iterrows()
    ]

    return {
        "total_kwh":                    round(total_kwh, 2),
        "base_load_kwh":                round(base_load_kwh, 2),
        "operational_kwh":              round(operational_kwh, 2),
        "ghost_load_kwh":               round(ghost_load_kwh, 2),
        "ghost_cost_per_hr":            round(ghost_cost_per_hr, 2),
        "estimated_monthly_waste_cost": round(estimated_monthly_waste_cost, 2),
        "savings_potential_pct":        round(savings_potential_pct, 1),
        "hourly_heatmap":               hourly_heatmap,
    }


def zone_health_scores(readings, org_id: int | None = None) -> dict:
    """
    Score each zone 0–100 for consumption health based on three signals:
      A (40 pts) — anomaly rate via the trained IsolationForest
      B (30 pts) — coefficient of variation (consumption stability)
      C (30 pts) — 7-day consumption trend direction

    Requires a trained model bundle. Raises RuntimeError if none exists.
    """
    bundle = load_bundle(org_id)
    if bundle is None:
        raise RuntimeError(
            f"No model trained for org {org_id}. Train the model first."
            if org_id else "No model trained. Train the model first."
        )

    prep = bundle["prep"]
    iso  = bundle["iso"]

    if not readings:
        return {"zones": []}

    df = prep.clean_orm(readings)
    if df.empty:
        return {"zones": []}

    now      = df["timestamp"].max()
    split_ts = now - pd.Timedelta(days=7)

    zones_out = []
    for zone_name in df["zone"].unique():
        zdf = df[df["zone"] == zone_name].copy()

        # Signal A: anomaly rate (0–40 pts)
        try:
            X     = prep.anomaly_X(zdf, fit=False)
            preds = iso.predict(X)
            anomaly_rate = float((preds == -1).sum() / len(preds) * 100)
        except Exception:
            anomaly_rate = 0.0
        score_a = max(0.0, 40.0 - anomaly_rate * 2)

        # Signal B: coefficient of variation (0–30 pts)
        kwh_vals = zdf["consumption_kwh"].values
        mean_val = float(np.mean(kwh_vals))
        std_val  = float(np.std(kwh_vals))
        cv       = std_val / mean_val if mean_val > 0 else 0.0
        score_b  = float(np.clip(30.0 - cv * 15, 0, 30))

        # Signal C: 7-day trend (0–30 pts)
        recent_df = zdf[zdf["timestamp"] >= split_ts]
        prior_df  = zdf[zdf["timestamp"] <  split_ts]

        if len(recent_df) >= 5 and len(prior_df) >= 5:
            recent_avg = float(recent_df["consumption_kwh"].mean())
            prior_avg  = float(prior_df["consumption_kwh"].mean())
            trend_pct  = ((recent_avg - prior_avg) / prior_avg * 100) if prior_avg > 0 else 0.0
            if trend_pct <= -5:
                score_c = 30.0
            elif trend_pct <= 10:
                score_c = float(30.0 - (trend_pct + 5) / 15 * 15)
            else:
                score_c = float(max(0.0, 15.0 - (trend_pct - 10)))
        else:
            trend_pct = 0.0
            score_c   = 15.0  # neutral — not enough data

        health_score = int(np.clip(round(score_a + score_b + score_c), 0, 100))
        status = "green" if health_score >= 70 else ("amber" if health_score >= 40 else "red")

        # Worst signal drives the recommendation
        if score_a <= min(score_a, score_b) and score_a < 30:
            recommendation = "High anomaly rate detected - check equipment in this zone"
        elif score_b < score_a and score_b < 20:
            recommendation = "Unstable consumption variance - investigate load fluctuations"
        elif score_c < 10:
            recommendation = "Rising consumption trend - review scheduling or equipment"
        else:
            recommendation = "Zone operating within normal parameters"

        zones_out.append({
            "zone":             zone_name,
            "health_score":     health_score,
            "status":           status,
            "anomaly_rate_pct": round(anomaly_rate, 1),
            "trend_pct":        round(trend_pct, 1),
            "recommendation":   recommendation,
        })

    status_order = {"red": 0, "amber": 1, "green": 2}
    zones_out.sort(key=lambda z: (status_order[z["status"]], -z["health_score"]))

    return {"zones": zones_out}


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
