from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv
import jwt
import os
from typing import Optional
from datetime import datetime
import logging
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET")
security = HTTPBearer()


class TokenPayload(BaseModel):
    userId: str
    isSubscribed: bool
    subscriptionStatus: str
    activePlans: list[str]
    exp: int
    iat: int


async def verify_auth_token(
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> TokenPayload:
    """Verify token is valid but don't check subscription status"""
    try:
        token = credentials.credentials
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=401,
                detail="Token expired",
                headers={"WWW-Authenticate": "Bearer"}
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"}
            )

        # Only verify token expiration
        if payload.get("exp", 0) < datetime.utcnow().timestamp():
            raise HTTPException(
                status_code=401,
                detail="Token expired",
                headers={"WWW-Authenticate": "Bearer"}
            )

        return TokenPayload(**payload)

    except Exception as e:
        logger.error(f"Token verification error: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Authentication failed",
            headers={"WWW-Authenticate": "Bearer"}
        )


async def verify_subscription_token(
    token: TokenPayload = Depends(verify_auth_token)
) -> TokenPayload:
    """Verify token and check subscription status"""
    if not token.isSubscribed:
        raise HTTPException(
            status_code=403,
            detail="Active subscription required"
        )
    return token
