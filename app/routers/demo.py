"""
Public demo-booking endpoint.
Accepts form submissions from /demo and sends the details to the GreenPulse inbox via Resend.
No authentication required.
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr

from app.email import send_demo_request_email
from app.limiter import limiter

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoRequest(BaseModel):
    full_name: str
    business_name: str
    email: EmailStr
    phone: str
    preferred_date: str   # "YYYY-MM-DD"
    preferred_time: str   # e.g. "10:00 AM"
    message: str = ""


@router.post("/request")
@limiter.limit("10/hour")
def submit_demo_request(body: DemoRequest, request: Request):
    """Send a demo booking request to the GreenPulse team inbox."""
    send_demo_request_email(
        full_name=body.full_name,
        business_name=body.business_name,
        email=body.email,
        phone=body.phone,
        preferred_date=body.preferred_date,
        preferred_time=body.preferred_time,
        message=body.message,
    )
    return {"status": "sent"}
