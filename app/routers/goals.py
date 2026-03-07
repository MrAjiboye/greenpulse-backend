from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from typing import List

from app.database import get_db, naive_utc
from app.models import User, Goal, EnergyReading, WasteLog, UserRole, GoalCategory
from app.schemas import GoalCreate, GoalUpdate, GoalResponse
from app.auth import get_current_active_user, require_role

router = APIRouter(prefix="/goals", tags=["Goals"])

CARBON_KWH   = 0.000233
CARBON_WASTE = 0.0008
RECYCLABLE   = {"recycling", "compost", "recyclable", "compostable"}

ManagerOrAdmin = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN))


def _org_q(query, current_user, model):
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(model.organization_id == current_user.organization_id)


def _compute_progress(db: Session, goal: Goal, current_user: User) -> dict:
    start = naive_utc(goal.period_start) if goal.period_start.tzinfo else goal.period_start
    end   = naive_utc(goal.period_end)   if goal.period_end.tzinfo   else goal.period_end

    if goal.category == GoalCategory.ENERGY:
        actual = _org_q(
            db.query(func.sum(EnergyReading.consumption_kwh))
            .filter(EnergyReading.timestamp >= start, EnergyReading.timestamp <= end),
            current_user, EnergyReading
        ).scalar() or 0.0

    elif goal.category == GoalCategory.WASTE:
        actual = _org_q(
            db.query(func.sum(WasteLog.weight_kg))
            .filter(WasteLog.timestamp >= start, WasteLog.timestamp <= end),
            current_user, WasteLog
        ).scalar() or 0.0

    else:  # CARBON
        kwh = _org_q(
            db.query(func.sum(EnergyReading.consumption_kwh))
            .filter(EnergyReading.timestamp >= start, EnergyReading.timestamp <= end),
            current_user, EnergyReading
        ).scalar() or 0.0

        rows = _org_q(
            db.query(WasteLog.stream, func.sum(WasteLog.weight_kg))
            .filter(WasteLog.timestamp >= start, WasteLog.timestamp <= end)
            .group_by(WasteLog.stream),
            current_user, WasteLog
        ).all()
        recycled = sum(kg for s, kg in rows if s and s.lower() in RECYCLABLE and kg)
        actual = max(kwh * CARBON_KWH - recycled * CARBON_WASTE, 0.0)

    pct = (actual / goal.target_value * 100) if goal.target_value else 0.0
    if pct < 80:
        st = "on_track"
    elif pct <= 100:
        st = "at_risk"
    else:
        st = "exceeded"

    return {"actual_value": round(actual, 3), "progress_pct": round(pct, 1), "status": st}


@router.get("", response_model=List[GoalResponse])
def list_goals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    goals = _org_q(
        db.query(Goal).order_by(Goal.created_at.desc()),
        current_user, Goal
    ).all()

    result = []
    for g in goals:
        progress = _compute_progress(db, g, current_user)
        result.append(GoalResponse(
            id=g.id, name=g.name, category=g.category,
            target_value=g.target_value, unit=g.unit,
            period_start=g.period_start, period_end=g.period_end,
            created_at=g.created_at, **progress,
        ))
    return result


@router.post("", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
def create_goal(
    body: GoalCreate,
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    if current_user.organization_id is None and current_user.role != UserRole.ADMIN:
        raise HTTPException(400, "You must belong to an organisation to create goals.")

    goal = Goal(
        organization_id=current_user.organization_id,
        name=body.name,
        category=body.category,
        target_value=body.target_value,
        unit=body.unit,
        period_start=body.period_start,
        period_end=body.period_end,
        created_by=current_user.id,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    progress = _compute_progress(db, goal, current_user)
    return GoalResponse(
        id=goal.id, name=goal.name, category=goal.category,
        target_value=goal.target_value, unit=goal.unit,
        period_start=goal.period_start, period_end=goal.period_end,
        created_at=goal.created_at, **progress,
    )


@router.patch("/{goal_id}", response_model=GoalResponse)
def update_goal(
    goal_id: int,
    body: GoalUpdate,
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    goal = _org_q(db.query(Goal).filter(Goal.id == goal_id), current_user, Goal).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    for field, val in body.model_dump(exclude_none=True).items():
        setattr(goal, field, val)
    db.commit()
    db.refresh(goal)
    progress = _compute_progress(db, goal, current_user)
    return GoalResponse(
        id=goal.id, name=goal.name, category=goal.category,
        target_value=goal.target_value, unit=goal.unit,
        period_start=goal.period_start, period_end=goal.period_end,
        created_at=goal.created_at, **progress,
    )


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(
    goal_id: int,
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    goal = _org_q(db.query(Goal).filter(Goal.id == goal_id), current_user, Goal).first()
    if not goal:
        raise HTTPException(404, "Goal not found")
    db.delete(goal)
    db.commit()
