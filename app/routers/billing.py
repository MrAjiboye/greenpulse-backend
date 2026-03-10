import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Organization, User
from app.auth import get_current_user

stripe.api_key = settings.STRIPE_SECRET_KEY

PRICE_IDS = {
    "core": settings.STRIPE_PRICE_CORE,
    "pro":  settings.STRIPE_PRICE_PRO,
}

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    plan: str  # "core" or "pro"


@router.post("/create-checkout-session")
def create_checkout_session(
    body: CheckoutRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.plan not in PRICE_IDS:
        raise HTTPException(status_code=400, detail="Invalid plan. Must be 'core' or 'pro'.")

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found.")

    price_id = PRICE_IDS[body.plan]

    kwargs = dict(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        billing_address_collection="required",
        metadata={"organization_id": str(org.id), "plan": body.plan},
        success_url=f"{settings.FRONTEND_URL}/dashboard?upgraded=1",
        cancel_url=f"{settings.FRONTEND_URL}/pricing",
    )

    if org.stripe_customer_id:
        kwargs["customer"] = org.stripe_customer_id
    else:
        kwargs["customer_email"] = current_user.email

    session = stripe.checkout.Session.create(**kwargs)
    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature.")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        org_id = int(data.get("metadata", {}).get("organization_id", 0))
        plan = data.get("metadata", {}).get("plan", "core")
        customer_id = data.get("customer")

        org = db.query(Organization).filter(Organization.id == org_id).first()
        if org:
            org.plan = plan
            if customer_id:
                org.stripe_customer_id = customer_id
            db.commit()

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        items = data.get("items", {}).get("data", [])
        price_id = items[0]["price"]["id"] if items else None

        plan = None
        if price_id == settings.STRIPE_PRICE_CORE:
            plan = "core"
        elif price_id == settings.STRIPE_PRICE_PRO:
            plan = "pro"

        if customer_id and plan:
            org = db.query(Organization).filter(
                Organization.stripe_customer_id == customer_id
            ).first()
            if org:
                org.plan = plan
                db.commit()

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id:
            org = db.query(Organization).filter(
                Organization.stripe_customer_id == customer_id
            ).first()
            if org:
                org.plan = "free"
                db.commit()

    return JSONResponse(content={"received": True})


@router.get("/status")
def billing_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    return {
        "plan": org.plan if org else "free",
        "stripe_customer_id": org.stripe_customer_id if org else None,
    }
