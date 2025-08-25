import pandas as pd
from datetime import datetime, timezone
import json
import os
import hashlib
import aiohttp
import asyncio
from typing import List
from datetime import datetime
import uuid

try:
    from .ev import (
        handle_ev_player_props,
        handle_ev_spreads,
        handle_ev_totals,
        handle_ev_h2h,
    )
    from .player_props import validate_player_prop
    from .utils import (
        calculate_implied_probability,
        calculate_kelly_percentage,
        decimal_to_american,
        power_devig,
    )
    from .ratelimiter import RateLimiter
except ImportError:
    from ev import (
        handle_ev_player_props,
        handle_ev_spreads,
        handle_ev_totals,
        handle_ev_h2h,
    )
    from player_props import validate_player_prop
    from utils import (
        calculate_implied_probability,
        calculate_kelly_percentage,
        decimal_to_american,
        power_devig,
    )
    from ratelimiter import RateLimiter


class OddsArbitrageFinder:
    def __init__(self, api_key, state="md"):
        # Initialize basic attributes first
        self.api_key = api_key
        self.state = state.lower()
        self.base_url = "https://api.the-odds-api.com/v4/sports"
        self.output_dir = "src/findBets"

        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)



        # Initialize session and rate limiter
        self.session = None
        self.rate_limiter = RateLimiter()

        # Initialize storage for opportunities
        self.all_opportunities = []
        self.all_odds_data = []
        self.all_plus_ev = []
        self.all_player_props = {}

        # Thresholds
        self.low_hold_threshold = 1.1
        self.ev_threshold = 2.0

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
            "MLB": [
                "batter_home_runs",
                "batter_hits",
                "batter_total_bases",
                "batter_runs_scored",
            ],
        }

        # Sports to track
        self.sports = [
            "americanfootball_ncaaf",
            "basketball_ncaab",
            "basketball_nba",
            "icehockey_nhl",
            "baseball_mlb",
            "americanfootball_nfl",
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
            "williamhill_us",
            "caesars",
            "betrivers",
            "pinnacle",
            "fanatics",
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
                    await self.rate_limiter.handle_rate_limit()
                    # Retry with backoff
                    return await self.make_request(url, params)

                response.raise_for_status()
                data = await response.json()

                # Reset retry count on successful request
                self.rate_limiter.reset_retry_count()
                return data

        except aiohttp.ClientError as e:
            if isinstance(e, aiohttp.ClientResponseError):
                if e.status == 429:  # Handle rate limit in exception case
                    await self.rate_limiter.handle_rate_limit()
                    return await self.make_request(url, params)
            return None

        except Exception as e:
            return None

    async def get_featured_odds(self, sport: str) -> List[dict]:
        """Fetch odds for featured markets"""
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"{self.base_url}/{sport}/odds"

        params = {
            "apiKey": self.api_key,
            "bookmakers": ",".join(self.bookmakers),
            "markets": ",".join(self.featured_markets),
            "oddsFormat": "decimal",
            "commenceTimeFrom": current_time,
            "includeLinks": "true",
            "regions": "us",
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
                                    "odds": decimal_to_american(
                                        outcome.get("price", 0)
                                    ),
                                    "link": bookmaker.get("link", ""),
                                }

                                # Modify team name for player props to include player name
                                if "player_" in market.get("key", "").lower():
                                    player_name = outcome.get("description", "")
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
                                                        "american": decimal_to_american(
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
                                row_df = pd.DataFrame([row], columns=list(row.keys()))
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



        return data if data else []

    async def get_dfs(self) -> tuple:
        """Get DataFrames for arbitrage and +EV opportunities"""
        try:
            # Generate arbitrage table
            arb_df = await self.generate_arbitrage_table()

            # Create EV DataFrame
            ev_df = pd.DataFrame(self.all_plus_ev)

            if not ev_df.empty:
                # Ensure consistent primary key generation between raw and EV data
                ev_df["primary_key"] = [str(uuid.uuid4()) for _ in range(len(ev_df))]
                ev_df["timestamp"] = datetime.now(timezone.utc)

            if not arb_df.empty:
                arb_df["primary_key"] = [str(uuid.uuid4()) for _ in range(len(arb_df))]
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
            raw_df = self.raw_data.astype(object).where(pd.notnull(self.raw_data), None)
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

            return arb_df, ev_df

        except Exception as e:
            print(f"Error in get_dfs: {str(e)}")
            return pd.DataFrame(), pd.DataFrame()

    async def generate_arbitrage_table(self) -> pd.DataFrame:
        """Generate table of arbitrage opportunities"""
        try:
            # Fetch odds for all sports
            all_featured_odds = []

            # Get all sports data concurrently
            tasks = [self.get_featured_odds(sport) for sport in self.sports]
            featured_odds_results = await asyncio.gather(*tasks)

            for sport, featured_odds in zip(self.sports, featured_odds_results):
                if featured_odds:
                    all_featured_odds.extend([(sport, game) for game in featured_odds])

            # Process all games concurrently
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
                return pd.DataFrame()

        except Exception as e:
            print(f"Error generating arbitrage table: {str(e)}")
            return pd.DataFrame()

    async def process_game(self, game: dict) -> tuple:
        """Process a single game for arbitrage and +EV opportunities"""
        try:
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
                    "regions": "us",
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
                        "regions": "us",
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
                                        "odds": decimal_to_american(
                                            outcome.get("price", 0)
                                        ),
                                        "link": bookmaker.get("link", ""),
                                    }

                                    # Modify team name for player props to include player name
                                    if "player_" in market.get("key", "").lower():
                                        player_name = outcome.get("description", "")
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
            print(f"Error processing game {game.get('id', 'unknown')}: {str(e)}")
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
            "regions": "us",
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
                                "american_odds": decimal_to_american(outcome["price"]),
                                "link": bm.get("link", ""),
                            }
                            for outcome in market["outcomes"]
                        ]

            if market_data["books"]:
                odds_data.append(market_data)

        return odds_data

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
                                    "under": {"odds": {}},
                                }
                                point = odds1.get("point")

                                for bm in all_bookmakers:
                                    if not isinstance(bm, dict) or "title" not in bm:
                                        continue

                                    bm_title = bm["title"].lower()
                                    if bm_title not in [
                                        b.lower() for b in self.bookmakers
                                    ]:
                                        continue

                                    for market in bm.get("markets", []):
                                        if market["key"].lower() != market_type:
                                            continue

                                        for outcome in market["outcomes"]:
                                            # For spreads, ensure points match or are opposites
                                            outcome_point = float(
                                                outcome.get("point", 0) or 0
                                            )
                                            if "spreads" in market_type:
                                                if point is not None:
                                                    point_float = float(point)
                                                    if not (
                                                        abs(outcome_point - point_float)
                                                        <= 0.1
                                                        or abs(
                                                            outcome_point + point_float
                                                        )
                                                        <= 0.1
                                                    ):
                                                        continue
                                            elif point != outcome.get("point"):
                                                continue

                                            decimal_odds = outcome["price"]
                                            american_odds = decimal_to_american(
                                                decimal_odds
                                            )

                                            # Determine side (over/under)
                                            if "spreads" in market_type:
                                                side = (
                                                    "over"
                                                    if outcome_point > 0
                                                    or (
                                                        outcome_point == 0
                                                        and outcome["name"]
                                                        == odds1["team"]
                                                    )
                                                    else "under"
                                                )
                                            else:
                                                side = (
                                                    "over"
                                                    if outcome["name"] == odds1["team"]
                                                    else "under"
                                                )

                                            market_data[side]["odds"][bm_title] = {
                                                "american": american_odds,
                                                "decimal": decimal_odds,
                                                "point": outcome_point,
                                                "link": bm.get("link", ""),
                                            }

                                # Ensure both sides have the same bookmakers with valid data
                                all_bookmakers_used = set(
                                    market_data["over"]["odds"].keys()
                                ) | set(market_data["under"]["odds"].keys())
                                for side in ["over", "under"]:
                                    for bm in all_bookmakers_used:
                                        if (
                                            bm not in market_data[side]["odds"]
                                            or market_data[side]["odds"][bm] is None
                                        ):
                                            other_side = (
                                                "under" if side == "over" else "over"
                                            )
                                            if (
                                                bm in market_data[other_side]["odds"]
                                                and market_data[other_side]["odds"][bm]
                                                is not None
                                            ):
                                                other_data = market_data[other_side][
                                                    "odds"
                                                ][bm].copy()
                                                if (
                                                    "spreads" in market_type
                                                    and other_data["point"] is not None
                                                ):
                                                    other_data["point"] = -float(
                                                        other_data["point"]
                                                    )
                                                market_data[side]["odds"][
                                                    bm
                                                ] = other_data
                                            else:
                                                market_data["over"]["odds"].pop(
                                                    bm, None
                                                )
                                                market_data["under"]["odds"].pop(
                                                    bm, None
                                                )

                                prob1 = calculate_implied_probability(odds1["price"])
                                prob2 = calculate_implied_probability(odds2["price"])
                                total_prob = prob1 + prob2

                                if total_prob <= self.low_hold_threshold:
                                    stake1, stake2 = calculate_kelly_percentage(
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
                                        player_name = validate_player_prop(
                                            market_type, player_name
                                        )

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
                                            "team1_odds": decimal_to_american(
                                                odds1["price"]
                                            ),
                                            "team1_point": odds1.get("point"),
                                            "team1_stake": round(stake1, 2),
                                            "team1_link": odds1["link"],
                                            "team2_name": odds2["team"],
                                            "team2_book": odds2["bookmaker"],
                                            "team2_odds": decimal_to_american(
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

            # First, get Pinnacle odds for each market
            pinnacle_odds = {}
            for bm in all_bookmakers:
                if not isinstance(bm, dict) or "title" not in bm:
    
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
                                "american": decimal_to_american(outcome["price"]),
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

                # Process opportunities based on market type
                opportunities = []
                if market_type == "h2h":
                    opportunities = handle_ev_h2h(
                        market_type=market_type,
                        game=game,
                        pinnacle_odds=pinnacle_odds,
                        all_bookmakers=all_bookmakers,
                        ev_threshold=self.ev_threshold,
                    )
                elif "spreads" in market_type:
                    opportunities = handle_ev_spreads(
                        market_type=market_type,
                        game=game,
                        pinnacle_odds=pinnacle_odds,
                        all_bookmakers=all_bookmakers,
                        ev_threshold=self.ev_threshold,
                    )
                elif "totals" in market_type:
                    opportunities = handle_ev_totals(
                        market_type=market_type,
                        game=game,
                        pinnacle_odds=pinnacle_odds,
                        all_bookmakers=all_bookmakers,
                        ev_threshold=self.ev_threshold,
                    )
                elif "player_" in market_type:
                    opportunities = handle_ev_player_props(
                        market_type=market_type,
                        game=game,
                        pinnacle_odds=pinnacle_odds,
                        all_bookmakers=all_bookmakers,
                        ev_threshold=self.ev_threshold,
                    )
                else:
    
                    continue

                plus_ev_opportunities.extend(opportunities)

            return plus_ev_opportunities

        except Exception as e:
            print(f"Error finding +EV bets: {str(e)}")
            return []


async def get_all_data() -> tuple:
    """Get all odds data, arbitrage, and EV opportunities"""

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("ODDS_API_KEY is not set")

    async with OddsArbitrageFinder(api_key) as finder:
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

        return raw_df, arb_df, ev_df


async def main():
    raw_df, arb_df, ev_df = await get_all_data()

    raw_df.to_csv("data/raw_odds_data.csv", index=False)
    arb_df.to_csv("data/arbitrage.csv", index=False)
    ev_df.to_csv("data/ev_opportunities.csv", index=False)


if __name__ == "__main__":
    asyncio.run(main())
