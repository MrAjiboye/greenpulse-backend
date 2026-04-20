"""
Auto-retraining scheduler
─────────────────────────
Runs as a FastAPI startup background task.

Triggers a retrain when:
  1. Enough new readings have arrived since the last training run
     (default: every ML_RETRAIN_EVERY_N_READINGS new records, min 100)
  2. The daily scheduled window fires (default: 02:00 UTC)

Also runs auto_insights() after every successful retrain.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("greenpulse.ml.scheduler")

_task: asyncio.Task | None = None
_last_daily_retrain_date = None       # tracks date of last daily-window retrain
_last_training_total_readings = 0     # tracks total readings count at last train


async def _scheduler_loop() -> None:
    """Main async loop -- checks every 30 minutes."""
    global _last_daily_retrain_date, _last_training_total_readings

    from app.config import settings
    from app.database import SessionLocal
    from app.ml.trainer import train, MIN_SAMPLES
    from app.ml.predictor import auto_insights
    from app.models import EnergyReading, Organization

    interval_seconds = 30 * 60  # 30-minute check cadence
    retrain_threshold: int = getattr(settings, "ML_RETRAIN_EVERY_N_READINGS", 100)
    auto_retrain: bool      = getattr(settings, "ML_AUTO_RETRAIN", True)

    logger.info(
        "ML scheduler started — checking every %d min | auto_retrain=%s | threshold=%d",
        interval_seconds // 60,
        auto_retrain,
        retrain_threshold,
    )

    while True:
        await asyncio.sleep(interval_seconds)

        if not auto_retrain:
            continue

        try:
            db = SessionLocal()
            try:
                total_readings = db.query(EnergyReading).count()
                new_since_train = total_readings - _last_training_total_readings

                now_utc = datetime.now(timezone.utc)
                today   = now_utc.date()

                # Daily window: 02:00 UTC -- only once per calendar day
                is_daily_window = (
                    now_utc.hour == 2
                    and now_utc.minute < 35
                    and _last_daily_retrain_date != today
                )

                should_retrain = (
                    new_since_train >= retrain_threshold
                    or is_daily_window
                )

                if should_retrain and total_readings >= MIN_SAMPLES:
                    logger.info(
                        "Per-org retrain triggered — total=%d new_since_last=%d daily_window=%s",
                        total_readings, new_since_train, is_daily_window,
                    )

                    # Train a separate model for each organisation that has enough data
                    orgs = db.query(Organization).all()
                    for org in orgs:
                        org_readings = (
                            db.query(EnergyReading)
                            .filter(EnergyReading.organization_id == org.id)
                            .order_by(EnergyReading.timestamp)
                            .all()
                        )
                        if len(org_readings) < MIN_SAMPLES:
                            logger.debug("Skipping org %d — only %d readings", org.id, len(org_readings))
                            continue
                        try:
                            result = train(org_readings, org_id=org.id)
                            logger.info("Org %d retrain complete: %s", org.id, result.get("status"))
                            insight_result = auto_insights(db, organization_id=org.id)
                            logger.info("Org %d auto-insights: %s", org.id, insight_result)
                        except Exception as org_err:
                            logger.error("Org %d training failed: %s", org.id, org_err)

                    _last_training_total_readings = total_readings
                    if is_daily_window:
                        _last_daily_retrain_date = today
                        try:
                            _send_daily_digests(db)
                        except Exception as digest_err:
                            logger.warning("Daily digest emails failed: %s", digest_err)

                else:
                    logger.debug(
                        "No retrain needed — total=%d new_since_last=%d",
                        total_readings, new_since_train,
                    )

            finally:
                db.close()

        except asyncio.CancelledError:
            logger.info("ML scheduler cancelled.")
            break
        except Exception as e:
            logger.error("ML scheduler error: %s", e, exc_info=True)


def _send_daily_digests(db) -> None:
    """Send daily digest emails to all managers who have email_digest_freq='daily'."""
    from app.models import Organization, User, UserRole, EnergyReading, Insight, InsightStatus
    from app.email import send_daily_digest_email
    from app.config import settings
    from datetime import timedelta, timezone
    import sqlalchemy as sa

    cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)

    # Get all active managers with daily digest enabled
    managers = db.query(User).filter(
        User.role == UserRole.MANAGER,
        User.is_active == True,
        User.email_digest_freq == "daily",
        User.organization_id != None,
    ).all()

    for manager in managers:
        try:
            org_id = manager.organization_id
            org = db.query(Organization).filter(Organization.id == org_id).first()
            org_name = org.name if org else "your organisation"

            # 7-day energy total
            rows = db.query(EnergyReading).filter(
                EnergyReading.organization_id == org_id,
                EnergyReading.timestamp >= cutoff_7d,
            ).all()
            total_kwh = sum(r.consumption_kwh for r in rows)

            # Pending insights
            pending = db.query(Insight).filter(
                Insight.organization_id == org_id,
                Insight.status == InsightStatus.PENDING,
            ).all()
            total_savings = sum(i.estimated_savings or 0 for i in pending)

            # Anomaly count reuses the predictor scan — import lazily
            from app.ml.predictor import anomaly_scan
            anomaly_count = 0
            if rows:
                try:
                    scan = anomaly_scan(rows, db=None)
                    anomaly_count = len([a for a in scan["anomalies"] if a["severity"] == "high"])
                except Exception:
                    pass

            send_daily_digest_email(
                to_email=manager.email,
                first_name=manager.first_name,
                org_name=org_name,
                total_kwh_7d=total_kwh,
                insight_count=len(pending),
                anomaly_count=anomaly_count,
                estimated_monthly_savings=total_savings,
                dashboard_url=f"{settings.FRONTEND_URL}/dashboard",
            )
            logger.info("Daily digest sent to %s", manager.email)
        except Exception as e:
            logger.warning("Daily digest to %s failed: %s", manager.email, e)


def start_scheduler() -> None:
    """Start the background scheduler loop. Call once on app startup."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_scheduler_loop())
        logger.info("ML scheduler task created.")


def stop_scheduler() -> None:
    """Cancel the scheduler gracefully. Call on app shutdown."""
    global _task
    if _task and not _task.done():
        _task.cancel()
        logger.info("ML scheduler task cancelled.")
