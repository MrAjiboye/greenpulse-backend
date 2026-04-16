"""
Demo Data Refresher
===================
Clears and re-seeds all demo data for The Green Plate so that the
dashboard shows current, realistic data anchored to TODAY.

Run via Railway shell:
    python refresh_demo_data.py

Or locally against production DB:
    DATABASE_URL=<your-railway-url> python refresh_demo_data.py
"""

import random
from datetime import datetime, timedelta, timezone

random.seed(42)

from app.database import SessionLocal
from app.models import (
    Organization, User,
    EnergyReading, WasteLog,
    Insight, InsightCategory, InsightStatus,
    Notification, NotificationType,
    Goal, GoalCategory,
)

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ── Zones — expanded to look like a multi-system commercial facility ──────────
ZONES = {
    "Main Kitchen":       420,   # heavy appliances
    "Dining Room":        180,   # lighting, heating, POS
    "Bar & Cellar":       140,   # fridges, lighting
    "Back Office":         60,   # computers, lighting
    "HVAC Unit A":        310,   # central heating/cooling
    "Cold Storage":       220,   # walk-in fridge/freezer
    "Event Space":        150,   # banqueting/function room
    "Exterior Lighting":   45,   # car park and signage
}

def _time_factor(hour: int, is_weekend: bool) -> float:
    if hour < 6:
        return 0.28
    if hour < 9:
        return 0.55
    if 11 <= hour <= 14:
        return 1.00
    if 14 < hour < 17:
        return 0.62
    if 17 <= hour <= 22:
        return 1.00 if not is_weekend else 1.22
    return 0.38


def run():
    db = SessionLocal()
    now = _now()

    try:
        org = db.query(Organization).filter(Organization.name == "The Green Plate").first()
        if not org:
            print("ERROR: Demo org not found. Run seed_demo_restaurant.py first.")
            return

        print(f"Refreshing data for: {org.name} (id={org.id})")

        # ── 1. Clear existing data ────────────────────────────────────────────
        deleted_e = db.query(EnergyReading).filter(EnergyReading.organization_id == org.id).delete()
        deleted_w = db.query(WasteLog).filter(WasteLog.organization_id == org.id).delete()
        deleted_i = db.query(Insight).filter(Insight.organization_id == org.id).delete()
        deleted_n = db.query(Notification).filter(Notification.organization_id == org.id).delete()
        db.query(Goal).filter(Goal.organization_id == org.id).delete()
        db.commit()
        print(f"  Cleared: {deleted_e} energy readings, {deleted_w} waste logs, "
              f"{deleted_i} insights, {deleted_n} notifications")

        # ── 2. Energy readings — 90 days × hourly × 8 zones ──────────────────
        readings = []
        for days_ago in range(90, 0, -1):
            ts_day = now - timedelta(days=days_ago)
            is_weekend = ts_day.weekday() >= 5
            for hour in range(24):
                ts = ts_day.replace(hour=hour, minute=0, second=0, microsecond=0)
                tf = _time_factor(hour, is_weekend)
                for zone, base in ZONES.items():
                    kwh = base * tf * (1 + random.uniform(-0.10, 0.10))
                    # Occasional anomaly spike (~2% chance)
                    if random.random() < 0.02:
                        kwh *= random.uniform(1.8, 2.6)
                    readings.append(EnergyReading(
                        timestamp=ts,
                        consumption_kwh=round(kwh, 2),
                        zone=zone,
                        facility_id=1,
                        organization_id=org.id,
                    ))

        # ── Today: readings up to current hour ────────────────────────────────
        is_weekend_today = now.weekday() >= 5
        for hour in range(now.hour + 1):
            ts = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            tf = _time_factor(hour, is_weekend_today)
            for zone, base in ZONES.items():
                kwh = base * tf * (1 + random.uniform(-0.08, 0.08))
                readings.append(EnergyReading(
                    timestamp=ts,
                    consumption_kwh=round(kwh, 2),
                    zone=zone,
                    facility_id=1,
                    organization_id=org.id,
                ))

        # ── Inject a live anomaly: HVAC spike 2 hours ago ─────────────────────
        spike_hour = max(0, now.hour - 2)
        spike_ts = now.replace(hour=spike_hour, minute=0, second=0, microsecond=0)
        readings.append(EnergyReading(
            timestamp=spike_ts,
            consumption_kwh=round(ZONES["HVAC Unit A"] * 2.7, 2),
            zone="HVAC Unit A",
            facility_id=1,
            organization_id=org.id,
        ))

        db.bulk_save_objects(readings)
        db.commit()
        print(f"  Created {len(readings)} energy readings (90 days + today, 8 zones)")

        # ── 3. Waste logs — 90 days ───────────────────────────────────────────
        streams   = ["Recycling", "Compost", "Landfill"]
        locations = ["Main Kitchen", "Bar Area", "Dining Room", "Event Space"]
        waste_logs = []
        for days_ago in range(90, 0, -1):
            for _ in range(random.randint(2, 6)):
                ts = now - timedelta(days=days_ago, hours=random.randint(8, 22))
                stream = random.choice(streams)
                weight = (
                    random.uniform(10, 60) if stream == "Recycling"
                    else random.uniform(20, 80) if stream == "Compost"
                    else random.uniform(15, 50)
                )
                waste_logs.append(WasteLog(
                    timestamp=ts.replace(tzinfo=None),
                    stream=stream,
                    weight_kg=round(weight, 2),
                    location=random.choice(locations),
                    contamination_detected=random.random() < 0.06,
                    facility_id=1,
                    organization_id=org.id,
                ))
        db.bulk_save_objects(waste_logs)
        db.commit()
        print(f"  Created {len(waste_logs)} waste logs")

        # ── 4. AI Insights ────────────────────────────────────────────────────
        insights = [
            dict(
                title="HVAC Unit A running 2.7x above baseline",
                description="HVAC Unit A spiked to 837 kWh at 06:00 today — 2.7x the expected load for that hour. This is consistent with a stuck relay or a faulty thermostat. Immediate investigation recommended. Estimated ongoing cost if unresolved: £340/month.",
                category=InsightCategory.ENERGY, confidence_score=0.94,
                estimated_savings=340.0, status=InsightStatus.PENDING,
                created_at=now - timedelta(hours=1),
            ),
            dict(
                title="Cold Storage defrost cycle misaligned with off-peak tariff",
                description="The walk-in cold storage defrost cycle runs at 14:00 daily — mid-peak tariff window. Shifting it to 02:00 would reduce electricity cost for that cycle by approximately £120/month with no operational impact.",
                category=InsightCategory.ENERGY, confidence_score=0.91,
                estimated_savings=120.0, status=InsightStatus.PENDING,
                created_at=now - timedelta(hours=6),
            ),
            dict(
                title="Event Space consuming full load on non-event days",
                description="Event Space energy use is consistent 7 days a week despite the space being used only 3–4 days. Lighting and HVAC are running at full load on dark days. Scheduling could save approximately £180/month.",
                category=InsightCategory.ENERGY, confidence_score=0.88,
                estimated_savings=180.0, status=InsightStatus.PENDING,
                created_at=now - timedelta(days=1),
            ),
            dict(
                title="Overnight kitchen extraction fan running unnecessarily",
                description="Main Kitchen extraction fans are active from midnight to 06:00 with no cooking activity. Auto-shutoff after service ends could save approximately £210/month.",
                category=InsightCategory.ENERGY, confidence_score=0.92,
                estimated_savings=210.0, status=InsightStatus.PENDING,
                created_at=now - timedelta(days=2),
            ),
            dict(
                title="Compost diversion rate below 40%",
                description="Only 37% of kitchen waste is reaching compost. Industry benchmark for similar operations is 58–65%. Adding a dedicated compost bin at the main prep station could recover approximately £95/month in waste collection costs.",
                category=InsightCategory.WASTE, confidence_score=0.83,
                estimated_savings=95.0, status=InsightStatus.PENDING,
                created_at=now - timedelta(days=3),
            ),
            dict(
                title="Bar & Cellar fridges efficiency restored",
                description="Following the coil clean last week, Bar & Cellar fridge draw is back within normal range. This insight has been applied — estimated £75/month saving now active.",
                category=InsightCategory.OPERATIONS, confidence_score=0.89,
                estimated_savings=75.0, status=InsightStatus.APPLIED,
                created_at=now - timedelta(days=7),
            ),
        ]
        for data in insights:
            db.add(Insight(organization_id=org.id, facility_id=1, **data))
        db.commit()
        print(f"  Created {len(insights)} AI insights")

        # ── 5. Notifications ──────────────────────────────────────────────────
        notifications = [
            dict(
                title="HVAC Unit A anomaly detected",
                message="HVAC Unit A spiked to 2.7x expected load at 06:00 today. A new insight has been generated — review recommended.",
                type=NotificationType.ALERT, read=False,
                created_at=now - timedelta(hours=1),
            ),
            dict(
                title="New insight: Event Space scheduling",
                message="AI detected Event Space running at full load on non-event days. Estimated saving: £180/month.",
                type=NotificationType.INSIGHT, read=False,
                created_at=now - timedelta(hours=5),
            ),
            dict(
                title="Weekly energy summary",
                message=f"This week The Green Plate used 28,140 kWh across all zones. Down 8% on last week. Cold Storage and HVAC remain the largest consumers.",
                type=NotificationType.INSIGHT, read=False,
                created_at=now - timedelta(days=1),
            ),
            dict(
                title="Cold Storage insight generated",
                message="GreenPulse identified a tariff misalignment in your cold storage defrost schedule. Potential saving: £120/month.",
                type=NotificationType.INSIGHT, read=True,
                created_at=now - timedelta(days=2),
            ),
            dict(
                title="Bar & Cellar fridge insight applied",
                message="Coil clean completed. Fridge efficiency is back within normal range. Saving of £75/month now active.",
                type=NotificationType.SUCCESS, read=True,
                created_at=now - timedelta(days=7),
            ),
            dict(
                title="IoT data ingestion active",
                message="8 zones are actively reporting. Last reading received 0 minutes ago.",
                type=NotificationType.SYSTEM, read=True,
                created_at=now - timedelta(hours=3),
            ),
        ]
        for data in notifications:
            db.add(Notification(organization_id=org.id, **data))
        db.commit()
        print(f"  Created {len(notifications)} notifications")

        # ── 6. Goals ─────────────────────────────────────────────────────────
        demo_user = db.query(User).filter(User.email == "demo@thegreenplate.co.uk").first()
        manager_id = demo_user.id if demo_user else None
        year = now.year
        goals = [
            dict(
                name="Reduce total energy 15% by Q3",
                category=GoalCategory.ENERGY,
                target_value=15.0, unit="%",
                period_start=datetime(year, 4, 1, 0, 0, 0),
                period_end=datetime(year, 9, 30, 23, 59, 59),
                created_by=manager_id,
            ),
            dict(
                name="Cut landfill waste to under 25%",
                category=GoalCategory.WASTE,
                target_value=25.0, unit="%",
                period_start=datetime(year, 1, 1, 0, 0, 0),
                period_end=datetime(year, 12, 31, 23, 59, 59),
                created_by=manager_id,
            ),
            dict(
                name="Reduce carbon footprint by 20%",
                category=GoalCategory.CARBON,
                target_value=20.0, unit="%",
                period_start=datetime(year, 1, 1, 0, 0, 0),
                period_end=datetime(year, 12, 31, 23, 59, 59),
                created_by=manager_id,
            ),
        ]
        for data in goals:
            db.add(Goal(organization_id=org.id, **data))
        db.commit()
        print(f"  Created {len(goals)} goals")

        print("\n" + "=" * 60)
        print("Demo data refresh complete!")
        print("=" * 60)
        print(f"  Org:      The Green Plate (id={org.id})")
        print(f"  Zones:    {len(ZONES)} (energy + IoT)")
        print(f"  Readings: {len(readings)} (90 days + today)")
        print(f"  Insights: {len(insights)} ({sum(1 for i in insights if i['status'] == InsightStatus.PENDING)} pending)")
        print(f"  Live alert: HVAC Unit A spike 2h ago")
        print("=" * 60)
        print("Login: demo@thegreenplate.co.uk / DemoPass2026!")
        print("=" * 60)

    except Exception as e:
        print(f"ERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run()
