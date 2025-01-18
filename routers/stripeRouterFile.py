from datetime import datetime
import asyncio
import httpx
import stripe
from stripe import StripeError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import os
from dotenv import load_dotenv
import supabase
from supabase import create_client, Client
import hmac
import hashlib

load_dotenv()


stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)
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
    print(request)
    try:
        # Create or retrieve Stripe customer
        customer = None
        customers = stripe.Customer.list(email=request.email)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=request.email,
                metadata={
                    "clerk_user_id": request.clerk_user_id
                }
            )

        # Create checkout session
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
            }
        )

        return JSONResponse({"url": checkout_session.url})

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PortalRequest(BaseModel):
    customer_id: str
    return_url: str


@router.post("/create-portal-session")
async def create_portal_session(request: PortalRequest):
    try:
        session = stripe.billing_portal.Session.create(
            customer=request.customer_id,
            return_url=request.return_url
        )
        return JSONResponse({"url": session.url})
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


load_dotenv()


async def handle_subscription_created(subscription):
    """Handle new subscription creation"""
    try:
        clerk_user_id = subscription.metadata.get('clerk_user_id')
        if not clerk_user_id:
            print("No clerk_user_id found in subscription metadata")
            return

        # Get subscription details
        subscription_data = {
            "subscription_id": subscription.id,
            "customer_id": subscription.customer,
            "status": subscription.status,
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
            "clerk_user_id": clerk_user_id
        }

        # Update Clerk user metadata
        await update_clerk_user_metadata(
            clerk_user_id,
            {
                "public_metadata": {
                    "role": "subscriber",
                    "subscription_status": subscription.status,
                    "subscription_id": subscription.id,
                    "plan": subscription.plan.nickname or subscription.plan.id
                }
            }
        )

        # Store subscription in Supabase
        data, error = supabase.table("subscriptions").insert(
            subscription_data).execute()
        if error:
            print(f"Error storing subscription in Supabase: {error}")

    except Exception as e:
        print(f"Error in handle_subscription_created: {e}")


async def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    try:
        clerk_user_id = subscription.metadata.get('clerk_user_id')
        if not clerk_user_id:
            return

        # Update subscription data
        subscription_data = {
            "status": subscription.status,
            "current_period_end": datetime.fromtimestamp(subscription.current_period_end),
            "current_period_start": datetime.fromtimestamp(subscription.current_period_start),
        }

        # Update Clerk metadata
        await update_clerk_user_metadata(
            clerk_user_id,
            {
                "public_metadata": {
                    "subscription_status": subscription.status,
                }
            }
        )

        # Update Supabase
        data, error = supabase.table("subscriptions")\
            .update(subscription_data)\
            .eq("subscription_id", subscription.id)\
            .execute()

        if error:
            print(f"Error updating subscription in Supabase: {error}")

    except Exception as e:
        print(f"Error in handle_subscription_updated: {e}")


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

        # Update Supabase
        data, error = supabase.table("subscriptions")\
            .update({"status": "cancelled"})\
            .eq("subscription_id", subscription.id)\
            .execute()

        if error:
            print(f"Error updating subscription in Supabase: {error}")

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
        raise HTTPException(status_code=400, detail='Invalid payload')
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail='Invalid signature')

    try:
        if event.type == 'customer.subscription.created':
            await handle_subscription_created(event.data.object)
        elif event.type == 'customer.subscription.updated':
            await handle_subscription_updated(event.data.object)
        elif event.type == 'customer.subscription.deleted':
            await handle_subscription_deleted(event.data.object)

        return JSONResponse({'status': 'success'})
    except Exception as e:
        print(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Error processing webhook")
