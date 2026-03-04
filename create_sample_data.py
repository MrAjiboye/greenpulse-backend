from datetime import datetime, timedelta, timezone
import random

def _now():
    """Naive UTC datetime (SQLite-compatible, deprecation-free)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
from app.database import SessionLocal, engine, Base
from app.models import User, EnergyReading, WasteLog, Insight, InsightCategory, InsightStatus, Notification, NotificationType
from app.auth import get_password_hash

# ── Seed configuration ──────────────────────────────────────────────────────
random.seed(42)

# Zone base loads (kWh)
ZONE_BASES = {
    "Zone A":           500,
    "Zone B":           380,
    "Production Floor": 650,
    "Office":           220,
}

def _time_factor(hour: int, is_weekend: bool) -> float:
    """Realistic time-of-day multiplier with weekend suppression."""
    if is_weekend:
        return 0.40
    if hour < 6:
        return 0.65
    if hour < 9:
        return 0.85
    if hour < 18:
        return 1.00
    if hour < 21:
        return 0.80
    return 0.65


def create_sample_data():
    """Create sample data for testing"""

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # ── 1. Demo users ──────────────────────────────────────────────────
        print("Creating demo users...")
        demo_users = [
            {
                "email": "admin@greenpulseanalytics.com",
                "first_name": "Sam", "last_name": "Rivera",
                "role": "admin", "job_title": "Sustainability Director",
                "department": "Operations",
            },
            {
                "email": "alex@greenpulseanalytics.com",
                "first_name": "Alex", "last_name": "Johnson",
                "role": "manager", "job_title": "Facilities Manager",
                "department": "Facilities",
            },
            {
                "email": "viewer@greenpulseanalytics.com",
                "first_name": "Taylor", "last_name": "Chen",
                "role": "viewer", "job_title": "Sustainability Analyst",
                "department": "Sustainability",
            },
        ]
        for u in demo_users:
            existing = db.query(User).filter(User.email == u["email"]).first()
            if not existing:
                db.add(User(
                    email=u["email"],
                    hashed_password=get_password_hash("password123"),
                    first_name=u["first_name"], last_name=u["last_name"],
                    role=u["role"], job_title=u["job_title"],
                    department=u["department"], is_active=True,
                ))
                print(f"  Created {u['role']}: {u['email']}")
            else:
                print(f"  Already exists: {u['email']}")
        db.commit()

        # ── 2. Energy readings — 30 days × 24 h × 4 zones ─────────────────
        print("\nCreating energy readings...")
        existing_readings = db.query(EnergyReading).count()
        if existing_readings == 0:
            now = _now()
            count = 0
            for days_ago in range(30):
                ts_day = now - timedelta(days=days_ago)
                is_weekend = ts_day.weekday() >= 5
                for hour in range(24):
                    timestamp = ts_day.replace(hour=hour, minute=0, second=0, microsecond=0)
                    tf = _time_factor(hour, is_weekend)
                    for zone, base in ZONE_BASES.items():
                        consumption = base * tf
                        consumption *= (1 + random.uniform(-0.08, 0.08))  # ±8% variation
                        if random.random() < 0.04:                         # 4% anomaly spike
                            consumption *= random.uniform(2.0, 3.0)
                        db.add(EnergyReading(
                            timestamp=timestamp,
                            consumption_kwh=round(consumption, 2),
                            zone=zone,
                            facility_id=1,
                        ))
                        count += 1
            db.commit()
            print(f"  Created {count} energy readings (30 days × 24 h × 4 zones)")
        else:
            print(f"  Energy readings already exist ({existing_readings} records)")

        # ── 3. Waste logs — 30 days ────────────────────────────────────────
        print("\nCreating waste logs...")
        existing_waste = db.query(WasteLog).count()
        if existing_waste == 0:
            streams = ["Recycling", "Compost", "Landfill"]
            locations = ["Main Kitchen", "Loading Dock A", "Cafeteria", "Production Area"]
            for days_ago in range(30):
                for _ in range(random.randint(2, 4)):
                    timestamp = _now() - timedelta(days=days_ago, hours=random.randint(0, 23))
                    stream = random.choice(streams)
                    weight = (
                        random.uniform(50, 200) if stream == "Recycling"
                        else random.uniform(30, 120) if stream == "Compost"
                        else random.uniform(40, 150)
                    )
                    db.add(WasteLog(
                        timestamp=timestamp,
                        stream=stream,
                        weight_kg=round(weight, 2),
                        location=random.choice(locations),
                        contamination_detected=random.random() < 0.05,
                        facility_id=1,
                    ))
            db.commit()
            print(f"  Created {db.query(WasteLog).count()} waste logs")
        else:
            print(f"  Waste logs already exist ({existing_waste} records)")

        # ── 4. AI insights — 10 ────────────────────────────────────────────
        print("\nCreating AI insights...")
        existing_insights = db.query(Insight).count()
        if existing_insights == 0:
            now = _now()
            insights_data = [
                {
                    "title": "Optimize HVAC Schedule for Zone B",
                    "description": "HVAC units in Zone B are running at 80% capacity during non-operational hours (8 PM – 6 AM). Adjusting to standby mode will significantly reduce energy waste.",
                    "category": InsightCategory.ENERGY, "confidence_score": 0.92,
                    "estimated_savings": 2450.0, "status": InsightStatus.APPLIED,
                    "created_at": now - timedelta(days=28),
                },
                {
                    "title": "Reduce Contamination in Cafeteria Recycling",
                    "description": "Visual sensors detected high rates of liquid contamination in paper recycling bins near the cafeteria exit. Improved signage and a liquids disposal station could reduce rejection fees by 15%.",
                    "category": InsightCategory.WASTE, "confidence_score": 0.88,
                    "estimated_savings": 850.0, "status": InsightStatus.DISMISSED,
                    "created_at": now - timedelta(days=21),
                },
                {
                    "title": "Consolidate Pickup Schedules",
                    "description": "Waste pickup data shows bins are only 40% full on Tuesdays and Thursdays. Switching to a Mon-Wed-Fri schedule will reduce haulage costs and lower fleet emissions.",
                    "category": InsightCategory.OPERATIONS, "confidence_score": 0.75,
                    "estimated_savings": 1200.0, "status": InsightStatus.PENDING,
                    "created_at": now - timedelta(days=14),
                },
                {
                    "title": "LED Retrofit Phase 2",
                    "description": "Parking structure lighting consumes 12,500 kWh monthly. Retrofitting with LED fixtures would reduce consumption by 60% with an 18-month ROI.",
                    "category": InsightCategory.ENERGY, "confidence_score": 0.95,
                    "estimated_savings": 1100.0, "status": InsightStatus.PENDING,
                    "created_at": now - timedelta(days=10),
                },
                {
                    "title": "Preventive Maintenance Alert — Chiller 03",
                    "description": "Chiller Unit 03 is showing 15% efficiency degradation. Scheduled maintenance now will prevent costly emergency repairs and restore optimal performance.",
                    "category": InsightCategory.OPERATIONS, "confidence_score": 0.89,
                    "estimated_savings": 5200.0, "status": InsightStatus.APPLIED,
                    "created_at": now - timedelta(days=7),
                },
                {
                    "title": "Solar Panel Monitoring Gap",
                    "description": "Three roof-mounted solar panels in Zone A have not reported generation data in 72 hours. A sensor fault may be masking underperformance worth up to £680/month in unrealised generation.",
                    "category": InsightCategory.ENERGY, "confidence_score": 0.83,
                    "estimated_savings": 680.0, "status": InsightStatus.PENDING,
                    "created_at": now - timedelta(days=5),
                },
                {
                    "title": "Water Heating Off-Peak Shift",
                    "description": "Hot water demand peaks at 7–9 AM during peak-rate tariff hours. Pre-heating the tank at 3–5 AM (off-peak) could save £920/month on electricity bills.",
                    "category": InsightCategory.ENERGY, "confidence_score": 0.87,
                    "estimated_savings": 920.0, "status": InsightStatus.PENDING,
                    "created_at": now - timedelta(days=4),
                },
                {
                    "title": "Compost Bin Placement Optimisation",
                    "description": "Compost bins are currently located only in the kitchen area. Adding bins near the Production Floor could capture an additional 200 kg/month of organic waste and improve the diversion rate by 8%.",
                    "category": InsightCategory.WASTE, "confidence_score": 0.79,
                    "estimated_savings": 340.0, "status": InsightStatus.APPLIED,
                    "created_at": now - timedelta(days=3),
                },
                {
                    "title": "Peak Demand Charge Reduction",
                    "description": "Analysis of your tariff structure shows that 12% of your electricity bill comes from peak-demand charges triggered by the Production Floor. Staggering equipment start-up sequences could save £3,100/month.",
                    "category": InsightCategory.ENERGY, "confidence_score": 0.91,
                    "estimated_savings": 3100.0, "status": InsightStatus.PENDING,
                    "created_at": now - timedelta(days=2),
                },
                {
                    "title": "Fleet Idle Reduction Protocol",
                    "description": "Delivery vehicles idling at Loading Dock A for an average of 22 minutes per visit account for 8% of site transport emissions. A structured idle-reduction protocol could save £1,750/month in fuel and carbon costs.",
                    "category": InsightCategory.OPERATIONS, "confidence_score": 0.84,
                    "estimated_savings": 1750.0, "status": InsightStatus.PENDING,
                    "created_at": now - timedelta(days=1),
                },
            ]
            for data in insights_data:
                db.add(Insight(**data))
            db.commit()
            print(f"  Created {len(insights_data)} AI insights")
        else:
            print(f"  AI insights already exist ({existing_insights} records)")

        # ── 5. Notifications — 13 ─────────────────────────────────────────
        print("\nCreating notifications...")
        existing_notifications = db.query(Notification).count()
        if existing_notifications == 0:
            now = _now()
            notifications_data = [
                {"title": "Contamination Alert — Loading Dock A",
                 "message": "High contamination detected in recycling stream at Loading Dock A. Immediate inspection recommended.",
                 "type": NotificationType.ALERT, "read": False, "created_at": now - timedelta(hours=1)},
                {"title": "Energy Spike Detected — Zone B",
                 "message": "Zone B energy consumption exceeded the 20% anomaly threshold at 02:00 AM. Review HVAC schedule.",
                 "type": NotificationType.WARNING, "read": False, "created_at": now - timedelta(hours=3)},
                {"title": "New AI Insight Available",
                 "message": "A new high-confidence insight (95%) has been generated for LED Retrofit Phase 2. Estimated savings: £1,100/mo.",
                 "type": NotificationType.INSIGHT, "read": False, "created_at": now - timedelta(hours=6)},
                {"title": "Insight Applied — HVAC Optimisation",
                 "message": "The 'Optimize HVAC Schedule for Zone B' insight was successfully applied. Savings tracking begins this cycle.",
                 "type": NotificationType.SUCCESS, "read": True, "created_at": now - timedelta(hours=12)},
                {"title": "System Maintenance Scheduled",
                 "message": "GreenPulse backend maintenance is scheduled for Sunday 03:00–04:00 AM UTC. Expect brief downtime.",
                 "type": NotificationType.SYSTEM, "read": True, "created_at": now - timedelta(days=1)},
                {"title": "Weekly Waste Report Ready",
                 "message": "Your weekly waste summary is ready. Total waste logged: 1,240 kg across 3 streams.",
                 "type": NotificationType.INSIGHT, "read": False, "created_at": now - timedelta(days=1, hours=4)},
                {"title": "Chiller 03 Maintenance Due",
                 "message": "Preventive maintenance for Chiller Unit 03 is overdue by 3 days. Schedule service to avoid efficiency loss.",
                 "type": NotificationType.WARNING, "read": False, "created_at": now - timedelta(days=2)},
                {"title": "New User Registered",
                 "message": "A new VIEWER account was created for taylor@greenpulseanalytics.com. Review access in Admin Panel.",
                 "type": NotificationType.SYSTEM, "read": True, "created_at": now - timedelta(days=2, hours=6)},
                {"title": "Peak Demand Alert — Production Floor",
                 "message": "Production Floor demand reached 820 kW at 09:15 AM — 18% above baseline. Consider staggering equipment start-up times.",
                 "type": NotificationType.ALERT, "read": False, "created_at": now - timedelta(days=3)},
                {"title": "Solar Panel Sensor Fault Detected",
                 "message": "Three solar panels in Zone A have not reported generation data for 72 hours. A maintenance check is recommended.",
                 "type": NotificationType.WARNING, "read": False, "created_at": now - timedelta(days=4)},
                {"title": "Insight Applied — Compost Bin Placement",
                 "message": "Compost bins have been added to the Production Floor. Diversion rate improvement will be tracked over the next 30 days.",
                 "type": NotificationType.SUCCESS, "read": True, "created_at": now - timedelta(days=5)},
                {"title": "Monthly Energy Report Available",
                 "message": "Your January energy report is ready. Total consumption: 43,200 kWh — down 7% vs December.",
                 "type": NotificationType.INSIGHT, "read": True, "created_at": now - timedelta(days=6)},
                {"title": "Recycling Diversion Rate Improved",
                 "message": "This week's recycling diversion rate reached 68% — a new record. Keep up the great work!",
                 "type": NotificationType.SUCCESS, "read": True, "created_at": now - timedelta(days=7)},
            ]
            for data in notifications_data:
                db.add(Notification(**data))
            db.commit()
            print(f"  Created {len(notifications_data)} notifications")
        else:
            print(f"  Notifications already exist ({existing_notifications} records)")

        print("\n" + "=" * 60)
        print("Sample data creation complete!")
        print("=" * 60)
        print("DEMO CREDENTIALS (password: password123)")
        print("  ADMIN   admin@greenpulseanalytics.com")
        print("  MANAGER alex@greenpulseanalytics.com")
        print("  VIEWER  viewer@greenpulseanalytics.com")
        print("=" * 60)

    except Exception as e:
        print(f"ERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    create_sample_data()
