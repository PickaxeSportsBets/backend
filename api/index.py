from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
from routers import db_router, stripe_router, utilites_router
from routers.emailCron import router as email_cron_router, lifespan
import jwt
import os
from typing import Optional
from datetime import datetime
import logging
from src.auth import verify_auth_token, TokenPayload
from src.db import get_db
from src.db.models import *

from sqlalchemy.orm import Session
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    docs_url="/api/py/docs",
    openapi_url="/api/py/openapi.json",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pickaxe-website-production.up.railway.app",
        "http://localhost:3000",
        "https://localhost:3000",
        "https://pickaxebets.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(db_router, prefix="/api/v1/db")
app.include_router(stripe_router, prefix="/api")
app.include_router(utilites_router, prefix="/api/v1/utilites")
app.include_router(email_cron_router, prefix="/api/v1/emailCron")
JWT_SECRET = os.getenv("JWT_SECRET")
security = HTTPBearer()


@app.get("/api/py/helloFastApi")
def hello_fast_api():
    return {"message": "Hello from FastAPI"}


class SubscriptionResponse(BaseModel):
    is_subscribed: bool
    subscription_status: str
    active_plans: List[str]
    user_id: str


@app.get("/protected")
async def protected_route(token: TokenPayload = Depends(verify_auth_token), db: Session = Depends(get_db)):
    """Route for authenticated users (subscription not required). With the user ID, then check the subscription status"""
    user_id = token.userId
    subscriptions = db.query(Subscriptions).filter(
        Subscriptions.clerk_user_id == user_id).all()
    if len(subscriptions) == 0:
        return {
            "message": "Protected content",
            "user": token.userId,
            "isSubscribed": False,
            "subscriptionStatus": "No subscription found"
        }
    for subscription in subscriptions:
        if subscription.status == "active" or subscription.current_period_end > datetime.datetime.now(datetime.UTC):
            return {
                "message": "Protected content",
                "user": token.userId,
                "isSubscribed": True,
                "subscriptionStatus": subscription.status
            }
    return {
        "message": "Protected content",
        "user": token.userId,
        "isSubscribed": False,
        "subscriptionStatus": "No active subscription found"
    }

# run: uvicorn api:app --reload


# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=8000)
