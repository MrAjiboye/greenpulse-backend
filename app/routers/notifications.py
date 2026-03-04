from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, UserRole, Notification
from app.schemas import NotificationResponse
from app.auth import get_current_active_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _org_q(query, current_user):
    if current_user.role == UserRole.ADMIN:
        return query
    return query.filter(Notification.organization_id == current_user.organization_id)


@router.get("/", response_model=dict)
def get_notifications(
    unread: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Get paginated notifications with optional unread filter"""
    query = _org_q(db.query(Notification), current_user)

    if unread:
        query = query.filter(Notification.read == False)

    total = query.count()
    unread_count = _org_q(db.query(Notification), current_user).filter(Notification.read == False).count()
    items = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "items": [NotificationResponse.model_validate(n) for n in items],
        "total": total,
        "unread_count": unread_count,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


@router.patch("/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Mark a single notification as read"""
    notification = _org_q(
        db.query(Notification).filter(Notification.id == notification_id),
        current_user
    ).first()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.read = True
    db.commit()
    return {"message": "Notification marked as read", "id": notification_id}


@router.patch("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Mark all unread notifications as read"""
    updated = _org_q(db.query(Notification), current_user).filter(
        Notification.read == False
    ).update({"read": True})
    db.commit()
    return {"message": "All notifications marked as read", "updated": updated}
