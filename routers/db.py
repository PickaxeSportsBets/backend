from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from src.findBets import OddsArbitrageFinder
from datetime import datetime, timezone
import pandas as pd
import json
from src.findBets import OddsArbitrageFinderV2
import logging
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


@router.post("/refresh_ev")
async def update_ev():
    api_key = os.environ.get("ODDS_API_KEY")
    arbitrage_finder = OddsArbitrageFinder(api_key)
    data = arbitrage_finder.generate_ev_ONLY()
    ev_json = json.loads(data.to_json(orient="records"))
    (supabase.table("plus_ev").upsert(ev_json).execute())
    (supabase.table("plus_ev")).delete().lt(
        "commence_time", datetime.now(timezone.utc)).execute()

    return {
        "code": 200,
        "status": "Success"
    }


@router.post('/refresh_arbitrage')
async def updage_arb():
    api_key = os.environ.get("ODDS_API_KEY")
    arbitrage_finder = OddsArbitrageFinder(api_key)
    data = arbitrage_finder.generate_arb_only()
    arbitrage_json = json.loads(data.to_json(orient="records"))
    (supabase.table("arbitrage").upsert(arbitrage_json).execute())
    (supabase.table("arbitrage")).delete().lt(
        "commence_time", datetime.now(timezone.utc)).execute()


@router.post("/update_db")
async def update_db():
    try:
        api_key = os.environ.get("ODDS_API_KEY")
        arbitrage_finder = OddsArbitrageFinder(api_key)
        data = arbitrage_finder.run_update_db()

        # Process arbitrage data
        arbitrage_df = data[0]
        if not arbitrage_df.empty:
            # Remove duplicates based on primary_key before converting to JSON
            arbitrage_df = arbitrage_df.drop_duplicates(subset=['primary_key'])
            arbitrage_json = json.loads(arbitrage_df.to_json(orient="records"))

            # First delete old records
            supabase.table("arbitrage").delete().lt(
                "commence_time", datetime.now(timezone.utc)).execute()

            # Then insert new records in batches to prevent overwhelming the database
            batch_size = 100
            for i in range(0, len(arbitrage_json), batch_size):
                batch = arbitrage_json[i:i + batch_size]
                supabase.table("arbitrage").upsert(
                    batch,
                    on_conflict='primary_key',
                    ignore_duplicates=True
                ).execute()

        # Process plus_ev data
        plus_ev_df = data[1]
        if not plus_ev_df.empty:
            # Remove duplicates based on primary_key before converting to JSON
            plus_ev_df = plus_ev_df.drop_duplicates(subset=['primary_key'])
            plus_ev_json = json.loads(plus_ev_df.to_json(orient="records"))

            # First delete old records
            supabase.table("plus_ev").delete().lt(
                "commence_time", datetime.now(timezone.utc)).execute()

            # Then insert new records in batches
            for i in range(0, len(plus_ev_json), batch_size):
                batch = plus_ev_json[i:i + batch_size]
                supabase.table("plus_ev").upsert(
                    batch,
                    on_conflict='primary_key',
                    ignore_duplicates=True
                ).execute()

        return {
            "code": 200,
            "status": "Success",
            "message": "Database updated successfully"
        }

    except Exception as e:
        # Log the error for debugging
        logging.error(f"Error in update_db: {str(e)}", exc_info=True)

        # Return a more detailed error response
        return {
            "code": 500,
            "status": "Error",
            "message": str(e),
            "type": type(e).__name__
        }


@router.post("/update_dbV2")
async def update_db():
    try:
        api_key = os.environ.get("ODDS_API_KEY")
        arbitrage_finder = OddsArbitrageFinderV2(api_key)
        data = await arbitrage_finder.get_dfs()

        # Process arbitrage data
        arbitrage_df = data[0]
        if not arbitrage_df.empty:
            arbitrage_df = arbitrage_df.drop_duplicates(subset=['primary_key'])
            arbitrage_json = json.loads(arbitrage_df.to_json(orient="records"))

            supabase.table("arbitrage").delete().lt(
                "commence_time", datetime.now(timezone.utc)).execute()

            batch_size = 100
            for i in range(0, len(arbitrage_json), batch_size):
                batch = arbitrage_json[i:i + batch_size]
                supabase.table("arbitrage").upsert(
                    batch,
                    on_conflict='primary_key',
                    ignore_duplicates=True
                ).execute()

        # Process plus_ev data
        plus_ev_df = data[1]
        if not plus_ev_df.empty:
            plus_ev_df = plus_ev_df.drop_duplicates(subset=['primary_key'])
            plus_ev_json = json.loads(plus_ev_df.to_json(orient="records"))

            supabase.table("plus_ev").delete().lt(
                "commence_time", datetime.now(timezone.utc)).execute()

            for i in range(0, len(plus_ev_json), batch_size):
                batch = plus_ev_json[i:i + batch_size]
                supabase.table("plus_ev").upsert(
                    batch,
                    on_conflict='primary_key',
                    ignore_duplicates=True
                ).execute()

        return {
            "code": 200,
            "status": "Success",
            "message": "Database updated successfully"
        }

    except Exception as e:
        # Log the error for debugging
        logging.error(f"Error in update_db: {str(e)}", exc_info=True)

        # Return a more detailed error response
        return {
            "code": 500,
            "status": "Error",
            "message": str(e),
            "type": type(e).__name__
        }
