from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import EnergyReading, Organization, User, UserRole
from app.schemas import UserAdminResponse, UserRoleUpdate
from app.auth import require_role

router = APIRouter(prefix="/admin", tags=["Admin"])

# Shorthand dependency — all routes in this router are ADMIN-only
AdminOnly = Depends(require_role(UserRole.ADMIN))


@router.get("/users", response_model=dict)
def list_users(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """List all users (admin only)"""
    query = db.query(User).order_by(User.created_at.desc())
    total = query.count()
    users = query.offset(offset).limit(limit).all()

    return {
        "items": [UserAdminResponse.model_validate(u) for u in users],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


@router.patch("/users/{user_id}/role", response_model=UserAdminResponse)
def update_user_role(
    user_id: int,
    body: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_admin: User = AdminOnly,
):
    """Change a user's role (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )
    user.role = body.role
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/status", response_model=UserAdminResponse)
def toggle_user_status(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = AdminOnly,
):
    """Activate or deactivate a user account (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = AdminOnly,
):
    """Permanently delete a user (admin only)"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    db.delete(user)
    db.commit()


# ── Organisation management ────────────────────────────────────────────────────

@router.get("/organizations", response_model=dict)
def list_organizations(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """List all organisations with user and reading counts (admin only)"""
    query = db.query(Organization).order_by(Organization.created_at.desc())
    total = query.count()
    orgs  = query.offset(offset).limit(limit).all()

    items = []
    for org in orgs:
        user_count    = db.query(func.count(User.id)).filter(User.organization_id == org.id).scalar()
        reading_count = db.query(func.count(EnergyReading.id)).filter(EnergyReading.organization_id == org.id).scalar()
        items.append({
            "id":            org.id,
            "name":          org.name,
            "user_count":    user_count,
            "reading_count": reading_count,
            "created_at":    org.created_at.isoformat() if org.created_at else None,
            # Show only first/last 4 chars of the key for security
            "iot_api_key_hint": (
                f"{org.iot_api_key[:4]}...{org.iot_api_key[-4:]}" if org.iot_api_key else None
            ),
        })

    return {
        "items":    items,
        "total":    total,
        "limit":    limit,
        "offset":   offset,
        "has_more": offset + limit < total,
    }


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(
    org_id: int,
    db: Session = Depends(get_db),
    _: User = AdminOnly,
):
    """Delete an organisation and all its users (admin only)"""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    db.delete(org)
    db.commit()
