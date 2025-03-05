from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from src.findBets import OddsArbitrageFinder
from datetime import datetime, timezone
import pandas as pd
import json
from src.findBets import get_all_data
import asyncio
import logging
import time
import traceback
import orjson
import gzip
import base64
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


@router.post("/update_dbV3")
async def update_db():
    try:
        data = await get_all_data()
        arb_df = data[1]
        ev_df = data[2]
        arb_json = json.loads(arb_df.to_json(orient="records"))
        ev_json = json.loads(ev_df.to_json(orient="records"))

        # Delete all existing records from arbitrage table with a WHERE clause
        supabase.table("arbitrage").delete().neq(
            'primary_key', 'no_match').execute()
        supabase.table("plus_ev").delete().neq(
            'primary_key', 'no_match').execute()
        batch_size = 1000
        for i in range(0, len(arb_json), batch_size):
            batch = arb_json[i:i + batch_size]
            supabase.table("arbitrage").insert(batch).execute()
        for i in range(0, len(ev_json), batch_size):
            batch = ev_json[i:i + batch_size]
            supabase.table("plus_ev").insert(batch).execute()
        # for i in range(0, len(raw_json), batch_size):
        #     batch = raw_json[i:i + batch_size]
        #     supabase.table("ev_graph").insert(batch).execute()

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


# 1. Fetch all data from raw df, then delete the old records in ev graph 2 that are past their commence time.
# 2. Fetch existing records from ev graph 2.
# 3. Split raw df into two parts: existing bets and new bets.
# 4. Process existing bets:
#   - Add new entries to past_data for existing bets.
#   - Create update with ONLY the fields we're changing.
#   - upsert to graph
# 5. Process new bets:
#   - Create full record with initial past_data.
#   - Remove None values.
#   - Insert to graph
# 5:


@router.post("/update_graph")
async def update_db():
    try:
        start_time = time.time()
        data = await get_all_data()
        raw_df = data[0]

        # Clear old records
        clear_start = time.time()
        try:
            supabase.table("ev_graph_v2").delete().or_(
                "commence_time.lt." +
                str(datetime.now(timezone.utc)) + ", commence_time.is.null"
            ).execute()
            print(
                f"Clear old records: {time.time() - clear_start:.2f} seconds")
        except Exception as e:
            print(f"Warning: Error clearing old records: {str(e)}")

        # Fetch existing bet keys only (reducing egress usage)
        fetch_start = time.time()
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                existing_bets_response = supabase.table(
                    "ev_graph_v2").select("bet_key").execute()
                existing_bets_set = {item["bet_key"]
                                     for item in existing_bets_response.data}

                # Fetch existing past data for those bet keys
                past_data_response = supabase.table(
                    "ev_graph_past_data").select("bet_key, past_data").execute()

                # Decompress past data if it is stored as Gzip
                existing_past_data_dict = {}
                for item in past_data_response.data:
                    try:
                        compressed_data = base64.b64decode(item["past_data"])
                        past_data = orjson.loads(
                            gzip.decompress(compressed_data))
                    except:
                        past_data = item["past_data"]  # Fallback to raw JSON

                    existing_past_data_dict[item["bet_key"]] = past_data

                print(
                    f"Fetch existing bet keys & past data: {time.time() - fetch_start:.2f} seconds")
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                print(
                    f"Retry {attempt + 1}/{max_retries} fetching data: {str(e)}")
                await asyncio.sleep(retry_delay)

        # Process records in smaller chunks
        process_start = time.time()
        raw_records = json.loads(raw_df.to_json(orient="records"))
        ev_graph_records = []
        past_data_updates = []
        past_data_inserts = []
        processed_keys = set()

        chunk_size = 500  # Smaller chunk size
        for chunk_start in range(0, len(raw_records), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(raw_records))
            chunk = raw_records[chunk_start:chunk_end]

            for row in chunk:
                bet_key = row.get("bet_key")

                if bet_key in processed_keys:
                    continue
                processed_keys.add(bet_key)

                # Create new history entry with current odds data
                new_entry = {
                    "timestamp": row.get("timestamp"),
                    "odds": row.get("odds"),
                    "bookmaker": row.get("bookmaker"),
                    "team": row.get("team") if row.get("team") else None,
                    "market_point": row.get("market_point") if row.get("market_point") else None,
                }

                # CASE 1: Existing bet - update past_data
                if bet_key in existing_bets_set:
                    past_data = existing_past_data_dict.get(bet_key, [])

                    # Ensure past_data is a list before appending
                    if not isinstance(past_data, list):
                        past_data = [past_data]

                    past_data.append(new_entry)  # Append new entry

                    # Compress JSON using gzip & base64 encode
                    compressed_past_data = base64.b64encode(
                        gzip.compress(orjson.dumps(past_data))
                    ).decode()

                    past_data_updates.append(
                        {"bet_key": bet_key, "past_data": compressed_past_data}
                    )  # Update list

                # CASE 2: New bet - Insert into ev_graph_v2 & initialize past_data
                else:
                    ev_graph_records.append({
                        "bet_key": bet_key,
                        "sport_key": row.get("sport_key"),
                        "game": row.get("game"),
                        "team": row.get("team"),
                        "home_team": row.get("home_team"),
                        "away_team": row.get("away_team"),
                        "sport": row.get("sport"),
                        "market_type": row.get("market_type"),
                        "market_point": row.get("market_point"),
                        "odds": row.get("odds"),
                        "timestamp": row.get("timestamp"),
                        "commence_time": row.get("commence_time"),
                        "bookmaker": row.get("bookmaker"),
                        "link": row.get("link"),
                    })

                    # Initialize past_data for new bet (compressed)
                    compressed_past_data = base64.b64encode(
                        gzip.compress(orjson.dumps([new_entry]))
                    ).decode()

                    past_data_inserts.append(
                        {"bet_key": bet_key, "past_data": compressed_past_data}
                    )

        print(f"Process records: {time.time() - process_start:.2f} seconds")
        print(
            f"Total new records: {len(ev_graph_records)}, Past data updates: {len(past_data_updates)}, Past data inserts: {len(past_data_inserts)}")

        # Function for batch upsert with retry
        async def upsert_with_retry(table, batch, retries=3):
            for attempt in range(retries):
                try:
                    await asyncio.sleep(0.1)  # Small delay between requests
                    supabase.table(table).upsert(
                        batch, on_conflict="bet_key").execute()
                    return True
                except Exception as e:
                    if attempt == retries - 1:
                        print(
                            f"Failed all retries for {table} batch: {str(e)}")
                        return False
                    print(
                        f"Retry {attempt + 1}/{retries} for {table}: {str(e)}")
                    await asyncio.sleep(retry_delay)
            return False

        # Upsert ev_graph_v2 records
        batch_size = 100
        upsert_start = time.time()

        for i in range(0, len(ev_graph_records), batch_size):
            batch = ev_graph_records[i:i + batch_size]
            await upsert_with_retry("ev_graph_v2", batch)

        # Update existing past data in ev_graph_past_data
        for i in range(0, len(past_data_updates), batch_size):
            batch = past_data_updates[i:i + batch_size]
            await upsert_with_retry("ev_graph_past_data", batch)

        # Insert new past data records in ev_graph_past_data
        for i in range(0, len(past_data_inserts), batch_size):
            batch = past_data_inserts[i:i + batch_size]
            await upsert_with_retry("ev_graph_past_data", batch)

        print(f"Upsert operations: {time.time() - upsert_start:.2f} seconds")

        execution_time = time.time() - start_time
        print(f"Execution time: {execution_time:.2f} seconds")

        return {
            "code": 200,
            "status": "Success",
            "message": f"Database updated successfully in {execution_time:.2f} seconds",
            "metrics": {
                "total_records_processed": len(raw_records),
                "unique_records_upserted": len(ev_graph_records),
                "past_data_updates": len(past_data_updates),
                "past_data_inserts": len(past_data_inserts),
                "execution_time_seconds": execution_time,
            },
        }
    except Exception as e:
        print(f"Error in update_db: {str(e)}")
        traceback.print_exc()
        return {
            "code": 500,
            "status": "Error",
            "message": str(e),
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
