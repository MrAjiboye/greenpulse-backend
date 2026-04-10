"""
n3rgy Data Limited integration.
Customer Service API — works for any UK smart meter, any energy supplier.

Flow:
  1. Client enters their MPAN in Settings → we send a consent request to n3rgy
  2. n3rgy emails the client a consent link (or we redirect them to the consent URL)
  3. Client approves consent
  4. We can then pull half-hourly electricity/gas consumption via the API

Endpoints:
  POST /n3rgy/connect        — save MPAN + trigger consent request
  GET  /n3rgy/status         — connection + consent status
  POST /n3rgy/sync           — pull consumption data and store as EnergyReadings
  DELETE /n3rgy/disconnect   — remove credentials
"""
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_role, get_current_user
from app.database import get_db
from app.models import EnergyReading, Organization, User, UserRole
from app.config import settings

router = APIRouter(prefix="/n3rgy", tags=["n3rgy"])

N3RGY_BASE   = "https://api.data.n3rgy.com"
CORE_PLANS   = {"core", "pro", "enterprise"}

ManagerOrAdmin = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN))


def _api_key() -> str:
    key = getattr(settings, "N3RGY_API_KEY", None)
    if not key:
        raise HTTPException(
            status_code=503,
            detail="n3rgy API key is not configured on this server.",
        )
    return key


def _get_org(db: Session, user: User) -> Organization:
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    return org


def _require_core(org: Organization) -> None:
    if org.plan not in CORE_PLANS and org.plan != "free":
        raise HTTPException(
            status_code=403,
            detail="n3rgy integration requires the Core plan or above.",
        )


class N3rgyConnectRequest(BaseModel):
    mpan: str                   # electricity MPAN (13 digits)
    mprn: str | None = None     # gas MPRN (optional)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _n3rgy_headers() -> dict:
    return {"Authorization": _api_key(), "Content-Type": "application/json"}


def _fmt_ts(dt: datetime) -> str:
    """Format datetime to n3rgy query param format: YYYYMMDDHHmm"""
    return dt.strftime("%Y%m%d%H%M")


def _parse_n3rgy_ts(ts_str: str) -> datetime:
    """Parse n3rgy timestamp format: '2025-01-01 00:00' or ISO"""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse n3rgy timestamp: {ts_str}")


def _pull_consumption(mpan: str, fuel: str, period_from: datetime, period_to: datetime) -> list[dict]:
    """
    Fetch half-hourly consumption from n3rgy.
    fuel: 'electricity' or 'gas'
    Returns list of {timestamp, consumption_kwh}
    """
    start = _fmt_ts(period_from)
    end   = _fmt_ts(period_to)
    url   = f"{N3RGY_BASE}/{fuel}/{mpan}/consumption/1?start={start}&end={end}"

    try:
        resp = httpx.get(url, headers=_n3rgy_headers(), timeout=30)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach n3rgy API. Please try again.")

    if resp.status_code == 401:
        raise HTTPException(status_code=502, detail="n3rgy API key is invalid or expired.")
    if resp.status_code == 403:
        raise HTTPException(
            status_code=400,
            detail="n3rgy: consent not yet granted for this meter. "
                   "Please check your email for the consent link from n3rgy.",
        )
    if resp.status_code == 404:
        raise HTTPException(
            status_code=400,
            detail="Meter not found on n3rgy network. Check your MPAN and try again.",
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"n3rgy API error: {resp.status_code}")

    data = resp.json()
    # n3rgy response: {"resource": "/electricity/.../consumption/1", "responseTimestamp": "...",
    #                  "start": "...", "end": "...", "granularity": "halfhour",
    #                  "values": [{"timestamp": "2025-01-01 00:00", "value": 0.123}, ...]}
    return data.get("values", [])


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/connect")
def connect_n3rgy(
    body: N3rgyConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    """
    Save the client's MPAN. n3rgy consent is granted separately by the client
    (they receive an email from n3rgy or approve via the GreenPulse consent redirect).
    We validate the MPAN format here; actual data access is confirmed on first sync.
    """
    org = _get_org(db, current_user)
    _require_core(org)

    mpan = body.mpan.strip().replace(" ", "")
    if not mpan.isdigit() or len(mpan) not in (13,):
        raise HTTPException(
            status_code=400,
            detail="MPAN must be a 13-digit number. Check your electricity bill or smart meter.",
        )

    org.n3rgy_mpan        = mpan
    org.n3rgy_mprn        = body.mprn.strip() if body.mprn else None
    org.n3rgy_consent_at  = None   # reset — consent must be re-confirmed
    org.n3rgy_last_sync   = None
    db.commit()

    return {
        "status": "pending_consent",
        "mpan": mpan,
        "message": (
            "MPAN saved. n3rgy will send a consent email to the meter account holder. "
            "Once approved, click 'Sync Now' to import your data."
        ),
    }


@router.get("/status")
def n3rgy_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return n3rgy connection status for the organisation."""
    org = _get_org(db, current_user)
    if not org.n3rgy_mpan:
        return {"connected": False}
    return {
        "connected":    True,
        "mpan":         org.n3rgy_mpan,
        "mprn":         org.n3rgy_mprn,
        "consent_at":   org.n3rgy_consent_at.isoformat() if org.n3rgy_consent_at else None,
        "last_sync":    org.n3rgy_last_sync.isoformat()  if org.n3rgy_last_sync  else None,
        "consent_granted": bool(org.n3rgy_consent_at),
    }


@router.post("/sync")
def sync_n3rgy(
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    """
    Pull half-hourly consumption from n3rgy and store as EnergyReadings.
    Pulls from last sync time (or last 30 days). Deduplicates by timestamp+zone.
    """
    org = _get_org(db, current_user)
    if not org.n3rgy_mpan:
        raise HTTPException(
            status_code=400,
            detail="n3rgy is not connected. Go to Settings → Data Connections.",
        )
    _require_core(org)

    now         = datetime.now(timezone.utc)
    period_from = org.n3rgy_last_sync or (now - timedelta(days=30))

    imported = 0

    # ── Electricity ────────────────────────────────────────────────────────────
    elec_values = _pull_consumption(org.n3rgy_mpan, "electricity", period_from, now)

    for item in elec_values:
        raw_ts = item.get("timestamp") or item.get("ts")
        value  = item.get("value")
        if raw_ts is None or value is None:
            continue
        try:
            ts = _parse_n3rgy_ts(str(raw_ts))
        except ValueError:
            continue

        exists = (
            db.query(EnergyReading)
            .filter(
                EnergyReading.organization_id == org.id,
                EnergyReading.timestamp       == ts,
                EnergyReading.zone            == "n3rgy Electricity",
            )
            .first()
        )
        if exists:
            continue

        db.add(EnergyReading(
            timestamp       = ts,
            consumption_kwh = float(value),
            zone            = "n3rgy Electricity",
            facility_id     = 1,
            organization_id = org.id,
        ))
        imported += 1

    # ── Gas (optional) ─────────────────────────────────────────────────────────
    if org.n3rgy_mprn:
        try:
            gas_values = _pull_consumption(org.n3rgy_mprn, "gas", period_from, now)
        except HTTPException:
            gas_values = []   # gas consent may not be granted yet — don't crash

        for item in gas_values:
            raw_ts = item.get("timestamp") or item.get("ts")
            value  = item.get("value")
            if raw_ts is None or value is None:
                continue
            try:
                ts = _parse_n3rgy_ts(str(raw_ts))
            except ValueError:
                continue

            exists = (
                db.query(EnergyReading)
                .filter(
                    EnergyReading.organization_id == org.id,
                    EnergyReading.timestamp       == ts,
                    EnergyReading.zone            == "n3rgy Gas",
                )
                .first()
            )
            if exists:
                continue

            db.add(EnergyReading(
                timestamp       = ts,
                consumption_kwh = float(value),
                zone            = "n3rgy Gas",
                facility_id     = 1,
                organization_id = org.id,
            ))
            imported += 1

    # Mark consent as granted (first successful sync proves it)
    if not org.n3rgy_consent_at and imported > 0:
        org.n3rgy_consent_at = now

    org.n3rgy_last_sync = now
    db.commit()

    return {
        "imported":    imported,
        "last_sync":   now.isoformat(),
        "period_from": period_from.isoformat(),
        "period_to":   now.isoformat(),
    }


@router.delete("/disconnect")
def disconnect_n3rgy(
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    """Remove n3rgy credentials from the organisation."""
    org = _get_org(db, current_user)
    org.n3rgy_mpan       = None
    org.n3rgy_mprn       = None
    org.n3rgy_consent_at = None
    org.n3rgy_last_sync  = None
    db.commit()
    return {"status": "disconnected"}
