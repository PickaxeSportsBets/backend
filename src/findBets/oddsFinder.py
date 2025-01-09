import requests
import pandas as pd
from datetime import datetime, timezone
import time
import json
import logging
import os
from .betslip import BetslipURLGenerator


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
            'alternate_spreads_q1', 'alternate_spreads_h1',
            'alternate_totals_q1', 'alternate_totals_h1',
            'team_totals_h1', 'team_totals_h2',
            'team_totals_q1', 'team_totals_q2', 'team_totals_q3', 'team_totals_q4',
            'alternate_team_totals_h1', 'alternate_team_totals_h2'
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
            # 'us2': ['espnbet', 'hardrockbet']
        }

        self.low_hold_threshold = 1.03
        self.ev_threshold = 2.0  # Minimum +EV percentage to include
        self.all_odds_data = []
        self.all_opportunities = []
        self.all_plus_ev = []
        self.all_player_props = {}  # Add this to store player props
        self.url_generator = BetslipURLGenerator()
        self.state = state.lower()

    def get_player_props(self, sport, event_id):
        """Fetch player props for a specific event"""
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger('props_fetcher')

        url = f"{self.base_url}/{sport}/events/{event_id}/odds"
        sport_name = sport.upper().split(
            '_')[1] if '_' in sport else sport.upper()

        if sport_name not in self.player_props:
            return []

        prop_markets = self.player_props.get(sport_name, [])

        if not prop_markets:
            return []

        all_bookmakers = []

        # First fetch US bookmakers
        try:
            us_params = {
                'apiKey': self.api_key,
                'regions': 'us',
                'markets': ','.join(prop_markets),
                'oddsFormat': 'decimal',
                'includeLinks': 'true'
            }

            # logger.info(f"Fetching US props for {sport} event {event_id}")
            response = requests.get(url, params=us_params)
            if response.status_code == 200:
                data = response.json()
                us_bookmakers = data.get('bookmakers', [])
                # logger.info(f"Found {len(us_bookmakers)} US bookmakers with props")
                all_bookmakers.extend(us_bookmakers)

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching US props: {e}")

        # Then fetch Pinnacle separately
        try:
            eu_params = {
                'apiKey': self.api_key,
                'regions': 'eu',
                'markets': ','.join(prop_markets),
                'oddsFormat': 'decimal',
                'includeLinks': 'true'
            }

            # logger.info(f"Fetching Pinnacle props for {sport} event {event_id}")
            response = requests.get(url, params=eu_params)
            if response.status_code == 200:
                data = response.json()
                eu_bookmakers = data.get('bookmakers', [])
                # logger.info(f"Found {len(eu_bookmakers)} EU bookmakers with props")

                all_bookmakers.extend(eu_bookmakers)

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Pinnacle props: {e}")

        return all_bookmakers

    def process_player_props(self, bookmakers, sport):
        markets = {}

        # Include both US and Pinnacle
        valid_books = [book.lower()
                       for book in self.regions['us'] + ['pinnacle']]
        bookmakers = [bm for bm in bookmakers if bm['title'].lower()
                      in valid_books]

        sport_name = sport.upper().split(
            '_')[1] if '_' in sport else sport.upper()
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

    def get_events(self, sport):
        """Fetch upcoming events for a sport"""
        url = f"{self.base_url}/{sport}/events"
        params = {
            'apiKey': self.api_key,
            'regions': 'us'
        }

        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            current_time = datetime.now(timezone.utc)

            # Filter for upcoming games only
            return [
                event for event in data
                if datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')) > current_time
            ]
        except requests.exceptions.RequestException as e:
            print(f"Error fetching events for {sport}: {e}")
            return []

    def get_featured_odds(self, sport):
        """Fetch odds for featured markets with multiple regions"""
        current_time = datetime.now(
            timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        all_odds = []

        # Track seen events to avoid duplicates
        seen_events = set()

        # Fetch odds for each region
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

            try:
                response = requests.get(url, params=params)
                response.raise_for_status()
                odds_data = response.json()

                for event in odds_data:
                    event_id = event['id']

                    if event_id not in seen_events:
                        # For first occurrence of event, add all matching bookmakers
                        if bookmakers:
                            # Filter specific bookmakers if specified
                            event['bookmakers'] = [
                                b for b in event['bookmakers']
                                if b['title'].lower() in [bm.lower() for bm in bookmakers]
                            ]
                        all_odds.append(event)
                        seen_events.add(event_id)
                    else:
                        # For duplicate events, only add new bookmakers
                        existing_event = next(
                            e for e in all_odds if e['id'] == event_id)
                        existing_books = {b['title']
                                          for b in existing_event['bookmakers']}

                        for bookmaker in event['bookmakers']:
                            if bookmakers and bookmaker['title'].lower() not in [bm.lower() for bm in bookmakers]:
                                continue
                            if bookmaker['title'] not in existing_books:
                                existing_event['bookmakers'].append(bookmaker)

            except requests.exceptions.RequestException as e:
                print(
                    f"Error fetching odds for {sport} in region {region}: {e}")

        return all_odds

    def get_event_odds(self, sport, event_id):
        """Fetch odds for additional markets for a specific event"""
        url = f"{self.base_url}/{sport}/events/{event_id}/odds"
        all_odds = []

        try:
            params = {
                'apiKey': self.api_key,
                'regions': 'us',
                'markets': ','.join(self.additional_markets),
                'oddsFormat': 'decimal',
                'includeLinks': 'true'
            }

            response = requests.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                bookmakers = data.get('bookmakers', [])
                all_odds.extend(bookmakers)

        except requests.exceptions.RequestException as e:
            if response.status_code != 404:
                print(
                    f"Error fetching additional odds for event {event_id}: {e}")

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

    def process_markets(self, bookmakers, market_type):
        """Process markets from bookmakers data with improved handling of all market types"""
        markets = {}

        for bookmaker in bookmakers:
            for market in bookmaker['markets']:
                if market['key'] == market_type:
                    # Create unique key based on market type and point
                    market_key = market['key']
                    if any(word in market_type for word in ['spreads', 'totals', 'team_totals']):
                        # Include period/quarter in key if present
                        period = '_'.join(market_type.split(
                            '_')[1:]) if '_' in market_type else ''
                        market_key = f"{market['key']}_{period}"

                    if market_key not in markets:
                        markets[market_key] = []

                    for outcome in market['outcomes']:
                        point = outcome.get('point')
                        book_name, params = self.url_generator.parse_existing_url(
                            outcome.get('link', ''))

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

        return markets

    def find_opportunities(self, game, additional_odds=None):
        opportunities = []

        us_books = self.regions['us']
        all_bookmakers = game['bookmakers']
        if additional_odds:
            all_bookmakers.extend(additional_odds)

        all_bookmakers = [bm for bm in all_bookmakers if bm['title'].lower() in [
            b.lower() for b in us_books]]

        for market_type in self.featured_markets + self.additional_markets:
            markets = self.process_markets(all_bookmakers, market_type)

            for market_key, market_odds in markets.items():
                odds_by_team = {}
                for odds in market_odds:
                    if 'team_totals' in market_type:
                        # For team totals, include team name and over/under in key
                        # Get team name without Over/Under
                        team_name = odds['team'].split(' ')[0]
                        is_over = 'OVER' in odds['team'].upper()
                        key = f"{team_name}_{'over' if is_over else 'under'}_{odds.get('point', '')}"
                    else:
                        key = f"{odds['team']}_{odds.get('point', '')}"

                    if key not in odds_by_team or odds['price'] > odds_by_team[key]['price']:
                        odds_by_team[key] = odds

                processed_pairs = set()
                for key1, odds1 in odds_by_team.items():
                    for key2, odds2 in odds_by_team.items():
                        if key1 != key2:
                            if odds1['bookmaker'] == odds2['bookmaker']:
                                continue

                            if 'team_totals' in market_type:
                                # Parse team and over/under from keys
                                team1, type1, _ = key1.split('_')
                                team2, type2, _ = key2.split('_')

                                # Must be same team
                                if team1 != team2:
                                    continue

                                # Must be over vs under
                                if not ((type1 == 'over' and type2 == 'under') or
                                        (type1 == 'under' and type2 == 'over')):
                                    continue

                                # Points must match
                                if odds1.get('point') != odds2.get('point'):
                                    continue

                            elif 'spreads' in market_type:
                                if odds1['team'] == odds2['team']:
                                    continue

                                point1 = float(odds1.get('point', 0) or 0)
                                point2 = float(odds2.get('point', 0) or 0)

                                if point1 == 0 or point2 == 0:
                                    continue

                                if abs(point1 + point2) > 0.1:
                                    continue

                            elif 'totals' in market_type and 'team_totals' not in market_type:
                                if odds1.get('point') != odds2.get('point'):
                                    continue

                                if not (('OVER' in odds1['team'].upper() and 'UNDER' in odds2['team'].upper()) or
                                        ('UNDER' in odds1['team'].upper() and 'OVER' in odds2['team'].upper())):
                                    continue

                            elif 'h2h' in market_type:
                                if odds1['team'] == odds2['team']:
                                    continue

                            pair_key = tuple(sorted([key1, key2]))
                            if pair_key in processed_pairs:
                                continue

                            processed_pairs.add(pair_key)

                            prob1 = self.calculate_implied_probability(
                                odds1['price'])
                            prob2 = self.calculate_implied_probability(
                                odds2['price'])
                            total_prob = prob1 + prob2

                            if total_prob <= self.low_hold_threshold:
                                stake1, stake2 = self.calculate_kelly_percentage(
                                    prob1, prob2, odds1['price'], odds2['price']
                                )

                                hold_percentage = (total_prob - 1) * 100
                                opportunity_type = 'Arbitrage' if total_prob < 1 else 'Low Hold'
                                profit_percentage = round(
                                    ((1 / total_prob) - 1) * 100, 2) if total_prob < 1 else 0

                                opportunities.append({
                                    'sport': game['sport_title'],
                                    'opportunity_type': opportunity_type,
                                    'market_type': market_type,
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
        # logger.info(f"Checking player props for {game['sport_key']} game: {game['home_team']} vs {game['away_team']}")
        player_props = self.get_player_props(game['sport_key'], game['id'])

        if player_props:
            prop_markets = self.process_player_props(
                player_props, game['sport_key'])

            for market_key, market_odds in prop_markets.items():
                prop_type = market_odds[0]['prop_type']
                player_name = market_odds[0]['player']
                prop_readable = self.get_prop_description(
                    prop_type, game['sport_key'])

                # logger.info(f"Analyzing {prop_readable} prop for {player_name}")

                odds_by_outcome = {}
                for odds in market_odds:
                    key = f"{odds['team']}_{odds['bookmaker']}_{odds.get('point', '')}"
                    if key not in odds_by_outcome or odds['price'] > odds_by_outcome[key]['price']:
                        odds_by_outcome[key] = odds

                processed_pairs = set()
                for key1, odds1 in odds_by_outcome.items():
                    for key2, odds2 in odds_by_outcome.items():
                        if odds1['bookmaker'] == odds2['bookmaker']:
                            continue

                        if key1 != key2 and odds1['point'] == odds2['point']:
                            if 'OVER' in odds1['team'].upper() and 'UNDER' in odds2['team'].upper():
                                pair_key = tuple(sorted([key1, key2]))
                                if pair_key not in processed_pairs:
                                    processed_pairs.add(pair_key)

                                    prob1 = self.calculate_implied_probability(
                                        odds1['price'])
                                    prob2 = self.calculate_implied_probability(
                                        odds2['price'])
                                    total_prob = prob1 + prob2

                                    # logger.debug(f"""
                                    #     Potential opportunity found:
                                    #     Player: {player_name}
                                    #     Prop: {prop_readable}
                                    #     Book1: {odds1['bookmaker']} {odds1['team']} {odds1['point']} @ {odds1['price']} (prob: {prob1:.4f})
                                    #     Book2: {odds2['bookmaker']} {odds2['team']} {odds2['point']} @ {odds2['price']} (prob: {prob2:.4f})
                                    #     Total probability: {total_prob:.4f}
                                    # """)

                                    if total_prob <= self.low_hold_threshold:
                                        stake1, stake2 = self.calculate_kelly_percentage(
                                            prob1, prob2, odds1['price'], odds2['price']
                                        )

                                        hold_percentage = (
                                            total_prob - 1) * 100
                                        opportunity_type = 'Arbitrage' if total_prob < 1 else 'Low Hold'
                                        profit_percentage = round(
                                            ((1 / total_prob) - 1) * 100, 2) if total_prob < 1 else 0

                                        # if profit_percentage > 10:
                                        #     logger.warning(f"""
                                        #         Suspiciously high profit percentage ({profit_percentage}%)!
                                        #         Please verify this opportunity manually:
                                        #         {player_name} {prop_readable}
                                        #         {odds1['bookmaker']}: {odds1['team']} {odds1['point']} @ {odds1['price']}
                                        #         {odds2['bookmaker']}: {odds2['team']} {odds2['point']} @ {odds2['price']}
                                        #     """)

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
                                # Use 0.1 to account for floating point precision
                                if abs(point1 + point2) > 0.1:
                                    # print(f"Checking spreads: {odds1['team']} {point1} vs {odds2['team']} {point2}")
                                    continue

                            # Validate totals matching
                            if ('totals' in market_type or 'alternate_totals' in market_type):
                                if odds1.get('point') != odds2.get('point'):
                                    continue

                            pair_key = tuple(sorted([key1, key2]))
                            if pair_key not in processed_pairs:
                                processed_pairs.add(pair_key)

                                prob1 = self.calculate_implied_probability(
                                    odds1['price'])
                                prob2 = self.calculate_implied_probability(
                                    odds2['price'])

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
                                        # Add link for first bet
                                        'team1_link': odds1.get('link', ''),
                                        'team2_name': odds2['team'],
                                        'team2_book': odds2['bookmaker'],
                                        'team2_odds': self.decimal_to_american(odds2['price']),
                                        'team2_point': odds2.get('point'),
                                        'team2_stake': round(stake2, 2),
                                        # Add link for second bet
                                        'team2_link': odds2.get('link', ''),
                                        'profit_percentage': round(((1 / (prob1 + prob2)) - 1) * 100, 2)
                                    })

        return arbitrage_opportunities

    def find_plus_ev_bets(self, game, additional_odds=None):
        """Find plus EV betting opportunities across all markets"""
        plus_ev_opportunities = []
        logging.basicConfig(level=logging.INFO)
        logger = logging.getLogger('plus_ev_finder')

        all_markets = self.featured_markets + self.additional_markets

        all_bookmakers = game['bookmakers']
        if additional_odds:
            all_bookmakers.extend(additional_odds)

        for market_type in all_markets:
            markets = self.process_markets(all_bookmakers, market_type)
            for market_key, market_odds in markets.items():
                # Find Pinnacle odds
                pinnacle_odds = []
                market_data = {}  # Store all market data

                # First, collect all odds by outcome
                for odds in market_odds:
                    outcome_key = f"{odds['team']}_{odds.get('point', '')}"
                    if outcome_key not in market_data:
                        market_data[outcome_key] = {'odds': {}}

                    book = odds['bookmaker'].lower()
                    market_data[outcome_key]['odds'][book] = {
                        'american': self.decimal_to_american(odds['price']),
                        'decimal': odds['price'],
                        'link': odds.get('link', '')
                    }

                    if odds['bookmaker'].lower() == 'pinnacle':
                        pinnacle_odds.append(odds)

                # Skip if we don't have complete Pinnacle odds
                if not pinnacle_odds or len(pinnacle_odds) != 2:
                    continue

                # Sort Pinnacle odds consistently
                pinnacle_odds.sort(key=lambda x: x['team'])
                pinnacle_american = [self.decimal_to_american(
                    odds['price']) for odds in pinnacle_odds]

                # Calculate fair odds using power method
                fair_odds = power_devig(pinnacle_american)

                # Compare US books against fair odds
                for odds in market_odds:
                    if odds['bookmaker'].lower() in [b.lower() for b in self.regions['us']]:
                        american_odds = self.decimal_to_american(odds['price'])

                        # Find matching fair odds based on market type
                        team_index = 0 if odds['team'] == pinnacle_odds[0]['team'] else 1
                        fair_odd = fair_odds[team_index]

                        # Convert both to decimal for proper comparison
                        def to_decimal(american):
                            if american > 0:
                                return 1 + (american / 100)
                            else:
                                return 1 - (100 / american)

                        decimal_odds = to_decimal(american_odds)
                        fair_decimal = to_decimal(fair_odd)

                        # Compare decimal odds - higher decimal odds are always better
                        if decimal_odds > fair_decimal:
                            # Calculate EV percentage
                            ev_percentage = (
                                (decimal_odds / fair_decimal) - 1) * 100

                            if ev_percentage >= self.ev_threshold:
                                # Prepare market display name
                                if 'spreads' in market_type:
                                    market_display = f"Spread {odds.get('point', '')}"
                                elif 'totals' in market_type:
                                    market_display = f"Total {odds.get('point', '')}"
                                else:
                                    market_display = 'Moneyline'

                                # Add period/quarter info if present
                                if '_' in market_type:
                                    period = market_type.split('_')[1].upper()
                                    if period.startswith('Q'):
                                        market_display = f"Q{period[1]} {market_display}"
                                    elif period.startswith('H'):
                                        market_display = f"H{period[1]} {market_display}"
                                    elif period.startswith('P'):
                                        market_display = f"P{period[1]} {market_display}"

                                # logger.info(f"""Found +EV opportunity:
                                #     Book: {odds['bookmaker']}
                                #     Team: {odds['team']}
                                #     Odds: {american_odds} (fair: {fair_odd})
                                #     EV: {ev_percentage:.2f}%
                                # """)

                                plus_ev_opportunities.append({
                                    'sport': game['sport_title'],
                                    'market_type': market_display,
                                    'market_point': odds.get('point'),
                                    'game': f"{game['home_team']} vs {game['away_team']}",
                                    'commence_time': game['commence_time'],
                                    'team': odds['team'],
                                    'bookmaker': odds['bookmaker'],
                                    'odds': american_odds,
                                    'fair_odds': fair_odd,
                                    'ev_percentage': round(ev_percentage, 2),
                                    'link': odds.get('link', ''),
                                    'market_data': market_data
                                })

        # Check player props
        try:
            if game['id'] in self.all_player_props:
                player_props = self.all_player_props[game['id']]['props']
                prop_markets = self.process_player_props(
                    player_props, game['sport_key'])

                for market_key, market_odds in prop_markets.items():
                    if not market_odds:
                        continue

                    prop_type = market_odds[0]['prop_type']
                    player_name = market_odds[0]['player']
                    prop_readable = self.get_prop_description(
                        prop_type, game['sport_key'])

                    # Group by over/under and point
                    market_data = {}  # Store all market data for this prop
                    fanduel_odds = {'over': {}, 'under': {}}

                    # First collect all odds
                    for odds in market_odds:
                        point = str(odds['point'])
                        side = 'over' if 'OVER' in odds['team'].upper(
                        ) else 'under'

                        if point not in market_data:
                            market_data[point] = {
                                'over': {'odds': {}}, 'under': {'odds': {}}}

                        book = odds['bookmaker'].lower()
                        market_data[point][side]['odds'][book] = {
                            'american': self.decimal_to_american(odds['price']),
                            'decimal': odds['price'],
                            'link': odds.get('link', '')
                        }

                        if book == 'fanduel':  # Changed from 'pinnacle' to 'fanduel'
                            fanduel_odds[side][point] = odds

                    # Check each line where we have both FanDuel odds
                    common_points = set(fanduel_odds['over'].keys()) & set(
                        fanduel_odds['under'].keys())
                    for point in common_points:
                        fd_over = fanduel_odds['over'][point]
                        fd_under = fanduel_odds['under'][point]

                        # Calculate fair odds using FanDuel lines
                        fd_over_american = self.decimal_to_american(
                            fd_over['price'])
                        fd_under_american = self.decimal_to_american(
                            fd_under['price'])
                        fair_odds = power_devig(
                            [fd_over_american, fd_under_american])

                        # Check other books at this point
                        for odds in market_odds:
                            if odds['point'] != float(point) or odds['bookmaker'].lower() == 'fanduel':
                                continue

                            if odds['bookmaker'].lower() in [b.lower() for b in self.regions['us']]:
                                american_odds = self.decimal_to_american(
                                    odds['price'])
                                is_over = 'OVER' in odds['team'].upper()
                                fair_odd = fair_odds[0] if is_over else fair_odds[1]

                                # Existing EV calculation logic remains the same
                                if (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd) or \
                                    (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd) or \
                                        (american_odds > 0 and fair_odd < 0):

                                    if american_odds > 0:
                                        decimal_odds = (
                                            american_odds / 100) + 1
                                    else:
                                        decimal_odds = (
                                            100 / abs(american_odds)) + 1

                                    if fair_odd > 0:
                                        fair_prob = 100 / (fair_odd + 100)
                                    else:
                                        fair_prob = abs(
                                            fair_odd) / (abs(fair_odd) + 100)

                                    ev_percentage = (
                                        decimal_odds * fair_prob - 1) * 100

                                    if ev_percentage >= self.ev_threshold:
                                        logger.info(
                                            f"Found +EV prop vs FanDuel: {odds['bookmaker']} {odds['team']} @ {american_odds}")
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
            logger.error(
                f"Error processing player props: {str(e)}", exc_info=True)

        return plus_ev_opportunities

    def generate_arbitrage_table(self):
        #print("Analyzing...")
        self.all_opportunities = []
        self.all_odds_data = []
        self.all_plus_ev = []
        self.all_player_props = {}  # Reset player props

        for sport in self.sports:
            featured_odds = self.get_featured_odds(sport)

            for game in featured_odds:
                # Collect regular odds data
                odds_data = self.collect_all_odds(game)
                self.all_odds_data.extend(odds_data)

                # Fetch and store player props
                props = self.get_player_props(game['sport_key'], game['id'])
                if props:
                    self.all_player_props[game['id']] = {
                        'props': props,
                        'game': game
                    }

                # Process opportunities
                additional_odds = self.get_event_odds(sport, game['id'])
                opportunities = self.find_opportunities(game, additional_odds)
                self.all_opportunities.extend(opportunities)

                plus_ev = self.find_plus_ev_bets(game)
                #print(plus_ev)
                self.all_plus_ev.extend(plus_ev)

        if self.all_opportunities:
            df = pd.DataFrame(self.all_opportunities)
            df['timestamp'] = datetime.now(timezone.utc)

            ev_df = pd.DataFrame(self.all_plus_ev)
            ev_df['primary_key'] = ev_df['game'].astype(str) + ev_df['team'].astype(
                str) + ev_df['market_type'].astype(str) + ev_df['market_point'].astype(str) + ev_df['bookmaker'].astype(str)
            ev_df.to_csv('plus_ev_bets.csv', index=False)

            columns = [
                'opportunity_type', 'hold_percentage',
                'sport', 'market_type', 'prop_description',
                'market_point', 'game', 'commence_time',
                'team1_name', 'team1_book', 'team1_odds', 'team1_point', 'team1_stake', 'team1_link',
                'team2_name', 'team2_book', 'team2_odds', 'team2_point', 'team2_stake', 'team2_link',
                'profit_percentage', 'timestamp'
            ]
            return df[columns], ev_df
        else:
            return pd.DataFrame(columns=[
                'opportunity_type', 'hold_percentage',
                'sport', 'market_type', 'prop_description',
                'market_point', 'game', 'commence_time',
                'team1_name', 'team1_book', 'team1_odds', 'team1_point', 'team1_stake', 'team1_link',
                'team2_name', 'team2_book', 'team2_odds', 'team2_point', 'team2_stake', 'team2_link',
                'profit_percentage', 'timestamp'
            ]), ev_df

    def collect_all_odds(self, game):
        """Collect and organize all odds for the odds screen"""
        odds_data = []

        # Combine standard and additional bookmakers
        all_bookmakers = game['bookmakers']

        # Process each market type
        for market_type in self.featured_markets:
            markets = self.process_markets(all_bookmakers, market_type)

            for market_key, market_odds in markets.items():
                # Create a standardized format for each market
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
                odds_data.append(market_data)

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
            pinnacle_odds = sorted(
                pinnacle_market['books']['pinnacle'], key=lambda x: x['team'])

        if not pinnacle_odds or len(pinnacle_odds) != 2:
            return {}

        # Use power method instead of simple remove_vig
        pinnacle_american = [odds['american_odds'] for odds in pinnacle_odds]
        fair_odds = power_devig(pinnacle_american)

        return {
            pinnacle_odds[0]['team']: fair_odds[0],
            pinnacle_odds[1]['team']: fair_odds[1]
        }

    def run_update_db(self):
        data = self.generate_arbitrage_table()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        arbitrage_df = data[0]
        plus_ev_df = data[1]
        return data


# def main():
#     api_key = os.getenv('ODDS_API_KEY')
#     arbitrage_finder = OddsArbitrageFinder(api_key)
#     arbitrage_finder.run_update_db()


# if __name__ == "__main__":
#     main()
