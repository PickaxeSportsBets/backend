import requests
import pandas as pd
from datetime import datetime, timezone
import time
import json
import logging
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
        
        # Set up logging
        self.setup_logging()
        
        # Initialize logger for this class
        self.logger = logging.getLogger("odds_finder")
        self.logger.info("Initializing OddsArbitrageFinder...")

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
            "NBA": [
                "player_points",
                "player_rebounds",
                "player_assists",
                "player_threes",
                "player_blocks",
                "player_steals"
            ],
            "NFL": [
                "player_pass_tds",
                "player_pass_yards",
                "player_rush_yards",
                "player_receptions",
                "player_reception_yards",
                "player_touchdowns"
            ],
            "NHL": [
                "player_points",
                "player_assists",
                "player_shots"
            ],
            "MLB": [
                "player_hits",
                "player_strikeouts",
                "player_home_runs"
            ]
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
        self.featured_markets = [
            "h2h",
            "spreads",
            "totals"
        ]
        
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
            "hardrock_bet",
            "betmgm",
            # "williamhill_us",
            "betrivers",
            "pinnacle",
            "fanatics",
            "caesars",
            "fliff"
        ]

        # Store initial data
        self.raw_data = pd.DataFrame()

    def setup_logging(self):
        """Configure logging for the application"""
        # Create logs directory if it doesn't exist
        log_dir = os.path.join(self.output_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)

        # Set up root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Remove any existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # Console handler with color formatting
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_format)
        root_logger.addHandler(console_handler)

        # File handler for detailed debug logs
        debug_file = os.path.join(log_dir, "debug.log")
        file_handler = logging.FileHandler(debug_file)
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
        )
        file_handler.setFormatter(file_format)
        root_logger.addHandler(file_handler)

        # File handler for errors
        error_file = os.path.join(log_dir, "error.log")
        error_handler = logging.FileHandler(error_file)
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_format)
        root_logger.addHandler(error_handler)

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
                    self.logger.warning("Rate limit exceeded - implementing backoff strategy")
                    await self.rate_limiter.handle_rate_limit()
                    return await self.make_request(url, params)  # Retry with backoff
                
                response.raise_for_status()
                data = await response.json()
                
                # Reset retry count on successful request
                self.rate_limiter.reset_retry_count()
                return data

        except aiohttp.ClientError as e:
            self.logger.error(f"Error making request: {str(e)}")
            if isinstance(e, aiohttp.ClientResponseError):
                self.logger.error(f"Status: {e.status}")
                self.logger.error(f"Message: {e.message}")
                
                if e.status == 429:  # Handle rate limit in exception case
                    await self.rate_limiter.handle_rate_limit()
                    return await self.make_request(url, params)
            return None

        except Exception as e:
            self.logger.error(f"Unexpected error in make_request: {str(e)}", exc_info=True)
            return None

    async def get_player_props(self, sport: str, event_id: str) -> List[dict]:
        """Fetch player props for a specific event"""
        sport_name = sport.upper().split("_")[1] if "_" in sport else sport.upper()
        logger = logging.getLogger("player_props")
        
        if sport_name not in self.player_props:
            logger.debug(f"No player props configured for sport: {sport_name}")
            return []

        prop_markets = self.player_props[sport_name]
        logger.info(f"Fetching player props for {sport_name} event {event_id}")
        
        # Split markets into smaller batches to avoid large requests
        batch_size = 3
        all_bookmakers = []
        
        for i in range(0, len(prop_markets), batch_size):
            market_batch = prop_markets[i:i + batch_size]
            logger.debug(f"Fetching market batch: {market_batch}")
            
            url = f"{self.base_url}/{sport}/events/{event_id}/odds"
            params = {
                "apiKey": self.api_key,
                # "regions": self.state,
                "bookmakers": ",".join(self.bookmakers),
                "markets": ",".join(market_batch),
                "oddsFormat": "decimal"
            }

            try:
                data = await self.make_request(url, params)
                if not data:
                    logger.warning(f"No data received for market batch in event {event_id}")
                    continue
                    
                if "bookmakers" in data:
                    all_bookmakers.extend(data["bookmakers"])
                    logger.info(f"Received data from {len(data['bookmakers'])} bookmakers for market batch")
                    
                # Add delay between market batches
                if i + batch_size < len(prop_markets):
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error fetching market batch for event {event_id}: {str(e)}")
                continue
            
        if not all_bookmakers:
            logger.warning(f"No bookmaker data received for any markets in event {event_id}")
            
        return all_bookmakers

    def process_player_props(self, bookmakers: List[dict], sport: str) -> Dict:
        """Process player props data"""
        logger = logging.getLogger("player_props_processor")
        markets = {}
        sport_name = sport.upper().split("_")[1] if "_" in sport else sport.upper()
        prop_markets = self.player_props.get(sport_name, [])

        logger.info(f"Processing player props for {sport_name}")
        logger.debug(f"Available prop markets: {prop_markets}")
        logger.info(f"Processing data from {len(bookmakers)} bookmakers")

        for bookmaker in bookmakers:
            logger.debug(f"Processing bookmaker: {bookmaker['title']}")
            if bookmaker["title"].lower() not in [b.lower() for b in self.bookmakers]:
                logger.debug(f"Skipping bookmaker {bookmaker['title']} - not in configured list")
                continue

            for market in bookmaker.get("markets", []):
                if market["key"] not in prop_markets:
                    logger.debug(f"Skipping market {market['key']} - not in prop markets list")
                    continue

                logger.debug(f"Processing market: {market['key']} with {len(market['outcomes'])} outcomes")
                for outcome in market["outcomes"]:
                    player_name = outcome.get("description", "Unknown")
                    if player_name == "Unknown":
                        logger.warning(f"Missing player description in outcome: {outcome}")
                        continue

                    prop_type = market["key"].replace("player_", "")
                    point = outcome.get("point")
                    market_key = f"{market['key']}_{player_name}_{point}" if point else f"{market['key']}_{player_name}"

                    if market_key not in markets:
                        markets[market_key] = []

                    markets[market_key].append({
                        "bookmaker": bookmaker["title"],
                        "player": player_name,
                        "prop_type": prop_type,
                        "team": outcome["name"],
                        "price": outcome.get("price", 0),
                        "point": point,
                        "link": bookmaker.get("link", ""),
                    })
                    logger.debug(f"Added outcome for {player_name} - {prop_type} - {outcome['name']} @ {outcome.get('price', 0)}")

        logger.info(f"Processed {len(markets)} unique player prop markets")
        for market_key, outcomes in markets.items():
            logger.debug(f"Market {market_key}: {len(outcomes)} outcomes")
            for outcome in outcomes:
                logger.debug(f"  - {outcome['bookmaker']}: {outcome['team']} @ {outcome['price']}")
        
        return markets

    async def get_featured_odds(self, sport: str) -> List[dict]:
        """Fetch odds for featured markets"""
        logger = logging.getLogger("featured_odds")
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{self.base_url}/{sport}/odds"
        
        params = {
            "apiKey": self.api_key,
            "bookmakers": ",".join(self.bookmakers),
            "markets": ",".join(self.featured_markets),  # Only request base markets
            "oddsFormat": "decimal",
            "commenceTimeFrom": current_time,
            # "regions": self.state,  # Add regions parameter
            "includeLinks": "true",
        }

        data = await self.make_request(url, params)
        if data:
            # Store raw data in DataFrame
            if len(data) > 0:
                df = pd.json_normalize(data)
                df['sport'] = sport
                df['timestamp'] = datetime.now(timezone.utc)
                if self.raw_data.empty:
                    self.raw_data = df
                else:
                    self.raw_data = pd.concat([self.raw_data, df], ignore_index=True)
                
                # Log bookmaker information
                bookmakers_found = set()
                for event in data:
                    for bm in event.get('bookmakers', []):
                        bookmakers_found.add(bm.get('title', '').lower())
                
                logger.info(f"Found data from bookmakers: {sorted(bookmakers_found)}")
                missing_books = set(b.lower() for b in self.bookmakers) - bookmakers_found
                if missing_books:
                    logger.warning(f"Missing data from bookmakers: {sorted(missing_books)}")
            
        return data if data else []

    async def get_dfs(self) -> tuple:
        """Get DataFrames for arbitrage and +EV opportunities"""
        try:
            self.logger.info("Starting odds analysis...")
            
            # Generate arbitrage table
            self.logger.info("Generating arbitrage opportunities table...")
            arb_df = await self.generate_arbitrage_table()
            self.logger.info(f"Found {len(arb_df)} arbitrage/low hold opportunities")
            
            # Create EV DataFrame
            self.logger.info("Processing +EV opportunities...")
            ev_df = pd.DataFrame(self.all_plus_ev)
            
            if not ev_df.empty:
                self.logger.info(f"Found {len(ev_df)} +EV opportunities")
                ev_df["primary_key"] = (
                    ev_df["game"].astype(str)
                    + ev_df["team"].astype(str)
                    + ev_df["market_type"].astype(str)
                    + ev_df["market_point"].astype(str)
                    + ev_df["bookmaker"].astype(str)
                ).apply(lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest())
                ev_df["timestamp"] = datetime.now(timezone.utc)
            else:
                self.logger.info("No +EV opportunities found")

            if not arb_df.empty:
                arb_df["primary_key"] = (
                    arb_df["market_type"].astype(str)
                    + arb_df["game"].astype(str)
                    + arb_df["team1_name"].astype(str)
                    + arb_df["team2_name"].astype(str)
                    + arb_df["commence_time"].astype(str)
                    + arb_df["team1_book"].astype(str)
                    + arb_df["team2_book"].astype(str)
                    + arb_df["team1_odds"].astype(str)
                    + arb_df["team2_odds"].astype(str)
                    + arb_df["market_type"].astype(str)
                    + (arb_df["prop_description"].fillna("").astype(str))
                ).apply(lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest())

            # Save raw data
            if not self.raw_data.empty:
                raw_data_path = os.path.join(self.output_dir, "raw_odds_data.csv")
                self.raw_data.to_csv(raw_data_path, index=False)
                self.logger.info(f"Saved raw odds data to {raw_data_path}")

            self.logger.info("Analysis complete")
            return arb_df, ev_df

        except Exception as e:
            self.logger.error(f"Error in get_dfs: {str(e)}", exc_info=True)
            return pd.DataFrame(), pd.DataFrame()

    async def generate_arbitrage_table(self) -> pd.DataFrame:
        """Generate table of arbitrage opportunities"""
        try:
            self.logger.info("Starting arbitrage table generation...")
            
            # Fetch odds for all sports
            self.logger.info("Fetching odds for all sports...")
            all_featured_odds = []
            
            # Get all sports data concurrently
            tasks = [self.get_featured_odds(sport) for sport in self.sports]
            featured_odds_results = await asyncio.gather(*tasks)
            
            for sport, featured_odds in zip(self.sports, featured_odds_results):
                if featured_odds:
                    self.logger.info(f"Received data for {sport}: {len(featured_odds)} games")
                    all_featured_odds.extend([(sport, game) for game in featured_odds])
                else:
                    self.logger.warning(f"No odds data received for {sport}")

            # Process all games concurrently
            self.logger.info(f"Processing {len(all_featured_odds)} total games...")
            game_tasks = [self.process_game(game) for _, game in all_featured_odds]
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
                self.logger.info(f"Creating DataFrame with {len(self.all_opportunities)} opportunities")
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
                ]
                
                # Ensure all columns exist, fill missing with None
                for col in columns:
                    if col not in df.columns:
                        df[col] = None
                        
                return df[columns]
            else:
                self.logger.info("No arbitrage opportunities found")
                return pd.DataFrame()

        except Exception as e:
            self.logger.error(f"Error generating arbitrage table: {str(e)}", exc_info=True)
            return pd.DataFrame()

    async def process_game(self, game: dict) -> tuple:
        """Process a single game for arbitrage and +EV opportunities"""
        try:
            self.logger.debug(f"Processing game: {game['home_team']} vs {game['away_team']}")
            
            # Initialize lists to store all odds
            all_odds = []
            tasks = []
            
            if game.get('id'):
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
                sport_name = game['sport_key'].upper().split("_")[1] if "_" in game['sport_key'] else game['sport_key'].upper()
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
                
                # Process results
                for result in results:
                    if isinstance(result, Exception):
                        self.logger.error(f"Error fetching odds: {str(result)}")
                        continue
                    if result and "bookmakers" in result:
                        all_odds.extend(result["bookmakers"])

            # Process opportunities
            opportunities = await self.find_opportunities(game, all_odds) if all_odds else []
            odds_data = await self.collect_all_odds(game)
            plus_ev = await self.find_plus_ev_bets(game, all_odds) if all_odds else []

            return opportunities, plus_ev, odds_data

        except Exception as e:
            self.logger.error(f"Error processing game {game.get('id', 'unknown')}: {str(e)}")
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
                "books": {}
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
                                "american_odds": self.decimal_to_american(outcome["price"]),
                                "link": bm.get("link", "")
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

    def calculate_kelly_percentage(self, prob_a: float, prob_b: float, odds_a: float, odds_b: float) -> tuple:
        """Calculate optimal bet sizing for arbitrage opportunities"""
        try:
            stake_a = (odds_b * 100) / (odds_a + odds_b)
            stake_b = (odds_a * 100) / (odds_a + odds_b)
            
            if stake_a <= 0 or stake_b <= 0:
                return (50.0, 50.0)
                
            return (stake_a, stake_b)
        except (ZeroDivisionError, ValueError):
            return (50.0, 50.0)

    async def find_opportunities(self, game: dict, additional_odds: List[dict] = None) -> List[dict]:
        """Find arbitrage and low hold opportunities in a game"""
        try:
            opportunities = []
            logger = logging.getLogger("opportunities_finder")
            
            # Combine all bookmakers
            all_bookmakers = []
            if "bookmakers" in game:
                all_bookmakers.extend(game["bookmakers"])
            if additional_odds:
                all_bookmakers.extend(additional_odds)

            # Filter to configured bookmakers
            all_bookmakers = [
                bm for bm in all_bookmakers 
                if isinstance(bm, dict) and "title" in bm and bm["title"].lower() in [b.lower() for b in self.bookmakers]
            ]

            # Process all market types
            all_markets = self.featured_markets + self.alternate_markets
            
            for market_type in all_markets:
                market_type = market_type.lower()  # Convert to lowercase
                logger.debug(f"Processing market type: {market_type}")
                
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
                                market["key"].lower()
                            )
                            
                            if key not in odds_by_team or outcome["price"] > odds_by_team[key]["price"]:
                                odds_by_team[key] = {
                                    "bookmaker": bm["title"],
                                    "team": outcome["name"],
                                    "price": outcome["price"],
                                    "point": outcome.get("point"),
                                    "link": bm.get("link", ""),
                                    "market_key": market["key"].lower()
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
                                
                        elif "totals" in market_type:
                            if odds1.get("point") == odds2.get("point"):
                                valid_pair = (
                                    ("OVER" in odds1["team"].upper() and "UNDER" in odds2["team"].upper()) or
                                    ("UNDER" in odds1["team"].upper() and "OVER" in odds2["team"].upper())
                                )

                        if valid_pair:
                            pair_key = tuple(sorted([key1, key2]))
                            if pair_key not in processed_pairs:
                                processed_pairs.add(pair_key)
                                
                                # Collect all bookmaker odds for this market point
                                market_data = {"over": {"odds": {}}, "under": {"odds": {}}}
                                point = odds1.get("point")
                                
                                for bm in all_bookmakers:
                                    if not isinstance(bm, dict) or "title" not in bm:
                                        continue
                                        
                                    bm_title = bm["title"].lower()
                                    
                                    for market in bm.get("markets", []):
                                        if market["key"].lower() != market_type:
                                            continue
                                            
                                        for outcome in market["outcomes"]:
                                            if point != outcome.get("point"):
                                                continue
                                                
                                            decimal_odds = outcome["price"]
                                            american_odds = self.decimal_to_american(decimal_odds)
                                            
                                            # Determine if it's over/under or team1/team2
                                            if "totals" in market_type:
                                                side = "over" if "OVER" in outcome["name"].upper() else "under"
                                            elif "spreads" in market_type:
                                                # For spreads, team with positive spread goes in "over", negative in "under"
                                                outcome_point = float(outcome.get("point", 0) or 0)
                                                side = "over" if outcome_point > 0 else "under"
                                            else:
                                                side = "over" if outcome["name"] == odds1["team"] else "under"
                                                
                                            market_data[side]["odds"][bm_title] = {
                                                "american": american_odds,
                                                "decimal": decimal_odds,
                                                "link": bm.get("link")
                                            }
                                
                                prob1 = self.calculate_implied_probability(odds1["price"])
                                prob2 = self.calculate_implied_probability(odds2["price"])
                                total_prob = prob1 + prob2
                                
                                if total_prob <= self.low_hold_threshold:
                                    stake1, stake2 = self.calculate_kelly_percentage(
                                        prob1, prob2, odds1["price"], odds2["price"]
                                    )
                                    
                                    hold_percentage = (total_prob - 1) * 100
                                    opportunity_type = "Arbitrage" if total_prob < 1 else "Low Hold"
                                    profit_percentage = round(((1 / total_prob) - 1) * 100, 2) if total_prob < 1 else 0
                                    
                                    opportunities.append({
                                        "sport": game["sport_title"],
                                        "opportunity_type": opportunity_type,
                                        "market_type": market_type,
                                        "market_point": odds1.get("point"),
                                        "game": f"{game['home_team']} vs {game['away_team']}",
                                        "commence_time": game["commence_time"],
                                        "team1_name": odds1["team"],
                                        "team1_book": odds1["bookmaker"],
                                        "team1_odds": self.decimal_to_american(odds1["price"]),
                                        "team1_point": odds1.get("point"),
                                        "team1_stake": round(stake1, 2),
                                        "team1_link": odds1["link"],
                                        "team2_name": odds2["team"],
                                        "team2_book": odds2["bookmaker"],
                                        "team2_odds": self.decimal_to_american(odds2["price"]),
                                        "team2_point": odds2.get("point"),
                                        "team2_stake": round(stake2, 2),
                                        "team2_link": odds2["link"],
                                        "hold_percentage": round(hold_percentage, 2),
                                        "profit_percentage": profit_percentage,
                                        "market_data": market_data
                                    })

            return opportunities

        except Exception as e:
            logger.error(f"Error finding opportunities: {str(e)}", exc_info=True)
            return []

    async def find_plus_ev_bets(self, game: dict, additional_odds: List[dict] = None) -> List[dict]:
        """Find +EV betting opportunities using Pinnacle as the sharp book"""
        try:
            plus_ev_opportunities = []
            logger = logging.getLogger("plus_ev_finder")
            
            # Combine bookmakers
            all_bookmakers = []
            if isinstance(game, dict) and "bookmakers" in game:
                all_bookmakers.extend(game["bookmakers"])
            if additional_odds:
                if isinstance(additional_odds, list):
                    all_bookmakers.extend(additional_odds)
                elif isinstance(additional_odds, dict) and "bookmakers" in additional_odds:
                    all_bookmakers.extend(additional_odds["bookmakers"])

            # First, get Pinnacle odds for each market
            pinnacle_odds = {}
            for bm in all_bookmakers:
                if not isinstance(bm, dict) or "title" not in bm:
                    logger.debug(f"Skipping invalid bookmaker data: {bm}")
                    continue
                    
                if bm["title"].lower() == "pinnacle":
                    for market in bm.get("markets", []):
                        market_key = market["key"].lower()  # Convert to lowercase
                        if market_key not in pinnacle_odds:
                            pinnacle_odds[market_key] = []
                        pinnacle_odds[market_key].extend([
                            {
                                "team": outcome["name"],
                                "price": outcome["price"],
                                "point": outcome.get("point"),
                                "american": self.decimal_to_american(outcome["price"])
                            }
                            for outcome in market["outcomes"]
                        ])

            # Process each market type
            for market_type in self.featured_markets + self.alternate_markets:
                market_type = market_type.lower()  # Convert to lowercase
                if market_type not in pinnacle_odds:
                    continue
                    
                pin_odds = pinnacle_odds[market_type]
                
                # Group Pinnacle odds by point/line
                pin_odds_by_point = {}
                for odds in pin_odds:
                    key = str(odds.get("point", "0"))
                    if key not in pin_odds_by_point:
                        pin_odds_by_point[key] = []
                    pin_odds_by_point[key].append(odds)

                # For each point/line, find matching odds from other books
                for point, pin_odds_list in pin_odds_by_point.items():
                    if len(pin_odds_list) != 2:  # Need both sides for devig
                        continue
                        
                    # Devig Pinnacle odds
                    pin_american = [odds["american"] for odds in pin_odds_list]
                    fair_odds = power_devig(pin_american)
                    
                    # Collect all bookmaker odds for this market point
                    market_data = {"over": {"odds": {}}, "under": {"odds": {}}}
                    
                    for bm in all_bookmakers:
                        if not isinstance(bm, dict) or "title" not in bm:
                            continue
                            
                        bm_title = bm["title"].lower()
                        
                        for market in bm.get("markets", []):
                            if market["key"].lower() != market_type:
                                continue
                                
                            for outcome in market["outcomes"]:
                                if point != "0" and str(outcome.get("point", "0")) != point:
                                    continue
                                    
                                decimal_odds = outcome["price"]
                                american_odds = self.decimal_to_american(decimal_odds)
                                
                                side = "over" if "OVER" in outcome["name"].upper() else "under"
                                market_data[side]["odds"][bm_title] = {
                                    "american": american_odds,
                                    "decimal": decimal_odds,
                                    "link": bm.get("link")
                                }
                    
                    # Check each book's odds against fair odds
                    for bm in all_bookmakers:
                        if not isinstance(bm, dict) or "title" not in bm:
                            continue
                            
                        if bm["title"].lower() == "pinnacle":
                            continue
                            
                        for market in bm.get("markets", []):
                            if market["key"].lower() != market_type:
                                continue
                                
                            for outcome in market["outcomes"]:
                                if point != "0" and str(outcome.get("point", "0")) != point:
                                    continue
                                    
                                american_odds = self.decimal_to_american(outcome["price"])
                                
                                # Match the outcome to the correct fair odds
                                if "totals" in market_type:
                                    idx = 0 if "OVER" in outcome["name"].upper() else 1
                                else:
                                    idx = 0 if outcome["name"] == pin_odds_list[0]["team"] else 1
                                    
                                fair_odd = fair_odds[idx]
                                
                                # Check if odds are +EV
                                is_better = (
                                    (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd) or
                                    (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd) or
                                    (american_odds > 0 and fair_odd < 0)
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
                                        fair_prob = abs(fair_odd) / (abs(fair_odd) + 100)
                                        
                                    ev_percentage = (decimal_odds * fair_prob - 1) * 100
                                    
                                    if ev_percentage >= self.ev_threshold:
                                        plus_ev_opportunities.append({
                                            "sport": game["sport_title"],
                                            "market_type": market_type,
                                            "market_point": point if point != "0" else None,
                                            "game": f"{game['home_team']} vs {game['away_team']}",
                                            "commence_time": game["commence_time"],
                                            "team": outcome["name"],
                                            "bookmaker": bm["title"],
                                            "odds": american_odds,
                                            "fair_odds": fair_odd,
                                            "ev_percentage": round(ev_percentage, 2),
                                            "link": bm.get("link", ""),
                                            "market_data": market_data
                                        })

            return plus_ev_opportunities

        except Exception as e:
            logger.error(f"Error finding +EV bets: {str(e)}", exc_info=True)
            return []

async def main():
    """Main entry point for the application"""
    # Set up basic logging before we create the OddsArbitrageFinder
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("main")
    
    try:
        # Get API key from environment
        api_key = os.getenv('ODDS_API_KEY')
        if not api_key:
            logger.error("ODDS_API_KEY environment variable not set")
            return 1

        logger.info("Starting odds finder application...")
        
        async with OddsArbitrageFinder(api_key) as finder:
            logger.info("Fetching and analyzing odds data...")
            arb_df, ev_df = await finder.get_dfs()

            # Save arbitrage opportunities
            if not arb_df.empty:
                arb_path = os.path.join(finder.output_dir, "arbitrage.csv")
                arb_df.to_csv(arb_path, index=False)
                logger.info(f"Found {len(arb_df)} arbitrage/low hold opportunities")
                logger.info(f"Saved arbitrage opportunities to {arb_path}")
            else:
                logger.info("No arbitrage opportunities found")

            # Save +EV opportunities
            if not ev_df.empty:
                ev_path = os.path.join(finder.output_dir, "ev.json")
                ev_json = ev_df.to_dict(orient="records")
                with open(ev_path, "w") as f:
                    json.dump(ev_json, f, indent=2, default=str)
                logger.info(f"Found {len(ev_df)} +EV opportunities")
                logger.info(f"Saved +EV opportunities to {ev_path}")
            else:
                logger.info("No +EV opportunities found")

        return 0

    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Error running odds finder: {str(e)}")
        logger.error(traceback.format_exc())
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main())) 