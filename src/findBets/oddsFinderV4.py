import aiohttp
import asyncio
import pandas as pd
from datetime import datetime, timezone
import time
import json
import logging
import os
from typing import List, Dict
from collections import deque
from datetime import datetime, timedelta
import requests

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
    Devig odds using the power method to find fair probabilities
    """
    # Convert American odds to probabilities
    def american_to_prob(odds):
        if odds > 0:
            return 100 / (odds + 100)
        else:
            return abs(odds) / (abs(odds) + 100)
            
    probs = [american_to_prob(odds) for odds in odds_list]
    
    # Calculate the vig-free scalar
    power = 1  # Start with power of 1
    total = sum(prob ** power for prob in probs)
    while abs(total - 1) > 0.0001:  # Adjust power until probabilities sum to 1
        if total > 1:
            power += 0.0001
        else:
            power -= 0.0001
        total = sum(prob ** power for prob in probs)
    
    # Calculate fair probabilities
    fair_probs = [(prob ** power) / total for prob in probs]
    
    # Convert back to American odds
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
        
        self.featured_markets = ['h2h', 'spreads', 'totals']
        self.additional_markets = [
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
        self.all_player_props = {}  # Add this to store player props
        self.state = state.lower()

        self.logger = logging.getLogger('odds_finder')
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(handler)

        self.rate_limiter = RateLimiter()
        
        # Add session
        self.session = None
    

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def make_request(self, url: str, params: dict) -> dict:
        """Make API request with debug logging"""
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
        
        This method fetches player proposition bets (like points, assists, etc.) for a given event
        by making concurrent API requests to each region. It uses rate limiting and error handling
        to ensure reliable data retrieval.
        
        Args:
            sport: The sport identifier (e.g., 'basketball_nba')
            event_id: The unique identifier for the event
            
        Returns:
            List of bookmaker data containing player props
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
                'markets': ','.join(prop_markets),  # Combine all prop markets for this sport
                'oddsFormat': 'decimal',
                'includeLinks': 'true'
            }
            # Add the API request to our tasks list
            tasks.append(self.make_request(url, params))
        
        # Execute all region requests concurrently with proper rate limiting
        try:
            results = await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Error fetching player props: {str(e)}", exc_info=True)
            return []
        
        # Process and combine results from all regions
        all_bookmakers = []
        
        for data in results:
            if not data:  # Skip if request failed or returned no data
                continue
            
            # Get all bookmakers from the response
            region_bookmakers = data.get('bookmakers', [])
            
            # Filter bookmakers based on the regions configuration
            filtered_bookmakers = []
            for bm in region_bookmakers:
                # Check if this bookmaker is in any of our configured regions
                for region, bookmaker_list in self.regions.items():
                    if any(book.lower() in bm['title'].lower() for book in bookmaker_list):
                        filtered_bookmakers.append(bm)
                        break
            
            # Add the filtered bookmakers to our collection
            all_bookmakers.extend(filtered_bookmakers)
        
        return all_bookmakers

    def process_player_props(self, bookmakers, sport):
        markets = {}
        
        # Create bookmaker name mapping
        bookmaker_mapping = {
            'betmgm': 'BetMGM',
            'betrivers': 'BetRivers',
            'caesars': 'Caesars',
            'draftkings': 'DraftKings',
            'fanduel': 'FanDuel',
            'pinnacle': 'Pinnacle'
        }
        
        # Include all valid books
        valid_books = []
        for region, books in self.regions.items():
            for book in books:
                valid_books.append(bookmaker_mapping.get(book.lower(), book))
        valid_books.append('Pinnacle')  # Add Pinnacle
        
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
        """Convert prop type to readable format"""
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


    # Rest of the class implementation remains the same as it's sport-agnostic
    def decimal_to_american(self, decimal_odds):
        """
        Convert decimal odds to American odds format with proper error handling
        """
        try:
            # Handle invalid or edge case odds
            if decimal_odds <= 1:
                return -10000  # Return a very unfavorable line for invalid odds
                
            if decimal_odds >= 2:
                return round((decimal_odds - 1) * 100)
            else:
                return round(-100 / (decimal_odds - 1))
        except (ZeroDivisionError, ValueError):
            return -10000 


    async def get_events(self, sport: str) -> List[dict]:
        """Fetch upcoming events for a sport"""
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
        """Fetch odds for featured markets with multiple regions"""
        current_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        all_odds = []
        seen_events = set()
        
        # Fetch odds for each region concurrently
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
            tasks.append(self.make_request(url, params))
            
        results = await asyncio.gather(*tasks)
        
        for odds_data in results:
            if not odds_data:
                continue
                
            for event in odds_data:
                event_id = event['id']
                if event_id not in seen_events:
                    filtered_bookmakers = [
                        bm for bm in event['bookmakers']
                        if any(book.lower() in bm['title'].lower() for book in self.regions[region])
                    ]
                    event['bookmakers'] = filtered_bookmakers
                    all_odds.append(event)
                    seen_events.add(event_id)
        
        return all_odds

    async def get_event_odds(self, sport: str, event_id: str) -> List[dict]:
        """Fetch odds for additional markets with debug logging"""
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
                    # Check if this bookmaker is in our configured regions
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
        """Convert decimal odds to implied probability"""
        return 1 / decimal_odds

    def calculate_kelly_percentage(self, prob_a, prob_b, odds_a, odds_b):
        """
        Calculate optimal bet sizing for arbitrage/low hold situations.
        Returns stake percentages that create equal profit regardless of outcome.
        """
        try:
            # For arbitrage/low hold, we want stakes that equalize potential profit
            # Let's say we bet $X on A at decimal odds_a and $Y on B at decimal odds_b
            # For equal profit: X * odds_a = Y * odds_b
            # And X + Y = 100 (total percentage)
            
            stake_a = (odds_b * 100) / (odds_a + odds_b)
            stake_b = (odds_a * 100) / (odds_a + odds_b)
            
            # Ensure stakes are valid
            if stake_a <= 0 or stake_b <= 0:
                return (50.0, 50.0)
                
            return (stake_a, stake_b)
            
        except (ZeroDivisionError, ValueError):
            return (50.0, 50.0)
        
    def generate_bookmaker_filter(self):
        """Generate HTML for bookmaker filter section"""
        bookmakers = sorted(self.regions['us'])  # Get US bookmakers
        
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

    def store_odds_data(self, game, bookmakers, market_type):
        """Store odds data in DataFrame format"""
        odds_records = []
        
        markets = self.process_markets(bookmakers, market_type)
        for market_key, market_odds in markets.items():
            for odds in market_odds:
                record = {
                    'timestamp': datetime.now(timezone.utc),
                    'sport': game['sport_title'],
                    'game': f"{game['home_team']} vs {game['away_team']}",
                    'game_id': game['id'],
                    'commence_time': game['commence_time'],
                    'market_type': market_key,
                    'period': odds.get('period'),
                    'bookmaker': odds['bookmaker'],
                    'team': odds['team'],
                    'price': odds['price'],
                    'american_odds': self.decimal_to_american(odds['price']),
                    'point': odds.get('point'),
                    'link': odds.get('link', '')
                }
                odds_records.append(record)
                
        return pd.DataFrame(odds_records)

    async def process_game(self, game: dict, sport: str) -> tuple:
        """Modified process_game to store odds data"""
        try:
            additional_odds_task = asyncio.create_task(self.get_event_odds(sport, game['id']))
            props_task = asyncio.create_task(self.get_player_props(game['sport_key'], game['id']))
            
            additional_odds, props = await asyncio.gather(additional_odds_task, props_task)
            
            odds_df_list = []
            
            for market in self.featured_markets:
                odds_df = self.store_odds_data(game, game['bookmakers'], market)
                if not odds_df.empty:
                    odds_df_list.append(odds_df)
                
            if additional_odds:
                for market in self.additional_markets:
                    odds_df = self.store_odds_data(game, additional_odds, market)
                    if not odds_df.empty:
                        odds_df_list.append(odds_df)
            
            if props:
                self.all_player_props[game['id']] = {'props': props, 'game': game}
                prop_markets = self.process_player_props(props, game['sport_key'])
                
                prop_records = []
                for market_key, market_odds in prop_markets.items():
                    for odds in market_odds:
                        prop_records.append({
                            'timestamp': datetime.now(timezone.utc),
                            'sport': game['sport_title'],
                            'game': f"{game['home_team']} vs {game['away_team']}",
                            'game_id': game['id'],
                            'commence_time': game['commence_time'],
                            'market_type': 'player_prop',
                            'prop_type': odds['prop_type'],
                            'player': odds['player'],
                            'bookmaker': odds['bookmaker'], 
                            'team': odds['team'],
                            'price': odds['price'],
                            'american_odds': self.decimal_to_american(odds['price']),
                            'point': odds.get('point'),
                            'link': odds.get('link', '')
                        })
                
                if prop_records:
                    odds_df_list.append(pd.DataFrame(prop_records))
            
            # Define column dtypes
            dtype_dict = {
                'timestamp': 'datetime64[ns, UTC]',
                'sport': 'str',
                'game': 'str',
                'game_id': 'str',
                'commence_time': 'str',
                'market_type': 'str',
                'prop_type': 'str',
                'player': 'str',
                'bookmaker': 'str',
                'team': 'str',
                'price': 'float64',
                'american_odds': 'int64',
                'point': 'float64',
                'link': 'str'
            }
            
            # Filter non-empty DataFrames and ensure consistent dtypes
            valid_dfs = []
            for df in odds_df_list:
                if not df.empty:
                    for col in df.columns:
                        if col in dtype_dict:
                            df[col] = df[col].astype(dtype_dict[col])
                    valid_dfs.append(df)
                    
            full_odds_df = pd.concat(valid_dfs, ignore_index=True) if valid_dfs else pd.DataFrame(columns=dtype_dict.keys()).astype(dtype_dict)
            
            opportunities_task = asyncio.create_task(self.find_opportunities(game, additional_odds))
            plus_ev_task = asyncio.create_task(self.find_plus_ev_bets(game))
            
            opportunities, plus_ev = await asyncio.gather(opportunities_task, plus_ev_task)
            
            return opportunities, plus_ev, full_odds_df
                
        except Exception as e:
            self.logger.error(f"Error processing game {game['id']}: {str(e)}", exc_info=True)
            return [], [], pd.DataFrame()

    def process_markets(self, bookmakers, market_type, require_pinnacle=False):
        """Process markets with expanded derivatives"""
        markets = {}
        
        # Define base market types and derivatives
        base_markets = {
            'h2h': [
                'h2h', 
                'h2h_q1', 'h2h_q2', 'h2h_q3', 'h2h_q4',
                'h2h_h1', 'h2h_h2',
                'h2h_p1', 'h2h_p2', 'h2h_p3'
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
                'team_totals', 
                'team_totals_q1', 'team_totals_q2', 'team_totals_q3', 'team_totals_q4',
                'team_totals_h1', 'team_totals_h2'
            ]
        }
        
        # Get period info from market type
        period_info = None
        for period in ['q1', 'q2', 'q3', 'q4', 'h1', 'h2', 'p1', 'p2', 'p3']:
            if period in market_type:
                period_info = period
                break

        for bookmaker in bookmakers:
            for market in bookmaker.get('markets', []):
                # Check if market key belongs to the requested market type family
                is_matching_market = (
                    market['key'] == market_type or
                    (market_type in base_markets and market['key'] in base_markets[market_type])
                )
                
                if is_matching_market:
                    # For period markets, only match the same period
                    if period_info:
                        if period_info not in market['key']:
                            continue
                    
                    market_key = market['key']
                    if market_key not in markets:
                        markets[market_key] = []
                    
                    for outcome in market['outcomes']:
                        point = outcome.get('point')
                        link = outcome.get('link', '')
                        
                        # Handle team totals specially
                        if 'team_totals' in market_key:
                            team = outcome['name'].split(' ')[0]  # Get team name before Over/Under
                            name = f"{team} {outcome['name'].split(' ')[-1]}"  # Combine team with Over/Under
                        else:
                            name = outcome['name']
                        
                        markets[market_key].append({
                            'bookmaker': bookmaker['title'],
                            'team': name,
                            'price': outcome.get('price', 0),
                            'point': point,
                            'link': link,
                            'period': period_info
                        })
        
        return markets
    
    async def find_opportunities(self, game, additional_odds=None):
        """Enhanced opportunity finding for all market types"""
        opportunities = []
        logger = logging.getLogger('opportunities_finder')
        
        try:
            # Combine and filter bookmakers
            all_bookmakers = game['bookmakers']
            if additional_odds:
                all_bookmakers.extend(additional_odds)
            
            us_books = [book.lower() for book in self.regions['us']]
            all_bookmakers = [
                bm for bm in all_bookmakers 
                if bm['title'].lower().replace(' bet', '') in [b.replace('bet', '') for b in us_books]
            ]
            
            # Process all market types
            all_markets = self.featured_markets + self.additional_markets
            for market_type in all_markets:
                markets = self.process_markets(all_bookmakers, market_type)
                
                for market_key, market_odds in markets.items():
                    # Group odds by team and specific line
                    odds_by_team = {}
                    for odds in market_odds:
                        key_components = [odds['team']]
                        
                        # Add period to key if it exists
                        if odds.get('period'):
                            key_components.append(odds['period'])
                        
                        # Add point to key
                        key_components.append(str(odds.get('point', '')))
                        
                        key = '_'.join(key_components)
                        
                        if key not in odds_by_team or odds['price'] > odds_by_team[key]['price']:
                            odds_by_team[key] = odds

                    # Find matching pairs
                    processed_pairs = set()
                    for key1, odds1 in odds_by_team.items():
                        for key2, odds2 in odds_by_team.items():
                            if key1 != key2:
                                # Skip if same bookmaker
                                if odds1['bookmaker'].lower() == odds2['bookmaker'].lower():
                                    continue
                                
                                # Validate match based on market type
                                valid_pair = False
                                
                                # Handle period-specific markets
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
                                    if (odds1.get('point') == odds2.get('point') and
                                        (('OVER' in odds1['team'].upper() and 'UNDER' in odds2['team'].upper()) or
                                        ('UNDER' in odds1['team'].upper() and 'OVER' in odds2['team'].upper()))):
                                        valid_pair = True
                                        
                                elif 'team_totals' in market_type:
                                    team1 = odds1['team'].split(' ')[0]
                                    team2 = odds2['team'].split(' ')[0]
                                    if team1 == team2:  # Same team
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
                                            
                                            # Include period info in description if available
                                            period_desc = f" ({odds1.get('period', '').upper()})" if odds1.get('period') else ""
                                            
                                            opportunities.append({
                                                'sport': game['sport_title'],
                                                'opportunity_type': opportunity_type,
                                                'market_type': f"{market_type}{period_desc}",
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
                # Fetch player props data
                player_props_data = await self.get_player_props(game['sport_key'], game['id'])
                if player_props_data:
                    prop_markets = self.process_player_props(player_props_data, game['sport_key'])
                    
                    for market_key, market_odds in prop_markets.items():
                        prop_type = market_odds[0]['prop_type']
                        player_name = market_odds[0]['player']
                        prop_readable = self.get_prop_description(prop_type, game['sport_key'])
                        
                        # Group odds by outcome
                        odds_by_outcome = {}
                        for odds in market_odds:
                            key = f"{odds['team']}_{odds['bookmaker']}_{odds.get('point', '')}"
                            if key not in odds_by_outcome or odds['price'] > odds_by_outcome[key]['price']:
                                odds_by_outcome[key] = odds
                        
                        # Find matching pairs for props
                        processed_pairs = set()
                        for key1, odds1 in odds_by_outcome.items():
                            for key2, odds2 in odds_by_outcome.items():
                                # Skip if same bookmaker
                                if odds1['bookmaker'] == odds2['bookmaker']:
                                    continue
                                
                                if key1 != key2 and odds1['point'] == odds2['point']:
                                    # Validate over/under matching
                                    if 'OVER' in odds1['team'].upper() and 'UNDER' in odds2['team'].upper():
                                        pair_key = tuple(sorted([key1, key2]))
                                        if pair_key not in processed_pairs:
                                            processed_pairs.add(pair_key)
                                            
                                            # Calculate probabilities
                                            prob1 = self.calculate_implied_probability(odds1['price'])
                                            prob2 = self.calculate_implied_probability(odds2['price'])
                                            total_prob = prob1 + prob2
                                            
                                            if total_prob <= self.low_hold_threshold:
                                                # Calculate stakes and percentages
                                                stake1, stake2 = self.calculate_kelly_percentage(
                                                    prob1, prob2, odds1['price'], odds2['price']
                                                )
                                                
                                                hold_percentage = (total_prob - 1) * 100
                                                opportunity_type = 'Arbitrage' if total_prob < 1 else 'Low Hold'
                                                profit_percentage = round(((1 / total_prob) - 1) * 100, 2) if total_prob < 1 else 0
                                                
                                                prop_description = f"{player_name} - {prop_readable}"
                                                
                                                # Add prop opportunity to results
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

    # In the find_arbitrage method, replace the spread validation with this improved version:

    def find_arbitrage(self, game, additional_odds=None):
        """Find arbitrage opportunities in a single game with improved alternate market handling"""
        arbitrage_opportunities = []
        
        # Combine standard and additional bookmakers
        all_bookmakers = game['bookmakers']
        if additional_odds:
            all_bookmakers.extend(additional_odds)
    
        # Process each market type
        all_markets = self.featured_markets + self.additional_markets
        for market_type in all_markets:
            markets = self.process_markets(all_bookmakers, market_type)
            
            # Process each specific market (including each alternate line)
            for market_key, market_odds in markets.items():
                # Group odds by team and point combination
                odds_by_team = {}
                for odds in market_odds:
                    key = f"{odds['team']}_{odds.get('point', '')}"
                    if key not in odds_by_team or odds['price'] > odds_by_team[key]['price']:
                        odds_by_team[key] = odds
                
                # Find matching pairs for arbitrage
                processed_pairs = set()
                for key1, odds1 in odds_by_team.items():
                    for key2, odds2 in odds_by_team.items():
                        if key1 != key2:
                            # Skip if same team
                            if odds1['team'] == odds2['team']:
                                continue
                                
                            # Validate spread matching
                            if ('spreads' in market_type or 'alternate_spreads' in market_type):
                                point1 = float(odds1.get('point', 0) or 0)
                                point2 = float(odds2.get('point', 0) or 0)
                                
                                # Skip if points are not set
                                if point1 == 0 or point2 == 0:
                                    continue
                                    
                                # For spread bets, ensure the points are opposite and equal
                                if abs(point1 + point2) > 0.1:  # Use 0.1 to account for floating point precision
                                    # print(f"Checking spreads: {odds1['team']} {point1} vs {odds2['team']} {point2}")
                                    continue
                            
                            # Validate totals matching
                            if ('totals' in market_type or 'alternate_totals' in market_type):
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
                                    
                                    arbitrage_opportunities.append({
                                        'sport': game['sport_title'],
                                        'market_type': 'alternate_spreads' if 'alternate_spreads' in market_key 
                                                       else 'alternate_totals' if 'alternate_totals' in market_key
                                                       else market_key.split('_')[0],
                                        'market_point': odds1.get('point'),
                                        'game': f"{game['home_team']} vs {game['away_team']}",
                                        'commence_time': game['commence_time'],
                                        'team1_name': odds1['team'],
                                        'team1_book': odds1['bookmaker'],
                                        'team1_odds': self.decimal_to_american(odds1['price']),
                                        'team1_point': odds1.get('point'),
                                        'team1_stake': round(stake1, 2),
                                        'team1_link': odds1.get('link', ''),  # Add link for first bet
                                        'team2_name': odds2['team'],
                                        'team2_book': odds2['bookmaker'],
                                        'team2_odds': self.decimal_to_american(odds2['price']),
                                        'team2_point': odds2.get('point'),
                                        'team2_stake': round(stake2, 2),
                                        'team2_link': odds2.get('link', ''),  # Add link for second bet
                                        'profit_percentage': round(((1 / (prob1 + prob2)) - 1) * 100, 2)
                                    })
    
        return arbitrage_opportunities
    

    async def find_plus_ev_bets(self, game, additional_odds=None):
        """Enhanced plus EV finder with correct market handling and player props"""
        plus_ev_opportunities = []
        logger = logging.getLogger('plus_ev_finder')
        
        try:
            if additional_odds:
                all_bookmakers = game['bookmakers'] + additional_odds
            else:
                all_bookmakers = game['bookmakers']
            
            # Define market types
            main_markets = ['h2h', 'spreads', 'totals']
            period_markets = ['q1', 'q2', 'q3', 'q4', 'h1', 'h2', 'p1', 'p2', 'p3']
            alt_markets = ['alternate_spreads', 'alternate_totals']
            
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
                        pinnacle_odds = [odds for odds in odds_list if odds['bookmaker'].lower() == 'pinnacle']
                        if len(pinnacle_odds) != 2:
                            continue
                            
                        # Sort Pinnacle odds
                        if 'totals' in actual_market.lower():
                            pinnacle_odds.sort(key=lambda x: 'OVER' in x['team'].upper(), reverse=True)
                        elif 'spreads' in actual_market.lower():
                            point0 = float(pinnacle_odds[0].get('point', 0) or 0)
                            point1 = float(pinnacle_odds[1].get('point', 0) or 0)
                            if abs(point0 + point1) > 0.1:
                                continue
                        else:
                            pinnacle_odds.sort(key=lambda x: x['team'])
                        
                        # Convert and devig
                        pinnacle_american = [self.decimal_to_american(odds['price']) for odds in pinnacle_odds]
                        fair_odds = power_devig(pinnacle_american)
                        
                        # Market data structure
                        is_totals = 'totals' in actual_market.lower()
                        market_data = {'over': {'odds': {}}, 'under': {'odds': {}}} if is_totals else {}
                        
                        # Collect all odds for market data
                        for odds in odds_list:
                            book = odds['bookmaker'].lower()
                            if is_totals:
                                side = 'over' if 'OVER' in odds['team'].upper() else 'under'
                                if book not in market_data[side]['odds']:
                                    market_data[side]['odds'][book] = {
                                        'american': self.decimal_to_american(odds['price']),
                                        'decimal': odds['price'],
                                        'link': odds.get('link', '')
                                    }
                            else:
                                team = odds['team'].split(' ')[0]
                                if team not in market_data:
                                    market_data[team] = {'odds': {}}
                                if book not in market_data[team]['odds']:
                                    market_data[team]['odds'][book] = {
                                        'american': self.decimal_to_american(odds['price']),
                                        'decimal': odds['price'],
                                        'link': odds.get('link', '')
                                    }
                        
                        # Check US books
                        for odds in odds_list:
                            if odds['bookmaker'].lower() not in [b.lower() for b in self.regions['us']]:
                                continue
                                
                            american_odds = self.decimal_to_american(odds['price'])
                            
                            # Find matching fair odds
                            if is_totals:
                                team_index = 0 if 'OVER' in odds['team'].upper() else 1
                            elif 'spreads' in actual_market.lower():
                                point = float(odds.get('point', 0) or 0)
                                team = odds['team'].split(' ')[0]
                                team_index = next((i for i, pin_odds in enumerate(pinnacle_odds)
                                                if team.lower() == pin_odds['team'].split(' ')[0].lower() 
                                                and abs(point - float(pin_odds.get('point', 0) or 0)) < 0.1), -1)
                                if team_index == -1:
                                    continue
                            else:
                                team_index = next((i for i, pin_odds in enumerate(pinnacle_odds)
                                                if odds['team'].lower() in pin_odds['team'].lower()), 0)
                            
                            fair_odd = fair_odds[team_index]
                            
                            # Check if better than fair odds
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
                                    # Format market name
                                    display_market = actual_market.replace('_', ' ').upper()
                                    if is_period:
                                        display_market = f"{market_type.upper()} ({period.upper()})"
                                    
                                    # Format team name with point if needed
                                    team_name = odds['team']
                                    if point != '0' and ('spread' in actual_market.lower() or 'total' in actual_market.lower()):
                                        point_str = f"+{point}" if float(point) > 0 else point
                                        team_name = f"{team_name} ({point_str})"
                                    
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
            
            # Process all markets
            tasks = []
            
            # Main markets
            for market in main_markets:
                tasks.append(process_market(market))
                for period in period_markets:
                    tasks.append(process_market(market, True, period))
            
            # Alternate markets
            for market in alt_markets:
                tasks.append(process_market(market))
            
            # Collect results
            results = await asyncio.gather(*tasks)
            for result in results:
                plus_ev_opportunities.extend(result)
            
            # Process player props
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
                    
                    # Check each line with Pinnacle odds
                    common_points = set(pinnacle_odds['over'].keys()) & set(pinnacle_odds['under'].keys())
                    
                    for point in common_points:
                        pin_over = pinnacle_odds['over'][point]
                        pin_under = pinnacle_odds['under'][point]
                        
                        pin_over_american = self.decimal_to_american(pin_over['price'])
                        pin_under_american = self.decimal_to_american(pin_under['price'])
                        fair_odds = power_devig([pin_over_american, pin_under_american])
                        
                        for odds in market_odds:
                            if odds['point'] != float(point) or odds['bookmaker'].lower() == 'pinnacle':
                                continue
                            
                            if odds['bookmaker'].lower() in [b.lower() for b in self.regions['us']]:
                                american_odds = self.decimal_to_american(odds['price'])
                                is_over = 'OVER' in odds['team'].upper()
                                fair_odd = fair_odds[0] if is_over else fair_odds[1]
                                
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
                                            'market_type': f"PLAYER PROP - {prop_readable}",
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



    
    async def generate_arbitrage_table(self) -> tuple:
        """Modified to return both arbitrage opportunities and full odds data"""
        self.all_opportunities = []
        self.all_odds_data = []
        self.all_plus_ev = []
        self.all_player_props = {}
        odds_dfs = []
        
        async with self:
            sport_tasks = [self.get_featured_odds(sport) for sport in self.sports]
            featured_odds_by_sport = await asyncio.gather(*sport_tasks)
            
            game_tasks = []
            for sport, featured_odds in zip(self.sports, featured_odds_by_sport):
                for game in featured_odds:
                    game_tasks.append(self.process_game(game, sport))
            
            results = await asyncio.gather(*game_tasks)
            
            for opportunities, plus_ev, odds_df in results:
                self.all_opportunities.extend(opportunities)
                self.all_plus_ev.extend(plus_ev)
                if not odds_df.empty:
                    odds_dfs.append(odds_df)
            
        # Create DataFrames
        arb_df = pd.DataFrame(self.all_opportunities) if self.all_opportunities else pd.DataFrame()
        if arb_df.empty:
            arb_df = pd.DataFrame(columns=[
                'opportunity_type', 'hold_percentage', 'sport', 'market_type', 
                'prop_description', 'market_point', 'game', 'commence_time',
                'team1_name', 'team1_book', 'team1_odds', 'team1_point', 'team1_stake', 'team1_link',
                'team2_name', 'team2_book', 'team2_odds', 'team2_point', 'team2_stake', 'team2_link',
                'profit_percentage', 'timestamp'
            ])
        
        full_odds_df = pd.concat(odds_dfs, ignore_index=True) if odds_dfs else pd.DataFrame()
        
        return arb_df, full_odds_df
            
    async def collect_all_odds(self, game):
        """
        Asynchronously collect and organize all odds for the odds screen
        """
        odds_data = []
        
        # Process markets concurrently
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
                
                # Group odds by bookmaker
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
        
        # Create tasks for all markets concurrently
        tasks = [process_market(market_type) for market_type in self.featured_markets]
        results = await asyncio.gather(*tasks)
        
        # Filter out None results and extend odds_data
        odds_data.extend([r for r in results if r is not None])
        
        return odds_data

    
    def remove_vig(self, odds1, odds2):
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

    def get_fair_odds(self, game_data):
        if 'h2h' not in game_data['markets']:
            return {}
            
        # Get Pinnacle odds
        pinnacle_odds = None
        pinnacle_market = game_data['markets']['h2h']  # Changed this line
        if 'pinnacle' in pinnacle_market['books']:
            pinnacle_odds = sorted(pinnacle_market['books']['pinnacle'], key=lambda x: x['team'])
        
        if not pinnacle_odds or len(pinnacle_odds) != 2:
            return {}
            
        # Use power method instead of simple remove_vig
        pinnacle_american = [odds['american_odds'] for odds in pinnacle_odds]
        fair_odds = power_devig(pinnacle_american)
        
        return {
            pinnacle_odds[0]['team']: fair_odds[0],
            pinnacle_odds[1]['team']: fair_odds[1]
        }


async def main():
    # Set pandas display options to show all rows and columns
    pd.set_option('display.max_rows', None)          # Show all rows
    pd.set_option('display.max_columns', None)       # Show all columns
    pd.set_option('display.width', None)             # Auto-detect display width
    pd.set_option('display.max_colwidth', None)      # Show full content in each cell
    
    with open('key.txt', 'r') as f:
        api_key = f.read().strip()
    
    finder = OddsArbitrageFinder(api_key)

    
    arb_df, odds_df = await finder.generate_arbitrage_table()
    
    print(arb_df)
    
    odds_df.to_csv('odds_data.csv', index=False)

    ev_table = pd.DataFrame(finder.all_plus_ev)
    
    ev_json = ev_table.to_dict(orient='records')
    with open('ev.json', 'w') as f:
        json.dump(ev_json, f, indent=2, default=str)

if __name__ == "__main__":
    asyncio.run(main())