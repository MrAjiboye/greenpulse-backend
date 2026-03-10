"""
Octopus Energy API integration.
Core plan feature — fetches half-hourly electricity consumption and stores as EnergyReadings.
"""
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.models import EnergyReading, Organization, User, UserRole
from app.routers.auth import get_current_user

router = APIRouter(prefix="/octopus", tags=["octopus"])

OCTOPUS_BASE = "https://api.octopus.energy/v1"
CORE_PLANS = {"core", "pro", "enterprise"}

ManagerOrAdmin = Depends(require_role(UserRole.MANAGER, UserRole.ADMIN))


class OctopusConnectRequest(BaseModel):
    api_key: str
    mpan: str
    meter_serial: str


def _get_org(db: Session, user: User) -> Organization:
    org = db.query(Organization).filter(Organization.id == user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    return org


def _require_core(org: Organization) -> None:
    """Octopus integration requires Core plan or above."""
    if org.plan not in CORE_PLANS and org.plan != "free":
        raise HTTPException(
            status_code=403,
            detail="Octopus Energy integration requires the Core plan or above. Upgrade at greenpulse.com/pricing.",
        )


@router.post("/connect")
def connect_octopus(
    body: OctopusConnectRequest,
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    """Save Octopus credentials and validate them against the Octopus API."""
    org = _get_org(db, current_user)
    _require_core(org)

    # Validate credentials by fetching the most recent single reading
    url = (
        f"{OCTOPUS_BASE}/electricity-meter-points/{body.mpan}"
        f"/meters/{body.meter_serial}/consumption/"
        f"?page_size=1"
    )
    try:
        resp = httpx.get(url, auth=(body.api_key, ""), timeout=10)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach Octopus Energy API. Please try again.")

    if resp.status_code == 401:
        raise HTTPException(status_code=400, detail="Invalid Octopus API key. Check your account settings.")
    if resp.status_code == 404:
        raise HTTPException(status_code=400, detail="Meter not found. Double-check your MPAN and meter serial number.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Octopus API returned {resp.status_code}. Try again later.")

    org.octopus_api_key = body.api_key
    org.octopus_mpan = body.mpan
    org.octopus_meter_serial = body.meter_serial
    db.commit()

    return {"status": "connected", "mpan": body.mpan, "meter_serial": body.meter_serial}


@router.get("/status")
def octopus_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current Octopus connection status."""
    org = _get_org(db, current_user)
    if not org.octopus_api_key:
        return {"connected": False}
    return {
        "connected": True,
        "mpan": org.octopus_mpan,
        "meter_serial": org.octopus_meter_serial,
        "last_sync": org.octopus_last_sync.isoformat() if org.octopus_last_sync else None,
    }


@router.post("/sync")
def sync_octopus(
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    """
    Fetch consumption data from Octopus Energy and store as EnergyReadings.
    Pulls data from the last sync time (or last 30 days if never synced).
    """
    org = _get_org(db, current_user)
    if not org.octopus_api_key:
        raise HTTPException(status_code=400, detail="Octopus Energy is not connected. Go to Settings → Data Connections.")

    _require_core(org)

    now = datetime.now(timezone.utc)
    period_from = org.octopus_last_sync or (now - timedelta(days=30))

    url = (
        f"{OCTOPUS_BASE}/electricity-meter-points/{org.octopus_mpan}"
        f"/meters/{org.octopus_meter_serial}/consumption/"
        f"?page_size=1500"
        f"&period_from={period_from.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"&period_to={now.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"&order_by=period"
    )

    try:
        resp = httpx.get(url, auth=(org.octopus_api_key, ""), timeout=30)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Could not reach Octopus Energy API.")

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Octopus API error: {resp.status_code}")

    results = resp.json().get("results", [])
    imported = 0

    for item in results:
        interval_start = item.get("interval_start")
        consumption = item.get("consumption")
        if interval_start is None or consumption is None:
            continue

        # Parse ISO timestamp
        try:
            ts = datetime.fromisoformat(interval_start.replace("Z", "+00:00"))
        except ValueError:
            continue

        # Avoid duplicates — check if a reading with this exact timestamp already exists
        exists = (
            db.query(EnergyReading)
            .filter(
                EnergyReading.organization_id == org.id,
                EnergyReading.timestamp == ts,
                EnergyReading.zone == "Octopus Import",
            )
            .first()
        )
        if exists:
            continue

        reading = EnergyReading(
            timestamp=ts,
            consumption_kwh=float(consumption),
            zone="Octopus Import",
            facility_id=1,
            organization_id=org.id,
        )
        db.add(reading)
        imported += 1

    org.octopus_last_sync = now
    db.commit()

    return {
        "imported": imported,
        "last_sync": now.isoformat(),
        "period_from": period_from.isoformat(),
        "period_to": now.isoformat(),
    }


@router.delete("/disconnect")
def disconnect_octopus(
    db: Session = Depends(get_db),
    current_user: User = ManagerOrAdmin,
):
    """Remove Octopus credentials from the organisation."""
    org = _get_org(db, current_user)
    org.octopus_api_key = None
    org.octopus_mpan = None
    org.octopus_meter_serial = None
    org.octopus_last_sync = None
    db.commit()
    return {"status": "disconnected"}
