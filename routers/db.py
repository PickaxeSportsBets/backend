from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from src.findBets import OddsArbitrageFinder
from datetime import datetime, timezone
import pandas as pd
import json

load_dotenv()

url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

router = APIRouter(tags=["db"])


@router.get("/evBets")
async def get_ev_bets():
    response = supabase.table("plus_ev").select(
        "*").order('ev_percentage', desc=True).execute()
    data = response.data
    return data


@router.get("/arbitrageBets")
async def get_arbitrage_bets():
    response = supabase.table("arbitrage").select("*").execute()
    data = response.data
    return data


@router.get("/")
async def read_root():
    return {"Hello": "World"}


@router.post("/update_db")
async def update_db():
    api_key = os.environ.get("ODDS_API_KEY")
    arbitrage_finder = OddsArbitrageFinder(api_key)
    data = arbitrage_finder.run_update_db()

    arbitrage_df = data[0]
    arbitrage_json = json.loads(arbitrage_df.to_json(orient="records"))

    plus_ev_df = data[1]
    plus_ev_json = json.loads(plus_ev_df.to_json(orient="records"))

    (supabase.table("arbitrage").upsert(arbitrage_json).execute())
    (supabase.table("arbitrage")).delete().lt(
        "commence_time", datetime.now(timezone.utc)).execute()

    (supabase.table("plus_ev").upsert(plus_ev_json).execute())
    (supabase.table("plus_ev")).delete().lt(
        "commence_time", datetime.now(timezone.utc)).execute()

    return {
        "code": 200,
        "status": "Success"
    }


@router.get("/items/")
async def read_items(q: str = Query(None)):
    if q:
        return {"q": q}
    return {"q": "None"}
