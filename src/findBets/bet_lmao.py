import requests
import pandas as pd
from datetime import datetime, timezone
import time
import json
import os
import hashlib
import aiohttp
import asyncio
import sys
from typing import List, Dict, Tuple, Optional
from collections import deque
from datetime import datetime, timedelta
import traceback
from pathlib import Path
import uuid


class RateLimiter:
    def __init__(self, calls_per_second: int = 25):
        self.calls_per_second = calls_per_second
        self.timestamps = deque(maxlen=calls_per_second)
        self.lock = asyncio.Lock()
        self.retry_delays = [1, 2, 4]  # Shorter delays for faster recovery
        self.current_retry = 0
        self.total_requests = 0
        self.last_reset = datetime.now()
        self.consecutive_429s = 0  # Track consecutive rate limit hits

    async def acquire(self):
        async with self.lock:
            now = datetime.now()

            # Clean up old timestamps
            while self.timestamps and (now - self.timestamps[0]) > timedelta(seconds=1):
                self.timestamps.popleft()

            # If we're at the limit, wait until we have room
            if len(self.timestamps) >= self.calls_per_second:
                wait_time = 1.0 - (now - self.timestamps[0]).total_seconds()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            self.timestamps.append(now)

    async def handle_rate_limit(self):
        self.consecutive_429s += 1
        if self.current_retry < len(self.retry_delays):
            delay = self.retry_delays[self.current_retry]
            self.current_retry += 1
            await asyncio.sleep(delay)
        else:
            await asyncio.sleep(self.retry_delays[-1])

        self.timestamps.clear()
        self.total_requests = max(0, self.total_requests - 10)

    def reset_retry_count(self):
        self.current_retry = 0
        self.consecutive_429s = 0  # Reset consecutive rate limits counter


def power_devig(odds_list):
    """Devig odds using the power method to find fair probabilities"""

    def american_to_prob(odds):
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return abs(odds) / (abs(odds) + 100)

    probs = [american_to_prob(odds) for odds in odds_list]
    power = 1
    total = sum(prob**power for prob in probs)
    while abs(total - 1) > 0.0001:
        if total > 1:
            power += 0.0001
        else:
            power -= 0.0001
        total = sum(prob**power for prob in probs)

    fair_probs = [(prob**power) / total for prob in probs]

    def prob_to_american(prob):
        if prob >= 0.5:
            return -100 * prob / (1 - prob)
        else:
            return 100 * (1 - prob) / prob

    return [round(prob_to_american(p)) for p in fair_probs]


class OddsArbitrageFinder:
    def __init__(self, api_key, state="md"):
        # Initialize basic attributes first
        self.api_key = api_key
        self.state = state.lower()
        self.base_url = "https://api.the-odds-api.com/v4/sports"
        self.output_dir = "src/findBets"

        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)

        print("Initializing OddsArbitrageFinder...")

        # Initialize session and rate limiter
        self.session = None
        self.rate_limiter = RateLimiter()

        # Initialize storage for opportunities
        self.all_opportunities = []
        self.all_odds_data = []
        self.all_plus_ev = []
        self.all_player_props = {}

        # Thresholds
        self.low_hold_threshold = 1.05
        self.ev_threshold = 3.0

        # Player props configuration
        self.player_props = {
            "NFL": [
                "player_pass_yds",
                "player_pass_tds",
                "player_pass_completions",
                "player_rush_attempts",
                "player_receptions",
                "player_reception_yds",
            ],
            "NBA": [
                "player_points",
                "player_rebounds",
                "player_assists",
                "player_threes",
                "player_points_rebounds_assists",
            ],
            "NHL": [
                "player_points",
                "player_shots_on_goal",
                "player_goals",
                "player_assists",
            ],
        }

        # Sports to track
        self.sports = [
            # "americanfootball_ncaaf",
            "basketball_ncaab",
            "basketball_nba",
            "icehockey_nhl",
            # "americanfootball_nfl",
        ]

        # Market types
        self.featured_markets = ["h2h", "spreads", "totals"]

        self.alternate_markets = [
            "alternate_spreads",
            "alternate_totals",
            "spreads_q1",
            "spreads_q2",
            "spreads_q3",
            "spreads_q4",
            "spreads_h1",
            "spreads_h2",
            "spreads_p1",
            "spreads_p2",
            "spreads_p3",
            "totals_q1",
            "totals_q2",
            "totals_q3",
            "totals_q4",
            "totals_h1",
            "totals_h2",
            "totals_p1",
            "totals_p2",
            "totals_p3",
        ]

        # Bookmakers to track
        self.bookmakers = [
            "draftkings",
            "fanduel",
            "espnbet",
            "hardrockbet",
            "betmgm",
            # "williamhill_us",
            "betrivers",
            "pinnacle",
            "fanatics",
            "caesars",
            "fliff",
        ]

        # Store initial data
        self.raw_data = pd.DataFrame()

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def make_request(self, url: str, params: dict) -> dict:
        """Make API request with improved rate limiting and error handling"""
        try:
            await self.rate_limiter.acquire()

            async with self.session.get(url, params=params) as response:
                if response.status == 429:  # Rate limit exceeded
                    print("Rate limit exceeded - implementing backoff strategy")
                    await self.rate_limiter.handle_rate_limit()
                    # Retry with backoff
                    return await self.make_request(url, params)

                response.raise_for_status()
                data = await response.json()

                # Reset retry count on successful request
                self.rate_limiter.reset_retry_count()
                return data

        except aiohttp.ClientError as e:
            print(f"Error making request: {str(e)}")
            if isinstance(e, aiohttp.ClientResponseError):
                print(f"Status: {e.status}")
                print(f"Message: {e.message}")

                if e.status == 429:  # Handle rate limit in exception case
                    await self.rate_limiter.handle_rate_limit()
                    return await self.make_request(url, params)
            return None

        except Exception as e:
            print(f"Unexpected error in make_request: {str(e)}")
            return None

    async def get_player_props(self, sport: str, event_id: str) -> List[dict]:
        """Fetch player props for a specific event"""
        sport_name = sport.upper().split(
            "_")[1] if "_" in sport else sport.upper()

        if sport_name not in self.player_props:
            print(f"No player props configured for sport: {sport_name}")
            return []

        prop_markets = self.player_props[sport_name]
        print(f"Fetching player props for {sport_name} event {event_id}")

        # Split markets into smaller batches to avoid large requests
        batch_size = 3
        all_bookmakers = []

        for i in range(0, len(prop_markets), batch_size):
            market_batch = prop_markets[i: i + batch_size]
            print(f"Fetching market batch: {market_batch}")

            url = f"{self.base_url}/{sport}/events/{event_id}/odds"
            params = {
                "apiKey": self.api_key,
                # "regions": self.state,
                "bookmakers": ",".join(self.bookmakers),
                "markets": ",".join(market_batch),
                "oddsFormat": "decimal",
            }

            try:
                data = await self.make_request(url, params)
                if not data:
                    print(
                        f"No data received for market batch in event {event_id}")
                    continue

                if "bookmakers" in data:
                    all_bookmakers.extend(data["bookmakers"])
                    print(
                        f"Received data from {len(data['bookmakers'])} bookmakers for market batch"
                    )

                # Add delay between market batches
                if i + batch_size < len(prop_markets):
                    await asyncio.sleep(1)

            except Exception as e:
                print(
                    f"Error fetching market batch for event {event_id}: {str(e)}")
                continue

        if not all_bookmakers:
            print(
                f"No bookmaker data received for any markets in event {event_id}")

        return all_bookmakers

    def process_player_props(self, bookmakers: List[dict], sport: str) -> Dict:
        """Process player props data"""
        markets = {}
        sport_name = sport.upper().split(
            "_")[1] if "_" in sport else sport.upper()
        prop_markets = self.player_props.get(sport_name, [])

        print(f"Processing player props for {sport_name}")
        print(f"Available prop markets: {prop_markets}")
        print(f"Processing data from {len(bookmakers)} bookmakers")

        for bookmaker in bookmakers:
            print(f"Processing bookmaker: {bookmaker['title']}")
            if bookmaker["title"].lower() not in [b.lower() for b in self.bookmakers]:
                print(
                    f"Skipping bookmaker {bookmaker['title']} - not in configured list"
                )
                continue

            for market in bookmaker.get("markets", []):
                if market["key"] not in prop_markets:
                    print(
                        f"Skipping market {market['key']} - not in prop markets list")
                    continue

                print(
                    f"Processing market: {market['key']} with {len(market['outcomes'])} outcomes"
                )
                for outcome in market["outcomes"]:
                    player_name = outcome.get("description", "Unknown")
                    if player_name == "Unknown":
                        print(
                            f"Missing player description in outcome: {outcome}")
                        continue

                    prop_type = market["key"].replace("player_", "")
                    point = outcome.get("point")
                    market_key = (
                        f"{market['key']}_{player_name}_{point}"
                        if point
                        else f"{market['key']}_{player_name}"
                    )

                    if market_key not in markets:
                        markets[market_key] = []

                    markets[market_key].append(
                        {
                            "bookmaker": bookmaker["title"],
                            "player": player_name,
                            "prop_type": prop_type,
                            "team": outcome["name"],
                            "price": outcome.get("price", 0),
                            "point": point,
                            "link": bookmaker.get("link", ""),
                        }
                    )
                    print(
                        f"Added outcome for {player_name} - {prop_type} - {outcome['name']} @ {outcome.get('price', 0)}"
                    )

        print(f"Processed {len(markets)} unique player prop markets")
        for market_key, outcomes in markets.items():
            print(f"Market {market_key}: {len(outcomes)} outcomes")
            for outcome in outcomes:
                print(
                    f"  - {outcome['bookmaker']}: {outcome['team']} @ {outcome['price']}"
                )

        return markets

    async def get_featured_odds(self, sport: str) -> List[dict]:
        """Fetch odds for featured markets"""
        current_time = datetime.now(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{self.base_url}/{sport}/odds"

        params = {
            "apiKey": self.api_key,
            "bookmakers": ",".join(self.bookmakers),
            "markets": ",".join(self.featured_markets),
            "oddsFormat": "decimal",
            "commenceTimeFrom": current_time,
            "includeLinks": "true",
        }

        data = await self.make_request(url, params)
        if data:
            # Store raw data in DataFrame
            if len(data) > 0:
                for event in data:
                    for bookmaker in event.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            for outcome in market.get("outcomes", []):
                                row = {
                                    "id": event.get("id"),
                                    "sport_key": event.get("sport_key"),
                                    "sport_title": event.get("sport_title"),
                                    "commence_time": event.get("commence_time"),
                                    "home_team": event.get("home_team"),
                                    "away_team": event.get("away_team"),
                                    "sport": sport,
                                    "timestamp": datetime.now(timezone.utc),
                                    "game": f"{event.get('home_team')} vs {event.get('away_team')}",
                                    "team": outcome.get("name"),
                                    "market_type": market.get("key"),
                                    "market_point": outcome.get("point"),
                                    "bookmaker": bookmaker.get("title"),
                                    "odds": self.decimal_to_american(
                                        outcome.get("price", 0)
                                    ),
                                    "link": bookmaker.get("link", ""),
                                }

                                # Modify team name for player props to include player name
                                if "player_" in market.get("key", "").lower():
                                    player_name = outcome.get(
                                        "description", "")
                                    if player_name:
                                        row["team"] = (
                                            f"{player_name} {'Over' if 'OVER' in outcome.get('name', '').upper() else 'Under'}"
                                        )

                                # Add market data for spreads
                                if "spreads" in market.get("key", "").lower():
                                    market_data = {
                                        "team1": {"odds": {}},
                                        "team2": {"odds": {}},
                                    }
                                    for bm in event.get("bookmakers", []):
                                        if bm["title"].lower() not in [
                                            b.lower() for b in self.bookmakers
                                        ]:
                                            continue
                                        for mkt in bm.get("markets", []):
                                            if mkt["key"] == market["key"]:
                                                for out in mkt["outcomes"]:
                                                    side = (
                                                        "team1"
                                                        if out["name"]
                                                        == outcome["name"]
                                                        else "team2"
                                                    )
                                                    market_data[side]["odds"][
                                                        bm["title"].lower()
                                                    ] = {
                                                        "american": self.decimal_to_american(
                                                            out["price"]
                                                        ),
                                                        "decimal": out["price"],
                                                        "point": out.get("point"),
                                                        "link": bm.get("link"),
                                                    }
                                    row["market_data"] = market_data

                                row["bet_key"] = hashlib.sha256(
                                    (
                                        str(row["game"])
                                        + str(row["team"])
                                        + str(row["market_type"])
                                        + str(row["market_point"])
                                        + str(row["bookmaker"])
                                    ).encode("utf-8")
                                ).hexdigest()

                                # Create DataFrame with explicit dtypes
                                row_df = pd.DataFrame(
                                    [row], columns=list(row.keys()))
                                row_df = row_df.astype(
                                    {
                                        "id": str,
                                        "sport_key": str,
                                        "sport_title": str,
                                        "commence_time": str,
                                        "home_team": str,
                                        "away_team": str,
                                        "sport": str,
                                        "timestamp": "datetime64[ns, UTC]",
                                        "game": str,
                                        "team": str,
                                        "market_type": str,
                                        "market_point": "float64",
                                        "bookmaker": str,
                                        "odds": "int64",
                                        "link": str,
                                        "bet_key": str,
                                    }
                                )

                                if self.raw_data.empty:
                                    self.raw_data = row_df
                                else:
                                    # Ensure both DataFrames have the same columns
                                    missing_cols = set(row_df.columns) - set(
                                        self.raw_data.columns
                                    )
                                    for col in missing_cols:
                                        self.raw_data[col] = None

                                    missing_cols = set(self.raw_data.columns) - set(
                                        row_df.columns
                                    )
                                    for col in missing_cols:
                                        row_df[col] = None

                                    # Concatenate with explicit columns to maintain dtype consistency
                                    self.raw_data = pd.concat(
                                        [self.raw_data, row_df],
                                        ignore_index=True,
                                        axis=0,
                                    )

                # Log bookmaker information
                bookmakers_found = set()
                for event in data:
                    for bm in event.get("bookmakers", []):
                        bookmakers_found.add(bm.get("title", "").lower())

                print(
                    f"Found data from bookmakers: {sorted(bookmakers_found)}")
                missing_books = (
                    set(b.lower() for b in self.bookmakers) - bookmakers_found
                )
                if missing_books:
                    print(
                        f"Missing data from bookmakers: {sorted(missing_books)}")

        return data if data else []

    async def get_dfs(self) -> tuple:
        """Get DataFrames for arbitrage and +EV opportunities"""
        try:
            print("Starting odds analysis...")

            # Generate arbitrage table
            print("Generating arbitrage opportunities table...")
            arb_df = await self.generate_arbitrage_table()
            print(f"Found {len(arb_df)} arbitrage/low hold opportunities")

            # Create EV DataFrame
            print("Processing +EV opportunities...")
            ev_df = pd.DataFrame(self.all_plus_ev)

            if not ev_df.empty:
                print(f"Found {len(ev_df)} +EV opportunities")
                # Ensure consistent primary key generation between raw and EV data
                ev_df["primary_key"] = [str(uuid.uuid4())
                                        for _ in range(len(ev_df))]
                ev_df["timestamp"] = datetime.now(timezone.utc)
            else:
                print("No +EV opportunities found")

            if not arb_df.empty:
                arb_df["primary_key"] = [str(uuid.uuid4())
                                         for _ in range(len(arb_df))]
                arb_df["timestamp"] = datetime.now(timezone.utc)

            # Save raw data
            # if not self.raw_data.empty:
            # raw_data_path = os.path.join(
            #     self.output_dir, "raw_odds_data.csv")
            # self.raw_data.to_csv(raw_data_path, index=False)
            # print(f"Saved raw odds data to {raw_data_path}")

            # Convert timestamps to ISO format
            current_time = datetime.now(timezone.utc).isoformat()

            if not self.raw_data.empty:
                self.raw_data["timestamp"] = current_time

            if not arb_df.empty:
                arb_df["timestamp"] = current_time
            if not ev_df.empty:
                ev_df["timestamp"] = current_time

            # Convert DataFrames to JSON-serializable format
            raw_df = self.raw_data.astype(object).where(
                pd.notnull(self.raw_data), None)
            arb_df = arb_df.astype(object).where(pd.notnull(arb_df), None)
            ev_df = ev_df.astype(object).where(pd.notnull(ev_df), None)

            # Convert to records
            raw_data = raw_df.to_dict(orient="records")
            arb_data = arb_df.to_dict(orient="records")
            ev_data = ev_df.to_dict(orient="records")

            # Convert numpy types to native Python types
            raw_data = json.loads(json.dumps(raw_data, default=str))
            arb_data = json.loads(json.dumps(arb_data, default=str))
            ev_data = json.loads(json.dumps(ev_data, default=str))

            # Convert back to DataFrames, preserving all columns
            raw_df = pd.DataFrame(raw_data, columns=raw_df.columns)
            arb_df = pd.DataFrame(arb_data, columns=arb_df.columns)
            ev_df = pd.DataFrame(ev_data, columns=ev_df.columns)

            print(f"Found {len(raw_df)} raw odds entries")
            print(f"Found {len(arb_df)} arbitrage/low hold opportunities")
            print(f"Found {len(ev_df)} +EV opportunities")

            if not raw_df.empty:
                raw_df["timestamp"] = current_time
                # Removed market_type replacement to preserve original format
                
                raw_df["bet_key"] = raw_df.apply(
                    lambda row: hashlib.sha256(
                        (
                            str(row["game"])
                            + str(row["team"])
                            + str(row["market_type"])
                            + str(row["market_point"])
                            + str(row["bookmaker"])
                        ).encode("utf-8")
                    ).hexdigest(),
                    axis=1,
                )

            print("Analysis complete")
            return arb_df, ev_df

        except Exception as e:
            print(f"Error in get_dfs: {str(e)}")
            return pd.DataFrame(), pd.DataFrame()

    async def generate_arbitrage_table(self) -> pd.DataFrame:
        """Generate table of arbitrage opportunities"""
        try:
            print("Starting arbitrage table generation...")

            # Fetch odds for all sports
            print("Fetching odds for all sports...")
            all_featured_odds = []

            # Get all sports data concurrently
            tasks = [self.get_featured_odds(sport) for sport in self.sports]
            featured_odds_results = await asyncio.gather(*tasks)

            for sport, featured_odds in zip(self.sports, featured_odds_results):
                if featured_odds:
                    print(
                        f"Received data for {sport}: {len(featured_odds)} games")
                    all_featured_odds.extend(
                        [(sport, game) for game in featured_odds])
                else:
                    print(f"No odds data received for {sport}")

            # Process all games concurrently
            print(f"Processing {len(all_featured_odds)} total games...")
            game_tasks = [self.process_game(game)
                          for _, game in all_featured_odds]
            all_results = await asyncio.gather(*game_tasks)

            # Collect results
            for opportunities, plus_ev, odds_data in all_results:
                if opportunities:
                    self.all_opportunities.extend(opportunities)
                if plus_ev:
                    self.all_plus_ev.extend(plus_ev)
                if odds_data:
                    self.all_odds_data.extend(odds_data)

            # Create DataFrame
            if self.all_opportunities:
                print(
                    f"Creating DataFrame with {len(self.all_opportunities)} opportunities"
                )
                df = pd.DataFrame(self.all_opportunities)
                df["timestamp"] = datetime.now(timezone.utc)

                # Define columns in the order we want
                columns = [
                    "opportunity_type",
                    "hold_percentage",
                    "sport",
                    "market_type",
                    "prop_description",
                    "market_point",
                    "game",
                    "commence_time",
                    "team1_name",
                    "team1_book",
                    "team1_odds",
                    "team1_point",
                    "team1_stake",
                    "team1_link",
                    "team2_name",
                    "team2_book",
                    "team2_odds",
                    "team2_point",
                    "team2_stake",
                    "team2_link",
                    "profit_percentage",
                    "market_data",
                    "timestamp",
                    "player",  # Add player column
                ]

                # Ensure all columns exist, fill missing with None
                for col in columns:
                    if col not in df.columns:
                        df[col] = None

                return df[columns]
            else:
                print("No arbitrage opportunities found")
                return pd.DataFrame()

        except Exception as e:
            print(f"Error generating arbitrage table: {str(e)}")
            return pd.DataFrame()

    async def process_game(self, game: dict) -> tuple:
        """Process a single game for arbitrage and +EV opportunities"""
        try:
            print(
                f"Processing game: {game['home_team']} vs {game['away_team']}")

            # Initialize lists to store all odds
            all_odds = []
            tasks = []

            if game.get("id"):
                # 1. Create task for alternate markets
                url = f"{self.base_url}/{game['sport_key']}/events/{game['id']}/odds"
                alternate_params = {
                    "apiKey": self.api_key,
                    "bookmakers": ",".join(self.bookmakers),
                    "markets": ",".join(self.alternate_markets),
                    "oddsFormat": "decimal",
                    "includeLinks": "true",
                }
                tasks.append(self.make_request(url, alternate_params))

                # 2. Create tasks for player props if sport supports them
                sport_name = (
                    game["sport_key"].upper().split("_")[1]
                    if "_" in game["sport_key"]
                    else game["sport_key"].upper()
                )
                if sport_name in self.player_props:
                    prop_markets = self.player_props[sport_name]
                    prop_params = {
                        "apiKey": self.api_key,
                        "bookmakers": ",".join(self.bookmakers),
                        "markets": ",".join(prop_markets),
                        "oddsFormat": "decimal",
                        "includeLinks": "true",
                    }
                    tasks.append(self.make_request(url, prop_params))

                # Execute all tasks concurrently
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results and store in raw_data
                for result in results:
                    if isinstance(result, Exception):
                        print(f"Error fetching odds: {str(result)}")
                        continue
                    if result:
                        # Process each event's bookmakers and markets
                        rows = []
                        for bookmaker in result.get("bookmakers", []):
                            for market in bookmaker.get("markets", []):
                                for outcome in market.get("outcomes", []):
                                    row = {
                                        "id": result.get("id"),
                                        "sport_key": result.get("sport_key"),
                                        "sport_title": result.get("sport_title"),
                                        "commence_time": result.get("commence_time"),
                                        "home_team": result.get("home_team"),
                                        "away_team": result.get("away_team"),
                                        "sport": game["sport_key"],
                                        "timestamp": datetime.now(timezone.utc),
                                        "game": f"{result.get('home_team')} vs {result.get('away_team')}",
                                        "team": outcome.get("name"),
                                        "market_type": market.get("key"),
                                        "market_point": outcome.get("point"),
                                        "bookmaker": bookmaker.get("title"),
                                        "odds": self.decimal_to_american(
                                            outcome.get("price", 0)
                                        ),
                                        "link": bookmaker.get("link", ""),
                                    }

                                    # Modify team name for player props to include player name
                                    if "player_" in market.get("key", "").lower():
                                        player_name = outcome.get(
                                            "description", "")
                                        if player_name:
                                            row["team"] = (
                                                f"{player_name} {'Over' if 'OVER' in outcome.get('name', '').upper() else 'Under'}"
                                            )

                                    rows.append(row)

                        if rows:
                            new_df = pd.DataFrame(rows)
                            if self.raw_data.empty:
                                self.raw_data = new_df
                            else:
                                self.raw_data = pd.concat(
                                    [self.raw_data, new_df], ignore_index=True
                                )

                        if "bookmakers" in result:
                            all_odds.extend(result["bookmakers"])

            # Process opportunities
            opportunities = (
                await self.find_opportunities(game, all_odds) if all_odds else []
            )
            odds_data = await self.collect_all_odds(game)
            plus_ev = await self.find_plus_ev_bets(game, all_odds) if all_odds else []

            return opportunities, plus_ev, odds_data

        except Exception as e:
            print(
                f"Error processing game {game.get('id', 'unknown')}: {str(e)}")
            return [], [], []

    async def get_event_odds(self, sport: str, event_id: str) -> List[dict]:
        """Fetch odds for additional markets"""
        url = f"{self.base_url}/{sport}/events/{event_id}/odds"
        params = {
            "apiKey": self.api_key,
            "bookmakers": ",".join(self.bookmakers),
            "markets": ",".join(self.additional_markets),
            "oddsFormat": "decimal",
            "includeLinks": "true",
        }

        return await self.make_request(url, params) or []

    async def collect_all_odds(self, game: dict) -> List[dict]:
        """Collect all odds for a game"""
        odds_data = []
        for market_type in self.featured_markets:
            market_data = {
                "sport": game["sport_title"],
                "game": f"{game['home_team']} vs {game['away_team']}",
                "commence_time": game["commence_time"],
                "market_type": market_type,
                "books": {},
            }

            if "bookmakers" not in game:
                continue

            for bm in game["bookmakers"]:
                if not isinstance(bm, dict) or "title" not in bm:
                    continue

                if bm["title"].lower() not in [b.lower() for b in self.bookmakers]:
                    continue

                for market in bm.get("markets", []):
                    if market["key"] == market_type:
                        market_data["books"][bm["title"]] = [
                            {
                                "team": outcome["name"],
                                "price": outcome["price"],
                                "point": outcome.get("point"),
                                "american_odds": self.decimal_to_american(
                                    outcome["price"]
                                ),
                                "link": bm.get("link", ""),
                            }
                            for outcome in market["outcomes"]
                        ]

            if market_data["books"]:
                odds_data.append(market_data)

        return odds_data

    def decimal_to_american(self, decimal_odds: float) -> int:
        """Convert decimal odds to American format"""
        try:
            if decimal_odds <= 1:
                return -10000
            if decimal_odds >= 2:
                return round((decimal_odds - 1) * 100)
            return round(-100 / (decimal_odds - 1))
        except (ZeroDivisionError, ValueError):
            return -10000

    def calculate_implied_probability(self, decimal_odds: float) -> float:
        """Calculate implied probability from decimal odds"""
        return 1 / decimal_odds if decimal_odds > 0 else 0

    def calculate_kelly_percentage(
        self, prob_a: float, prob_b: float, odds_a: float, odds_b: float
    ) -> tuple:
        """Calculate optimal bet sizing for arbitrage opportunities"""
        try:
            stake_a = (odds_b * 100) / (odds_a + odds_b)
            stake_b = (odds_a * 100) / (odds_a + odds_b)

            if stake_a <= 0 or stake_b <= 0:
                return (50.0, 50.0)

            return (stake_a, stake_b)
        except (ZeroDivisionError, ValueError):
            return (50.0, 50.0)

    def validate_player_prop(self, market_type: str, player_name: str) -> str:
        """
        Validate that a player name exists for player props.
        Raises an error if player name is missing for a player prop.
        
        Args:
            market_type: The type of market
            player_name: The player name to validate
            
        Returns:
            The validated player name
            
        Raises:
            ValueError: If player name is missing for a player prop
        """
        if "player_" in market_type and (not player_name or player_name == "Unknown"):
            error_msg = f"Missing player name for player prop: {market_type}"
            print(error_msg)
            raise ValueError(error_msg)
        return player_name

    async def find_opportunities(
        self, game: dict, additional_odds: List[dict] = None
    ) -> List[dict]:
        """Find arbitrage and low hold opportunities in a game"""
        try:
            opportunities = []

            # Combine all bookmakers
            all_bookmakers = []
            if "bookmakers" in game:
                all_bookmakers.extend(game["bookmakers"])
            if additional_odds:
                all_bookmakers.extend(additional_odds)

            # Filter to configured bookmakers
            all_bookmakers = [
                bm
                for bm in all_bookmakers
                if isinstance(bm, dict)
                and "title" in bm
                and bm["title"].lower() in [b.lower() for b in self.bookmakers]
            ]

            # Process all market types
            all_markets = (
                self.featured_markets
                + self.alternate_markets
                + self.player_props.get(game["sport_title"].upper(), [])
            )

            for market_type in all_markets:
                market_type = market_type.lower()  # Convert to lowercase

                # Group odds by team and point
                odds_by_team = {}
                for bm in all_bookmakers:
                    for market in bm.get("markets", []):
                        if market["key"].lower() != market_type:
                            continue

                        for outcome in market["outcomes"]:
                            key = (
                                outcome["name"],
                                str(outcome.get("point", "")),
                                market["key"].lower(),
                            )

                            if (
                                key not in odds_by_team
                                or outcome["price"] > odds_by_team[key]["price"]
                            ):
                                odds_by_team[key] = {
                                    "bookmaker": bm["title"],
                                    "team": outcome["name"],
                                    "price": outcome["price"],
                                    "point": outcome.get("point"),
                                    "link": bm.get("link", ""),
                                    "market_key": market["key"].lower(),
                                    "description": outcome.get("description", ""),
                                }

                # Find matching pairs
                processed_pairs = set()
                for key1, odds1 in odds_by_team.items():
                    for key2, odds2 in odds_by_team.items():
                        if key1 == key2 or odds1["bookmaker"] == odds2["bookmaker"]:
                            continue

                        # Validate match based on market type
                        valid_pair = False

                        if "h2h" in market_type:
                            valid_pair = odds1["team"] != odds2["team"]

                        elif "spreads" in market_type:
                            if odds1["team"] != odds2["team"]:
                                point1 = float(odds1.get("point", 0) or 0)
                                point2 = float(odds2.get("point", 0) or 0)
                                valid_pair = abs(point1 + point2) <= 0.1

                        elif "totals" in market_type or "player_" in market_type:
                            if odds1.get("point") == odds2.get("point"):
                                valid_pair = (
                                    "OVER" in odds1["team"].upper()
                                    and "UNDER" in odds2["team"].upper()
                                ) or (
                                    "UNDER" in odds1["team"].upper()
                                    and "OVER" in odds2["team"].upper()
                                )
                                # For player props, also check if they're for the same player
                                if valid_pair and "player_" in market_type:
                                    valid_pair = odds1.get("description") == odds2.get(
                                        "description"
                                    )

                        if valid_pair:
                            pair_key = tuple(sorted([key1, key2]))
                            if pair_key not in processed_pairs:
                                processed_pairs.add(pair_key)

                                # Collect all bookmaker odds for this market point
                                market_data = {
                                    "over": {"odds": {}},
                                    "under": {"odds": {}}
                                }
                                point = odds1.get("point")

                                for bm in all_bookmakers:
                                    if not isinstance(bm, dict) or "title" not in bm:
                                        continue

                                    bm_title = bm["title"].lower()
                                    if bm_title not in [b.lower() for b in self.bookmakers]:
                                        continue

                                    for market in bm.get("markets", []):
                                        if market["key"].lower() != market_type:
                                            continue

                                        for outcome in market["outcomes"]:
                                            # For spreads, ensure points match or are opposites
                                            outcome_point = float(
                                                outcome.get("point", 0) or 0)
                                            if "spreads" in market_type:
                                                if point is not None:
                                                    point_float = float(point)
                                                    if not (abs(outcome_point - point_float) <= 0.1 or abs(outcome_point + point_float) <= 0.1):
                                                        continue
                                            elif point != outcome.get("point"):
                                                continue

                                            decimal_odds = outcome["price"]
                                            american_odds = self.decimal_to_american(
                                                decimal_odds)

                                            # Determine side (over/under)
                                            if "spreads" in market_type:
                                                side = "over" if outcome_point > 0 or (
                                                    outcome_point == 0 and outcome["name"] == odds1["team"]) else "under"
                                            else:
                                                side = "over" if outcome["name"] == odds1["team"] else "under"

                                            market_data[side]["odds"][bm_title] = {
                                                "american": american_odds,
                                                "decimal": decimal_odds,
                                                "point": outcome_point,
                                                "link": bm.get("link", "")
                                            }

                                # Ensure both sides have the same bookmakers with valid data
                                all_bookmakers_used = set(market_data["over"]["odds"].keys()) | set(
                                    market_data["under"]["odds"].keys())
                                for side in ["over", "under"]:
                                    for bm in all_bookmakers_used:
                                        if bm not in market_data[side]["odds"] or market_data[side]["odds"][bm] is None:
                                            other_side = "under" if side == "over" else "over"
                                            if bm in market_data[other_side]["odds"] and market_data[other_side]["odds"][bm] is not None:
                                                other_data = market_data[other_side]["odds"][bm].copy(
                                                )
                                                if "spreads" in market_type and other_data["point"] is not None:
                                                    other_data["point"] = - \
                                                        float(
                                                            other_data["point"])
                                                market_data[side]["odds"][bm] = other_data
                                            else:
                                                market_data["over"]["odds"].pop(
                                                    bm, None)
                                                market_data["under"]["odds"].pop(
                                                    bm, None)

                                prob1 = self.calculate_implied_probability(
                                    odds1["price"]
                                )
                                prob2 = self.calculate_implied_probability(
                                    odds2["price"]
                                )
                                total_prob = prob1 + prob2

                                if total_prob <= self.low_hold_threshold:
                                    stake1, stake2 = self.calculate_kelly_percentage(
                                        prob1, prob2, odds1["price"], odds2["price"]
                                    )

                                    hold_percentage = (total_prob - 1) * 100
                                    opportunity_type = (
                                        "Arbitrage" if total_prob < 1 else "Low Hold"
                                    )
                                    profit_percentage = (
                                        round(((1 / total_prob) - 1) * 100, 2)
                                        if total_prob < 1
                                        else 0
                                    )

                                    # Extract player name for player props
                                    player_name = None
                                    if "player_" in market_type:
                                        # Try to get player name from description
                                        if "description" in odds1:
                                            player_name = odds1["description"]
                                        elif "description" in odds2:
                                            player_name = odds2["description"]
                                        
                                        # Validate player name exists for player props
                                        player_name = self.validate_player_prop(market_type, player_name)
                                      

                                    opportunities.append(
                                        {
                                            "sport": game["sport_title"],
                                            "opportunity_type": opportunity_type,
                                            "market_type": market_type,
                                            "market_point": odds1.get("point"),
                                            "game": f"{game['home_team']} vs {game['away_team']}",
                                            "commence_time": game["commence_time"],
                                            "team1_name": odds1["team"],
                                            "team1_book": odds1["bookmaker"],
                                            "team1_odds": self.decimal_to_american(
                                                odds1["price"]
                                            ),
                                            "team1_point": odds1.get("point"),
                                            "team1_stake": round(stake1, 2),
                                            "team1_link": odds1["link"],
                                            "team2_name": odds2["team"],
                                            "team2_book": odds2["bookmaker"],
                                            "team2_odds": self.decimal_to_american(
                                                odds2["price"]
                                            ),
                                            "team2_point": odds2.get("point"),
                                            "team2_stake": round(stake2, 2),
                                            "team2_link": odds2["link"],
                                            "hold_percentage": round(
                                                hold_percentage, 2
                                            ),
                                            "profit_percentage": profit_percentage,
                                            "market_data": market_data,
                                            "player": player_name,  # Add player name field
                                        }
                                    )

            return opportunities

        except Exception as e:
            print(f"Error finding opportunities: {str(e)}")
            return []

    async def find_plus_ev_bets(
        self, game: dict, additional_odds: List[dict] = None
    ) -> List[dict]:
        """Find +EV betting opportunities using Pinnacle as the sharp book"""
        try:
            plus_ev_opportunities = []

            # Combine bookmakers
            all_bookmakers = []
            if isinstance(game, dict) and "bookmakers" in game:
                all_bookmakers.extend(game["bookmakers"])
            if additional_odds:
                if isinstance(additional_odds, list):
                    all_bookmakers.extend(additional_odds)
                elif (
                    isinstance(additional_odds, dict)
                    and "bookmakers" in additional_odds
                ):
                    all_bookmakers.extend(additional_odds["bookmakers"])

            # First, get Pinnacle odds for each market
            pinnacle_odds = {}
            for bm in all_bookmakers:
                if not isinstance(bm, dict) or "title" not in bm:
                    print(f"Skipping invalid bookmaker data: {bm}")
                    continue

                if bm["title"].lower() == "pinnacle":
                    for market in bm.get("markets", []):
                        # Convert to lowercase
                        market_key = market["key"].lower()
                        if market_key not in pinnacle_odds:
                            pinnacle_odds[market_key] = []

                        # For props, include description (player name) in the key
                        for outcome in market["outcomes"]:
                            outcome_data = {
                                "team": outcome["name"],
                                "price": outcome["price"],
                                "point": outcome.get("point"),
                                "american": self.decimal_to_american(outcome["price"]),
                                "description": outcome.get(
                                    "description", ""
                                ),  # Include player name
                            }
                            pinnacle_odds[market_key].append(outcome_data)

            # Process each market type
            all_markets = (
                self.featured_markets
                + self.alternate_markets
                + self.player_props.get(game["sport_title"].upper(), [])
            )

            for market_type in all_markets:
                market_type = market_type.lower()  # Convert to lowercase
                if market_type not in pinnacle_odds:
                    continue

                pin_odds = pinnacle_odds[market_type]

                # Group Pinnacle odds by point/line and player (for props)
                pin_odds_by_key = {}
                for odds in pin_odds:
                    # For props, include player name in the key
                    key = (str(odds.get("point", "0")),
                           odds.get("description", ""))
                    if key not in pin_odds_by_key:
                        pin_odds_by_key[key] = []
                    pin_odds_by_key[key].append(odds)

                # For each point/line/player combo, find matching odds from other books
                for key, pin_odds_list in pin_odds_by_key.items():
                    point, player = key
                    if len(pin_odds_list) != 2:  # Need both sides for devig
                        continue

                    # Devig Pinnacle odds
                    pin_american = [odds["american"] for odds in pin_odds_list]
                    fair_odds = power_devig(pin_american)

                    # For spreads, ensure points are properly formatted
                    if "spreads" in market_type:
                        for pin_odd in pin_odds_list:
                            pin_odd["point"] = float(
                                pin_odd.get("point", 0) or 0)

                        # Ensure points are opposites
                        if abs(pin_odds_list[0]["point"] + pin_odds_list[1]["point"]) <= 0.1:
                            point2 = -pin_odds_list[0]["point"]
                            pin_odds_list[1]["point"] = point2

                    # Initialize market_data with proper structure
                    market_data = {"over": {"odds": {}}, "under": {"odds": {}}}

                    # Add Pinnacle odds first
                    for pin_odd in pin_odds_list:
                        side = "over" if pin_odd == pin_odds_list[0] else "under"
                        market_data[side]["odds"]["pinnacle"] = {
                            "american": self.decimal_to_american(pin_odd["price"]),
                            "decimal": pin_odd["price"],
                            "point": pin_odd["point"],
                            "link": "https://www.pinnacle.com/en/betting"  # Add Pinnacle's base URL
                        }

                    # Track all bookmakers that have data
                    active_bookmakers = {"pinnacle"}

                    for bm in all_bookmakers:
                        if not isinstance(bm, dict) or "title" not in bm:
                            continue

                        bm_title = bm["title"].lower()
                        if bm_title == "pinnacle":
                            continue

                        for market in bm.get("markets", []):
                            if market["key"].lower() != market_type:
                                continue

                            for outcome in market["outcomes"]:
                                outcome_point = float(
                                    outcome.get("point", 0) or 0)

                                # For spreads, match exact point or its opposite
                                if "spreads" in market_type:
                                    if point != "0":
                                        point_float = float(point)
                                        if not (abs(outcome_point - point_float) <= 0.1 or abs(outcome_point + point_float) <= 0.1):
                                            continue
                                elif str(outcome.get("point", "0")) != point:
                                    continue

                                if player and player != outcome.get("description", ""):
                                    continue

                                decimal_odds = outcome["price"]
                                american_odds = self.decimal_to_american(
                                    decimal_odds)

                                # Determine side based on market type
                                if "spreads" in market_type:
                                    side = "over" if outcome_point > 0 or (
                                        outcome_point == 0 and outcome["name"] == pin_odds_list[0]["team"]) else "under"
                                else:
                                    side = "over" if outcome["name"] == pin_odds_list[0]["team"] else "under"

                                market_data[side]["odds"][bm_title] = {
                                    "american": american_odds,
                                    "decimal": decimal_odds,
                                    "point": outcome_point,
                                    "link": bm.get("link", "")
                                }
                                active_bookmakers.add(bm_title)

                    # Ensure both sides have the same bookmakers with valid data
                    for side in ["over", "under"]:
                        for bm in active_bookmakers:
                            if bm not in market_data[side]["odds"] or market_data[side]["odds"][bm] is None:
                                other_side = "under" if side == "over" else "over"
                                if bm in market_data[other_side]["odds"] and market_data[other_side]["odds"][bm] is not None:
                                    other_data = market_data[other_side]["odds"][bm].copy(
                                    )
                                    if "spreads" in market_type and other_data["point"] is not None:
                                        other_data["point"] = - \
                                            float(other_data["point"])
                                    market_data[side]["odds"][bm] = other_data
                                else:
                                    # If no data available, remove from both sides
                                    market_data["over"]["odds"].pop(bm, None)
                                    market_data["under"]["odds"].pop(bm, None)
                                    active_bookmakers.remove(bm)

                    # Check if odds are +EV
                    for bm_title, odds_data in market_data["over"]["odds"].items():
                        if bm_title == "pinnacle":
                            continue

                        american_odds = odds_data["american"]

                        # Match the outcome to the correct fair odds
                        fair_odd = fair_odds[0]  # Use first fair odd for over

                        # Check if odds are +EV
                        is_better = (
                            (american_odds > 0 and fair_odd >
                             0 and american_odds > fair_odd)
                            or (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd)
                            or (american_odds > 0 and fair_odd < 0)
                        )

                        if is_better:
                            # Calculate EV
                            if american_odds > 0:
                                decimal_odds = (american_odds / 100) + 1
                            else:
                                decimal_odds = (100 / abs(american_odds)) + 1

                            if fair_odd > 0:
                                fair_prob = 100 / (fair_odd + 100)
                            else:
                                fair_prob = abs(fair_odd) / \
                                    (abs(fair_odd) + 100)

                            ev_percentage = (
                                decimal_odds * fair_prob - 1) * 100

                            if ev_percentage >= self.ev_threshold:
                                # Validate player name exists for player props
                                if "player_" in market_type:
                                    player = self.validate_player_prop(market_type, player)
                            
                                    
                                opportunity = {
                                    "sport": game["sport_title"],
                                    "market_type": market_type,
                                    "market_point": point if point != "0" else None,
                                    "game": f"{game['home_team']} vs {game['away_team']}",
                                    "commence_time": game["commence_time"],
                                    # Use team from pinnacle odds
                                    "team": pin_odds_list[0]["team"],
                                    "bookmaker": bm_title,
                                    "odds": american_odds,
                                    "fair_odds": fair_odd,
                                    "ev_percentage": round(ev_percentage, 2),
                                    "link": odds_data["link"],
                                    "market_data": market_data,
                                    "player": player,
                                }
                                plus_ev_opportunities.append(opportunity)

                    # Check under side
                    for bm_title, odds_data in market_data["under"]["odds"].items():
                        if bm_title == "pinnacle":
                            continue

                        american_odds = odds_data["american"]

                        # Match the outcome to the correct fair odds
                        # Use second fair odd for under
                        fair_odd = fair_odds[1]

                        # Check if odds are +EV
                        is_better = (
                            (american_odds > 0 and fair_odd >
                             0 and american_odds > fair_odd)
                            or (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd)
                            or (american_odds > 0 and fair_odd < 0)
                        )

                        if is_better:
                            # Calculate EV
                            if american_odds > 0:
                                decimal_odds = (american_odds / 100) + 1
                            else:
                                decimal_odds = (100 / abs(american_odds)) + 1

                            if fair_odd > 0:
                                fair_prob = 100 / (fair_odd + 100)
                            else:
                                fair_prob = abs(fair_odd) / \
                                    (abs(fair_odd) + 100)

                            ev_percentage = (
                                decimal_odds * fair_prob - 1) * 100

                            if ev_percentage >= self.ev_threshold:
                                # Validate player name exists for player props
                              
                                if "player_" in market_type:
                                    player = self.validate_player_prop(market_type, player)
                              
                                    
                                opportunity = {
                                    "sport": game["sport_title"],
                                    "market_type": market_type,
                                    "market_point": point if point != "0" else None,
                                    "game": f"{game['home_team']} vs {game['away_team']}",
                                    "commence_time": game["commence_time"],
                                    # Use team from pinnacle odds
                                    "team": pin_odds_list[1]["team"],
                                    "bookmaker": bm_title,
                                    "odds": american_odds,
                                    "fair_odds": fair_odd,
                                    "ev_percentage": round(ev_percentage, 2),
                                    "link": odds_data["link"],
                                    "market_data": market_data,
                                    "player": player,
                                }
                                plus_ev_opportunities.append(opportunity)

            return plus_ev_opportunities

        except Exception as e:
            print(f"Error finding +EV bets: {str(e)}")
            return []


async def get_all_data() -> tuple:
    """Get all odds data, arbitrage, and EV opportunities"""

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("ODDS_API_KEY is not set")

    print("Starting odds data collection...")

    async with OddsArbitrageFinder(api_key) as finder:
        print("Fetching and analyzing odds data...")
        arb_df, ev_df = await finder.get_dfs()
        raw_df = finder.raw_data

        # Convert timestamps to ISO format
        current_time = datetime.now(timezone.utc).isoformat()

        if not raw_df.empty:
            raw_df["timestamp"] = current_time
            
            raw_df["bet_key"] = raw_df.apply(
                lambda row: hashlib.sha256(
                    (
                        str(row["game"])
                        + str(row["team"])
                        + str(row["market_type"])
                        + str(row["market_point"])
                        + str(row["bookmaker"])
                    ).encode("utf-8")
                ).hexdigest(),
                axis=1,
            )

        if not arb_df.empty:
            arb_df["timestamp"] = current_time
            arb_df.loc[
                (arb_df["team1_link"].isnull() | (arb_df["team1_link"] == ""))
                & arb_df["team1_book"].str.contains("Fanatics"),
                "team1_link",
            ] = "https://sportsbook.fanatics.com/"
            arb_df.loc[
                (arb_df["team2_link"].isnull() | (arb_df["team2_link"] == ""))
                & arb_df["team2_book"].str.contains("Fanatics"),
                "team2_link",
            ] = "https://sportsbook.fanatics.com/"
        if not ev_df.empty:
            ev_df["timestamp"] = current_time
            ev_df.loc[
                (ev_df["link"].isnull() | (ev_df["link"] == ""))
                & ev_df["bookmaker"].str.contains("Fanatics"),
                "link",
            ] = "https://sportsbook.fanatics.com/"

        raw_df = raw_df.astype(object).where(pd.notnull(raw_df), None)
        arb_df = arb_df.astype(object).where(pd.notnull(arb_df), None)
        ev_df = ev_df.astype(object).where(pd.notnull(ev_df), None)

        raw_data = raw_df.to_dict(orient="records")
        arb_data = arb_df.to_dict(orient="records")
        ev_data = ev_df.to_dict(orient="records")

        raw_data = json.loads(json.dumps(raw_data, default=str))
        arb_data = json.loads(json.dumps(arb_data, default=str))
        ev_data = json.loads(json.dumps(ev_data, default=str))

        raw_df = pd.DataFrame(raw_data, columns=raw_df.columns)
        arb_df = pd.DataFrame(arb_data, columns=arb_df.columns)
        ev_df = pd.DataFrame(ev_data, columns=ev_df.columns)

        print(f"Found {len(raw_df)} raw odds entries")
        print(f"Found {len(arb_df)} arbitrage/low hold opportunities")
        print(f"Found {len(ev_df)} +EV opportunities")

        return raw_df, arb_df, ev_df


async def main():
    print("Starting odds finder application...")

    raw_df, arb_df, ev_df = await get_all_data()

    raw_df.to_csv("src/findBets/raw_odds_data.csv", index=False)
    arb_df.to_csv("src/findBets/arbitrage.csv", index=False)
    ev_df.to_csv("src/findBets/ev_opportunities.csv", index=False)


if __name__ == "__main__":
    asyncio.run(main())
