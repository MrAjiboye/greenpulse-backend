from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from app.database import get_db, naive_utc
from app.models import User, UserRole, EnergyReading, WasteLog, Insight, InsightStatus
from app.schemas import DashboardStats
from app.auth import get_current_active_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _org_q(query, current_user, model):
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get dashboard statistics"""

    # Current energy consumption (latest reading)
    latest_energy = _org_q(
        db.query(EnergyReading).order_by(EnergyReading.timestamp.desc()),
        current_user, EnergyReading
    ).first()
    current_energy = latest_energy.consumption_kwh if latest_energy else 0.0

    # Total savings from applied insights
    applied_insights = _org_q(
        db.query(Insight).filter(Insight.status == InsightStatus.APPLIED),
        current_user, Insight
    ).all()
    total_savings = sum(insight.estimated_savings for insight in applied_insights)

    # Count of applied insights
    insights_applied = len(applied_insights)

    # Carbon reduced: savings (£) ÷ £0.28/kWh × 0.000233 tCO2/kWh (UK grid average)
    kwh_saved = (total_savings / 0.28) if total_savings > 0 else 0
    carbon_reduced = kwh_saved * 0.000233

    return DashboardStats(
        current_energy_kwh=current_energy,
        total_savings=total_savings,
        insights_applied=insights_applied,
        carbon_reduced_tons=carbon_reduced
    )

@router.get("/recent-energy")
def get_recent_energy(
    limit: int = 24,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get recent energy readings for charts"""
    readings = _org_q(
        db.query(EnergyReading).order_by(EnergyReading.timestamp.desc()),
        current_user, EnergyReading
    ).limit(limit).all()

    return {
        "readings": [
            {
                "timestamp": r.timestamp.isoformat(),
                "consumption_kwh": r.consumption_kwh,
                "zone": r.zone
            }
            for r in reversed(readings)
        ]
    }

@router.get("/waste-breakdown")
def get_waste_breakdown(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get waste breakdown by stream"""
    cutoff_date = naive_utc(datetime.now(timezone.utc) - timedelta(days=days))

    base_q = db.query(
        WasteLog.stream,
        func.sum(WasteLog.weight_kg).label('total_weight')
    ).filter(WasteLog.timestamp >= cutoff_date)

    if current_user.role != UserRole.ADMIN:
        base_q = base_q.filter(WasteLog.organization_id == current_user.organization_id)

    breakdown = base_q.group_by(WasteLog.stream).all()

    return {
        "breakdown": [
            {"stream": stream, "weight_kg": float(total)}
            for stream, total in breakdown
        ]
    }
