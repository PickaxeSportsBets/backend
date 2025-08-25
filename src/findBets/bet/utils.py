from typing import List

def power_devig(odds_list):
    """Devig odds using the power method to find fair probabilities
    
    This function removes the vig (bookmaker margin) from a set of odds using the power method.
    It converts American odds to probabilities, applies a power adjustment to normalize them,
    and returns fair American odds without the bookmaker's edge.
    
    Args:
        odds_list (list): List of American odds values (e.g. [+150, -180]) representing
                          all possible outcomes of a market. For two-way markets like spreads
                          or totals, this should contain exactly 2 values.
                          
                          For spreads betting specifically:
                          - For Team A at -3.5 points, the input would be:
                            [Team A's odds at -3.5, Team B's odds at +3.5]
                          - The function is called separately for each team's perspective
                          
    Returns:
        list: List of fair American odds with vig removed, in the same order as the input.
             
             The output is always a list of fair odds that directly corresponds to the input odds:
             - output[0] = fair odds for the first input (odds_list[0])
             - output[1] = fair odds for the second input (odds_list[1])
             
             For spreads betting specifically:
             When called with [Team A odds at -3.5, Team B odds at +3.5]:
             - output[0] = fair odds for Team A at -3.5 points
             - output[1] = fair odds for Team B at +3.5 points
             
             These fair odds represent the true probability of each outcome and can be used
             to identify +EV betting opportunities when compared with odds offered by bookmakers.
    """

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


def decimal_to_american(decimal_odds: float) -> int:
    """Convert decimal odds to American format"""
    try:
        if decimal_odds <= 1:
            return -10000
        if decimal_odds >= 2:
            return round((decimal_odds - 1) * 100)
        return round(-100 / (decimal_odds - 1))
    except (ZeroDivisionError, ValueError):
        return -10000


def calculate_implied_probability(decimal_odds: float) -> float:
    """Calculate implied probability from decimal odds"""
    return 1 / decimal_odds if decimal_odds > 0 else 0


def calculate_kelly_percentage(
    prob_a: float, prob_b: float, odds_a: float, odds_b: float
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


def market_consensus_fair_odds(bookmakers_data: List[dict]) -> dict:
    """
    Calculate market consensus fair odds by devigging each book individually,
    then applying weighted averaging with Pinnacle at 0.6 and others at 0.4.
    
    Args:
        bookmakers_data: List of bookmaker data dictionaries, each containing:
            - 'bookmaker': str (bookmaker name)
            - 'outcomes': List of dicts with 'team', 'american_odds', 'decimal_odds'
    
    Returns:
        dict: Fair odds for each team/outcome
    """
    # Group outcomes by team across all bookmakers
    team_outcomes = {}
    
    for bookmaker in bookmakers_data:
        bookmaker_name = bookmaker['bookmaker'].lower()
        
        # Determine weight: Pinnacle gets 0.6, others get 0.4
        weight = 0.6 if bookmaker_name == 'pinnacle' else 0.4
        
        for outcome in bookmaker['outcomes']:
            team = outcome['team']
            american_odds = outcome['american_odds']
            
            if team not in team_outcomes:
                team_outcomes[team] = []
            
            team_outcomes[team].append({
                'american_odds': american_odds,
                'weight': weight,
                'bookmaker': bookmaker_name
            })
    
    # For each team, we need to find the corresponding opposite outcome
    # to create two-way markets for devigging
    market_pairs = {}
    
    # Find pairs of teams that form two-way markets
    teams = list(team_outcomes.keys())
    for i, team1 in enumerate(teams):
        for team2 in teams[i+1:]:
            # Check if these teams form a two-way market (same game)
            # This is a simplified check - you might need more sophisticated logic
            if len(teams) == 2:  # Simple case: exactly 2 teams
                market_pairs[(team1, team2)] = (team1, team2)
                break
    
    fair_odds_result = {}
    
    for (team1, team2) in market_pairs.values():
        # Collect all bookmakers that have both teams
        bookmakers_with_both = {}
        
        for bookmaker in bookmakers_data:
            bookmaker_name = bookmaker['bookmaker'].lower()
            team1_odds = None
            team2_odds = None
            
            for outcome in bookmaker['outcomes']:
                if outcome['team'] == team1:
                    team1_odds = outcome['american_odds']
                elif outcome['team'] == team2:
                    team2_odds = outcome['american_odds']
            
            if team1_odds is not None and team2_odds is not None:
                bookmakers_with_both[bookmaker_name] = {
                    'team1_odds': team1_odds,
                    'team2_odds': team2_odds,
                    'weight': 0.6 if bookmaker_name == 'pinnacle' else 0.4
                }
        
        if len(bookmakers_with_both) < 2:
            # Need at least 2 bookmakers for consensus
            continue
        
        # Devig each bookmaker's two-way market
        devigged_odds = {}
        for bookmaker_name, odds_data in bookmakers_with_both.items():
            two_way_odds = [odds_data['team1_odds'], odds_data['team2_odds']]
            fair_odds = power_devig(two_way_odds)
            devigged_odds[bookmaker_name] = {
                'team1_fair': fair_odds[0],
                'team2_fair': fair_odds[1],
                'weight': odds_data['weight']
            }
        
        # Calculate weighted average fair odds
        team1_weighted_sum = 0
        team2_weighted_sum = 0
        total_weight = 0
        
        for bookmaker_name, devigged_data in devigged_odds.items():
            weight = devigged_data['weight']
            team1_weighted_sum += devigged_data['team1_fair'] * weight
            team2_weighted_sum += devigged_data['team2_fair'] * weight
            total_weight += weight
        
        if total_weight > 0:
            team1_consensus = team1_weighted_sum / total_weight
            team2_consensus = team2_weighted_sum / total_weight
            
            fair_odds_result[team1] = round(team1_consensus)
            fair_odds_result[team2] = round(team2_consensus)
    
    return fair_odds_result


def calculate_ev_percentage(bookmaker_odds: int, fair_odds: int) -> float:
    """
    Calculate Expected Value percentage given bookmaker odds and fair odds.
    
    Args:
        bookmaker_odds: American odds offered by bookmaker
        fair_odds: Fair American odds (market consensus)
    
    Returns:
        float: EV percentage (positive = +EV, negative = -EV)
    """
    # Convert to decimal odds
    if bookmaker_odds > 0:
        bookmaker_decimal = (bookmaker_odds / 100) + 1
    else:
        bookmaker_decimal = (100 / abs(bookmaker_odds)) + 1
    
    if fair_odds > 0:
        fair_decimal = (fair_odds / 100) + 1
    else:
        fair_decimal = (100 / abs(fair_odds)) + 1
    
    # Calculate fair probability
    fair_prob = 1 / fair_decimal
    
    # Calculate EV: (probability of winning * payout) - (probability of losing * stake)
    ev_percentage = (fair_prob * (bookmaker_decimal - 1) - (1 - fair_prob)) * 100
    
    return round(ev_percentage, 2)


def example_market_consensus_usage():
    """
    Example of how to use the new market consensus fair odds calculation.
    This demonstrates the step-by-step process.
    """
    # Example data structure (similar to what you'd get from your API)
    sample_bookmakers = [
        {
            'bookmaker': 'pinnacle',
            'outcomes': [
                {'team': 'Team A', 'american_odds': -110, 'decimal_odds': 1.91},
                {'team': 'Team B', 'american_odds': -110, 'decimal_odds': 1.91}
            ]
        },
        {
            'bookmaker': 'fanduel',
            'outcomes': [
                {'team': 'Team A', 'american_odds': -115, 'decimal_odds': 1.87},
                {'team': 'Team B', 'american_odds': -105, 'decimal_odds': 1.95}
            ]
        },
        {
            'bookmaker': 'draftkings',
            'outcomes': [
                {'team': 'Team A', 'american_odds': -112, 'decimal_odds': 1.89},
                {'team': 'Team B', 'american_odds': -108, 'decimal_odds': 1.93}
            ]
        }
    ]
    
    # Step 1: Calculate market consensus fair odds
    fair_odds = market_consensus_fair_odds(sample_bookmakers)
    print(f"Market consensus fair odds: {fair_odds}")
    
    # Step 2: Find value bets by comparing each bookmaker's odds to consensus
    ev_threshold = 2.0  # 2% EV threshold
    
    for bookmaker in sample_bookmakers:
        bookmaker_name = bookmaker['bookmaker']
        
        for outcome in bookmaker['outcomes']:
            team = outcome['team']
            bookmaker_odds = outcome['american_odds']
            
            if team in fair_odds:
                fair_odd = fair_odds[team]
                ev_percentage = calculate_ev_percentage(bookmaker_odds, fair_odd)
                
                if ev_percentage >= ev_threshold:
                    print(f"+EV Found: {bookmaker_name} {team} @ {bookmaker_odds} "
                          f"(Fair: {fair_odd}, EV: {ev_percentage}%)")
                elif ev_percentage <= -ev_threshold:
                    print(f"-EV Found: {bookmaker_name} {team} @ {bookmaker_odds} "
                          f"(Fair: {fair_odd}, EV: {ev_percentage}%)")


def compare_approaches():
    """
    Compare the old Pinnacle-only approach vs new market consensus approach.
    """
    # Example with Pinnacle offering different odds than market consensus
    sample_data = [
        {
            'bookmaker': 'pinnacle',
            'outcomes': [
                {'team': 'Team A', 'american_odds': -120, 'decimal_odds': 1.83},
                {'team': 'Team B', 'american_odds': +100, 'decimal_odds': 2.00}
            ]
        },
        {
            'bookmaker': 'fanduel',
            'outcomes': [
                {'team': 'Team A', 'american_odds': -110, 'decimal_odds': 1.91},
                {'team': 'Team B', 'american_odds': -110, 'decimal_odds': 1.91}
            ]
        },
        {
            'bookmaker': 'draftkings',
            'outcomes': [
                {'team': 'Team A', 'american_odds': -112, 'decimal_odds': 1.89},
                {'team': 'Team B', 'american_odds': -108, 'decimal_odds': 1.93}
            ]
        }
    ]
    
    # Old approach: Use only Pinnacle
    pinnacle_odds = [-120, +100]  # Team A, Team B
    old_fair_odds = power_devig(pinnacle_odds)
    print(f"Old approach (Pinnacle only) fair odds: Team A {old_fair_odds[0]}, Team B {old_fair_odds[1]}")
    
    # New approach: Market consensus
    new_fair_odds = market_consensus_fair_odds(sample_data)
    print(f"New approach (market consensus) fair odds: {new_fair_odds}")
    
    # Compare EV calculations
    fanduel_team_a = -110
    old_ev = calculate_ev_percentage(fanduel_team_a, old_fair_odds[0])
    new_ev = calculate_ev_percentage(fanduel_team_a, new_fair_odds['Team A'])
    
    print(f"FanDuel Team A @ {fanduel_team_a}:")
    print(f"  Old approach EV: {old_ev}%")
    print(f"  New approach EV: {new_ev}%")
    print(f"  Difference: {new_ev - old_ev}%")
