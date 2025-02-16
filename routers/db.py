from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from src.findBets import OddsArbitrageFinder
from datetime import datetime, timezone
import pandas as pd
import json
from src.findBets import OddsArbitrageFinderV2
from src.findBets import OddsArbitrageFinderV3
from src.findBets import OddsArbitrageFinderV4
from src.findBets import OddsArbitrageFinderV5
from src.findBets import get_all_data
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


# Deprecated
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


def sanitize_value(value):
    """Convert None to a default value for double precision columns."""
    return value if value is not None else 0.0


def sanitize_data(data):
    """Sanitize all values in the data dictionary."""
    # Define columns and their expected types
    integer_columns = ['odds', 'fair_odds']  # these need to be integers
    # these need to be floats
    float_columns = ['market_point', 'ev_percentage']

    for record in data:
        # Handle integer columns
        for key in integer_columns:
            if key in record:
                if record[key] is None:
                    record[key] = 0
                else:
                    try:
                        # First convert to float to handle any decimal values, then to int
                        record[key] = int(float(str(record[key])))
                    except (ValueError, TypeError):
                        record[key] = 0

        # Handle float columns
        for key in float_columns:
            if key in record:
                if record[key] is None:
                    record[key] = 0.0
                else:
                    try:
                        # Convert to float, handling string inputs
                        record[key] = float(str(record[key]))
                    except (ValueError, TypeError):
                        record[key] = 0.0

    return data


@router.post("/update_dbV2")
async def update_db():
    try:
        api_key = os.environ.get("ODDS_API_KEY")
        arbitrage_finder = OddsArbitrageFinderV3(api_key)
        data = await arbitrage_finder.get_dfs()

        # Process arbitrage data
        arbitrage_df = data[0]
        if not arbitrage_df.empty:
            arbitrage_df = arbitrage_df.drop_duplicates(subset=['primary_key'])
            arbitrage_json = json.loads(arbitrage_df.to_json(orient="records"))
            arbitrage_json = sanitize_data(arbitrage_json)

            # Delete all existing records from arbitrage table with a WHERE clause
            supabase.table("arbitrage").delete().neq(
                'primary_key', 'no_match').execute()

            # Insert new records in batches
            batch_size = 100
            for i in range(0, len(arbitrage_json), batch_size):
                batch = arbitrage_json[i:i + batch_size]
                supabase.table("arbitrage").insert(batch).execute()

        # Process plus_ev data
        plus_ev_df = data[1]
        if not plus_ev_df.empty:
            plus_ev_df = plus_ev_df.drop_duplicates(subset=['primary_key'])
            plus_ev_json = json.loads(plus_ev_df.to_json(orient="records"))
            plus_ev_json = sanitize_data(plus_ev_json)

            # Delete all existing records from plus_ev table with a WHERE clause
            supabase.table("plus_ev").delete().neq(
                'primary_key', 'no_match').execute()

            # Insert new records in batches
            for i in range(0, len(plus_ev_json), batch_size):
                batch = plus_ev_json[i:i + batch_size]
                supabase.table("plus_ev").insert(batch).execute()
                supabase.table("ev_graph").insert(batch).execute()

        return {
            "code": 200,
            "status": "Success",
            "message": "Database updated successfully"
        }
    except Exception as e:
        # Log the full error for debugging
        print(f"Error in update_db: {str(e)}")
        return {
            "code": 500,
            "status": "Error",
            "message": str(e)
        }


@router.post("/update_dbV3")
async def update_db():
    try:
        data = await get_all_data()
        print(data)
        return {
            "code": 200,
            "status": "Success",
            "message": "Database updated successfully"
        }
    except Exception as e:
        print(f"Error in update_dbV3: {str(e)}")
        return {
            "code": 500,
            "status": "Error",
            "message": str(e)
        }


@router.delete("/delete_old_ev_graph")
async def delete_old_ev_graph():
    try:
        supabase.table("ev_graph").delete().lt(
            "commence_time", datetime.now(timezone.utc)).execute()
        return {
            "code": 200,
            "status": "Success",
            "message": "Old ev_graph data deleted successfully"
        }
    except Exception as e:
        # Log the full error for debugging
        print(f"Error in delete_old_ev_graph: {str(e)}")
        return {
            "code": 500,
            "status": "Error",
            "message": str(e)
        }
