"""
ML Router
─────────
Admin-only endpoints for model management.
Public endpoints (no admin required) for dashboard display.

Admin endpoints  →  /admin/ml/...
Public endpoints →  /ml/...
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import get_current_active_user, require_role
from app.database import get_db, naive_utc
from app.ml.cloud import cloud_health
from app.ml.predictor import anomaly_scan, auto_insights, forecast
from app.ml.trainer import MIN_SAMPLES, load_bundle, train
from app.models import EnergyReading, User, UserRole

logger = logging.getLogger("greenpulse.ml")

# ── Two routers ────────────────────────────────────────────────────────────────
router        = APIRouter(prefix="/admin/ml", tags=["ML Engine (Admin)"])
public_router = APIRouter(prefix="/ml",       tags=["ML Engine (Public)"])

AdminOnly = Depends(require_role(UserRole.ADMIN))


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/status")
def get_ml_status(_: User = AdminOnly):
    """Full model metadata — admin only."""
    bundle = load_bundle()
    if bundle is None:
        return {"trained": False, "min_samples_required": MIN_SAMPLES}
    return {
        "trained":          True,
        "version":          bundle.get("version", 1),
        "trained_at":       bundle["trained_at"],
        "n_samples":        bundle["n_samples"],
        "n_clean":          bundle.get("n_clean", bundle["n_samples"]),
        "metrics":          bundle.get("metrics", {}),
        "data_quality":     bundle.get("data_quality", {}),
        "ensemble_weights": {
            "gbr": round(bundle.get("w_gbr", 0.7), 3),
            "lr":  round(bundle.get("w_lr",  0.3), 3),
        },
        "cloud": cloud_health(),
    }


@router.post("/train")
def train_model(
    organization_id: int | None = Query(default=None, description="Limit training data to this organisation. Omit to train on all data."),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """
    Train all models on energy readings.
    Pass ?organization_id=<id> to train on a single organisation's data only.
    Runs IsolationForest + GradientBoosting + LinearRegression
    with TimeSeriesSplit cross-validation.
    """
    q = db.query(EnergyReading).order_by(EnergyReading.timestamp)
    if organization_id is not None:
        q = q.filter(EnergyReading.organization_id == organization_id)
    all_readings = q.all()
    if len(all_readings) < MIN_SAMPLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_SAMPLES} readings (have {len(all_readings)}).",
        )
    try:
        return train(all_readings)
    except Exception as e:
        logger.error("Training failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/train-and-insights")
def train_and_generate_insights(
    organization_id: int | None = Query(default=None, description="Scope training and insights to this organisation."),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """Train models then immediately auto-generate Insight records.
    Pass ?organization_id=<id> to train on and direct insights to a single organisation."""
    q = db.query(EnergyReading).order_by(EnergyReading.timestamp)
    if organization_id is not None:
        q = q.filter(EnergyReading.organization_id == organization_id)
    all_readings = q.all()
    if len(all_readings) < MIN_SAMPLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Need at least {MIN_SAMPLES} readings (have {len(all_readings)}).",
        )
    train_result   = train(all_readings)
    insight_result = auto_insights(db, organization_id=organization_id)
    return {**train_result, "insights": insight_result}


@router.get("/anomalies")
def get_anomalies(
    days: int = Query(default=7, ge=1, le=90),
    organization_id: int | None = Query(default=None, description="Limit scan to this organisation's readings."),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """Anomaly scan on the last N days of readings (default 7).
    Pass ?organization_id=<id> to scan a single organisation only."""
    cutoff = naive_utc(datetime.now(timezone.utc) - timedelta(days=days))
    q = db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff)
    if organization_id is not None:
        q = q.filter(EnergyReading.organization_id == organization_id)
    readings = q.all()
    if not readings:
        return {"anomalies": [], "total_checked": 0, "anomaly_count": 0, "anomaly_rate_pct": 0.0}
    try:
        return anomaly_scan(readings, db=db)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/forecast")
def get_forecast(
    hours: int = Query(default=168, ge=24, le=720),
    organization_id: int | None = Query(default=None, description="Base the forecast on this organisation's readings."),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """Generate N-hour energy forecast using ensemble model (default 168 h = 7 days).
    Pass ?organization_id=<id> to forecast from a single organisation's data."""
    q = db.query(EnergyReading).order_by(EnergyReading.timestamp.desc())
    if organization_id is not None:
        q = q.filter(EnergyReading.organization_id == organization_id)
    last_readings = q.limit(336).all()
    try:
        return forecast(horizon_hours=hours, last_readings=last_readings)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/generate-insights")
def generate_insights(
    organization_id: int | None = Query(default=None, description="Scope insights to this organisation."),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """Manually trigger insight and notification generation from latest data.
    Pass ?organization_id=<id> to analyse and direct insights to a single organisation."""
    return auto_insights(db, organization_id=organization_id)


@router.get("/cloud")
def get_cloud_status(_: User = AdminOnly):
    """Return cloud ML provider health and configuration."""
    return cloud_health()


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENDPOINTS  (any authenticated user — for the user dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

@public_router.get("/status")
def public_ml_status(current_user: User = Depends(get_current_active_user)):
    """Lightweight model status — for the user dashboard."""
    bundle = load_bundle()
    if bundle is None:
        return {"trained": False}
    return {
        "trained":    True,
        "trained_at": bundle["trained_at"],
        "n_samples":  bundle["n_samples"],
    }


@public_router.get("/forecast")
def public_forecast(
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Short-term forecast for the user dashboard (default 24 h)."""
    last_readings = (
        db.query(EnergyReading)
        .order_by(EnergyReading.timestamp.desc())
        .limit(168)
        .all()
    )
    try:
        return forecast(horizon_hours=hours, last_readings=last_readings)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@public_router.get("/anomalies/recent")
def public_recent_anomalies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Last 24 h anomaly summary — for the user dashboard alert badge."""
    cutoff = naive_utc(datetime.now(timezone.utc) - timedelta(hours=24))
    readings = db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff).all()
    if not readings:
        return {"anomaly_count": 0, "total_checked": 0, "anomalies": []}
    try:
        return anomaly_scan(readings, db=None)
    except RuntimeError:
        return {"anomaly_count": 0, "total_checked": len(readings), "anomalies": []}
