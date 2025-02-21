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
import asyncio
import logging
import time
import traceback
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
        raw_df = data[0]
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


@router.post("/update_graph")
async def update_db():
    try:
        start_time = time.time()
        data = await get_all_data()
        raw_df = data[0]

        # Only delete records that are past their commence time
        clear_start = time.time()
        supabase.table("ev_graph_v2").delete().lt(
            "commence_time", datetime.now(timezone.utc)).execute()
        print(f"Clear old records: {time.time() - clear_start:.2f} seconds")

        # Fetch existing records - get FULL records to preserve all data
        fetch_start = time.time()
        existing_bets_response = supabase.table(
            "ev_graph_v2").select("*").execute()
        existing_bets = {item['bet_key']: item for item in existing_bets_response.data}
        print(
            f"Fetch existing records: {time.time() - fetch_start:.2f} seconds")

        # Process raw records
        process_start = time.time()
        raw_records = json.loads(raw_df.to_json(orient="records"))
        all_updates = []
        processed_keys = set()

        chunk_size = 1000
        for chunk_start in range(0, len(raw_records), chunk_size):
            chunk_end = min(chunk_start + chunk_size, len(raw_records))
            chunk = raw_records[chunk_start:chunk_end]

            for row in chunk:
                bet_key = row.get('bet_key')

                # Skip if we've already processed this bet_key in this batch
                if bet_key in processed_keys:
                    continue
                processed_keys.add(bet_key)

                # Create new history entry with current odds data
                new_entry = {
                    'timestamp': row.get('timestamp'),
                    'odds': row.get('odds'),
                    'bookmaker': row.get('bookmaker')
                }

                # Add optional fields to history entry
                if row.get('team'):
                    new_entry['team'] = row.get('team')
                if row.get('market_point'):
                    new_entry['market_point'] = row.get('market_point')

                # CASE 1: Existing record - append to past_data
                if bet_key in existing_bets:
                    existing_record = existing_bets[bet_key]

                    # Get current past_data and ensure it's a list
                    past_data = existing_record.get('past_data', [])
                    if not isinstance(past_data, list):
                        past_data = [past_data] if past_data else []

                    # Add new entry to past_data
                    past_data.append(new_entry)

                    # Create update with ONLY the fields we're changing
                    update = {
                        'bet_key': bet_key,
                        'past_data': past_data,
                        'sport_key': row.get('sport_key'),
                        'game': row.get('game'),
                        'team': row.get('team'),
                        'home_team': row.get('home_team'),
                        'away_team': row.get('away_team'),
                        'sport': row.get('sport'),
                        'market_type': row.get('market_type'),
                        'market_point': row.get('market_point'),
                        'odds': row.get('odds'),
                        'timestamp': row.get('timestamp')
                    }

                    # Update market_data if present
                    if row.get('market_data'):
                        update['market_data'] = row.get('market_data')

                    all_updates.append(update)

                # CASE 2: New record - create full record with initial past_data
                else:
                    new_record = {
                        'bet_key': bet_key,
                        # Initialize past_data with first entry
                        'past_data': [new_entry],
                        'odds': row.get('odds'),
                        'timestamp': row.get('timestamp'),
                        'commence_time': row.get('commence_time'),
                        'sport_key': row.get('sport_key'),
                        'game': row.get('game'),
                        'team': row.get('team'),
                        'sport_title': row.get('sport_title'),
                        'home_team': row.get('home_team'),
                        'away_team': row.get('away_team'),
                        'sport': row.get('sport'),
                        'market_type': row.get('market_type'),
                        'market_point': row.get('market_point'),
                        'bookmaker': row.get('bookmaker'),
                        'link': row.get('link')
                    }

                    # Remove None values
                    all_updates.append(new_record)

        print(f"Process records: {time.time() - process_start:.2f} seconds")
        print(f"Total records to upsert: {len(all_updates)}")
        # Write all updates to JSON file for debugging/backup
        # json_start = time.time()
        # filename = f'updates.json'

        # try:
        #     with open(filename, 'w') as f:
        #         json.dump(all_updates, f, indent=2)
        #     print(f"Updates written to {filename}")
        #     print(f"JSON write time: {time.time() - json_start:.2f} seconds")
        # except Exception as e:
        #     print(f"Error writing updates to JSON: {str(e)}")

        # Optimized upsert in batches
        batch_size = 2000
        upsert_start = time.time()
        for i in range(0, len(all_updates), batch_size):
            batch = all_updates[i:i + batch_size]
            try:
                supabase.table("ev_graph_v2").upsert(
                    batch, on_conflict='bet_key').execute()
            except Exception as e:
                print(f"Error in batch upsert {i}-{i+len(batch)}: {str(e)}")

                # Fallback to smaller batches
                fallback_size = 100
                for j in range(0, len(batch), fallback_size):
                    sub_batch = batch[j:j + fallback_size]
                    try:
                        supabase.table("ev_graph_v2").upsert(
                            sub_batch, on_conflict='bet_key').execute()
                    except Exception as inner_e:
                        print(f"Error in fallback batch: {str(inner_e)}")

                        # Last resort: one by one
                        for record in sub_batch:
                            try:
                                supabase.table("ev_graph_v2").upsert(
                                    [record], on_conflict='bet_key').execute()
                            except Exception as record_e:
                                print(
                                    f"Failed to upsert record {record['bet_key']}: {str(record_e)}")

        print(f"Upsert operations: {time.time() - upsert_start:.2f} seconds")

        end_time = time.time()
        execution_time = end_time - start_time
        print(f"Execution time: {execution_time:.2f} seconds")

        return {
            "code": 200,
            "status": "Success",
            "message": f"Database updated successfully in {execution_time:.2f} seconds",
            "metrics": {
                "total_records_processed": len(raw_records),
                "unique_records_upserted": len(all_updates),
                "execution_time_seconds": execution_time
            }
        }
    except Exception as e:
        print(f"Error in update_db: {str(e)}")
        traceback.print_exc()
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
