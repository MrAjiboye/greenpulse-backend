import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session
from datetime import timedelta
from jose import JWTError, jwt
from app.database import get_db
from app.models import Organization, User, UserRole
from app.schemas import UserCreate, UserResponse, Token, UserUpdate, ForgotPasswordRequest, ResetPasswordRequest
from app.auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_active_user,
)
from app.config import settings
from app.email import send_verification_email
from app.limiter import limiter

router = APIRouter(prefix="/auth", tags=["Authentication"])

_email_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="email-verification")

EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24  # 24 hours


def _make_verify_token(email: str) -> str:
    return _email_signer.dumps(email)


def _decode_verify_token(token: str) -> str | None:
    try:
        return _email_signer.loads(token, max_age=EMAIL_VERIFY_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


@router.post("/register", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def register(
    request: Request,
    user_data: UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Register a new user and send a verification email."""
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Split full_name into first / last (everything after first space → last_name)
    parts = user_data.full_name.strip().split(" ", 1)
    first = parts[0]
    last  = parts[1] if len(parts) > 1 else ""

    # Create the organisation first
    org = Organization(
        name=user_data.organization_name.strip(),
        iot_api_key=secrets.token_hex(32),
    )
    db.add(org)
    db.flush()  # get org.id without committing

    new_user = User(
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        first_name=first,
        last_name=last,
        role=UserRole.MANAGER,
        organization_id=org.id,
        email_verified=False,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Send verification email in the background
    token = _make_verify_token(new_user.email)
    background_tasks.add_task(send_verification_email, new_user.email, token, first)

    return {"message": "Verification email sent", "email": new_user.email}


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login and get access token"""
    user = db.query(User).filter(User.email == form_data.username).first()

    # Block OAuth-only users from password login
    if user and user.hashed_password == "" and user.oauth_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This account uses {user.oauth_provider.capitalize()} sign-in. Please use the SSO button to log in.",
        )

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="email_not_verified",
        )

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/verify-email", response_model=Token)
def verify_email(token: str, db: Session = Depends(get_db)):
    """Verify email address via signed token from the inbox link."""
    email = _decode_verify_token(token)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link. Please request a new one.",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not user.email_verified:
        user.email_verified = True
        db.commit()

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/resend-verification")
@limiter.limit("3/minute")
def resend_verification(
    request: Request,
    body: ForgotPasswordRequest,   # reuses {email: str} schema
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Resend the email verification link."""
    user = db.query(User).filter(User.email == body.email).first()
    # Silent 200 whether user exists or not — don't leak account info
    if not user or user.email_verified:
        return {"message": "If that email is pending verification you will receive a new link"}

    token = _make_verify_token(user.email)
    background_tasks.add_task(send_verification_email, user.email, token, user.first_name)
    return {"message": "Verification email resent"}


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """Get current user information"""
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_profile(
    updates: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Update current user's profile or password"""
    if updates.new_password:
        if current_user.hashed_password == "" and current_user.oauth_provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth accounts cannot set a password.",
            )
        if not updates.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="current_password is required to set a new password",
            )
        if not verify_password(updates.current_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incorrect current password",
            )
        current_user.hashed_password = get_password_hash(updates.new_password)

    # full_name takes precedence; split into first / last
    if updates.full_name is not None:
        parts = updates.full_name.strip().split(" ", 1)
        current_user.first_name = parts[0]
        current_user.last_name  = parts[1] if len(parts) > 1 else ""
    else:
        if updates.first_name is not None:
            current_user.first_name = updates.first_name
        if updates.last_name is not None:
            current_user.last_name = updates.last_name

    if updates.job_title is not None:
        current_user.job_title = updates.job_title
    if updates.department is not None:
        current_user.department = updates.department
    if updates.company_name is not None:
        current_user.company_name = updates.company_name

    # OAuth users completing their profile: create org if they don't have one yet
    if updates.organization_name and current_user.organization_id is None:
        org = Organization(
            name=updates.organization_name.strip(),
            iot_api_key=secrets.token_hex(32),
        )
        db.add(org)
        db.flush()
        current_user.organization_id = org.id
        current_user.role = UserRole.MANAGER

    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/forgot-password")
@limiter.limit("3/minute")
def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Send a password reset link via email."""
    from app.email import send_email as _send
    user = db.query(User).filter(User.email == body.email).first()
    # Always return 200 to avoid leaking which emails are registered
    if not user:
        return {"message": "If that email is registered you will receive a reset link"}

    reset_token = create_access_token(
        data={"sub": user.email, "purpose": "password_reset"},
        expires_delta=timedelta(minutes=15),
    )
    reset_url = f"{settings.FRONTEND_URL}/reset-password/{reset_token}"

    def _send_reset():
        subject = "Reset your GreenPulse password"
        html = f"""
<html><body style="font-family:sans-serif;background:#f9fafb;padding:40px 0;">
<table width="560" style="background:#fff;border-radius:12px;margin:0 auto;padding:40px;box-shadow:0 1px 3px rgba(0,0,0,.1);">
<tr><td style="background:linear-gradient(135deg,#059669,#10b981);padding:24px 40px;border-radius:8px 8px 0 0;text-align:center;">
  <span style="font-size:24px;font-weight:800;color:#fff;">🌿 GreenPulse</span>
</td></tr>
<tr><td style="padding:32px 40px;">
  <h2 style="margin:0 0 12px;font-size:20px;color:#111827;">Hi {user.first_name}, reset your password</h2>
  <p style="color:#6b7280;line-height:1.6;margin:0 0 24px;">
    We received a request to reset your password. Click the button below. This link expires in <strong>15 minutes</strong>.
  </p>
  <table><tr><td style="background:#059669;border-radius:8px;">
    <a href="{reset_url}" style="display:inline-block;padding:12px 28px;color:#fff;font-weight:600;text-decoration:none;">Reset password</a>
  </td></tr></table>
  <p style="margin:24px 0 0;font-size:13px;color:#9ca3af;">If you didn't request this, you can safely ignore this email.</p>
</td></tr>
</table>
</body></html>"""
        plain = f"Reset your GreenPulse password:\n\n{reset_url}\n\nExpires in 15 minutes."
        try:
            _send(user.email, subject, html, plain)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Failed to send password reset email: %s", e)

    background_tasks.add_task(_send_reset)
    return {"message": "If that email is registered you will receive a reset link"}


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    """Reset password using a reset token"""
    credentials_error = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid or expired reset token",
    )
    try:
        payload = jwt.decode(body.token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "password_reset":
            raise credentials_error
        email: str = payload.get("sub")
        if not email:
            raise credentials_error
    except JWTError:
        raise credentials_error

    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise credentials_error

    user.hashed_password = get_password_hash(body.new_password)
    db.commit()
    return {"message": "Password reset successful"}
