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
