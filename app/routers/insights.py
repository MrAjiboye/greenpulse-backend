from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Insight, InsightAction, InsightStatus, UserRole
from app.schemas import InsightResponse, InsightActionCreate
from app.auth import get_current_active_user, require_role

router = APIRouter(prefix="/insights", tags=["AI Insights"])


def _org_q(query, current_user, model):
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


@router.get("/", response_model=dict)
def get_insights(
    status: str = None,
    category: str = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get paginated list of AI insights"""
    query = _org_q(db.query(Insight), current_user, Insight)

    if status:
        query = query.filter(Insight.status == status)
    if category:
        query = query.filter(Insight.category == category)

    total = query.count()
    items = query.order_by(Insight.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [InsightResponse.model_validate(i) for i in items],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


@router.get("/{insight_id}", response_model=InsightResponse)
def get_insight(
    insight_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get specific insight by ID"""
    insight = _org_q(
        db.query(Insight).filter(Insight.id == insight_id),
        current_user, Insight
    ).first()

    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")

    return insight


@router.post("/{insight_id}/apply")
def apply_insight(
    insight_id: int,
    action_data: InsightActionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Apply an insight"""
    insight = _org_q(
        db.query(Insight).filter(Insight.id == insight_id),
        current_user, Insight
    ).first()

    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")

    insight.status = InsightStatus.APPLIED

    action = InsightAction(
        insight_id=insight_id,
        user_id=current_user.id,
        action="applied",
        reason=action_data.reason,
    )

    db.add(action)
    db.commit()
    return {"message": "Insight applied successfully", "insight_id": insight_id}


@router.post("/{insight_id}/dismiss")
def dismiss_insight(
    insight_id: int,
    action_data: InsightActionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    """Dismiss an insight"""
    insight = _org_q(
        db.query(Insight).filter(Insight.id == insight_id),
        current_user, Insight
    ).first()

    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")

    insight.status = InsightStatus.DISMISSED

    action = InsightAction(
        insight_id=insight_id,
        user_id=current_user.id,
        action="dismissed",
        reason=action_data.reason,
    )

    db.add(action)
    db.commit()
    return {"message": "Insight dismissed", "insight_id": insight_id}
