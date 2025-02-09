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
from typing import List, Dict
from collections import deque
from datetime import datetime, timedelta
import traceback


class RateLimiter:
    def __init__(self, calls_per_second: int = 25):
        self.calls_per_second = calls_per_second
        self.timestamps = deque(maxlen=calls_per_second)
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = datetime.now()

            # Remove timestamps older than 1 second
            while self.timestamps and (now - self.timestamps[0]) > timedelta(seconds=1):
                self.timestamps.popleft()

            # If we've made too many calls in the last second, wait
            if len(self.timestamps) >= self.calls_per_second:
                wait_time = 1 - (now - self.timestamps[0]).total_seconds()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            # Add current timestamp
            self.timestamps.append(now)


def power_devig(odds_list):
    """
    Devig odds using the power method to find fair probabilities.
    Takes a list of American odds and returns fair American odds.
    """
    def american_to_prob(odds):
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return abs(odds) / (abs(odds) + 100)

    probs = [american_to_prob(odds) for odds in odds_list]

    # Calculate the vig-free scalar
    power = 1  # Start with power of 1
    total = sum(prob ** power for prob in probs)
    while abs(total - 1) > 0.0001:
        if total > 1:
            power += 0.0001
        else:
            power -= 0.0001
        total = sum(prob ** power for prob in probs)

    # Calculate fair probabilities
    fair_probs = [(prob ** power) / total for prob in probs]

    def prob_to_american(prob):
        if prob >= 0.5:
            return -100 * prob / (1 - prob)
        else:
            return 100 * (1 - prob) / prob

    return [round(prob_to_american(p)) for p in fair_probs]


class OddsArbitrageFinder:
    def __init__(self, api_key, state='md'):
        self.api_key = api_key
        self.state = state.lower()
        self.base_url = "https://api.the-odds-api.com/v4/sports"
        self.sports = [
            'americanfootball_ncaaf',
            'basketball_ncaab',
            'basketball_nba',
            'icehockey_nhl',
            'americanfootball_nfl'
        ]

        # Base markets that we'll iterate over explicitly:
        self.featured_markets = ['h2h', 'spreads', 'totals']

        # Additional market keys (used for fetching extra lines from the API) but
        # we will NOT loop over them separately in find_opportunities / find_arbitrage / find_plus_ev_bets.
        self.additional_markets = [
            'alternate_spreads', 'alternate_totals',
            'spreads_q1', 'spreads_q2', 'spreads_q3', 'spreads_q4',
            'spreads_h1', 'spreads_h2',
            'spreads_p1', 'spreads_p2', 'spreads_p3',
            'totals_q1', 'totals_q2', 'totals_q3', 'totals_q4',
            'totals_h1', 'totals_h2',
            'totals_p1', 'totals_p2', 'totals_p3',
        ]

        # Add player prop markets by sport
        self.player_props = {
            'NFL': [
                'player_pass_yds', 'player_pass_tds', 'player_pass_completions',
                'player_rush_attempts', 'player_receptions', 'player_reception_yds'
            ],
            'NBA': [
                'player_points', 'player_rebounds', 'player_assists',
                'player_threes', 'player_points_rebounds_assists'
            ],
            'NHL': [
                'player_points', 'player_shots_on_goal', 'player_goals',
                'player_assists'
            ]
        }

        self.regions = {
            'us': ['betmgm', 'betrivers', 'caesars', 'draftkings', 'fanduel'],
            'eu': ['pinnacle']
        }

        self.low_hold_threshold = 1.03
        self.ev_threshold = 2.0  # Minimum +EV percentage to include
        self.all_odds_data = []
        self.all_opportunities = []
        self.all_plus_ev = []
        self.all_player_props = {}  # Store player props
        self.state = state.lower()

        self.logger = logging.getLogger('odds_finder')
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)

        self.rate_limiter = RateLimiter()

        # For aiohttp requests
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def make_request(self, url: str, params: dict) -> dict:
        """Make API request with debug logging."""
        await self.rate_limiter.acquire()

        with open('api_debug.log', 'a') as f:
            f.write(f"\n\n=== Making API Request ===")
            f.write(f"\nTime: {datetime.now()}")
            f.write(f"\nURL: {url}")
            f.write(f"\nParams: {json.dumps(params, indent=2)}")

        try:
            async with self.session.get(url, params=params) as response:
                with open('api_debug.log', 'a') as f:
                    f.write(f"\nStatus code: {response.status}")
                    f.write(f"\nHeaders: {dict(response.headers)}")

                if response.status == 429:  # Rate limit exceeded
                    with open('api_debug.log', 'a') as f:
                        f.write("\nRate limit exceeded - waiting 1 second")
                    await asyncio.sleep(1)  # Wait 1 second
                    return await self.make_request(url, params)  # Retry

                response.raise_for_status()
                data = await response.json()

                with open('api_debug.log', 'a') as f:
                    f.write("\nResponse data:")
                    f.write(json.dumps(data, indent=2))
                    f.write("\n=== End API Request ===\n")

                return data

        except aiohttp.ClientError as e:
            with open('api_debug.log', 'a') as f:
                f.write(f"\nError making request: {str(e)}")
                if isinstance(e, aiohttp.ClientResponseError):
                    f.write(f"\nStatus: {e.status}")
                    f.write(f"\nMessage: {e.message}")
                f.write("\n=== End API Request (Error) ===\n")
            return None

        except Exception as e:
            with open('api_debug.log', 'a') as f:
                f.write(f"\nUnexpected error: {str(e)}")
                f.write(traceback.format_exc())
                f.write("\n=== End API Request (Error) ===\n")
            return None

    async def get_player_props(self, sport: str, event_id: str) -> List[dict]:
        """
        Asynchronously fetch player props for a specific event from all regions.
        """
        logger = logging.getLogger('props_fetcher')

        # Determine the sport name for prop market lookup
        sport_name = sport.upper().split('_')[1] if '_' in sport else sport.upper()

        # Early return if sport doesn't have player props configured
        if sport_name not in self.player_props:
            logger.debug(f"No player props configured for sport: {sport_name}")
            return []

        # Get the list of prop markets for this sport
        prop_markets = self.player_props.get(sport_name, [])
        if not prop_markets:
            logger.debug(f"Empty prop markets list for sport: {sport_name}")
            return []

        # Prepare base URL and tasks list for concurrent execution
        url = f"{self.base_url}/{sport}/events/{event_id}/odds"
        tasks = []

        # Create a task for each region to fetch props concurrently
        for region, bookmakers in self.regions.items():
            params = {
                'apiKey': self.api_key,
                'regions': region,
                'markets': ','.join(prop_markets),
                'oddsFormat': 'decimal',
                'includeLinks': 'true'
            }
            tasks.append(self.make_request(url, params))

        try:
            results = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Error fetching player props: {str(e)}", exc_info=True)
            return []

        # Process and combine results from all regions
        all_bookmakers = []

        for data in results:
            if not data:
                continue
            region_bookmakers = data.get('bookmakers', [])

            # Filter based on configured regions
            filtered_bookmakers = []
            for bm in region_bookmakers:
                for region, bookmaker_list in self.regions.items():
                    if any(book.lower() in bm['title'].lower() for book in bookmaker_list):
                        filtered_bookmakers.append(bm)
                        break

            all_bookmakers.extend(filtered_bookmakers)

        return all_bookmakers

    def get_display_market_type(self, market_type: str, period_info: str = None) -> str:
        """
        Convert internal market types to display-friendly formats.
        """
        base_market_map = {
            'h2h': 'Moneyline',
            'spreads': 'Spread',
            'alternate_spreads': 'Alternate Spread',
            'totals': 'Total',
            'alternate_totals': 'Alternate Total',
            'team_totals': 'Team Total'
        }

        period_map = {
            'q1': 'Q1',
            'q2': 'Q2',
            'q3': 'Q3',
            'q4': 'Q4',
            'h1': 'H1',
            'h2': 'H2',
            'p1': 'P1',
            'p2': 'P2',
            'p3': 'P3'
        }

        base_market = market_type
        embedded_period = None

        for period in period_map.keys():
            if f"_{period}" in market_type:
                base_market = market_type.split(f"_{period}")[0]
                embedded_period = period
                break

        display_market = base_market_map.get(base_market, base_market.replace('_', ' ').title())

        period = period_info or embedded_period
        if period:
            display_period = period_map.get(period, period.upper())
            display_market = f"{display_market} {display_period}"

        return display_market

    def process_player_props(self, bookmakers, sport):
        """
        Process and group player props by (player, prop_type).
        """
        markets = {}
        bookmaker_mapping = {
            'betmgm': 'BetMGM',
            'betrivers': 'BetRivers',
            'caesars': 'Caesars',
            'draftkings': 'DraftKings',
            'fanduel': 'FanDuel',
            'pinnacle': 'Pinnacle'
        }

        valid_books = []
        for region, books in self.regions.items():
            for book in books:
                valid_books.append(bookmaker_mapping.get(book.lower(), book))
        valid_books.append('Pinnacle')  # Ensure Pinnacle included

        # Filter bookmakers using mapped names
        bookmakers = [bm for bm in bookmakers if bm['title'] in valid_books]

        sport_name = sport.upper().split('_')[1] if '_' in sport else sport.upper()
        prop_markets = self.player_props.get(sport_name, [])

        for bookmaker in bookmakers:
            for market in bookmaker['markets']:
                if market['key'] in prop_markets:
                    for outcome in market['outcomes']:
                        player_name = outcome.get('description', 'Unknown')
                        prop_type = market['key'].replace('player_', '')
                        market_key = f"{market['key']}_{player_name}"

                        if market_key not in markets:
                            markets[market_key] = []

                        markets[market_key].append({
                            'bookmaker': bookmaker['title'],
                            'player': player_name,
                            'prop_type': prop_type,
                            'team': outcome['name'],
                            'price': outcome.get('price', 0),
                            'point': outcome.get('point'),
                            'link': bookmaker.get('link', '')
                        })

        return markets

    def get_prop_description(self, prop_type, sport_name):
        """
        Convert prop_type (e.g., 'pass_yds') to a readable format (e.g., 'Passing Yards').
        """
        prop_map = {
            'pass_yds': 'Passing Yards',
            'pass_tds': 'Passing TDs',
            'pass_completions': 'Completions',
            'rush_attempts': 'Rush Attempts',
            'receptions': 'Receptions',
            'reception_yds': 'Receiving Yards',
            'points': 'Points',
            'rebounds': 'Rebounds',
            'assists': 'Assists',
            'threes': '3-Pointers Made',
            'blocks': 'Blocks',
            'steals': 'Steals',
            'blocks_steals': 'Blocks + Steals',
            'turnovers': 'Turnovers',
            'points_rebounds_assists': 'PRA',
            'points_rebounds': 'Points + Rebounds',
            'points_assists': 'Points + Assists',
            'rebounds_assists': 'Rebounds + Assists',
            'power_play_points': 'Power Play Points',
            'blocked_shots': 'Blocked Shots',
            'shots_on_goal': 'Shots on Goal',
            'goals': 'Goals',
            'total_saves': 'Saves'
        }

        return prop_map.get(prop_type, prop_type.replace('_', ' ').title())

    def decimal_to_american(self, decimal_odds):
        """
        Convert decimal odds to American odds format with edge case handling.
        """
        try:
            if decimal_odds <= 1:
                return -10000
            if decimal_odds >= 2:
                return round((decimal_odds - 1) * 100)
            else:
                return round(-100 / (decimal_odds - 1))
        except (ZeroDivisionError, ValueError):
            return -10000

    async def get_events(self, sport: str) -> List[dict]:
        """
        Fetch upcoming events for a sport.
        """
        url = f"{self.base_url}/{sport}/events"
        params = {
            'apiKey': self.api_key,
            'regions': 'us'
        }

        data = await self.make_request(url, params)
        if not data:
            return []

        current_time = datetime.now(timezone.utc)
        return [
            event for event in data
            if datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')) > current_time
        ]

    async def get_featured_odds(self, sport: str) -> List[dict]:
        """
        Fetch odds for featured (base) markets with debug logging.
        """
        logger = logging.getLogger('api_fetcher')
        logger.setLevel(logging.DEBUG)

        fh = logging.FileHandler('api_debug.log')
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        all_odds = []
        seen_events = set()

        logger.debug(f"\n=== Fetching featured odds for {sport} ===")
        logger.debug(f"Featured markets requested: {self.featured_markets}")

        tasks = []
        for region, bookmakers in self.regions.items():
            url = f"{self.base_url}/{sport}/odds"
            params = {
                'apiKey': self.api_key,
                'regions': region,
                'markets': ','.join(self.featured_markets),
                'oddsFormat': 'decimal',
                'commenceTimeFrom': current_time,
                'includeLinks': 'true'
            }

            logger.debug(f"\nMaking request for region {region}:")
            logger.debug(f"URL: {url}")
            logger.debug(f"Parameters: {params}")

            tasks.append(self.make_request(url, params))

        results = await asyncio.gather(*tasks)

        for region, odds_data in zip(self.regions.keys(), results):
            if not odds_data:
                logger.warning(f"No data received for region {region}")
                continue

            logger.debug(f"\nReceived data for region {region}")
            logger.debug(f"Number of events: {len(odds_data)}")

            for event in odds_data:
                event_id = event['id']
                if event_id not in seen_events:
                    available_markets = set()
                    for bm in event['bookmakers']:
                        for market in bm.get('markets', []):
                            available_markets.add(market['key'])

                    logger.debug(
                        f"\nEvent: {event.get('home_team')} vs {event.get('away_team')}")
                    logger.debug(f"Available markets: {list(available_markets)}")

                    # Filter out bookmakers that aren't in the region's list
                    filtered_bookmakers = [
                        bm for bm in event['bookmakers']
                        if any(book.lower() in bm['title'].lower() for book in self.regions[region])
                    ]
                    event['bookmakers'] = filtered_bookmakers
                    all_odds.append(event)
                    seen_events.add(event_id)

        logger.debug(f"\nTotal events collected: {len(all_odds)}")
        logger.removeHandler(fh)
        return all_odds

    async def get_event_odds(self, sport: str, event_id: str) -> List[dict]:
        """
        Fetch odds for additional markets (derivatives like alternate spreads/totals, etc.).
        """
        with open('api_debug.log', 'a') as f:
            f.write(f"\n\n=== Fetching odds for event {event_id} ===")
            f.write(f"\nSport: {sport}")
            f.write(f"\nTime: {datetime.now()}")

            url = f"{self.base_url}/{sport}/events/{event_id}/odds"
            all_odds = []

            tasks = []
            for region, bookmakers in self.regions.items():
                params = {
                    'apiKey': self.api_key,
                    'regions': region,
                    'markets': ','.join(self.additional_markets),
                    'oddsFormat': 'decimal',
                    'includeLinks': 'true'
                }
                f.write(f"\n\nMaking request for region {region}:")
                f.write(f"\nURL: {url}")
                f.write(f"\nParams: {json.dumps(params, indent=2)}")
                tasks.append(self.make_request(url, params))

            results = await asyncio.gather(*tasks)

            f.write("\n\nAPI Responses:")
            for region, data in zip(self.regions.keys(), results):
                f.write(f"\n\nRegion: {region}")
                f.write(f"\nResponse: {json.dumps(data, indent=2)}")

                if not data:
                    f.write("\nNo data received")
                    continue

                region_bookmakers = data.get('bookmakers', [])
                f.write(f"\nFound {len(region_bookmakers)} bookmakers")

                filtered_bookmakers = []
                for bm in region_bookmakers:
                    for r, bookmaker_list in self.regions.items():
                        if any(book.lower() in bm['title'].lower() for book in bookmaker_list):
                            filtered_bookmakers.append(bm)
                            f.write(f"\nAdded bookmaker: {bm['title']}")
                            break

                all_odds.extend(filtered_bookmakers)

            f.write(f"\n\nFinal bookmaker count: {len(all_odds)}")
            f.write("=== End event odds fetch ===\n")

            return all_odds

    def calculate_implied_probability(self, decimal_odds):
        """Convert decimal odds to implied probability."""
        return 1 / decimal_odds

    def calculate_kelly_percentage(self, prob_a, prob_b, odds_a, odds_b):
        """
        Calculate stake percentages to equalize potential profit in an arbitrage or low-hold scenario.
        """
        try:
            # For arbitrage or low hold, we want X * odds_a = Y * odds_b, with X + Y = 100
            stake_a = (odds_b * 100) / (odds_a + odds_b)
            stake_b = (odds_a * 100) / (odds_a + odds_b)
            if stake_a <= 0 or stake_b <= 0:
                return (50.0, 50.0)
            return (stake_a, stake_b)
        except (ZeroDivisionError, ValueError):
            return (50.0, 50.0)

    def generate_bookmaker_filter(self):
        """Generate HTML snippet for bookmaker filters (not critical for logic)."""
        bookmakers = sorted(self.regions['us'])

        html = """
        <div class="bookmaker-filter">
            <div class="filter-label">Filter Bookmakers:</div>
            <div class="bookmaker-checkboxes">
        """

        for book in bookmakers:
            html += f"""
                <label class="bookmaker-checkbox">
                    <input type="checkbox" class="book-filter" value="{book.lower()}" checked>
                    {book}
                </label>
            """

        html += """
            </div>
        </div>
        """
        return html

    async def process_game(self, game: dict, sport: str) -> tuple:
        """
        Process a single game for:
           1. Arbitrage/low hold opportunities
           2. +EV bets
           3. Gathering structured odds data
        """
        try:
            additional_odds_task = asyncio.create_task(self.get_event_odds(sport, game['id']))
            props_task = asyncio.create_task(self.get_player_props(game['sport_key'], game['id']))

            additional_odds, props = await asyncio.gather(additional_odds_task, props_task)

            if props:
                self.all_player_props[game['id']] = {
                    'props': props,
                    'game': game
                }

            # Create tasks for the "core" processes
            opportunities_task = asyncio.create_task(self.find_opportunities(game, additional_odds))
            odds_data_task = asyncio.create_task(self.collect_all_odds(game))
            plus_ev_task = asyncio.create_task(self.find_plus_ev_bets(game, additional_odds))

            opportunities, odds_data, plus_ev = await asyncio.gather(
                opportunities_task,
                odds_data_task,
                plus_ev_task
            )

            return opportunities, plus_ev, odds_data

        except Exception as e:
            self.logger.error(f"Error processing game {game['id']}: {str(e)}", exc_info=True)
            return [], [], []

    def process_markets(self, bookmakers, market_type, require_pinnacle=False):
        """
        Process the given market_type, collecting all relevant derivatives (Q1, alt lines, etc.)
        exactly once. No duplication.
        """
        markets = {}
        logger = logging.getLogger('market_processor')

        logger.debug(f"Starting process_markets for market_type: {market_type}")
        logger.debug(f"Number of bookmakers: {len(bookmakers)}")

        # This dictionary ensures that passing in 'spreads' or 'totals' will pick up
        # derivative lines (spreads_q1, alternate_spreads, etc.) so we don't have to
        # iterate over them separately.
        base_markets = {
            'h2h': [
                'h2h'
            ],
            'spreads': [
                'spreads',
                'spreads_q1', 'spreads_q2', 'spreads_q3', 'spreads_q4',
                'spreads_h1', 'spreads_h2',
                'spreads_p1', 'spreads_p2', 'spreads_p3',
                'alternate_spreads'
            ],
            'totals': [
                'totals',
                'totals_q1', 'totals_q2', 'totals_q3', 'totals_q4',
                'totals_h1', 'totals_h2',
                'totals_p1', 'totals_p2', 'totals_p3',
                'alternate_totals',
                'team_totals'
            ]
        }

        period_info = None
        for period in ['q1', 'q2', 'q3', 'q4', 'h1', 'h2', 'p1', 'p2', 'p3']:
            if f"_{period}" in market_type:
                period_info = period
                break

        # Determine if the market_type is a "base" market or already a derivative
        # If it's base, we gather everything from base_markets dict.
        # If it's derivative, we just process that single key.
        to_check = []
        if market_type in base_markets:
            to_check = base_markets[market_type]
        else:
            # If user passes in something like 'spreads_q1' directly, we only gather that
            to_check = [market_type]

        logger.debug(f"Market type(s) to check: {to_check}")

        for bookmaker in bookmakers:
            logger.debug(f"Processing bookmaker {bookmaker['title']}")
            logger.debug(f"Available markets: {[m['key'] for m in bookmaker.get('markets', [])]}")

            for market in bookmaker.get('markets', []):
                if market['key'] in to_check:
                    # For partial matching by period, skip if mismatch
                    if period_info and period_info not in market['key']:
                        continue

                    market_key = market['key']
                    if market_key not in markets:
                        markets[market_key] = []

                    for outcome in market['outcomes']:
                        point = outcome.get('point')
                        link = outcome.get('link', '')

                        markets[market_key].append({
                            'bookmaker': bookmaker['title'],
                            'team': outcome['name'],
                            'price': outcome.get('price', 0),
                            'point': point,
                            'link': link,
                            'period': period_info if period_info else None
                        })
                        logger.debug(f"Added outcome for {outcome['name']} to {market_key}")

        logger.debug(f"Final markets found: {list(markets.keys())}")
        return markets

    async def find_opportunities(self, game, additional_odds=None):
        """
        Find arbitrage or low-hold opportunities for base markets
        (and their derivative lines) exactly once (no duplicates).
        """
        opportunities = []
        logger = logging.getLogger('opportunities_finder')

        try:
            all_bookmakers = game['bookmakers']
            if additional_odds:
                all_bookmakers.extend(additional_odds)

            # ONLY use base markets. process_markets() will pull derivatives automatically.
            all_markets = self.featured_markets  # ['h2h', 'spreads', 'totals']

            for market_type in all_markets:
                markets = self.process_markets(all_bookmakers, market_type)
                if not markets:
                    continue

                for market_key, market_odds in markets.items():
                    odds_by_team = {}
                    for odds in market_odds:
                        key_components = [odds['team']]
                        if odds.get('period'):
                            key_components.append(odds['period'])
                        key_components.append(str(odds.get('point', '')))

                        key = '_'.join(key_components)
                        if key not in odds_by_team or odds['price'] > odds_by_team[key]['price']:
                            odds_by_team[key] = odds

                    processed_pairs = set()
                    for key1, odds1 in odds_by_team.items():
                        for key2, odds2 in odds_by_team.items():
                            if key1 == key2:
                                continue
                            if odds1['bookmaker'].lower() == odds2['bookmaker'].lower():
                                continue

                            valid_pair = False
                            same_period = (odds1.get('period') == odds2.get('period'))
                            if odds1.get('period') and not same_period:
                                continue

                            if 'h2h' in market_type:
                                if odds1['team'] != odds2['team']:
                                    valid_pair = True

                            elif 'spreads' in market_type:
                                if odds1['team'] != odds2['team']:
                                    point1 = float(odds1.get('point', 0) or 0)
                                    point2 = float(odds2.get('point', 0) or 0)
                                    if point1 != 0 and point2 != 0 and abs(point1 + point2) <= 0.1:
                                        valid_pair = True

                            elif 'totals' in market_type:
                                same_point = (odds1.get('point') == odds2.get('point'))
                                if same_point:
                                    combo = odds1['team'].upper() + odds2['team'].upper()
                                    # Over/Under check
                                    if 'OVER' in combo and 'UNDER' in combo:
                                        valid_pair = True

                            elif 'team_totals' in market_type:
                                team1 = odds1['team'].split(' ')[0]
                                team2 = odds2['team'].split(' ')[0]
                                if team1 == team2:
                                    is_over1 = 'OVER' in odds1['team'].upper()
                                    is_over2 = 'OVER' in odds2['team'].upper()
                                    if is_over1 != is_over2 and odds1.get('point') == odds2.get('point'):
                                        valid_pair = True

                            if valid_pair:
                                pair_key = tuple(sorted([key1, key2]))
                                if pair_key not in processed_pairs:
                                    processed_pairs.add(pair_key)

                                    prob1 = self.calculate_implied_probability(odds1['price'])
                                    prob2 = self.calculate_implied_probability(odds2['price'])
                                    total_prob = prob1 + prob2

                                    if total_prob <= self.low_hold_threshold:
                                        stake1, stake2 = self.calculate_kelly_percentage(
                                            prob1, prob2, odds1['price'], odds2['price']
                                        )
                                        hold_percentage = (total_prob - 1) * 100
                                        opportunity_type = 'Arbitrage' if total_prob < 1 else 'Low Hold'
                                        profit_percentage = round(((1 / total_prob) - 1) * 100, 2) if total_prob < 1 else 0

                                        period_desc = odds1.get('period', '')
                                        market_display = self.get_display_market_type(market_type, period_desc)

                                        opportunities.append({
                                            'sport': game['sport_title'],
                                            'opportunity_type': opportunity_type,
                                            'market_type': market_display,
                                            'market_point': odds1.get('point'),
                                            'game': f"{game['home_team']} vs {game['away_team']}",
                                            'commence_time': game['commence_time'],
                                            'team1_name': odds1['team'],
                                            'team1_book': odds1['bookmaker'],
                                            'team1_odds': self.decimal_to_american(odds1['price']),
                                            'team1_point': odds1.get('point'),
                                            'team1_stake': round(stake1, 2),
                                            'team1_link': odds1.get('link', ''),
                                            'team2_name': odds2['team'],
                                            'team2_book': odds2['bookmaker'],
                                            'team2_odds': self.decimal_to_american(odds2['price']),
                                            'team2_point': odds2.get('point'),
                                            'team2_stake': round(stake2, 2),
                                            'team2_link': odds2.get('link', ''),
                                            'hold_percentage': round(hold_percentage, 2),
                                            'profit_percentage': profit_percentage
                                        })

            # Process player props
            try:
                player_props_data = await self.get_player_props(game['sport_key'], game['id'])
                if player_props_data:
                    prop_markets = self.process_player_props(player_props_data, game['sport_key'])
                    for market_key, market_odds in prop_markets.items():
                        prop_type = market_odds[0]['prop_type']
                        player_name = market_odds[0]['player']
                        prop_readable = self.get_prop_description(prop_type, game['sport_key'])

                        odds_by_outcome = {}
                        for odds in market_odds:
                            # Combine "team" name and point to form a unique key
                            key = f"{odds['team']}_{odds.get('point', '')}_{odds['bookmaker']}"
                            if key not in odds_by_outcome or odds['price'] > odds_by_outcome[key]['price']:
                                odds_by_outcome[key] = odds

                        processed_pairs = set()
                        for key1, odds1 in odds_by_outcome.items():
                            for key2, odds2 in odds_by_outcome.items():
                                if key1 == key2:
                                    continue
                                if odds1['bookmaker'] == odds2['bookmaker']:
                                    continue
                                if odds1['point'] != odds2['point']:
                                    continue

                                if 'OVER' in odds1['team'].upper() and 'UNDER' in odds2['team'].upper():
                                    pair_key = tuple(sorted([key1, key2]))
                                    if pair_key not in processed_pairs:
                                        processed_pairs.add(pair_key)

                                        prob1 = self.calculate_implied_probability(odds1['price'])
                                        prob2 = self.calculate_implied_probability(odds2['price'])
                                        total_prob = prob1 + prob2

                                        if total_prob <= self.low_hold_threshold:
                                            stake1, stake2 = self.calculate_kelly_percentage(
                                                prob1, prob2, odds1['price'], odds2['price']
                                            )
                                            hold_percentage = (total_prob - 1) * 100
                                            opportunity_type = 'Arbitrage' if total_prob < 1 else 'Low Hold'
                                            profit_percentage = round(((1 / total_prob) - 1) * 100, 2) if total_prob < 1 else 0
                                            prop_description = f"{player_name} - {prop_readable}"

                                            opportunities.append({
                                                'sport': game['sport_title'],
                                                'opportunity_type': opportunity_type,
                                                'market_type': 'player_prop',
                                                'prop_description': prop_description,
                                                'market_point': odds1['point'],
                                                'game': f"{game['home_team']} vs {game['away_team']}",
                                                'commence_time': game['commence_time'],
                                                'team1_name': f"{odds1['team']} ({odds1['point']})",
                                                'team1_book': odds1['bookmaker'],
                                                'team1_odds': self.decimal_to_american(odds1['price']),
                                                'team1_point': odds1['point'],
                                                'team1_stake': round(stake1, 2),
                                                'team1_link': odds1['link'],
                                                'team2_name': f"{odds2['team']} ({odds2['point']})",
                                                'team2_book': odds2['bookmaker'],
                                                'team2_odds': self.decimal_to_american(odds2['price']),
                                                'team2_point': odds2['point'],
                                                'team2_stake': round(stake2, 2),
                                                'team2_link': odds2['link'],
                                                'hold_percentage': round(hold_percentage, 2),
                                                'profit_percentage': profit_percentage
                                            })
            except Exception as e:
                logger.error(f"Error processing player props: {str(e)}", exc_info=True)

        except Exception as e:
            logger.error(f"Error finding opportunities: {str(e)}", exc_info=True)

        return opportunities

    def find_arbitrage(self, game, additional_odds=None):
        """
        Alternate older method that finds straight arbitrage (no +EV logic) for base markets.
        """
        arbitrage_opportunities = []

        all_bookmakers = game['bookmakers']
        if additional_odds:
            all_bookmakers.extend(additional_odds)

        # Only iterate over featured (base) markets. Derivatives are collected within process_markets().
        all_markets = self.featured_markets  # ['h2h', 'spreads', 'totals']

        for market_type in all_markets:
            markets = self.process_markets(all_bookmakers, market_type)

            for market_key, market_odds in markets.items():
                odds_by_team = {}
                for odds in market_odds:
                    key = f"{odds['team']}_{odds.get('point', '')}"
                    if key not in odds_by_team or odds['price'] > odds_by_team[key]['price']:
                        odds_by_team[key] = odds

                processed_pairs = set()
                for key1, odds1 in odds_by_team.items():
                    for key2, odds2 in odds_by_team.items():
                        if key1 == key2:
                            continue
                        if odds1['team'] == odds2['team']:
                            continue

                        # Validate spreads
                        if 'spreads' in market_type:
                            point1 = float(odds1.get('point', 0) or 0)
                            point2 = float(odds2.get('point', 0) or 0)
                            if point1 == 0 or point2 == 0 or abs(point1 + point2) > 0.1:
                                continue

                        # Validate totals
                        if 'totals' in market_type:
                            if odds1.get('point') != odds2.get('point'):
                                continue

                        pair_key = tuple(sorted([key1, key2]))
                        if pair_key not in processed_pairs:
                            processed_pairs.add(pair_key)

                            prob1 = self.calculate_implied_probability(odds1['price'])
                            prob2 = self.calculate_implied_probability(odds2['price'])

                            if prob1 + prob2 < 1:
                                stake1, stake2 = self.calculate_kelly_percentage(
                                    prob1, prob2, odds1['price'], odds2['price']
                                )
                                period_desc = odds1.get('period', '')
                                market_display = self.get_display_market_type(market_type, period_desc)

                                arbitrage_opportunities.append({
                                    'sport': game['sport_title'],
                                    'market_type': market_display,
                                    'market_point': odds1.get('point'),
                                    'game': f"{game['home_team']} vs {game['away_team']}",
                                    'commence_time': game['commence_time'],
                                    'team1_name': odds1['team'],
                                    'team1_book': odds1['bookmaker'],
                                    'team1_odds': self.decimal_to_american(odds1['price']),
                                    'team1_point': odds1.get('point'),
                                    'team1_stake': round(stake1, 2),
                                    'team1_link': odds1.get('link', ''),
                                    'team2_name': odds2['team'],
                                    'team2_book': odds2['bookmaker'],
                                    'team2_odds': self.decimal_to_american(odds2['price']),
                                    'team2_point': odds2.get('point'),
                                    'team2_stake': round(stake2, 2),
                                    'team2_link': odds2.get('link', ''),
                                    'profit_percentage': round(((1 / (prob1 + prob2)) - 1) * 100, 2)
                                })

        return arbitrage_opportunities

    async def find_plus_ev_bets(self, game, additional_odds=None):
        """
        Find +EV bets using Pinnacle as a "true" line, then devigging to get fair odds,
        and comparing them to US books. Avoid duplication by only calling base markets.
        """
        plus_ev_opportunities = []
        logger = logging.getLogger('plus_ev_finder')

        try:
            if additional_odds:
                all_bookmakers = game['bookmakers'] + additional_odds
            else:
                all_bookmakers = game['bookmakers']

            # We only process the 3 main base markets. Derivatives are included internally.
            main_markets = ['h2h', 'spreads', 'totals']

            async def process_market(market_type, is_period=False, period=None):
                if is_period and not period:
                    return []

                actual_market = f"{market_type}_{period}" if is_period else market_type
                markets = self.process_markets(all_bookmakers, actual_market, require_pinnacle=True)
                opportunities = []

                for market_key, market_odds in markets.items():
                    if len(market_odds) < 2:
                        continue

                    # Group by point/line
                    grouped_odds = {}
                    for odds in market_odds:
                        key = str(odds.get('point', '0'))
                        if key not in grouped_odds:
                            grouped_odds[key] = []
                        grouped_odds[key].append(odds)

                    for point, odds_list in grouped_odds.items():
                        pinnacle_odds = [o for o in odds_list if o['bookmaker'].lower() == 'pinnacle']
                        if len(pinnacle_odds) != 2:
                            continue

                        # Sort Pinnacle odds for TOTALS
                        if 'totals' in actual_market.lower():
                            pinnacle_odds.sort(key=lambda x: 'OVER' in x['team'].upper(), reverse=True)
                        # Sort Pinnacle odds for SPREADS
                        elif 'spreads' in actual_market.lower():
                            point0 = float(pinnacle_odds[0].get('point', 0) or 0)
                            point1 = float(pinnacle_odds[1].get('point', 0) or 0)
                            if abs(point0 + point1) > 0.1:
                                continue
                        else:
                            pinnacle_odds.sort(key=lambda x: x['team'])

                        pinnacle_american = [self.decimal_to_american(o['price']) for o in pinnacle_odds]
                        fair_odds = power_devig(pinnacle_american)

                        # Collect all odds for potential display
                        is_totals = ('totals' in actual_market.lower())
                        market_data = {}
                        if is_totals:
                            market_data = {'over': {'odds': {}}, 'under': {'odds': {}}}
                        else:
                            # For spreads/h2h we group by team
                            pass

                        for odds in odds_list:
                            book = odds['bookmaker'].lower()
                            american_odds = self.decimal_to_american(odds['price'])

                            if is_totals:
                                side = 'over' if 'OVER' in odds['team'].upper() else 'under'
                                if side not in market_data:
                                    market_data[side] = {'odds': {}}
                                market_data[side]['odds'][book] = {
                                    'american': american_odds,
                                    'decimal': odds['price'],
                                    'link': odds.get('link', '')
                                }
                            else:
                                team = odds['team'].split(' ')[0]
                                if team not in market_data:
                                    market_data[team] = {'odds': {}}
                                market_data[team]['odds'][book] = {
                                    'american': american_odds,
                                    'decimal': odds['price'],
                                    'link': odds.get('link', '')
                                }

                        for odds in odds_list:
                            if odds['bookmaker'].lower() not in [b.lower() for b in self.regions['us']]:
                                continue
                            american_odds = self.decimal_to_american(odds['price'])

                            # Find which side in fair_odds array
                            if is_totals:
                                team_index = 0 if 'OVER' in odds['team'].upper() else 1
                            elif 'spreads' in actual_market.lower():
                                # Compare points
                                pt = float(odds.get('point', 0) or 0)
                                match_idx = -1
                                for i, p_odds in enumerate(pinnacle_odds):
                                    p_pt = float(p_odds.get('point', 0) or 0)
                                    # same side if abs(pt + p_pt) < 0.1 for spreads?
                                    # or if they match sign. We'll just guess based on sign
                                    if abs(pt - p_pt) < 0.1 and odds['team'].split(' ')[0].lower() in p_odds['team'].lower():
                                        match_idx = i
                                        break
                                if match_idx < 0:
                                    continue
                                team_index = match_idx
                            else:
                                # h2h
                                team_index = 0
                                if len(pinnacle_odds) == 2:
                                    if odds['team'].lower() in pinnacle_odds[1]['team'].lower():
                                        team_index = 1

                            fair_odd = fair_odds[team_index]

                            is_better = (
                                (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd) or
                                (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd) or
                                (american_odds > 0 and fair_odd < 0)
                            )
                            if is_better:
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
                                    display_market = actual_market.replace('_', ' ').upper()
                                    if is_period:
                                        display_market = f"{market_type.upper()} ({period.upper()})"

                                    team_name = odds['team']
                                    if point != '0' and ('spread' in actual_market.lower() or 'total' in actual_market.lower()):
                                        try:
                                            pt_val = float(point)
                                            sign = f"+{pt_val}" if pt_val > 0 else f"{pt_val}"
                                            team_name = f"{team_name} ({sign})"
                                        except:
                                            pass

                                    opportunities.append({
                                        'sport': game['sport_title'],
                                        'market_type': display_market,
                                        'market_point': point if point != '0' else None,
                                        'game': f"{game['home_team']} vs {game['away_team']}",
                                        'commence_time': game['commence_time'],
                                        'team': team_name,
                                        'bookmaker': odds['bookmaker'],
                                        'odds': american_odds,
                                        'fair_odds': fair_odd,
                                        'ev_percentage': round(ev_percentage, 2),
                                        'link': odds.get('link', ''),
                                        'market_data': market_data
                                    })

                return opportunities

            # Build tasks for only the base markets (no separate loops for alt/period markets)
            tasks = []
            for market in main_markets:
                tasks.append(process_market(market))

            # Run tasks concurrently
            results = await asyncio.gather(*tasks)
            for result in results:
                plus_ev_opportunities.extend(result)

            # Process player props (if any)
            if game['id'] in self.all_player_props:
                player_props = self.all_player_props[game['id']]['props']
                prop_markets = self.process_player_props(player_props, game['sport_key'])

                for market_key, market_odds in prop_markets.items():
                    if not market_odds:
                        continue

                    prop_type = market_odds[0]['prop_type']
                    player_name = market_odds[0]['player']
                    prop_readable = self.get_prop_description(prop_type, game['sport_key'])

                    # Group by point
                    market_data = {}
                    pinnacle_odds = {'over': {}, 'under': {}}

                    for odds in market_odds:
                        point = str(odds['point'])
                        side = 'over' if 'OVER' in odds['team'].upper() else 'under'
                        if point not in market_data:
                            market_data[point] = {'over': {'odds': {}}, 'under': {'odds': {}}}

                        book = odds['bookmaker'].lower()
                        market_data[point][side]['odds'][book] = {
                            'american': self.decimal_to_american(odds['price']),
                            'decimal': odds['price'],
                            'link': odds.get('link', '')
                        }

                        if book == 'pinnacle':
                            pinnacle_odds[side][point] = odds

                    common_points = set(pinnacle_odds['over'].keys()) & set(pinnacle_odds['under'].keys())
                    for point in common_points:
                        pin_over = pinnacle_odds['over'][point]
                        pin_under = pinnacle_odds['under'][point]
                        pin_over_american = self.decimal_to_american(pin_over['price'])
                        pin_under_american = self.decimal_to_american(pin_under['price'])
                        fair_two_way = power_devig([pin_over_american, pin_under_american])

                        for odds in market_odds:
                            if str(odds['point']) != point:
                                continue
                            if odds['bookmaker'].lower() == 'pinnacle':
                                continue
                            if odds['bookmaker'].lower() not in [b.lower() for b in self.regions['us']]:
                                continue

                            american_odds = self.decimal_to_american(odds['price'])
                            is_over = ('OVER' in odds['team'].upper())
                            fair_odd = fair_two_way[0 if is_over else 1]

                            is_better = (
                                (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd) or
                                (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd) or
                                (american_odds > 0 and fair_odd < 0)
                            )
                            if is_better:
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
                                        'sport': game['sport_title'],
                                        'market_type': f"Player Prop - {prop_readable}",
                                        'market_point': point,
                                        'game': f"{game['home_team']} vs {game['away_team']}",
                                        'commence_time': game['commence_time'],
                                        'team': f"{player_name} {odds['team']}",
                                        'bookmaker': odds['bookmaker'],
                                        'odds': american_odds,
                                        'fair_odds': fair_odd,
                                        'ev_percentage': round(ev_percentage, 2),
                                        'link': odds.get('link', ''),
                                        'market_data': market_data[point]
                                    })

        except Exception as e:
            logger.error(f"Error in find_plus_ev_bets: {str(e)}")

        return plus_ev_opportunities

    async def generate_arbitrage_table(self) -> pd.DataFrame:
        """
        Main method to generate arbitrage (and low-hold) opportunities across all sports.
        """
        print("Analyzing...")
        self.all_opportunities = []
        self.all_odds_data = []
        self.all_plus_ev = []
        self.all_player_props = {}

        async with self:
            sport_tasks = [self.get_featured_odds(sport) for sport in self.sports]
            featured_odds_by_sport = await asyncio.gather(*sport_tasks)

            game_tasks = []
            for sport, featured_odds in zip(self.sports, featured_odds_by_sport):
                for game in featured_odds:
                    game_tasks.append(self.process_game(game, sport))

            results = await asyncio.gather(*game_tasks)

            for opportunities, plus_ev, odds_data in results:
                self.all_opportunities.extend(opportunities)
                self.all_plus_ev.extend(plus_ev)
                self.all_odds_data.extend(odds_data)

        if self.all_opportunities:
            df = pd.DataFrame(self.all_opportunities)
            df['timestamp'] = datetime.now(timezone.utc)

            columns = [
                'opportunity_type', 'hold_percentage',
                'sport', 'market_type', 'prop_description',
                'market_point', 'game', 'commence_time',
                'team1_name', 'team1_book', 'team1_odds', 'team1_point', 'team1_stake', 'team1_link',
                'team2_name', 'team2_book', 'team2_odds', 'team2_point', 'team2_stake', 'team2_link',
                'profit_percentage', 'timestamp'
            ]
            return df[columns]
        else:
            return pd.DataFrame(columns=[
                'opportunity_type', 'hold_percentage',
                'sport', 'market_type', 'prop_description',
                'market_point', 'game', 'commence_time',
                'team1_name', 'team1_book', 'team1_odds', 'team1_point', 'team1_stake', 'team1_link',
                'team2_name', 'team2_book', 'team2_odds', 'team2_point', 'team2_stake', 'team2_link',
                'profit_percentage', 'timestamp'
            ])

    async def collect_all_odds(self, game):
        """
        Asynchronously collect and organize all base-market odds for display
        (without duplication).
        """
        odds_data = []

        async def process_market(market_type):
            markets = self.process_markets(game['bookmakers'], market_type)

            for market_key, market_odds in markets.items():
                market_data = {
                    'sport': game['sport_title'],
                    'game': f"{game['home_team']} vs {game['away_team']}",
                    'commence_time': game['commence_time'],
                    'market_type': market_type,
                    'market_point': None,
                    'outcomes': []
                }

                odds_by_bookmaker = {}
                for odds in market_odds:
                    book = odds['bookmaker']
                    if book not in odds_by_bookmaker:
                        odds_by_bookmaker[book] = []
                    odds_by_bookmaker[book].append({
                        'team': odds['team'],
                        'price': odds['price'],
                        'point': odds.get('point'),
                        'american_odds': self.decimal_to_american(odds['price']),
                        'link': odds.get('link', '')
                    })

                market_data['books'] = odds_by_bookmaker
                return market_data

        tasks = [process_market(m) for m in self.featured_markets]
        results = await asyncio.gather(*tasks)
        odds_data.extend([r for r in results if r is not None])

        return odds_data

    def remove_vig(self, odds1, odds2):
        """
        Simple two-way remove vig. This is not the power method, but a straightforward approach.
        """
        try:
            if odds1 < 0:
                dec1 = 1 - (100 / odds1)
            else:
                dec1 = (odds1 / 100) + 1

            if odds2 < 0:
                dec2 = 1 - (100 / odds2)
            else:
                dec2 = (odds2 / 100) + 1

            prob1 = 1 / dec1
            prob2 = 1 / dec2

            total_prob = prob1 + prob2
            fair_prob1 = prob1 / total_prob
            fair_prob2 = prob2 / total_prob

            fair_dec1 = 1 / fair_prob1
            fair_dec2 = 1 / fair_prob2

            fair_american1 = self.decimal_to_american(fair_dec1)
            fair_american2 = self.decimal_to_american(fair_dec2)

            return fair_american1, fair_american2
        except:
            return (-10000, -10000)

    def get_fair_odds(self, game_data):
        """
        Example method to get fair odds from Pinnacle for an h2h line.
        """
        if 'h2h' not in game_data['markets']:
            return {}

        pinnacle_market = game_data['markets']['h2h']
        if 'pinnacle' not in pinnacle_market['books']:
            return {}

        pinnacle_odds = sorted(
            pinnacle_market['books']['pinnacle'], key=lambda x: x['team']
        )

        if len(pinnacle_odds) != 2:
            return {}

        pinnacle_american = [odds['american_odds'] for odds in pinnacle_odds]
        fair_odds = power_devig(pinnacle_american)

        return {
            pinnacle_odds[0]['team']: fair_odds[0],
            pinnacle_odds[1]['team']: fair_odds[1]
        }

    async def get_dfs(self):
        """
        Return both the arbitrage table and the +EV table as DataFrames.
        """
        arb_df = await self.generate_arbitrage_table()
        ev_data = self.all_plus_ev
        ev_df = pd.DataFrame(ev_data)
        ev_df['primary_key'] = (
            ev_df['game'].astype(str)
            + ev_df['team'].astype(str)
            + ev_df['market_type'].astype(str)
            + ev_df['market_point'].astype(str)
            + ev_df['bookmaker'].astype(str)
        ).apply(lambda x: hashlib.sha256(x.encode('utf-8')).hexdigest())

        now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f%z')
        ev_df['timestamp'] = now_str
        arb_df['timestamp'] = now_str

        arb_df['primary_key'] = (
            arb_df['market_type'].astype(str)
            + arb_df['game'].astype(str)
            + arb_df['team1_name'].astype(str)
            + arb_df['team2_name'].astype(str)
            + arb_df['commence_time'].astype(str)
            + arb_df['team1_book'].astype(str)
            + arb_df['team2_book'].astype(str)
            + arb_df['team1_odds'].astype(str)
            + arb_df['team2_odds'].astype(str)
            + arb_df['market_type'].astype(str)
            + arb_df['prop_description'].astype(str)
        ).apply(lambda x: hashlib.sha256(x.encode('utf-8')).hexdigest())

        return arb_df, ev_df


async def main():
    pd.set_option('display.max_rows', None)
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)

    api_key = os.getenv('ODDS_API_KEY')
    finder = OddsArbitrageFinder(api_key)
    arbitrage_table = await finder.generate_arbitrage_table()

    arbitrage_table.to_csv('arbitrage.csv')

    ev_table = pd.DataFrame(finder.all_plus_ev)
    ev_json = ev_table.to_dict(orient='records')
    with open('ev.json', 'w') as f:
        json.dump(ev_json, f, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
