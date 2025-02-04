import asyncio
import aiohttp
import pandas as pd
from datetime import datetime, timezone
import time
import json
import logging
import os
from typing import List, Dict, Any, Optional, Set, Tuple
from functools import lru_cache
import hashlib
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import numpy as np
from betslip import BetslipURLGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('odds_finder')


class ConnectionPool:
    """Manages connection pooling for HTTP requests"""

    def __init__(self, size: int = 100):
        self.size = size
        self.connector = aiohttp.TCPConnector(
            limit=size,
            limit_per_host=10,
            enable_cleanup_closed=True,
            force_close=True
        )
        self.session = None

    async def get_session(self) -> aiohttp.ClientSession:
        if not self.session:
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self.session

    async def close(self):
        if self.session:
            await self.session.close()


@lru_cache(maxsize=512)
def power_devig(odds_tuple: Tuple[float, ...]) -> List[float]:
    """Optimized power devig using numpy"""
    odds_array = np.array(odds_tuple)

    def american_to_prob(odds):
        return np.where(
            odds > 0,
            100 / (odds + 100),
            np.abs(odds) / (np.abs(odds) + 100)
        )

    probs = american_to_prob(odds_array)

    # Binary search optimization
    left, right = 0.5, 2.0
    while right - left > 0.0001:
        power = (left + right) / 2
        total = np.sum(probs ** power)
        if total > 1:
            left = power
        else:
            right = power

    power = (left + right) / 2
    fair_probs = (probs ** power) / np.sum(probs ** power)

    def prob_to_american(prob):
        return np.where(
            prob >= 0.5,
            -100 * prob / (1 - prob),
            100 * (1 - prob) / prob
        )

    return list(np.round(prob_to_american(fair_probs)))


class OptimizedOddsArbitrageFinder:
    def __init__(self, api_key: str, state: str = 'md'):
        self.api_key = api_key
        self.state = state.lower()
        self.base_url = "https://api.the-odds-api.com/v4/sports"

        # Connection management
        self.connection_pool = ConnectionPool(size=100)
        self.session = None

        # Sports and markets configuration
        self.sports = {
            'americanfootball_ncaaf',
            'basketball_ncaab',
            'basketball_nba',
            'icehockey_nhl',
            'americanfootball_nfl'
        }

        self.featured_markets = {'h2h', 'spreads', 'totals'}
        self.additional_markets = {
            'alternate_spreads', 'alternate_totals',
            'h2h_q1', 'h2h_q2', 'h2h_q3', 'h2h_q4',
            'h2h_h1', 'h2h_h2',
            'h2h_p1', 'h2h_p2', 'h2h_p3',
            'spreads_q1', 'spreads_q2', 'spreads_q3', 'spreads_q4',
            'spreads_h1', 'spreads_h2',
            'spreads_p1', 'spreads_p2', 'spreads_p3',
            'totals_q1', 'totals_q2', 'totals_q3', 'totals_q4',
            'totals_h1', 'totals_h2',
            'totals_p1', 'totals_p2', 'totals_p3',
            'alternate_spreads_q1', 'alternate_spreads_h1',
            'alternate_totals_q1', 'alternate_totals_h1',
            'team_totals_h1', 'team_totals_h2',
            'team_totals_q1', 'team_totals_q2', 'team_totals_q3', 'team_totals_q4',
            'alternate_team_totals_h1', 'alternate_team_totals_h2'
        }

        # Player props configuration
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

        # Performance settings
        self.low_hold_threshold = 1.03
        self.ev_threshold = 2.0
        self.batch_size = 50
        self.max_workers = 10
        self._cache = {}
        self._cache_timeout = 300  # 5 minutes

        # Data storage
        self.all_odds_data = []
        self.all_opportunities = []
        self.all_plus_ev = []
        self.all_player_props = {}

        # Initialize components
        self.url_generator = BetslipURLGenerator()
        self.setup_logging()

    def setup_logging(self):
        self.logger = logging.getLogger('odds_finder')
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    async def initialize(self):
        """Initialize the session"""
        self.session = await self.connection_pool.get_session()

    async def close(self):
        """Close all connections"""
        await self.connection_pool.close()

    async def fetch_with_retries(self, url: str, params: Dict, max_retries: int = 3) -> Dict:
        """Improved fetch with retry logic and error handling"""
        for attempt in range(max_retries):
            try:
                async with self.session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    elif response.status == 429:  # Rate limit
                        await asyncio.sleep(2 ** attempt)
                        continue
                    response.raise_for_status()
            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                self.logger.error(f"Error fetching data: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        return {}

    async def fetch_odds_async(self, sport: str, region: str, markets: List[str]) -> Dict:
        """Fetch odds for a specific sport and region"""
        url = f"{self.base_url}/{sport}/odds"
        current_time = datetime.now(
            timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        params = {
            'apiKey': self.api_key,
            'regions': region,
            'markets': ','.join(markets),
            'oddsFormat': 'decimal',
            'commenceTimeFrom': current_time,
            'includeLinks': 'true'
        }

        try:
            return await self.fetch_with_retries(url, params)
        except Exception as e:
            self.logger.error(
                f"Error fetching odds for {sport} in {region}: {e}")
            return []

    async def get_events_async(self, sport: str) -> List[Dict]:
        """Async version of get_events"""
        cache_key = f"events_{sport}"
        if cache_key in self._cache:
            cached_data, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self._cache_timeout:
                return cached_data

        url = f"{self.base_url}/{sport}/events"
        params = {'apiKey': self.api_key, 'regions': 'us'}

        try:
            data = await self.fetch_with_retries(url, params)
            current_time = datetime.now(timezone.utc)

            events = [
                event for event in data
                if datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')) > current_time
            ]

            self._cache[cache_key] = (events, time.time())
            return events
        except Exception as e:
            self.logger.error(f"Error fetching events for {sport}: {e}")
            return []

    async def get_featured_odds_async(self, sport: str) -> List[Dict]:
        """Async version of get_featured_odds"""
        cache_key = f"featured_odds_{sport}"
        if cache_key in self._cache:
            cached_data, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self._cache_timeout:
                return cached_data

        tasks = []
        for region in self.regions:
            tasks.append(self.fetch_odds_async(
                sport,
                region,
                self.featured_markets
            ))

        results = await asyncio.gather(*tasks)

        # Process and combine results
        all_odds = []
        seen_events = set()

        for region_data in results:
            if not isinstance(region_data, list):
                continue

            for event in region_data:
                event_id = event['id']
                if event_id not in seen_events:
                    # Filter bookmakers
                    region = event.get('region', 'us')
                    allowed_books = self.regions.get(region, [])
                    event['bookmakers'] = [
                        b for b in event['bookmakers']
                        if b['title'].lower() in [book.lower() for book in allowed_books]
                    ]
                    all_odds.append(event)
                    seen_events.add(event_id)
                else:
                    # Add new bookmakers to existing event
                    existing_event = next(
                        e for e in all_odds if e['id'] == event_id)
                    existing_books = {b['title']
                                      for b in existing_event['bookmakers']}

                    new_bookmakers = [
                        b for b in event['bookmakers']
                        if b['title'] not in existing_books and
                        b['title'].lower() in [book.lower()
                                               for book in self.regions.get(event.get('region', 'us'), [])]
                    ]
                    existing_event['bookmakers'].extend(new_bookmakers)

        self._cache[cache_key] = (all_odds, time.time())
        return all_odds

    async def get_event_odds_async(self, sport: str, event_id: str) -> List[Dict]:
        """Async version of get_event_odds"""
        cache_key = f"event_odds_{sport}_{event_id}"
        if cache_key in self._cache:
            cached_data, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self._cache_timeout:
                return cached_data

        url = f"{self.base_url}/{sport}/events/{event_id}/odds"
        params = {
            'apiKey': self.api_key,
            'regions': 'us',
            'markets': ','.join(self.additional_markets),
            'oddsFormat': 'decimal',
            'includeLinks': 'true'
        }

        try:
            data = await self.fetch_with_retries(url, params)
            bookmakers = data.get('bookmakers', [])
            self._cache[cache_key] = (bookmakers, time.time())
            return bookmakers
        except Exception as e:
            self.logger.error(
                f"Error fetching additional odds for event {event_id}: {e}")
            return []

    async def get_player_props_async(self, sport: str, event_id: str) -> List[Dict]:
        """Async version of get_player_props"""
        sport_name = sport.upper().split(
            '_')[1] if '_' in sport else sport.upper()

        if sport_name not in self.player_props:
            return []

        prop_markets = self.player_props.get(sport_name, [])
        if not prop_markets:
            return []

        cache_key = f"player_props_{sport}_{event_id}"
        if cache_key in self._cache:
            cached_data, timestamp = self._cache[cache_key]
            if time.time() - timestamp < self._cache_timeout:
                return cached_data

        url = f"{self.base_url}/{sport}/events/{event_id}/odds"
        tasks = []

        # Fetch US and EU props concurrently
        for region in ['us', 'eu']:
            params = {
                'apiKey': self.api_key,
                'regions': region,
                'markets': ','.join(prop_markets),
                'oddsFormat': 'decimal',
                'includeLinks': 'true'
            }
            tasks.append(self.fetch_with_retries(url, params))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_props = []

        for result in results:
            if isinstance(result, dict):
                all_props.extend(result.get('bookmakers', []))

        self._cache[cache_key] = (all_props, time.time())
        return all_props

    @lru_cache(maxsize=1024)
    def calculate_implied_probability(self, decimal_odds: float) -> float:
        """Cached calculation of implied probability"""
        return 1 / decimal_odds

    @lru_cache(maxsize=1024)
    def decimal_to_american(self, decimal_odds: float) -> int:
        """Cached conversion to American odds"""
        try:
            if decimal_odds <= 1:
                return -10000
            if decimal_odds >= 2:
                return round((decimal_odds - 1) * 100)
            return round(-100 / (decimal_odds - 1))
        except (ZeroDivisionError, ValueError):
            return -10000

    def calculate_kelly_percentage(self, prob_a: float, prob_b: float,
                                   odds_a: float, odds_b: float) -> Tuple[float, float]:
        """Optimized Kelly calculation"""
        try:
            stake_a = (odds_b * 100) / (odds_a + odds_b)
            stake_b = (odds_a * 100) / (odds_a + odds_b)

            if stake_a <= 0 or stake_b <= 0:
                return (50.0, 50.0)

            return (stake_a, stake_b)
        except (ZeroDivisionError, ValueError):
            return (50.0, 50.0)

    def process_markets(self, bookmakers: List[Dict], market_type: str) -> Dict:
        """Optimized market processing with caching"""
        cache_key = f"markets_{market_type}_{hash(str(bookmakers))}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Use defaultdict to avoid repeated key checks
        markets = defaultdict(list)

        for bookmaker in bookmakers:
            for market in bookmaker['markets']:
                if market['key'] == market_type:
                    market_key = market['key']

                    # Efficient period/quarter handling
                    if any(word in market_type for word in ['spreads', 'totals', 'team_totals']):
                        period = '_'.join(market_type.split(
                            '_')[1:]) if '_' in market_type else ''
                        market_key = f"{market['key']}_{period}"

                    # Process outcomes
                    for outcome in market['outcomes']:
                        point = outcome.get('point')
                        book_name, params = self.url_generator.parse_existing_url(
                            outcome.get('link', '')
                        )

                        markets[market_key].append({
                            'bookmaker': bookmaker['title'],
                            'team': outcome['name'],
                            'price': outcome.get('price', 0),
                            'point': point,
                            'link': outcome.get('link', ''),
                            'market_id': params.get('market_id'),
                            'selection_id': params.get('selection_id'),
                            'event_id': params.get('event_id'),
                            'outcome_id': params.get('outcome_id')
                        })

        self._cache[cache_key] = dict(markets)
        return dict(markets)

    async def process_game(self, game: Dict) -> Tuple[List[Dict], List[Dict]]:
        """Process a single game concurrently"""
        try:
            # Fetch additional data concurrently
            additional_odds_task = self.get_event_odds_async(
                game['sport_key'],
                game['id']
            )
            props_task = self.get_player_props_async(
                game['sport_key'],
                game['id']
            )

            additional_odds, props = await asyncio.gather(
                additional_odds_task,
                props_task
            )

            if props:
                self.all_player_props[game['id']] = {
                    'props': props,
                    'game': game
                }

            # Process opportunities concurrently
            opportunities_task = self.find_opportunities(game, additional_odds)
            plus_ev_task = self.find_plus_ev_bets(game, additional_odds)

            opportunities, plus_ev = await asyncio.gather(
                opportunities_task,
                plus_ev_task
            )

            return opportunities, plus_ev

        except Exception as e:
            self.logger.error(
                f"Error processing game {game.get('id', 'unknown')}: {e}")
            return [], []

    def process_player_props(self, bookmakers: List[Dict], sport: str) -> Dict:
        """Optimized player props processing"""
        # Use sets for O(1) lookups
        valid_books = {book.lower()
                       for book in self.regions['us']} | {'pinnacle'}

        # Filter bookmakers once
        filtered_bookmakers = [
            bm for bm in bookmakers
            if bm['title'].lower() in valid_books
        ]

        sport_name = sport.upper().split(
            '_')[1] if '_' in sport else sport.upper()
        prop_markets = set(self.player_props.get(sport_name, []))

        # Use defaultdict to avoid key checks
        markets = defaultdict(list)

        for bookmaker in filtered_bookmakers:
            for market in bookmaker['markets']:
                if market['key'] in prop_markets:
                    for outcome in market['outcomes']:
                        player_name = outcome.get('description', 'Unknown')
                        prop_type = market['key'].replace('player_', '')
                        market_key = f"{market['key']}_{player_name}"

                        markets[market_key].append({
                            'bookmaker': bookmaker['title'],
                            'player': player_name,
                            'prop_type': prop_type,
                            'team': outcome['name'],
                            'price': outcome.get('price', 0),
                            'point': outcome.get('point'),
                            'link': bookmaker.get('link', '')
                        })

        return dict(markets)

    async def find_opportunities(self, game: Dict, additional_odds: Optional[List] = None) -> List[Dict]:
        """Optimized opportunity finding"""
        opportunities = []

        # Use sets for O(1) lookups
        us_books = {book.lower() for book in self.regions['us']}
        all_bookmakers = game['bookmakers']
        if additional_odds:
            all_bookmakers.extend(additional_odds)

        # Filter bookmakers once
        all_bookmakers = [
            bm for bm in all_bookmakers
            if bm['title'].lower() in us_books
        ]

        # Process markets concurrently
        async def process_market(market_type: str) -> List[Dict]:
            local_opportunities = []
            markets = self.process_markets(all_bookmakers, market_type)

            for market_key, market_odds in markets.items():
                # Optimize team odds lookup
                odds_by_team = {}
                for odds in market_odds:
                    if 'team_totals' in market_type:
                        team_name = odds['team'].split(' ')[0]
                        is_over = 'OVER' in odds['team'].upper()
                        key = f"{team_name}_{'over' if is_over else 'under'}_{
                            odds.get('point', '')}"
                    else:
                        key = f"{odds['team']}_{odds.get('point', '')}"

                    if key not in odds_by_team or odds['price'] > odds_by_team[key]['price']:
                        odds_by_team[key] = odds

                # Find opportunities using sets for efficient lookup
                processed_pairs = set()
                for key1, odds1 in odds_by_team.items():
                    for key2, odds2 in odds_by_team.items():
                        if self._should_process_pair(key1, key2, odds1, odds2, market_type, processed_pairs):
                            opportunity = await self._calculate_opportunity(
                                game, odds1, odds2, market_type
                            )
                            if opportunity:
                                local_opportunities.append(opportunity)

            return local_opportunities

        # Process all markets concurrently
        all_markets = self.featured_markets.union(self.additional_markets)
        market_tasks = [process_market(market_type)
                        for market_type in all_markets]
        market_results = await asyncio.gather(*market_tasks)

        # Combine results efficiently
        for result in market_results:
            opportunities.extend(result)

        # Process player props if available
        if props := await self.get_player_props_async(game['sport_key'], game['id']):
            prop_opportunities = await self._find_prop_opportunities(game, props)
            opportunities.extend(prop_opportunities)

        return opportunities

    async def find_plus_ev_bets(self, game: Dict, additional_odds: Optional[List] = None) -> List[Dict]:
        """Optimized EV calculation"""
        plus_ev_opportunities = []

        all_bookmakers = game['bookmakers']
        if additional_odds:
            all_bookmakers.extend(additional_odds)

        # Process moneyline markets efficiently
        async def process_moneyline_market() -> List[Dict]:
            markets = self.process_markets(all_bookmakers, 'h2h')
            local_opportunities = []

            for market_key, market_odds in markets.items():
                # Optimize data collection
                pinnacle_odds = []
                market_data = defaultdict(lambda: {'odds': {}})

                # Process all odds at once
                for odds in market_odds:
                    team = odds['team']
                    book = odds['bookmaker'].lower()

                    market_data[team]['odds'][book] = {
                        'american': self.decimal_to_american(odds['price']),
                        'decimal': odds['price'],
                        'link': odds.get('link', '')
                    }

                    if book == 'pinnacle':
                        pinnacle_odds.append(odds)

                if len(pinnacle_odds) != 2:
                    continue

                # Calculate fair odds once
                pinnacle_odds.sort(key=lambda x: x['team'])
                pinnacle_american = tuple(
                    self.decimal_to_american(odds['price'])
                    for odds in pinnacle_odds
                )
                fair_odds = power_devig(pinnacle_american)

                # Check opportunities efficiently
                for odds in market_odds:
                    if odds['bookmaker'].lower() in self.regions['us']:
                        opportunity = await self._calculate_ev_opportunity(
                            game, odds, pinnacle_odds, fair_odds, dict(
                                market_data)
                        )
                        if opportunity:
                            local_opportunities.append(opportunity)

            return local_opportunities

        # Process props efficiently
        async def process_props() -> List[Dict]:
            if game['id'] not in self.all_player_props:
                return []

            props_data = self.all_player_props[game['id']]
            prop_markets = self.process_player_props(
                props_data['props'],
                game['sport_key']
            )

            local_opportunities = []
            tasks = []

            for market_key, market_odds in prop_markets.items():
                if market_odds:
                    task = self._process_prop_ev(
                        game, market_odds, market_key
                    )
                    tasks.append(task)

            results = await asyncio.gather(*tasks)
            for opps in results:
                local_opportunities.extend(opps)

            return local_opportunities

        # Run both processes concurrently
        moneyline_opps, prop_opps = await asyncio.gather(
            process_moneyline_market(),
            process_props()
        )

        plus_ev_opportunities.extend(moneyline_opps)
        plus_ev_opportunities.extend(prop_opps)

        return plus_ev_opportunities

    async def run_update_db_async(self):
        """Optimized async database update"""
        try:
            await self.initialize()

            # Fetch all events concurrently
            events_tasks = [
                self.get_events_async(sport)
                for sport in self.sports
            ]
            all_events = await asyncio.gather(*events_tasks)

            # Process games in optimized batches
            all_opportunities = []
            all_plus_ev = []

            # Flatten events efficiently
            events = [
                event
                for sport_events in all_events
                for event in sport_events
            ]

            # Process in optimized batches with concurrent execution
            for i in range(0, len(events), self.batch_size):
                batch = events[i:i + self.batch_size]
                tasks = [self.process_game(game) for game in batch]
                results = await asyncio.gather(*tasks)

                # Extend results efficiently
                for opps, ev in results:
                    all_opportunities.extend(opps)
                    all_plus_ev.extend(ev)

            # Optimize DataFrame creation
            if all_opportunities:
                arb_df = pd.DataFrame(all_opportunities)
                current_time = datetime.now(timezone.utc).strftime(
                    '%Y-%m-%d %H:%M:%S.%f%z')
                arb_df['timestamp'] = current_time

                # Efficient primary key generation
                def generate_key(row):
                    key_components = [
                        str(row[col]) for col in [
                            'market_type', 'game', 'team1_name', 'team2_name',
                            'commence_time', 'team1_book', 'team2_book',
                            'team1_odds', 'team2_odds', 'market_type', 'prop_description'
                        ]
                    ]
                    return hashlib.sha256(''.join(key_components).encode()).hexdigest()

                arb_df['primary_key'] = arb_df.apply(generate_key, axis=1)
            else:
                arb_df = pd.DataFrame()

            if all_plus_ev:
                ev_df = pd.DataFrame(all_plus_ev)
                ev_df['timestamp'] = current_time

                # Efficient EV primary key generation
                def generate_ev_key(row):
                    key_components = [
                        str(row[col]) for col in [
                            'game', 'team', 'market_type',
                            'market_point', 'bookmaker'
                        ]
                    ]
                    return hashlib.sha256(''.join(key_components).encode()).hexdigest()

                ev_df['primary_key'] = ev_df.apply(generate_ev_key, axis=1)

                # Optimize memory usage
                ev_df = self._optimize_dataframe(ev_df)
            else:
                ev_df = pd.DataFrame()

            return arb_df, ev_df

        finally:
            await self.close()

    def run_update_db(self):
        """Synchronous wrapper for async update"""
        return asyncio.run(self.run_update_db_async())

    @staticmethod
    def _optimize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Optimize DataFrame memory usage"""
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = pd.Categorical(df[col])
            elif df[col].dtype == 'float64':
                df[col] = pd.to_numeric(df[col], downcast='float')
            elif df[col].dtype == 'int64':
                df[col] = pd.to_numeric(df[col], downcast='integer')
        return df

    def _should_process_pair(self, key1: str, key2: str, odds1: Dict,
                             odds2: Dict, market_type: str, processed_pairs: Set) -> bool:
        """Efficient pair processing check"""
        if key1 == key2 or odds1['bookmaker'] == odds2['bookmaker']:
            return False

        if (key1, key2) in processed_pairs:
            return False

        if 'team_totals' in market_type:
            team1, type1, _ = key1.split('_')
            team2, type2, _ = key2.split('_')

            return (
                team1 == team2 and
                ((type1 == 'over' and type2 == 'under') or
                 (type1 == 'under' and type2 == 'over')) and
                odds1.get('point') == odds2.get('point')
            )

        elif 'spreads' in market_type:
            if odds1['team'] == odds2['team']:
                return False

            point1 = float(odds1.get('point', 0) or 0)
            point2 = float(odds2.get('point', 0) or 0)

            return point1 != 0 and point2 != 0 and abs(point1 + point2) <= 0.1

        elif 'totals' in market_type and 'team_totals' not in market_type:
            return (
                odds1.get('point') == odds2.get('point') and
                (('OVER' in odds1['team'].upper() and 'UNDER' in odds2['team'].upper()) or
                 ('UNDER' in odds1['team'].upper() and 'OVER' in odds2['team'].upper()))
            )

        elif 'h2h' in market_type:
            return odds1['team'] != odds2['team']

        processed_pairs.add((key1, key2))
        processed_pairs.add((key2, key1))
        return True

    @staticmethod
    def get_prop_description(prop_type: str, sport_name: str) -> str:
        """Efficient prop type mapping"""
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


# def main():
#     api_key = os.getenv('ODDS_API_KEY')
#     arbitrage_finder = OptimizedOddsArbitrageFinder(api_key)
#     ev_df, arb_df = arbitrage_finder.run_update_db_async()


# if __name__ == "__main__":
#     main()
