"""
OAuth 2.0 endpoints — Google and Microsoft sign-in / sign-up.

Flow per provider:
  1. Frontend calls  GET /auth/{provider}/authorize
     → Returns { authorization_url: "..." }
  2. Browser redirects user to provider; user consents.
  3. Provider redirects back to  GET /auth/{provider}/callback?code=...&state=...
     → Exchanges code for tokens, fetches profile, creates/updates DB user.
     → Redirects browser to {FRONTEND_URL}/auth/callback?token=<jwt>[&is_new=true]

CSRF protection:  itsdangerous HMAC-signed state token (stateless, no session storage).
"""

import httpx
from datetime import timedelta
from urllib.parse import urlencode, urljoin

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.auth import create_access_token
from app.config import settings
from app.database import get_db
from app.models import User, UserRole

router = APIRouter(prefix="/auth", tags=["OAuth"])

_signer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="oauth-state")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _gen_state(provider: str) -> str:
    return _signer.dumps({"p": provider})


def _verify_state(state: str, provider: str, max_age: int = 600) -> bool:
    try:
        data = _signer.loads(state, max_age=max_age)
        return data.get("p") == provider
    except (BadSignature, SignatureExpired):
        return False


def _frontend_redirect(token: str, is_new: bool = False) -> RedirectResponse:
    params = {"token": token}
    if is_new:
        params["is_new"] = "true"
    url = f"{settings.FRONTEND_URL}/auth/callback?{urlencode(params)}"
    return RedirectResponse(url=url, status_code=302)


def _frontend_error(message: str) -> RedirectResponse:
    from urllib.parse import quote
    url = f"{settings.FRONTEND_URL}/auth/callback?error={quote(message)}"
    return RedirectResponse(url=url, status_code=302)


def _get_or_create_oauth_user(
    db: Session,
    *,
    email: str,
    first_name: str,
    last_name: str,
    provider: str,
    sub: str,
) -> tuple[User, bool]:
    """Return (user, is_new).  Creates user if not found; links provider if needed."""
    user = db.query(User).filter(User.email == email).first()
    is_new = False

    if user is None:
        user = User(
            email=email,
            hashed_password="",        # OAuth-only; no password
            first_name=first_name,
            last_name=last_name,
            oauth_provider=provider,
            oauth_sub=sub,
            role=UserRole.VIEWER,
            email_verified=True,       # Provider already verified the email
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new = True
    else:
        # Link provider to existing account if not already linked
        if not user.oauth_provider:
            user.oauth_provider = provider
            user.oauth_sub = sub
            db.commit()
            db.refresh(user)

    return user, is_new


def _issue_token(user: User) -> str:
    return create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


# ── Google ────────────────────────────────────────────────────────────────────

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_INFO_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"


@router.get("/google/authorize")
def google_authorize():
    """Return the Google OAuth authorization URL."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth is not configured on this server.",
        )
    params = {
        "client_id":     settings.GOOGLE_CLIENT_ID,
        "redirect_uri":  settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         _gen_state("google"),
        "access_type":   "online",
    }
    return {"authorization_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/google/callback")
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """Handle Google OAuth callback and redirect to frontend."""
    if error:
        return _frontend_error(f"Google sign-in was cancelled or failed: {error}")

    if not code or not state or not _verify_state(state, "google"):
        return _frontend_error("Invalid OAuth state. Please try signing in again.")

    # Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri":  settings.GOOGLE_REDIRECT_URI,
                "grant_type":    "authorization_code",
            },
        )

    if token_resp.status_code != 200:
        return _frontend_error("Failed to exchange Google authorization code.")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        return _frontend_error("No access token received from Google.")

    # Fetch Google user profile
    async with httpx.AsyncClient() as client:
        info_resp = await client.get(
            GOOGLE_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if info_resp.status_code != 200:
        return _frontend_error("Failed to fetch Google profile.")

    profile = info_resp.json()
    email      = profile.get("email", "")
    first_name = profile.get("given_name") or profile.get("name", "").split(" ", 1)[0]
    last_name  = profile.get("family_name") or (
        profile.get("name", "").split(" ", 1)[1]
        if " " in profile.get("name", "")
        else ""
    )
    sub = profile.get("id") or profile.get("sub", "")

    if not email:
        return _frontend_error("Google did not provide an email address.")

    user, is_new = _get_or_create_oauth_user(
        db,
        email=email,
        first_name=first_name,
        last_name=last_name,
        provider="google",
        sub=sub,
    )

    return _frontend_redirect(_issue_token(user), is_new=is_new)


# ── Microsoft ─────────────────────────────────────────────────────────────────

MS_AUTH_URL  = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MS_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MS_INFO_URL  = "https://graph.microsoft.com/v1.0/me"


@router.get("/microsoft/authorize")
def microsoft_authorize():
    """Return the Microsoft OAuth authorization URL."""
    if not settings.MICROSOFT_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Microsoft OAuth is not configured on this server.",
        )
    params = {
        "client_id":     settings.MICROSOFT_CLIENT_ID,
        "redirect_uri":  settings.MICROSOFT_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile User.Read",
        "state":         _gen_state("microsoft"),
        "response_mode": "query",
    }
    return {"authorization_url": f"{MS_AUTH_URL}?{urlencode(params)}"}


@router.get("/microsoft/callback")
async def microsoft_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
):
    """Handle Microsoft OAuth callback and redirect to frontend."""
    if error:
        msg = error_description or error
        return _frontend_error(f"Microsoft sign-in failed: {msg}")

    if not code or not state or not _verify_state(state, "microsoft"):
        return _frontend_error("Invalid OAuth state. Please try signing in again.")

    # Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            MS_TOKEN_URL,
            data={
                "code":          code,
                "client_id":     settings.MICROSOFT_CLIENT_ID,
                "client_secret": settings.MICROSOFT_CLIENT_SECRET,
                "redirect_uri":  settings.MICROSOFT_REDIRECT_URI,
                "grant_type":    "authorization_code",
            },
        )

    if token_resp.status_code != 200:
        return _frontend_error("Failed to exchange Microsoft authorization code.")

    ms_access_token = token_resp.json().get("access_token")
    if not ms_access_token:
        return _frontend_error("No access token received from Microsoft.")

    # Fetch Microsoft Graph profile
    async with httpx.AsyncClient() as client:
        info_resp = await client.get(
            MS_INFO_URL,
            headers={"Authorization": f"Bearer {ms_access_token}"},
        )

    if info_resp.status_code != 200:
        return _frontend_error("Failed to fetch Microsoft profile.")

    profile    = info_resp.json()
    email      = profile.get("mail") or profile.get("userPrincipalName", "")
    first_name = profile.get("givenName") or ""
    last_name  = profile.get("surname") or ""
    sub        = profile.get("id", "")

    if not email:
        return _frontend_error("Microsoft did not provide an email address.")

    user, is_new = _get_or_create_oauth_user(
        db,
        email=email,
        first_name=first_name,
        last_name=last_name,
        provider="microsoft",
        sub=sub,
    )

    return _frontend_redirect(_issue_token(user), is_new=is_new)
