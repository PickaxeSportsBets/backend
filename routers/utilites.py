from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from src.findBets import OddsArbitrageFinder
from datetime import datetime, timezone
import pandas as pd
import json
import logging
from src.auth import verify_auth_token, verify_subscription_token, TokenPayload
load_dotenv()

url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

router = APIRouter(tags=["utilites"])
