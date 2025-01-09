from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from ..findBets import OddsArbitrageFinder
from datetime import datetime, timezone
import pandas as pd
import json

load_dotenv()

url: str = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
key: str = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
supabase: Client = create_client(url, key)

router = APIRouter(tags=["db"])


@router.get("/")
async def read_root():
    return {"Hello": "World"}

@router.get("/arbitrage")
async def update_db():
    api_key = os.environ.get("ODDS_API_KEY")
    arbitrage_finder = OddsArbitrageFinder(api_key)
    data = arbitrage_finder.run_update_db()

    arbitrage_df = data[0]
    arbitrage_json = json.loads(arbitrage_df.to_json(orient="records"))

    return arbitrage_json

@router.get("/plus_ev")
async def update_db():
    api_key = os.environ.get("ODDS_API_KEY")
    arbitrage_finder = OddsArbitrageFinder(api_key)
    data = arbitrage_finder.run_update_db()

    plus_ev_df = data[1]

    plus_ev_json = json.loads(plus_ev_df.to_json(orient="records"))

    return plus_ev_json

@router.get("/items/")
async def read_items(q: str = Query(None)):
    if q:
        return {"q": q}
    return {"q": "None"}
