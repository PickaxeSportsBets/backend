from fastapi import APIRouter
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import logging
from contextlib import contextmanager, asynccontextmanager
from fastapi import FastAPI
import asyncio
from datetime import datetime, timezone
from clerk_backend_api import Clerk

load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")

supabase: Client = create_client(url, key)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting email cron background task")
    task = asyncio.create_task(send_daily_email())
    yield
    logger.info("Shutting down email cron background task")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

router = APIRouter(tags=["emailCron"])


@router.get("/emailCron")
async def emailCron():
    return {"message": "Email cron job"}


async def send_daily_email():
    while True:
        try:
            active_subscriptions = supabase.table("subscriptions").select(
                "*").eq("status", "active").lt("current_period_end", datetime.now(timezone.utc)).execute()

            logger.info("Sending daily email")
            users = set(subscription["clerk_user_id"]
                        for subscription in active_subscriptions.data)

            with Clerk(bearer_auth=os.environ.get("CLERK_SECRET_KEY")) as clerk:
                for user_id in users:
                    try:
                        user = clerk.users.get(user_id=user_id)
                        name = user.first_name + " " + user.last_name
                        email = user.email_addresses[0].email_address
                        logger.info(f"Name: {name}, Email: {email}")
                    except Exception as user_error:
                        logger.error(f"Error fetching user {
                                     user_id}: {str(user_error)}")
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Error in send_daily_email: {str(e)}")
            await asyncio.sleep(10)
