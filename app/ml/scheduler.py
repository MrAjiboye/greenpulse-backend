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
_last_daily_retrain_date = None  # tracks date of last daily-window retrain


async def _scheduler_loop() -> None:
    """Main async loop -- checks every 30 minutes."""
    global _last_daily_retrain_date

    from app.config import settings
    from app.database import SessionLocal
    from app.ml.trainer import load_bundle, train
    from app.ml.predictor import auto_insights
    from app.models import EnergyReading

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
                bundle = load_bundle()

                n_at_last_train = bundle["n_samples"] if bundle else 0
                new_since_train = total_readings - n_at_last_train

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

                if should_retrain and total_readings >= 10:
                    logger.info(
                        "Retraining triggered — total=%d new_since_last=%d daily_window=%s",
                        total_readings, new_since_train, is_daily_window,
                    )
                    all_readings = db.query(EnergyReading).order_by(EnergyReading.timestamp).all()
                    result = train(all_readings)
                    logger.info("Auto-retrain complete: %s", result)
                    if is_daily_window:
                        _last_daily_retrain_date = today

                    # Generate fresh insights after retrain
                    insight_result = auto_insights(db)
                    logger.info("Auto-insights: %s", insight_result)

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
