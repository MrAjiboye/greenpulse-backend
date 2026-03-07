import calendar
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone

from app.database import get_db, naive_utc
from app.models import User, EnergyReading, WasteLog, UserRole
from app.auth import get_current_active_user

router = APIRouter(prefix="/carbon", tags=["Carbon"])

CARBON_KWH    = 0.000233   # tCO2 per kWh (UK grid intensity)
CARBON_WASTE  = 0.0008     # MTCO2e per kg recycled / composted

RECYCLABLE_STREAMS = {"recycling", "compost", "recyclable", "compostable"}


def _org_q(query, current_user, model):
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


def _month_offset(year: int, month: int, delta: int):
    """Return (year, month) shifted by delta months."""
    total = (year * 12 + month - 1) + delta
    return total // 12, total % 12 + 1


def _month_bounds(year: int, month: int):
    """Return (start, end) as naive-UTC datetimes for a calendar month."""
    _, last_day = calendar.monthrange(year, month)
    ny, nm = _month_offset(year, month, 1)
    start = naive_utc(datetime(year, month, 1, tzinfo=timezone.utc))
    end   = naive_utc(datetime(ny, nm, 1, tzinfo=timezone.utc))
    return start, end


def _energy_tco2(db, current_user, start, end) -> float:
    result = _org_q(
        db.query(func.sum(EnergyReading.consumption_kwh))
        .filter(EnergyReading.timestamp >= start, EnergyReading.timestamp < end),
        current_user, EnergyReading
    ).scalar() or 0.0
    return round(result * CARBON_KWH, 4)


def _waste_offset_tco2(db, current_user, start, end) -> float:
    rows = _org_q(
        db.query(WasteLog.stream, func.sum(WasteLog.weight_kg))
        .filter(WasteLog.timestamp >= start, WasteLog.timestamp < end)
        .group_by(WasteLog.stream),
        current_user, WasteLog
    ).all()
    offset = sum(
        kg for stream, kg in rows
        if stream and stream.lower() in RECYCLABLE_STREAMS and kg
    )
    return round(offset * CARBON_WASTE, 4)


@router.get("/summary")
def get_carbon_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    cy, cm = now.year, now.month

    # This month
    this_start, this_end = _month_bounds(cy, cm)
    this_energy = _energy_tco2(db, current_user, this_start, this_end)
    this_offset = _waste_offset_tco2(db, current_user, this_start, this_end)

    # Last month
    ly, lm = _month_offset(cy, cm, -1)
    last_start, last_end = _month_bounds(ly, lm)
    last_energy = _energy_tco2(db, current_user, last_start, last_end)

    # YTD (Jan 1 → now)
    ytd_start = naive_utc(datetime(cy, 1, 1, tzinfo=timezone.utc))
    ytd_end   = naive_utc(now)
    ytd_energy = _energy_tco2(db, current_user, ytd_start, ytd_end)
    ytd_offset = _waste_offset_tco2(db, current_user, ytd_start, ytd_end)

    change_pct = round(
        ((this_energy - last_energy) / last_energy * 100) if last_energy else 0.0, 1
    )

    return {
        "this_month_tco2":       this_energy,
        "last_month_tco2":       last_energy,
        "ytd_tco2":              ytd_energy,
        "recycling_offset_tco2": ytd_offset,
        "net_tco2":              round(ytd_energy - ytd_offset, 4),
        "change_pct":            change_pct,
    }


@router.get("/trends")
def get_carbon_trends(
    months: int = 12,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    result = []
    for i in range(months - 1, -1, -1):
        ry, rm = _month_offset(now.year, now.month, -i)
        start, end = _month_bounds(ry, rm)
        energy = _energy_tco2(db, current_user, start, end)
        offset = _waste_offset_tco2(db, current_user, start, end)
        result.append({
            "month":       f"{ry:04d}-{rm:02d}",
            "label":       datetime(ry, rm, 1).strftime("%b %Y"),
            "energy_tco2": energy,
            "offset_tco2": offset,
            "net_tco2":    round(energy - offset, 4),
        })
    return {"trends": result}


@router.get("/breakdown")
def get_carbon_breakdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    now = datetime.now(timezone.utc)
    ytd_start = naive_utc(datetime(now.year, 1, 1, tzinfo=timezone.utc))
    ytd_end   = naive_utc(now)

    # Energy by zone (YTD)
    zone_rows = _org_q(
        db.query(EnergyReading.zone, func.sum(EnergyReading.consumption_kwh))
        .filter(EnergyReading.timestamp >= ytd_start, EnergyReading.timestamp <= ytd_end)
        .group_by(EnergyReading.zone),
        current_user, EnergyReading
    ).all()

    items = [
        {"name": zone, "tco2": round(kwh * CARBON_KWH, 4), "category": "energy"}
        for zone, kwh in zone_rows if kwh
    ]

    # Waste offset by stream (YTD, recyclable only)
    waste_rows = _org_q(
        db.query(WasteLog.stream, func.sum(WasteLog.weight_kg))
        .filter(WasteLog.timestamp >= ytd_start, WasteLog.timestamp <= ytd_end)
        .group_by(WasteLog.stream),
        current_user, WasteLog
    ).all()

    for stream, kg in waste_rows:
        if stream and stream.lower() in RECYCLABLE_STREAMS and kg:
            items.append({
                "name":     stream.title() + " (Offset)",
                "tco2":     round(kg * CARBON_WASTE, 4),
                "category": "offset",
            })

    return {"breakdown": items}
