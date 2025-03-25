from pytz import timezone
import datetime
from sqlalchemy.dialects.postgresql import insert
from fastapi import APIRouter, Depends
from fastapi import APIRouter, Depends, HTTPException, Query
from dotenv import load_dotenv
import os
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
from sqlalchemy.orm import Session
from src.db.db import get_db
from src.db.models import *
from sqlalchemy import desc
from typing import List, Dict, Any

load_dotenv()

router = APIRouter(tags=["db"])


@router.get("/evBets")
async def get_ev_bets(db: Session = Depends(get_db)):
    try:
        bets = db.query(PlusEv).order_by(desc(PlusEv.ev_percentage)).all()
        return [bet.__dict__ for bet in bets]
    except Exception as e:
        print(f"Error in get_ev_bets: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/arbitrageBets")
async def get_arbitrage_bets(db: Session = Depends(get_db)):
    try:
        bets = db.query(Arbitrage).all()
        return [bet.__dict__ for bet in bets]
    except Exception as e:
        print(f"Error in get_arbitrage_bets: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
async def read_root():
    return {"Hello": "World"}


@router.post("/update_dbV3")
async def update_db(db: Session = Depends(get_db)):
    try:
        data = await get_all_data()
        arb_df = data[1]
        ev_df = data[2]
        arb_json = json.loads(arb_df.to_json(orient="records"))
        ev_json = json.loads(ev_df.to_json(orient="records"))

        for record in arb_json:
            if 'market_data' in record and record['market_data'] is not None:
                market_data_str = orjson.dumps(record['market_data'])
                compressed_data = gzip.compress(market_data_str)
                record['market_data'] = base64.b64encode(
                    compressed_data).decode()
        for record in ev_json:
            if 'market_data' in record and record['market_data'] is not None:
                market_data_str = orjson.dumps(record['market_data'])
                compressed_data = gzip.compress(market_data_str)
                record['market_data'] = base64.b64encode(
                    compressed_data).decode()
        print(arb_json[0])
        print(ev_json[0])
        # Delete existing records
        db.query(Arbitrage).delete()
        db.query(PlusEv).delete()
        db.commit()

        # Insert new records in batches
        batch_size = 1000
        for i in range(0, len(arb_json), batch_size):
            batch = arb_json[i:i + batch_size]
            for record in batch:
                db.add(Arbitrage(**record))
            db.commit()

        for i in range(0, len(ev_json), batch_size):
            batch = ev_json[i:i + batch_size]
            for record in batch:
                db.add(PlusEv(**record))
            db.commit()

        return {
            "code": 200,
            "status": "Success",
            "message": "Database updated successfully"
        }
    except Exception as e:
        print(f"Error in update_dbV3: {str(e)}")
        db.rollback()
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
async def update_graph(db: Session = Depends(get_db)):
    try:
        start_time = time.time()
        data = await get_all_data()
        raw_df = data[0]

        # ✅ Step 1: Clear old records
        try:
            db.query(EvGraphV2).filter(
                (EvGraphV2.commence_time < datetime.datetime.now(timezone.utc)) |
                (EvGraphV2.commence_time.is_(None))
            ).delete()
            db.commit()
        except Exception as e:
            print(f"Warning: Error clearing old records: {str(e)}")
            db.rollback()

        # ✅ Step 2: Convert raw records
        raw_records = json.loads(raw_df.to_json(orient="records"))
        processed_records = {}

        for row in raw_records:
            bet_key = row.get("bet_key")
            new_entry = {
                "timestamp": row.get("timestamp"),
                "odds": row.get("odds"),
                "bookmaker": row.get("bookmaker"),
                "team": row.get("team"),
                "market_point": row.get("market_point"),
            }

            # If `bet_key` is seen before in this batch, append to its `past_data`
            if bet_key in processed_records:
                processed_records[bet_key]["past_data"].append(new_entry)
            else:
                record_data = {
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
                    "past_data": [new_entry],  # Initialize with one entry
                }
                processed_records[bet_key] = record_data

        # Convert dictionary to list of values
        final_records = list(processed_records.values())

        # ✅ Step 3: Bulk Upsert
        try:
            stmt = insert(EvGraphV2).values(final_records)
            stmt = stmt.on_conflict_do_update(
                index_elements=["bet_key"],  # Use unique bet_key constraint
                set_={
                    "sport_key": stmt.excluded.sport_key,
                    "game": stmt.excluded.game,
                    "team": stmt.excluded.team,
                    "home_team": stmt.excluded.home_team,
                    "away_team": stmt.excluded.away_team,
                    "sport": stmt.excluded.sport,
                    "market_type": stmt.excluded.market_type,
                    "market_point": stmt.excluded.market_point,
                    "odds": stmt.excluded.odds,
                    "timestamp": stmt.excluded.timestamp,
                    "commence_time": stmt.excluded.commence_time,
                    "bookmaker": stmt.excluded.bookmaker,
                    "link": stmt.excluded.link,
                    # Append new `past_data` to existing JSONB list
                    "past_data": EvGraphV2.past_data + stmt.excluded.past_data,
                }
            )
            db.execute(stmt)
            db.commit()
        except Exception as e:
            print(f"Error during bulk upsert: {str(e)}")
            db.rollback()
            return {"code": 500, "status": "Error", "message": str(e)}

        execution_time = time.time() - start_time
        return {
            "code": 200,
            "status": "Success",
            "message": f"Database updated in {execution_time:.2f} seconds",
            "metrics": {
                "total_records_processed": len(raw_records),
                "unique_records_upserted": len(final_records),
                "execution_time_seconds": execution_time,
            },
        }

    except Exception as e:
        print(f"Error in update_graph: {str(e)}")
        traceback.print_exc()
        db.rollback()
        return {"code": 500, "status": "Error", "message": str(e)}
