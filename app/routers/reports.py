from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta
from app.database import get_db, naive_utc
from app.models import User, UserRole, Insight, InsightAction, InsightStatus
from app.auth import get_current_active_user

router = APIRouter(prefix="/reports", tags=["Reports"])

# Category display colours used by the pie chart in Reports.jsx
_CAT_COLORS = {
    "energy":     "#3b82f6",
    "waste":      "#10b981",
    "operations": "#f97316",
}


def _org_q(query, current_user, model):
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


@router.get("/performance")
def get_performance_report(
    months: int = 6,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get savings performance report with monthly trend and category breakdown"""
    now = datetime.now(timezone.utc)
    # Use proper calendar month boundary for the cutoff
    first_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cutoff_date = naive_utc(first_of_current_month - relativedelta(months=months - 1))

    applied = _org_q(
        db.query(Insight).filter(
            Insight.status == InsightStatus.APPLIED,
            Insight.created_at >= cutoff_date,
        ),
        current_user, Insight
    ).all()

    pending = _org_q(
        db.query(Insight).filter(
            Insight.status == InsightStatus.PENDING,
            Insight.created_at >= cutoff_date,
        ),
        current_user, Insight
    ).all()

    total_realized  = sum(i.estimated_savings for i in applied)
    total_potential = sum(i.estimated_savings for i in pending)

    # ── Monthly savings trend — true calendar months, no duplicates ──────────
    savings_trend = []
    for m in range(months - 1, -1, -1):
        month_start = naive_utc(first_of_current_month - relativedelta(months=m))
        month_end   = naive_utc(first_of_current_month - relativedelta(months=m - 1)) if m > 0 else naive_utc(now)
        month_label = month_start.strftime("%b %Y")

        def _ts(insight):
            ca = insight.created_at
            return ca.replace(tzinfo=None) if ca.tzinfo else ca

        realized_this_month = sum(
            i.estimated_savings for i in applied
            if month_start <= _ts(i) < month_end
        )
        potential_this_month = sum(
            i.estimated_savings for i in pending
            if month_start <= _ts(i) < month_end
        )
        savings_trend.append({
            "month":     month_label,
            "realized":  round(realized_this_month, 2),
            "potential": round(potential_this_month, 2),
        })

    # ── Category breakdown ───────────────────────────────────────────────────
    all_insights = applied + pending
    cat_totals = {}
    for i in all_insights:
        cat = i.category.value
        cat_totals[cat] = cat_totals.get(cat, 0) + i.estimated_savings

    grand_total = sum(cat_totals.values()) or 1
    category_breakdown = [
        {
            "category":   cat,
            "label":      cat.capitalize(),
            "value":      round(amount, 2),
            "percentage": round(amount / grand_total * 100, 1),
            "color":      _CAT_COLORS.get(cat, "#9ca3af"),
        }
        for cat, amount in cat_totals.items()
    ]

    # ── CO2e reduced (UK grid: £0.28/kWh, 0.233 kg CO2/kWh = 0.000233 t/kWh) ─
    kwh_saved    = total_realized / 0.28 if total_realized > 0 else 0
    co2e_reduced = round(kwh_saved * 0.000233, 2)

    return {
        "total_realized_savings":  total_realized,
        "total_potential_savings": total_potential,
        "insights_applied":        len(applied),
        "insights_pending":        len(pending),
        "insights_total":          len(applied) + len(pending),
        "co2e_reduced_tons":       co2e_reduced,
        "savings_trend":           savings_trend,
        "category_breakdown":      category_breakdown,
        "breakdown_by_category": {
            "energy":     sum(i.estimated_savings for i in applied if i.category.value == "energy"),
            "waste":      sum(i.estimated_savings for i in applied if i.category.value == "waste"),
            "operations": sum(i.estimated_savings for i in applied if i.category.value == "operations"),
        },
    }


@router.get("/insights-log")
def get_insights_log(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get paginated insights action log"""
    # Filter InsightAction by the insight's organisation
    query = db.query(InsightAction).join(InsightAction.insight)
    if current_user.role != UserRole.ADMIN:
        query = query.filter(Insight.organization_id == current_user.organization_id)
    query = query.order_by(InsightAction.created_at.desc())
    total = query.count()
    actions = query.offset(offset).limit(limit).all()

    return {
        "items": [
            {
                "id":                a.id,
                "insight_id":        a.insight_id,
                "insight_title":     a.insight.title,
                "category":          a.insight.category.value,
                "action":            a.action,
                "reason":            a.reason,
                "estimated_savings": a.insight.estimated_savings,
                "user":              f"{a.user.first_name} {a.user.last_name}",
                "timestamp":         a.created_at.isoformat(),
            }
            for a in actions
        ],
        "total":    total,
        "limit":    limit,
        "offset":   offset,
        "has_more": offset + limit < total,
    }
