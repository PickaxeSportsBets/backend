from datetime import datetime
import asyncio
import httpx
from fastapi import APIRouter, Depends

import stripe
from stripe import StripeError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import os
from dotenv import load_dotenv
import hmac
import hashlib
from sqlalchemy.orm import Session
from src.db.db import get_db
from src.db.models import Subscriptions, FreeBet
from sqlalchemy.dialects.postgresql import insert
from src.auth import verify_auth_token, TokenPayload
from sqlalchemy import text, and_
from datetime import timezone

load_dotenv()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
# Environment variables
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
CLERK_API_BASE = "https://api.clerk.dev/v1"

router = APIRouter(tags=["stripeRouter"])


class CheckoutRequest(BaseModel):
    price_id: str
    clerk_user_id: str
    email: Optional[str] = None


@router.post("/create-checkout-session")
async def create_checkout_session(request: CheckoutRequest):
    print("Received request:", request)  # Debug log
    try:
        # Create or retrieve Stripe customer
        customer = None
        customers = stripe.Customer.list(email=request.email)
        if customers.data:
            customer = customers.data[0]
            # Update existing customer metadata
            stripe.Customer.modify(
                customer.id,
                metadata={
                    "clerk_user_id": request.clerk_user_id
                }
            )
        else:
            customer = stripe.Customer.create(
                email=request.email,
                metadata={
                    "clerk_user_id": request.clerk_user_id
                }
            )

        print("Customer metadata:", customer.metadata)  # Debug log

        # Create checkout session with metadata
        checkout_session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=['card'],
            line_items=[{
                'price': request.price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{"https://pickaxebets.com"}/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{"https://pickaxebets.com"}/cancel',
            metadata={
                "clerk_user_id": request.clerk_user_id
            },
            allow_promotion_codes=True,
            subscription_data={
                "metadata": {
                    "clerk_user_id": request.clerk_user_id
                }
            }
        )

        print("Session metadata:", checkout_session.metadata)  # Debug log
        return JSONResponse({"url": checkout_session.url})

    except stripe.error.StripeError as e:
        print("Stripe error:", str(e))  # Debug log
        raise HTTPException(status_code=400, detail=str(e))


class PortalRequest(BaseModel):
    customer_id: Optional[str] = None  # Make customer_id optional
    return_url: str
    email: Optional[str] = None


@router.post("/create-portal-session")
async def create_portal_session(request: PortalRequest):
    try:
        customer_id = request.customer_id

        # If no customer_id but email is provided, try to find customer
        if not customer_id and request.email:
            customers = stripe.Customer.list(email=request.email, limit=1)
            if customers.data:
                customer_id = customers.data[0].id
            else:
                raise HTTPException(
                    status_code=404,
                    detail="No customer found with this email"
                )

        if not customer_id:
            raise HTTPException(
                status_code=400,
                detail="Either customer_id or email must be provided"
            )

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.return_url
        )
        return JSONResponse({"url": session.url})
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def handle_subscription_created(subscription):
    """Handle new subscription creation"""
    try:
        clerk_user_id = subscription.metadata.get('clerk_user_id')
        if not clerk_user_id:
            print("No clerk_user_id found in subscription metadata")
            return

        # Get subscription details and convert datetimes to ISO strings
        subscription_data = {
            "subscription_id": subscription.id,
            "customer_id": subscription.customer,
            "status": subscription.status,
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end).isoformat(),
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start).isoformat(),
            "clerk_user_id": clerk_user_id
        }

        print(f"Subscription data: {subscription_data}")

        # Update Clerk user metadata
        await update_clerk_user_metadata(
            clerk_user_id,
            {
                "public_metadata": {
                    "role": "subscriber",
                    "subscription_status": subscription.status,
                    "subscription_id": subscription.id,
                    "plan": subscription.plan.nickname or subscription.plan.id,
                    "stripe_customer_id": subscription.customer
                }
            }
        )

        # Get database session
        db = next(get_db())
        try:
            # Check if subscription already exists
            existing_sub = db.query(Subscriptions).filter(
                Subscriptions.subscription_id == subscription.id
            ).first()

            if not existing_sub:
                # Create new subscription
                new_subscription = Subscriptions(**subscription_data)
                db.add(new_subscription)
                db.commit()
                print(f"Created new subscription: {new_subscription}")
            else:
                print(f"Subscription already exists: {existing_sub}")

        finally:
            db.close()

    except Exception as e:
        print(f"Error in handle_subscription_created: {e}")
        print(f"Full error details: {str(e)}")


async def handle_subscription_updated(subscription):
    """Handle subscription updates with retry logic"""
    max_retries = 3
    retry_delay = 1  # seconds

    for attempt in range(max_retries):
        try:
            clerk_user_id = subscription.metadata.get('clerk_user_id')
            if not clerk_user_id:
                print("No clerk_user_id found in subscription metadata")
                return

            # Update subscription data with ISO format dates
            subscription_data = {
                "status": subscription.status,
                "current_period_end": datetime.fromtimestamp(subscription.current_period_end).isoformat(),
                "current_period_start": datetime.fromtimestamp(subscription.current_period_start).isoformat(),
            }

            print(f"Attempt {attempt + 1}: Processing subscription update")
            print(f"Subscription ID: {subscription.id}")
            print(f"Update data: {subscription_data}")

            # Get database session
            db = next(get_db())
            try:
                # Check if subscription exists
                current_subscription = db.query(Subscriptions).filter(
                    Subscriptions.subscription_id == subscription.id
                ).first()

                if not current_subscription:
                    if attempt < max_retries - 1:
                        print(
                            f"Subscription not found, attempt {attempt + 1}/{max_retries}")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        # On final attempt, create the subscription if it doesn't exist
                        full_subscription_data = {
                            "subscription_id": subscription.id,
                            "customer_id": subscription.customer,
                            "status": subscription.status,
                            "current_period_end": datetime.fromtimestamp(subscription.current_period_end).isoformat(),
                            "current_period_start": datetime.fromtimestamp(subscription.current_period_start).isoformat(),
                            "clerk_user_id": clerk_user_id
                        }
                        new_subscription = Subscriptions(
                            **full_subscription_data)
                        db.add(new_subscription)
                        db.commit()
                        print(
                            f"Created new subscription on final attempt: {new_subscription}")
                else:
                    # Update existing subscription
                    for key, value in subscription_data.items():
                        setattr(current_subscription, key, value)
                    db.commit()
                    print(
                        f"Updated existing subscription: {current_subscription}")

                # Update Clerk metadata
                await update_clerk_user_metadata(
                    clerk_user_id,
                    {
                        "public_metadata": {
                            "role": "subscriber",
                            "subscription_status": subscription.status,
                            "subscription_id": subscription.id,
                            "plan": subscription.plan.nickname or subscription.plan.id,
                            "stripe_customer_id": subscription.customer
                        }
                    }
                )

                break  # Success, exit the retry loop

            finally:
                db.close()

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Attempt {attempt + 1} failed, retrying... Error: {e}")
                await asyncio.sleep(retry_delay)
            else:
                print(f"All attempts failed: {e}")
                raise


async def handle_subscription_deleted(subscription):
    """Handle subscription deletions"""
    try:
        clerk_user_id = subscription.metadata.get('clerk_user_id')
        if not clerk_user_id:
            return

        # Update Clerk metadata
        await update_clerk_user_metadata(
            clerk_user_id,
            {
                "public_metadata": {
                    "role": "user",
                    "subscription_status": "cancelled",
                    "subscription_id": None
                }
            }
        )

        # Get database session
        db = next(get_db())
        try:
            # Update subscription status in database
            subscription_record = db.query(Subscriptions).filter(
                Subscriptions.subscription_id == subscription.id
            ).first()

            if subscription_record:
                subscription_record.status = "cancelled"
                db.commit()
                print(
                    f"Updated subscription status to cancelled: {subscription_record}")

        finally:
            db.close()

    except Exception as e:
        print(f"Error in handle_subscription_deleted: {e}")


async def update_clerk_user_metadata(user_id: str, metadata: dict):
    """Helper function to update Clerk user metadata"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{CLERK_API_BASE}/users/{user_id}/metadata",
                headers={
                    "Authorization": f"Bearer {CLERK_SECRET_KEY}",
                    "Content-Type": "application/json"
                },
                json=metadata
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        print(f"Error updating Clerk metadata: {e}")
        raise


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError as e:
        print(f"ValueError in webhook: {str(e)}")
        raise HTTPException(status_code=400, detail='Invalid payload')
    except stripe.error.SignatureVerificationError as e:
        print(f"SignatureVerificationError in webhook: {str(e)}")
        raise HTTPException(status_code=400, detail='Invalid signature')

    try:
        if event.type == 'customer.subscription.created':
            print("Processing subscription created")
            await handle_subscription_created(event.data.object)
        elif event.type == 'customer.subscription.updated':
            print("Processing subscription updated")
            await handle_subscription_updated(event.data.object)
        elif event.type == 'customer.subscription.deleted':
            print("Processing subscription deleted")
            await handle_subscription_deleted(event.data.object)

        return JSONResponse({'status': 'success'})
    except Exception as e:
        print(f"Error processing webhook: {e}")
        print(f"Full error details: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error processing webhook: {str(e)}")


@router.get("/get-subscriptions/{clerk_user_id}")
async def get_subscriptions(clerk_user_id: str, db: Session = Depends(get_db), token: TokenPayload = Depends(verify_auth_token)):
    try:
        # Get all subscriptions from the database
        subscriptions = db.query(Subscriptions).filter(
            Subscriptions.clerk_user_id == clerk_user_id).all()

        # Return the subscriptions as a list of dictionaries
        return [subscription for subscription in subscriptions]
    except Exception as e:
        print(f"Error in get_subscriptions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error in get_subscriptions: {str(e)}")


@router.get("/get-subscriptions-active/{clerk_user_id}")
async def get_subscriptions(clerk_user_id: str, db: Session = Depends(get_db)):
    try:
        # Get all subscriptions from the database
        subscriptions = db.query(Subscriptions).filter(
            and_(Subscriptions.clerk_user_id == clerk_user_id,
                 Subscriptions.status != "cancelled")
        ).all()

        # Return the subscriptions as a list of dictionaries
        return [subscription for subscription in subscriptions]
    except Exception as e:
        print(f"Error in get_subscriptions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error in get_subscriptions: {str(e)}")


@router.get("/get-free-bet/{clerk_user_id}")
async def get_free_bet(clerk_user_id: str, db: Session = Depends(get_db)):
    try:
        free_bet = db.query(FreeBet).filter(
            FreeBet.clerk_id == clerk_user_id
        ).first()

        if not free_bet:
            return {
                "code": 404,
                "status": "Not Found",
                "message": "No free bet record found for this user"
            }

        return {
            "code": 200,
            "status": "Success",
            "data": {
                "clerk_id": free_bet.clerk_id,
                "lastRedemptionDate": free_bet.lastRedemptionDate
            }
        }
    except Exception as e:
        print(f"Error in get_free_bet: {str(e)}")
        db.rollback()
        return {
            "code": 500,
            "status": "Error",
            "message": str(e)
        }


@router.post("/upsert-free-bet/{clerk_user_id}")
async def upsert_free_bet(clerk_user_id: str, db: Session = Depends(get_db)):
    try:
        stmt = insert(FreeBet).values({
            "clerk_id": clerk_user_id,
            "lastRedemptionDate": datetime.now(timezone.utc)
        })

        stmt = stmt.on_conflict_do_update(
            index_elements=["clerk_id"],
            set_={
                "lastRedemptionDate": stmt.excluded.lastRedemptionDate,
            }
        )

        db.execute(stmt)
        db.commit()

        free_bet = db.query(FreeBet).filter(
            FreeBet.clerk_id == clerk_user_id
        ).first()

        return {
            "code": 200,
            "status": "Success",
            "data": {
                "clerk_id": free_bet.clerk_id,
                "lastRedemptionDate": free_bet.lastRedemptionDate,
            }
        }
    except Exception as e:
        print(f"Error in upsert_free_bet: {str(e)}")
        db.rollback()
        return {
            "code": 500,
            "status": "Error",
            "error": str(e)
        }
