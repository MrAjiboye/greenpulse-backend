"""
Team Router
───────────
Manage org members and send email invitations.

  GET  /team/members                 — list all users in org (MANAGER+)
  GET  /team/invites                 — list pending invites (MANAGER+)
  POST /team/invite                  — send invite email (MANAGER+)
  PATCH /team/members/{uid}/role     — change a member's role (MANAGER only)
  DELETE /team/members/{uid}         — remove member from org (MANAGER only)
  GET  /team/accept-invite?token=    — public; validate token → org/role info
  POST /team/accept-invite           — public; create/link user, return JWT
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_active_user,
    get_password_hash,
    require_role,
)
from app.database import get_db
from app.email import send_invite_email
from app.limiter import limiter
from app.models import Organization, TeamInvite, User, UserRole
from app.schemas import (
    AcceptInviteRequest,
    TeamInviteCreate,
    TeamInviteResponse,
    TeamMemberResponse,
)

logger = logging.getLogger("greenpulse.team")

router = APIRouter(prefix="/team", tags=["Team"])

INVITE_EXPIRY_DAYS = 7


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── List members ───────────────────────────────────────────────────────────────

@router.get("/members", response_model=list[TeamMemberResponse])
def list_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    query = db.query(User).filter(User.is_active == True)
    if current_user.role != UserRole.ADMIN:
        query = query.filter(User.organization_id == current_user.organization_id)
    return query.order_by(User.created_at).all()


# ── List pending invites ────────────────────────────────────────────────────────

@router.get("/invites", response_model=list[TeamInviteResponse])
def list_invites(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    query = db.query(TeamInvite).filter(
        TeamInvite.accepted_at == None,
        TeamInvite.expires_at > datetime.now(timezone.utc),
    )
    if current_user.role != UserRole.ADMIN:
        query = query.filter(TeamInvite.organization_id == current_user.organization_id)
    return query.order_by(TeamInvite.created_at.desc()).all()


# ── Send invite ────────────────────────────────────────────────────────────────

@router.post("/invite", response_model=TeamInviteResponse, status_code=201)
@limiter.limit("10/minute")
def send_invite(
    request: Request,
    payload: TeamInviteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id
    ).first()
    if not org:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Organisation not found.")

    # Expire any existing pending invite for this email+org
    db.query(TeamInvite).filter(
        TeamInvite.email == payload.email.lower(),
        TeamInvite.organization_id == org.id,
        TeamInvite.accepted_at == None,
    ).delete(synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)
    invite = TeamInvite(
        email=payload.email.lower(),
        organization_id=org.id,
        role=payload.role,
        token_hash=_hash_token(raw_token),
        invited_by=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    inviter_name = f"{current_user.first_name} {current_user.last_name}".strip() or current_user.email
    try:
        send_invite_email(payload.email, raw_token, org.name, inviter_name)
    except Exception as exc:
        logger.warning("Failed to send invite email to %s: %s", payload.email, exc)
        # Don't fail the request — the invite record exists; manager can resend

    logger.info("Invite sent to %s by %s (org=%s)", payload.email, current_user.email, org.name)
    return invite


# ── Change member role ─────────────────────────────────────────────────────────

@router.patch("/members/{uid}/role")
def update_member_role(
    uid: int,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    if uid == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role.")

    member = db.query(User).filter(User.id == uid).first()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if current_user.role != UserRole.ADMIN and member.organization_id != current_user.organization_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied.")

    try:
        new_role = UserRole(body.get("role", "").upper())
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid role.")

    member.role = new_role
    db.commit()
    return {"id": member.id, "role": member.role}


# ── Remove member ──────────────────────────────────────────────────────────────

@router.delete("/members/{uid}", status_code=204)
def remove_member(
    uid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN)),
):
    if uid == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove yourself.")

    member = db.query(User).filter(User.id == uid).first()
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if current_user.role != UserRole.ADMIN and member.organization_id != current_user.organization_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied.")

    member.organization_id = None
    db.commit()
    logger.info("Member %s removed from org by %s", member.email, current_user.email)


# ── Public: validate token ─────────────────────────────────────────────────────

@router.get("/accept-invite")
def get_invite_info(token: str, db: Session = Depends(get_db)):
    invite = db.query(TeamInvite).filter(
        TeamInvite.token_hash == _hash_token(token),
        TeamInvite.accepted_at == None,
        TeamInvite.expires_at > datetime.now(timezone.utc),
    ).first()
    if not invite:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or expired.")

    org = db.query(Organization).filter(Organization.id == invite.organization_id).first()
    return {
        "email": invite.email,
        "role": invite.role,
        "org_name": org.name if org else "your organisation",
    }


# ── Public: accept invite ──────────────────────────────────────────────────────

@router.post("/accept-invite")
def accept_invite(payload: AcceptInviteRequest, db: Session = Depends(get_db)):
    invite = db.query(TeamInvite).filter(
        TeamInvite.token_hash == _hash_token(payload.token),
        TeamInvite.accepted_at == None,
        TeamInvite.expires_at > datetime.now(timezone.utc),
    ).first()
    if not invite:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found or expired.")

    # If email already exists → link to org instead of creating duplicate
    existing = db.query(User).filter(User.email == invite.email).first()
    if existing:
        existing.organization_id = invite.organization_id
        existing.role = invite.role
        user = existing
    else:
        user = User(
            email=invite.email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            hashed_password=get_password_hash(payload.password),
            role=invite.role,
            organization_id=invite.organization_id,
            is_active=True,
            email_verified=True,
        )
        db.add(user)

    invite.accepted_at = datetime.now(timezone.utc)
    db.commit()
    if existing:
        db.refresh(existing)
    else:
        db.refresh(user)

    token = create_access_token({"sub": user.email})
    logger.info("Invite accepted by %s (org=%s)", user.email, invite.organization_id)
    return {"access_token": token, "token_type": "bearer"}
