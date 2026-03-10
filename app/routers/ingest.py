"""
Ingest Router
─────────────
Accepts energy data from multiple sources:

  POST /ingest/reading          — single reading (manual API / any client)
  POST /ingest/batch            — batch JSON array (CSV-exported, ETL jobs)
  POST /ingest/webhook/{device} — IoT device webhook (org API-key auth, no JWT needed)
  GET  /ingest/sources          — list registered IoT device sources

Webhook authentication
──────────────────────
IoT devices are expected to send:
  Header: X-API-Key: <org_iot_api_key>

Each organisation has its own unique IoT API key, auto-generated at sign-up.
Managers can view their key in the Settings page.
"""

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, File, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_active_user, require_role
from app.database import get_db, naive_utc
from app.limiter import limiter
from app.models import EnergyReading, Notification, NotificationType, Organization, User, UserRole, WasteLog
from app.schemas import EnergyReadingCreate, EnergyReadingResponse

logger = logging.getLogger("greenpulse.ingest")


def _check_spike(reading: EnergyReading, db: Session) -> None:
    """
    Lightweight real-time spike check triggered on every live ingest.

    Fires if the new reading exceeds mean + 3σ for that zone over the last 7 days.
    Requires at least 10 historical readings to establish a reliable baseline.
    Deduplicates: at most one alert per zone per hour (avoids alert fatigue from
    chatty meters).
    On a confirmed spike: creates an in-app Notification and emails all active
    MANAGER users in the organisation.
    """
    org_id = reading.organization_id
    kwh    = reading.consumption_kwh

    if not org_id or kwh <= 0:
        return

    # ── Fetch recent baseline for this zone ───────────────────────────────────
    cutoff_7d = naive_utc(datetime.now(timezone.utc) - timedelta(days=7))
    recent = (
        db.query(EnergyReading)
        .filter(
            EnergyReading.organization_id == org_id,
            EnergyReading.zone == reading.zone,
            EnergyReading.timestamp >= cutoff_7d,
            EnergyReading.consumption_kwh > 0,
        )
        .all()
    )
    if len(recent) < 10:
        return  # not enough history to call anything a spike

    values = np.array([r.consumption_kwh for r in recent], dtype=float)
    mean   = float(values.mean())
    std    = float(values.std())

    if std == 0 or kwh <= mean + 3.0 * std:
        return  # within normal range

    # ── Dedup: skip if we already alerted for this zone in the last hour ──────
    cutoff_1h = naive_utc(datetime.now(timezone.utc) - timedelta(hours=1))
    already_alerted = (
        db.query(Notification)
        .filter(
            Notification.organization_id == org_id,
            Notification.type == NotificationType.ALERT,
            Notification.created_at >= cutoff_1h,
            Notification.title.contains(reading.zone),
        )
        .first()
    )
    if already_alerted:
        return

    # ── Create in-app notification ────────────────────────────────────────────
    ratio = kwh / mean if mean > 0 else 0
    msg = (
        f"Zone '{reading.zone}' recorded {kwh:.1f} kWh — "
        f"{ratio:.1f}× its 7-day average ({mean:.1f} kWh). "
        "This may indicate a fault, equipment left on, or unscheduled usage."
    )
    db.add(Notification(
        title=f"Energy spike — {reading.zone}",
        message=msg,
        type=NotificationType.ALERT,
        read=False,
        organization_id=org_id,
    ))
    db.commit()
    logger.warning("Spike alert created — org=%s zone=%s kwh=%.2f (%.1f× avg)", org_id, reading.zone, kwh, ratio)

    # ── Email active managers in this org ─────────────────────────────────────
    try:
        from app.email import send_alert_email
        from app.config import settings

        org      = db.query(Organization).filter(Organization.id == org_id).first()
        org_name = org.name if org else "your organisation"
        managers = (
            db.query(User)
            .filter(
                User.organization_id == org_id,
                User.role == UserRole.MANAGER,
                User.is_active == True,
            )
            .all()
        )
        dashboard_url = f"{settings.FRONTEND_URL}/notifications"
        for manager in managers:
            try:
                send_alert_email(
                    to_email=manager.email,
                    first_name=manager.first_name,
                    alert_title=f"Energy spike — {reading.zone}",
                    alert_message=msg,
                    org_name=org_name,
                    anomaly_count=1,
                    dashboard_url=dashboard_url,
                )
            except Exception as mail_err:
                logger.warning("Spike email to %s failed: %s", manager.email, mail_err)
    except Exception as e:
        logger.warning("Spike email setup failed: %s", e)

router = APIRouter(prefix="/ingest", tags=["Data Ingestion"])

# free = 30-day trial with Core-level access
PRO_PLANS  = {"pro", "enterprise"}
CORE_PLANS = {"free", "core", "pro", "enterprise"}  # free gets Core features during trial


def _require_plan(org: Organization, allowed_plans: set, feature: str) -> None:
    """Raise 403 if the org's plan doesn't include the requested feature."""
    if org.plan not in allowed_plans:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{feature} is not available on your current plan. "
                "Please upgrade at greenpulse.com/pricing."
            ),
        )


# ── Single reading (manual / API) ──────────────────────────────────────────────

@router.post("/reading", response_model=EnergyReadingResponse, status_code=201)
@limiter.limit("60/minute")
def ingest_reading(
    request: Request,
    payload: EnergyReadingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Push a single energy reading from any authenticated client.
    Can be called by:
      - Your own backend scripts
      - A meter integration script running on-site
      - Google Cloud Functions / Lambda piping data from a cloud bucket
    """
    org_id = (
        payload.organization_id
        if current_user.role == UserRole.ADMIN and payload.organization_id
        else current_user.organization_id
    )
    if current_user.role != UserRole.ADMIN:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org:
            _require_plan(org, PRO_PLANS, "API data ingestion")
    reading = EnergyReading(
        timestamp=payload.timestamp,
        consumption_kwh=payload.consumption_kwh,
        zone=payload.zone,
        facility_id=payload.facility_id,
        organization_id=org_id,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)

    logger.info(
        "Ingested reading — zone=%s kwh=%.2f ts=%s user=%s",
        reading.zone, reading.consumption_kwh, reading.timestamp, current_user.email,
    )
    try:
        _check_spike(reading, db)
    except Exception as e:
        logger.warning("Spike check failed: %s", e)
    return reading


# ── Batch import ───────────────────────────────────────────────────────────────

class BatchPayload(BaseModel):
    readings: List[EnergyReadingCreate]


@router.post("/batch", status_code=201)
@limiter.limit("10/minute")
def ingest_batch(
    request: Request,
    payload: BatchPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Push multiple readings at once (ETL jobs, CSV imports, cloud batch pipelines).
    Maximum 1000 records per call.
    """
    if len(payload.readings) > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 1000 readings per batch.",
        )
    if current_user.role != UserRole.ADMIN:
        org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
        if org:
            _require_plan(org, PRO_PLANS, "API data ingestion")

    objects = [
        EnergyReading(
            timestamp=r.timestamp,
            consumption_kwh=r.consumption_kwh,
            zone=r.zone,
            facility_id=r.facility_id,
            organization_id=(
                r.organization_id
                if current_user.role == UserRole.ADMIN and r.organization_id
                else current_user.organization_id
            ),
        )
        for r in payload.readings
    ]
    db.bulk_save_objects(objects)
    db.commit()

    logger.info(
        "Batch ingest — %d readings | user=%s",
        len(objects), current_user.email,
    )
    return {"ingested": len(objects), "status": "ok"}


# ── IoT Webhook (org API-key auth) ─────────────────────────────────────────────

class IoTPayload(BaseModel):
    timestamp: Optional[datetime] = None
    consumption_kwh: float
    zone: str = "main"
    facility_id: int = 1
    # Common IoT extra fields — stored as metadata, ignored for ML
    device_id:       Optional[str]   = None
    unit:            Optional[str]   = "kWh"
    signal_strength: Optional[float] = None


@router.post("/webhook/{device_id}", status_code=201)
@limiter.limit("120/minute")
def iot_webhook(
    request: Request,
    device_id: str,
    payload: IoTPayload,
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """
    IoT device webhook — no JWT required, uses the organisation's IoT API key.

    Connect smart meters, Raspberry Pi loggers, or cloud IoT hubs here.
    Each organisation has a unique key visible in their Settings page.

    Supported sources (anything that can POST JSON):
      - Smart energy meters (e.g. Hildebrand Glow, Loop)
      - Google Cloud IoT Core
      - AWS IoT Core
      - Azure IoT Hub
      - Raspberry Pi / Arduino with HTTP library
    """
    org = db.query(Organization).filter(Organization.iot_api_key == x_api_key).first()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing IoT API key.",
        )
    _require_plan(org, PRO_PLANS, "IoT data ingestion")

    ts = payload.timestamp or datetime.now(timezone.utc)

    reading = EnergyReading(
        timestamp=ts,
        consumption_kwh=payload.consumption_kwh,
        zone=payload.zone,
        facility_id=payload.facility_id,
        organization_id=org.id,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)

    logger.info(
        "IoT webhook — org=%s device=%s zone=%s kwh=%.2f",
        org.name, device_id, reading.zone, reading.consumption_kwh,
    )
    try:
        _check_spike(reading, db)
    except Exception as e:
        logger.warning("Spike check failed: %s", e)
    return {"reading_id": reading.id, "status": "accepted"}


# ── CSV Upload ─────────────────────────────────────────────────────────────────

@router.post("/energy/csv", status_code=200)
@limiter.limit("5/minute")
async def ingest_energy_csv(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Upload a CSV of energy readings.
    Required columns: timestamp, consumption_kwh, zone
    Optional columns: facility_id (default 1)
    """
    content = await file.read()
    try:
        df = pd.read_csv(io.StringIO(content.decode("utf-8")))
    except Exception:
        raise HTTPException(400, "Could not parse CSV file. Ensure it is UTF-8 encoded.")

    required = {"timestamp", "consumption_kwh", "zone"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise HTTPException(400, f"Missing required columns: {', '.join(missing)}")

    df.columns = df.columns.str.lower()
    objects, errors = [], []

    for i, row in df.iterrows():
        try:
            ts = pd.to_datetime(row["timestamp"], utc=True).to_pydatetime()
            objects.append(EnergyReading(
                timestamp=ts,
                consumption_kwh=float(row["consumption_kwh"]),
                zone=str(row["zone"]).strip(),
                facility_id=int(row.get("facility_id", 1)),
                organization_id=current_user.organization_id,
            ))
        except Exception as e:
            errors.append({"row": int(i) + 2, "reason": str(e)})

    if objects:
        db.bulk_save_objects(objects)
        db.commit()

    logger.info("CSV energy import — %d imported, %d errors | user=%s", len(objects), len(errors), current_user.email)
    return {"imported": len(objects), "skipped": len(errors), "errors": errors[:20]}


@router.post("/waste/csv", status_code=200)
@limiter.limit("5/minute")
async def ingest_waste_csv(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """
    Upload a CSV of waste logs.
    Required columns: timestamp, stream, weight_kg, location
    Optional columns: contamination_detected (default false)
    """
    content = await file.read()
    try:
        df = pd.read_csv(io.StringIO(content.decode("utf-8")))
    except Exception:
        raise HTTPException(400, "Could not parse CSV file. Ensure it is UTF-8 encoded.")

    required = {"timestamp", "stream", "weight_kg", "location"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise HTTPException(400, f"Missing required columns: {', '.join(missing)}")

    df.columns = df.columns.str.lower()
    objects, errors = [], []

    for i, row in df.iterrows():
        try:
            ts = pd.to_datetime(row["timestamp"], utc=True).to_pydatetime()
            contaminated = str(row.get("contamination_detected", "false")).lower() in ("true", "1", "yes")
            objects.append(WasteLog(
                timestamp=ts,
                stream=str(row["stream"]).strip(),
                weight_kg=float(row["weight_kg"]),
                location=str(row["location"]).strip(),
                contamination_detected=contaminated,
                organization_id=current_user.organization_id,
            ))
        except Exception as e:
            errors.append({"row": int(i) + 2, "reason": str(e)})

    if objects:
        db.bulk_save_objects(objects)
        db.commit()

    logger.info("CSV waste import — %d imported, %d errors | user=%s", len(objects), len(errors), current_user.email)
    return {"imported": len(objects), "skipped": len(errors), "errors": errors[:20]}


# ── Source registry ────────────────────────────────────────────────────────────

@router.get("/sources")
def list_sources(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Return a summary of all data sources that have pushed readings,
    grouped by zone, with first/last seen timestamps and reading count.
    Scoped to the current user's organisation (admins see all).
    """
    import sqlalchemy as sa

    if current_user.role == UserRole.ADMIN:
        org_filter = ""
    else:
        org_filter = f"WHERE organization_id = {current_user.organization_id}"

    rows = db.execute(
        sa.text(f"""
            SELECT
                zone,
                COUNT(*)           AS reading_count,
                MIN(timestamp)     AS first_seen,
                MAX(timestamp)     AS last_seen,
                ROUND(AVG(consumption_kwh), 2) AS avg_kwh
            FROM energy_readings
            {org_filter}
            GROUP BY zone
            ORDER BY last_seen DESC
        """)
    ).fetchall()

    return {
        "sources": [
            {
                "zone":          row[0],
                "reading_count": row[1],
                "first_seen":    str(row[2])[:19] if row[2] else None,
                "last_seen":     str(row[3])[:19] if row[3] else None,
                "avg_kwh":       row[4],
            }
            for row in rows
        ]
    }
