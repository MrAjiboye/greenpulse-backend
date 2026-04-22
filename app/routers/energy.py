import calendar
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta, timezone
from app.database import get_db, naive_utc
from app.models import User, EnergyReading, UserRole
from app.schemas import EnergyReadingCreate, EnergyReadingResponse
from app.auth import get_current_active_user, require_role

router = APIRouter(prefix="/energy", tags=["Energy"])


def _org_q(query, current_user, model):
    """Restrict query to the current user's organisation (admins see all)."""
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


@router.get("/current")
def get_current_energy(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get current energy consumption with computed KPI fields"""
    from app.models import Insight, InsightStatus

    latest = _org_q(
        db.query(EnergyReading).order_by(EnergyReading.timestamp.desc()),
        current_user, EnergyReading
    ).first()

    if not latest:
        return {
            "consumption_kwh": 0, "current_load_kw": 0,
            "zone": "N/A", "timestamp": datetime.now(timezone.utc).isoformat(),
            "peak_demand_kw": None, "peak_time": None, "peak_limit_kw": 500,
            "power_factor": 0.97, "projected_cost_monthly": None,
            "estimated_savings": None, "baseline_deviation_pct": None,
        }

    # Last 24h readings for peak
    cutoff_24h = naive_utc(datetime.now(timezone.utc) - timedelta(hours=24))
    readings_24h = _org_q(
        db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff_24h)
        .order_by(EnergyReading.timestamp.asc()),
        current_user, EnergyReading
    ).all()

    # 7-day average for baseline deviation
    cutoff_7d = naive_utc(datetime.now(timezone.utc) - timedelta(days=7))
    readings_7d = _org_q(
        db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff_7d),
        current_user, EnergyReading
    ).all()
    avg_7d = (sum(r.consumption_kwh for r in readings_7d) / len(readings_7d)) if readings_7d else None

    peak = max(readings_24h, key=lambda r: r.consumption_kwh) if readings_24h else latest
    baseline_pct = round(((latest.consumption_kwh - avg_7d) / avg_7d) * 100, 1) if avg_7d else None

    # Projected monthly cost: avg hourly kWh × 24h × 30 days × £0.28/kWh (UK average)
    avg_hourly = (sum(r.consumption_kwh for r in readings_24h) / max(len(readings_24h), 1))
    projected_monthly = round(avg_hourly * 24 * 30 * 0.28, 0) if readings_24h else None

    # Monthly savings estimate from pending insights (org-scoped)
    pending_savings = sum(
        i.estimated_savings for i in
        _org_q(
            db.query(Insight).filter(Insight.status == InsightStatus.PENDING),
            current_user, Insight
        ).all()
    )
    monthly_savings = round(pending_savings / 12, 0) if pending_savings > 0 else None

    return {
        "consumption_kwh": latest.consumption_kwh,
        "current_load_kw": latest.consumption_kwh,
        "zone": latest.zone,
        "timestamp": latest.timestamp.isoformat(),
        "peak_demand_kw": peak.consumption_kwh,
        "peak_time": peak.timestamp.strftime("%H:%M"),
        "peak_limit_kw": 500,
        "power_factor": 0.97,
        "projected_cost_monthly": projected_monthly,
        "estimated_savings": monthly_savings,
        "baseline_deviation_pct": baseline_pct,
    }

@router.get("/trends")
def get_energy_trends(
    hours: int = 24,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get energy consumption trends.
    Returns readings from the last `hours` window. If none exist in that window
    (e.g. demo/historical data), falls back to the most recent 24 readings so
    the chart always has something to show."""
    cutoff_time = naive_utc(datetime.now(timezone.utc) - timedelta(hours=hours))

    readings = _org_q(
        db.query(EnergyReading).filter(EnergyReading.timestamp >= cutoff_time)
        .order_by(EnergyReading.timestamp.asc()),
        current_user, EnergyReading
    ).all()

    # Fallback: if no data in the requested window, return the most recent 24 readings
    if not readings:
        readings = list(reversed(
            _org_q(
                db.query(EnergyReading).order_by(EnergyReading.timestamp.desc()),
                current_user, EnergyReading
            ).limit(24).all()
        ))

    return {
        "trends": [
            {
                "timestamp": r.timestamp.isoformat(),
                "consumption_kwh": r.consumption_kwh,
                "zone": r.zone
            }
            for r in readings
        ]
    }

@router.get("/anomalies")
def get_energy_anomalies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Detect energy anomalies using per-zone stats over a 30-day baseline."""
    from collections import defaultdict

    now = datetime.now(timezone.utc)

    # 30-day window for a solid per-zone baseline
    baseline_cutoff = naive_utc(now - timedelta(days=30))
    # Only surface anomalies from the last 7 days
    recent_cutoff   = naive_utc(now - timedelta(days=7))

    all_readings = _org_q(
        db.query(EnergyReading).filter(EnergyReading.timestamp >= baseline_cutoff),
        current_user, EnergyReading
    ).all()

    if not all_readings:
        return {"anomalies": []}

    # Build per-zone baseline from full 30-day window
    zone_readings: dict[str, list] = defaultdict(list)
    for r in all_readings:
        zone_readings[r.zone or "Unknown"].append(r)

    anomalies = []
    for zone, readings in zone_readings.items():
        avg = sum(r.consumption_kwh for r in readings) / len(readings)
        threshold = avg * 1.5  # 50% above that zone's own baseline

        # Only report spikes from the last 7 days
        for r in readings:
            ts = r.timestamp if r.timestamp.tzinfo else r.timestamp.replace(tzinfo=timezone.utc)
            if ts < (now - timedelta(days=7)):
                continue
            if r.consumption_kwh > threshold:
                pct_above = round((r.consumption_kwh / avg - 1) * 100)
                anomalies.append({
                    "id":              r.id,
                    "timestamp":       r.timestamp.isoformat(),
                    "detected_at":     r.timestamp.isoformat(),
                    "consumption_kwh": r.consumption_kwh,
                    "zone":            r.zone,
                    "severity":        "high" if r.consumption_kwh > avg * 2.0 else "medium",
                    "title":           f"High consumption in {r.zone}",
                    "details":         f"{r.consumption_kwh:.1f} kWh — {pct_above}% above {zone} average ({avg:.1f} kWh)",
                })

    # High severity first, then most recent
    anomalies.sort(key=lambda a: (0 if a["severity"] == "high" else 1, a["timestamp"]), reverse=False)
    return {"anomalies": anomalies[:10]}

@router.get("/zones")
def get_zone_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get latest energy reading per zone"""
    from sqlalchemy import distinct

    zone_names = [
        z[0] for z in _org_q(
            db.query(distinct(EnergyReading.zone)),
            current_user, EnergyReading
        ).all()
    ]

    zones = []
    for zone in zone_names:
        latest = _org_q(
            db.query(EnergyReading).filter(EnergyReading.zone == zone)
            .order_by(EnergyReading.timestamp.desc()),
            current_user, EnergyReading
        ).first()
        if latest:
            zones.append({
                "zone": zone,
                "consumption_kwh": latest.consumption_kwh,
                "timestamp": latest.timestamp.isoformat(),
            })

    return {"zones": zones}


def _period_bounds(period: str, now: datetime):
    """Return (start, end, label) datetimes compatible with the active DB engine."""
    y, m = now.year, now.month
    if period == "this_month":
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        end   = now
        label = now.strftime("%b %Y")
    elif period == "last_month":
        pm = m - 1 or 12
        py = y if m > 1 else y - 1
        _, last_day = calendar.monthrange(py, pm)
        start = datetime(py, pm, 1, tzinfo=timezone.utc)
        end   = datetime(py, pm, last_day, 23, 59, 59, tzinfo=timezone.utc)
        label = datetime(py, pm, 1).strftime("%b %Y")
    elif period == "last_7d":
        start = now - timedelta(days=7)
        end   = now
        label = "Last 7 days"
    elif period == "last_30d":
        start = now - timedelta(days=30)
        end   = now
        label = "Last 30 days"
    else:
        raise HTTPException(400, f"Unknown period '{period}'. Use: this_month, last_month, last_7d, last_30d")
    # naive_utc strips tzinfo on SQLite, leaves aware on PostgreSQL
    return naive_utc(start), naive_utc(end), label


@router.get("/compare")
def compare_energy(
    period:     str = Query("this_month"),
    compare_to: str = Query("last_month"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Compare energy totals between two named periods."""
    now = datetime.now(timezone.utc)

    cur_start, cur_end, cur_label   = _period_bounds(period, now)
    prev_start, prev_end, prev_label = _period_bounds(compare_to, now)

    def _period_stats(start, end, label):
        rows = _org_q(
            db.query(EnergyReading).filter(
                EnergyReading.timestamp >= start,
                EnergyReading.timestamp <= end,
            ).order_by(EnergyReading.timestamp.asc()),
            current_user, EnergyReading
        ).all()
        total = sum(r.consumption_kwh for r in rows)
        days = max((end - start).days, 1)
        avg_daily = total / days
        cost = round(total * 0.28, 2)
        # daily buckets for trend overlay (date string → total kWh)
        daily = {}
        for r in rows:
            d = r.timestamp.date().isoformat()
            daily[d] = daily.get(d, 0.0) + r.consumption_kwh
        return {
            "label":         label,
            "total_kwh":     round(total, 2),
            "avg_daily_kwh": round(avg_daily, 2),
            "cost_gbp":      cost,
            "daily":         [{"date": k, "kwh": round(v, 2)} for k, v in sorted(daily.items())],
        }

    cur  = _period_stats(cur_start,  cur_end,  cur_label)
    prev = _period_stats(prev_start, prev_end, prev_label)

    change_pct = (
        round((cur["total_kwh"] - prev["total_kwh"]) / prev["total_kwh"] * 100, 1)
        if prev["total_kwh"] > 0 else None
    )

    return {"current": cur, "previous": prev, "change_pct": change_pct}


@router.post("/readings", response_model=EnergyReadingResponse)
def create_energy_reading(
    reading: EnergyReadingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Create a new energy reading"""
    new_reading = EnergyReading(**reading.model_dump())
    db.add(new_reading)
    db.commit()
    db.refresh(new_reading)
    return new_reading
