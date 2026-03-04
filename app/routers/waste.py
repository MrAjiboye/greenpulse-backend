from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from app.database import get_db, naive_utc
from app.models import User, WasteLog, UserRole
from app.schemas import WasteLogCreate, WasteLogResponse
from app.auth import get_current_active_user, require_role

router = APIRouter(prefix="/waste", tags=["Waste Management"])


def _org_q(query, current_user, model):
    """Restrict query to the current user's organisation (admins see all)."""
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


@router.get("/breakdown")
def get_waste_breakdown(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get waste breakdown by stream with diversion rate and carbon offset"""
    cutoff_date = naive_utc(datetime.now(timezone.utc) - timedelta(days=days))

    base_q = db.query(
        WasteLog.stream,
        func.sum(WasteLog.weight_kg).label("total_weight"),
        func.count(WasteLog.id).label("count"),
    ).filter(WasteLog.timestamp >= cutoff_date)

    if current_user.role != UserRole.ADMIN:
        base_q = base_q.filter(WasteLog.organization_id == current_user.organization_id)

    breakdown = base_q.group_by(WasteLog.stream).all()

    total_weight = float(sum(item.total_weight for item in breakdown))

    # Streams that go to landfill
    landfill_keywords = {"landfill", "general", "residual", "mixed"}
    # Streams that are recycled/composted (for carbon offset calc)
    recycled_keywords = {"recycling", "compost", "organic", "recyclable"}

    landfill_kg = sum(
        float(item.total_weight) for item in breakdown
        if item.stream.lower() in landfill_keywords
    )
    recycled_kg = sum(
        float(item.total_weight) for item in breakdown
        if item.stream.lower() in recycled_keywords
    )

    diversion_rate = round((1 - landfill_kg / total_weight) * 100, 1) if total_weight > 0 else 0.0
    # Carbon offset: recycled/composted waste avoids ~0.0008 MTCO2e per kg
    carbon_offset = round(recycled_kg * 0.0008, 2)

    # Compare vs previous period of same length
    prev_start = naive_utc(datetime.now(timezone.utc) - timedelta(days=days * 2))
    prev_end   = naive_utc(datetime.now(timezone.utc) - timedelta(days=days))
    prev_q = db.query(func.sum(WasteLog.weight_kg)).filter(
        WasteLog.timestamp >= prev_start,
        WasteLog.timestamp < prev_end,
    )
    if current_user.role != UserRole.ADMIN:
        prev_q = prev_q.filter(WasteLog.organization_id == current_user.organization_id)
    prev_total = prev_q.scalar() or 0.0
    vs_last_period = round(total_weight - float(prev_total), 1)

    streams = [
        {
            "stream": item.stream,
            "total_kg": float(item.total_weight),
            "weight_kg": float(item.total_weight),   # alias for compat
            "percentage": round(
                (item.total_weight / total_weight * 100) if total_weight > 0 else 0, 1
            ),
            "count": item.count,
        }
        for item in breakdown
    ]

    return {
        "streams": streams,
        "breakdown": [
            {"stream": s["stream"], "weight_kg": s["weight_kg"], "percentage": s["percentage"], "count": s["count"]}
            for s in streams
        ],
        "total_kg": total_weight,
        "total_weight_kg": total_weight,
        "diversion_rate": diversion_rate,
        "carbon_offset_mtco2e": carbon_offset,
        "vs_last_month_kg": vs_last_period,
    }


@router.get("/logs")
def get_waste_logs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get paginated waste logs"""
    query = _org_q(
        db.query(WasteLog).order_by(WasteLog.timestamp.desc()),
        current_user, WasteLog
    )
    total = query.count()
    logs = query.offset(offset).limit(limit).all()

    return {
        "items": [WasteLogResponse.model_validate(log) for log in logs],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


@router.post("/logs", response_model=WasteLogResponse)
def create_waste_log(
    log_data: WasteLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Log new waste entry"""
    new_log = WasteLog(**log_data.model_dump())
    db.add(new_log)
    db.commit()
    db.refresh(new_log)
    return new_log


@router.get("/contamination-alerts")
def get_contamination_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get unresolved contamination alerts"""
    alerts = _org_q(
        db.query(WasteLog).filter(
            WasteLog.contamination_detected == True,
            WasteLog.resolved == False,
        ).order_by(WasteLog.timestamp.desc()).limit(10),
        current_user, WasteLog
    ).all()

    return {
        "alerts": [
            {
                "id": alert.id,
                "timestamp": alert.timestamp.isoformat(),
                "stream": alert.stream,
                "location": alert.location,
                "weight_kg": alert.weight_kg,
                "resolved": alert.resolved,
            }
            for alert in alerts
        ]
    }


@router.patch("/contamination-alerts/{log_id}/resolve")
def resolve_contamination_alert(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Mark a contamination alert as resolved"""
    log = _org_q(
        db.query(WasteLog).filter(
            WasteLog.id == log_id,
            WasteLog.contamination_detected == True,
        ),
        current_user, WasteLog
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Alert not found")
    log.resolved = True
    db.commit()
    return {"message": "Alert resolved", "id": log_id}
