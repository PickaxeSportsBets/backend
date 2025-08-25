from typing import List, Dict, Any
try:
    from .utils import power_devig, decimal_to_american
    from .player_props import validate_player_prop
except ImportError:
    from utils import power_devig, decimal_to_american
    from player_props import validate_player_prop

"""
Imported functions:
    power_devig(odds_list): Removes bookmaker margin from odds using the power method.
        Takes a list of American odds (e.g., [+150, -180]) and returns fair odds with vig removed.
        Used throughout this module to calculate true probabilities from Pinnacle's odds, which
        are considered the sharpest in the market. The resulting fair odds serve as a baseline
        for comparing against other bookmakers to identify +EV betting opportunities.
        
        For spreads betting specifically:
        - The function is called twice for each point spread, once for each team
        - Each call uses a pair of related odds (e.g., Team A -3.5 and Team B +3.5)
        - The returned fair odds represent what the true odds should be without the bookmaker's edge
        - These fair odds are then compared with odds from other bookmakers to find +EV opportunities
        
    decimal_to_american(decimal_odds): Converts decimal odds (e.g., 2.50) to American format (e.g., +150).
        Used to standardize odds format when comparing across different bookmakers.
"""


def _check_ev_opportunities_spreads(
    market_type: str,
    game: dict,
    team_a_market_data: dict,
    team_b_market_data: dict,
    fair_odds: list,
    pin_odds_list: list,
    point: str,
    ev_threshold: float,
    column: str,
    is_favorite: bool
) -> List[dict]:
    plus_ev_opportunities = []
    market_data = team_a_market_data if column == "team_a" else team_b_market_data

    # Determine which side to check based on whether the team is favorite
    side = "under" if is_favorite else "over"
    team_index = 0 if is_favorite else 1  # 0 for favorite, 1 for underdog
    fair_odd = fair_odds[team_index]      # Select corresponding fair odds

    for bm_title, odds_data in market_data[side]["odds"].items():
        if bm_title == "pinnacle":
            continue

        american_odds = odds_data["american"]

        # Updated is_better condition to handle all cases correctly
        is_better = (
            (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd) or
            (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd) or
            (american_odds > 0 and fair_odd < 0) or
            (american_odds < 0 and fair_odd > 0 and abs(american_odds) < fair_odd)
        )

        if is_better:
            decimal_odds = (american_odds / 100) + \
                1 if american_odds > 0 else (100 / abs(american_odds)) + 1
            fair_prob = 100 / \
                (fair_odd + 100) if fair_odd > 0 else abs(fair_odd) / \
                (abs(fair_odd) + 100)
            ev_percentage = (decimal_odds * fair_prob - 1) * 100

            if ev_percentage >= ev_threshold:
                opportunity = {
                    "sport": game["sport_title"],
                    "market_type": market_type,
                    # Use actual point with sign instead of absolute value
                    "market_point": pin_odds_list[team_index]['point'],
                    "game": f"{game['home_team']} vs {game['away_team']}",
                    "commence_time": game["commence_time"],
                    "team": pin_odds_list[team_index]["team"],
                    "bookmaker": bm_title,
                    "odds": american_odds,
                    "fair_odds": fair_odd,
                    "ev_percentage": round(ev_percentage, 2),
                    "link": odds_data["link"],
                    "market_data": market_data,
                    "player": None
                }
                plus_ev_opportunities.append(opportunity)

    return plus_ev_opportunities


def handle_ev_spreads(
    market_type: str,
    game: dict,
    pinnacle_odds: dict,
    all_bookmakers: List[dict],
    ev_threshold: float,
) -> List[dict]:
    plus_ev_opportunities = []
    if market_type not in pinnacle_odds:
        return plus_ev_opportunities

    pin_odds = pinnacle_odds[market_type]

    # Pair outcomes with opposite points
    pairs = []
    processed = set()
    for i, odds1 in enumerate(pin_odds):
        if i in processed:
            continue
        point1 = float(odds1['point'])
        for j, odds2 in enumerate(pin_odds):
            if j in processed or i == j:
                continue
            point2 = float(odds2['point'])
            if abs(point1 + point2) < 0.1 and odds1['team'] != odds2['team']:
                pairs.append([odds1, odds2])
                processed.add(i)
                processed.add(j)
                break

    for pair in pairs:
        # Identify favorite (negative points) and underdog (positive points)
        favorite_odds = pair[0] if float(pair[0]['point']) < 0 else pair[1]
        underdog_odds = pair[1] if float(pair[1]['point']) > 0 else pair[0]
        point_abs = abs(float(favorite_odds['point']))

        # Devig odds
        fair_odds = power_devig(
            [favorite_odds['american'], underdog_odds['american']])

        # Initialize market data
        team_a_market_data = {"over": {"odds": {}}, "under": {"odds": {}}}
        team_b_market_data = {"over": {"odds": {}}, "under": {"odds": {}}}

        # Get team names
        team_a_name = favorite_odds["team"].lower(
        ) if favorite_odds["team"] == pair[0]["team"] else underdog_odds["team"].lower()
        team_b_name = underdog_odds["team"].lower(
        ) if underdog_odds["team"] == pair[1]["team"] else favorite_odds["team"].lower()

        # Determine which team is Team A and which is Team B
        team_a_is_favorite = favorite_odds["team"].lower() == team_a_name
        team_b_is_favorite = favorite_odds["team"].lower() == team_b_name



        # Pinnacle odds - ensure "over" has positive points and "under" has negative points
        # For team_a_market_data
        if team_a_is_favorite:
            # Team A is favorite (negative points)
            team_a_market_data["under"]["odds"]["pinnacle"] = {
                "american": favorite_odds['american'],
                "decimal": favorite_odds['price'],
                "point": favorite_odds['point'],  # Negative
                "link": "https://www.pinnacle.com/en/betting"
            }
            team_a_market_data["over"]["odds"]["pinnacle"] = {
                "american": underdog_odds['american'],
                "decimal": underdog_odds['price'],
                "point": underdog_odds['point'],  # Positive
                "link": "https://www.pinnacle.com/en/betting"
            }
        else:
            # Team A is underdog (positive points)
            team_a_market_data["over"]["odds"]["pinnacle"] = {
                "american": underdog_odds['american'],
                "decimal": underdog_odds['price'],
                "point": underdog_odds['point'],  # Positive
                "link": "https://www.pinnacle.com/en/betting"
            }
            team_a_market_data["under"]["odds"]["pinnacle"] = {
                "american": favorite_odds['american'],
                "decimal": favorite_odds['price'],
                "point": favorite_odds['point'],  # Negative
                "link": "https://www.pinnacle.com/en/betting"
            }

        # For team_b_market_data
        if team_b_is_favorite:
            # Team B is favorite (negative points)
            team_b_market_data["under"]["odds"]["pinnacle"] = {
                "american": favorite_odds['american'],
                "decimal": favorite_odds['price'],
                "point": favorite_odds['point'],  # Negative
                "link": "https://www.pinnacle.com/en/betting"
            }
            team_b_market_data["over"]["odds"]["pinnacle"] = {
                "american": underdog_odds['american'],
                "decimal": underdog_odds['price'],
                "point": underdog_odds['point'],  # Positive
                "link": "https://www.pinnacle.com/en/betting"
            }
        else:
            # Team B is underdog (positive points)
            team_b_market_data["over"]["odds"]["pinnacle"] = {
                "american": underdog_odds['american'],
                "decimal": underdog_odds['price'],
                "point": underdog_odds['point'],  # Positive
                "link": "https://www.pinnacle.com/en/betting"
            }
            team_b_market_data["under"]["odds"]["pinnacle"] = {
                "american": favorite_odds['american'],
                "decimal": favorite_odds['price'],
                "point": favorite_odds['point'],  # Negative
                "link": "https://www.pinnacle.com/en/betting"
            }

        # Other bookmakers
        active_bookmakers = {"pinnacle"}
        point_favorite = float(favorite_odds['point'])  # e.g., -3.5
        point_underdog = float(underdog_odds['point'])  # e.g., +3.5

        for bm in all_bookmakers:
            if not isinstance(bm, dict) or "title" not in bm:
                continue
            bm_title = bm["title"].lower()
            if bm_title == "pinnacle":
                continue

            spreads_market = next(
                (m for m in bm.get("markets", []) if m["key"].lower() == market_type), None)
            if not spreads_market:
                continue

            # Find outcomes for both teams with exact points
            outcomes = spreads_market["outcomes"]
            favorite_outcome = None
            underdog_outcome = None

            for outcome in outcomes:
                outcome_point = float(outcome.get("point", 0))
                outcome_team = outcome["name"].lower()
                odds_data = {
                    "american": decimal_to_american(outcome["price"]),
                    "decimal": outcome["price"],
                    "point": outcome_point,
                    "link": bm.get("link", "")
                }

                # Match by team and point
                if ((outcome_team in favorite_odds["team"].lower() or
                     favorite_odds["team"].lower() in outcome_team) and
                        outcome_point == point_favorite):
                    favorite_outcome = odds_data
                elif ((outcome_team in underdog_odds["team"].lower() or
                       underdog_odds["team"].lower() in outcome_team) and
                      outcome_point == point_underdog):
                    underdog_outcome = odds_data

            # Only include bookmaker if both outcomes are present
            if favorite_outcome and underdog_outcome:
                # For team_a_market_data
                if team_a_is_favorite:
                    # Negative points
                    team_a_market_data["under"]["odds"][bm_title] = favorite_outcome
                    # Positive points
                    team_a_market_data["over"]["odds"][bm_title] = underdog_outcome
                else:
                    # Positive points
                    team_a_market_data["over"]["odds"][bm_title] = underdog_outcome
                    # Negative points
                    team_a_market_data["under"]["odds"][bm_title] = favorite_outcome

                # For team_b_market_data
                if team_b_is_favorite:
                    # Negative points
                    team_b_market_data["under"]["odds"][bm_title] = favorite_outcome
                    # Positive points
                    team_b_market_data["over"]["odds"][bm_title] = underdog_outcome
                else:
                    # Positive points
                    team_b_market_data["over"]["odds"][bm_title] = underdog_outcome
                    # Negative points
                    team_b_market_data["under"]["odds"][bm_title] = favorite_outcome

                active_bookmakers.add(bm_title)



        # Check EV opportunities for both teams
        plus_ev_opportunities.extend(
            _check_ev_opportunities_spreads(
                market_type=market_type,
                game=game,
                team_a_market_data=team_a_market_data,
                team_b_market_data=team_b_market_data,
                fair_odds=fair_odds,
                pin_odds_list=[favorite_odds, underdog_odds],
                point=str(point_abs),
                ev_threshold=ev_threshold,
                column="team_a",
                is_favorite=team_a_is_favorite
            )
        )
        plus_ev_opportunities.extend(
            _check_ev_opportunities_spreads(
                market_type=market_type,
                game=game,
                team_a_market_data=team_a_market_data,
                team_b_market_data=team_b_market_data,
                fair_odds=fair_odds,
                pin_odds_list=[favorite_odds, underdog_odds],
                point=str(point_abs),
                ev_threshold=ev_threshold,
                column="team_b",
                is_favorite=team_b_is_favorite
            )
        )

    return plus_ev_opportunities


def handle_ev_totals(
    market_type: str,
    game: dict,
    pinnacle_odds: dict,
    all_bookmakers: List[dict],
    ev_threshold: float,
) -> List[dict]:
    """Handle EV opportunities for totals markets

    Args:
        market_type: The type of market (e.g., 'totals', 'totals_h1')
        game: Game data dictionary
        pinnacle_odds: Dictionary of Pinnacle odds by market type
        all_bookmakers: List of all bookmaker data
        ev_threshold: Minimum EV percentage threshold

    Returns:
        List of +EV opportunities
    """
    plus_ev_opportunities = []

    if market_type not in pinnacle_odds:
        return plus_ev_opportunities

    pin_odds = pinnacle_odds[market_type]

    # Group Pinnacle odds by point/line
    pin_odds_by_key = {}
    for odds in pin_odds:
        # No player description for totals
        key = (str(odds.get("point", "0")), "")
        if key not in pin_odds_by_key:
            pin_odds_by_key[key] = []
        pin_odds_by_key[key].append(odds)

    # For each point/line combo, find matching odds from other books
    for key, pin_odds_list in pin_odds_by_key.items():
        point, _ = key
        if len(pin_odds_list) != 2:  # Need both sides for devig
            continue

        # Devig Pinnacle odds
        pin_american = [odds["american"] for odds in pin_odds_list]
        fair_odds = power_devig(pin_american)

        # Initialize market_data with proper structure
        market_data = {"over": {"odds": {}}, "under": {"odds": {}}}

        # Add Pinnacle odds first
        for pin_odd in pin_odds_list:
            side = "over" if "OVER" in pin_odd["team"].upper() else "under"
            market_data[side]["odds"]["pinnacle"] = {
                "american": decimal_to_american(pin_odd["price"]),
                "decimal": pin_odd["price"],
                "point": pin_odd.get("point"),
                "link": "https://www.pinnacle.com/en/betting",
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
                    if str(outcome.get("point", "0")) != point:
                        continue

                    decimal_odds = outcome["price"]
                    american_odds = decimal_to_american(decimal_odds)

                    # Determine side based on outcome name (OVER/UNDER)
                    side = "over" if "OVER" in outcome["name"].upper(
                    ) else "under"

                    market_data[side]["odds"][bm_title] = {
                        "american": american_odds,
                        "decimal": decimal_odds,
                        "point": outcome.get("point"),
                        "link": bm.get("link", ""),
                    }
                    active_bookmakers.add(bm_title)

        # Ensure both sides have the same bookmakers with valid data
        for side in ["over", "under"]:
            for bm in list(
                active_bookmakers
            ):  # Create a copy to avoid modification during iteration
                if (
                    bm not in market_data[side]["odds"]
                    or market_data[side]["odds"][bm] is None
                ):
                    other_side = "under" if side == "over" else "over"
                    if (
                        bm in market_data[other_side]["odds"]
                        and market_data[other_side]["odds"][bm] is not None
                    ):
                        market_data[side]["odds"][bm] = market_data[other_side]["odds"][
                            bm
                        ].copy()
                    else:
                        # If no data available, remove from both sides
                        market_data["over"]["odds"].pop(bm, None)
                        market_data["under"]["odds"].pop(bm, None)
                        if bm in active_bookmakers:
                            active_bookmakers.remove(bm)

        # Find +EV opportunities
        plus_ev_opportunities.extend(
            _check_ev_opportunities(
                market_type=market_type,
                game=game,
                market_data=market_data,
                fair_odds=fair_odds,
                pin_odds_list=pin_odds_list,
                point=point,
                player=None,  # No player for totals
                ev_threshold=ev_threshold,
            )
        )

    return plus_ev_opportunities


def handle_ev_player_props(
    market_type: str,
    game: dict,
    pinnacle_odds: dict,
    all_bookmakers: List[dict],
    ev_threshold: float,
) -> List[dict]:
    """Handle EV opportunities for player prop markets

    Args:
        market_type: The type of market (e.g., 'player_points')
        game: Game data dictionary
        pinnacle_odds: Dictionary of Pinnacle odds by market type
        all_bookmakers: List of all bookmaker data
        ev_threshold: Minimum EV percentage threshold

    Returns:
        List of +EV opportunities
    """
    plus_ev_opportunities = []

    if market_type not in pinnacle_odds:
        return plus_ev_opportunities

    pin_odds = pinnacle_odds[market_type]

    # Group Pinnacle odds by point/line and player
    pin_odds_by_key = {}
    for odds in pin_odds:
        key = (str(odds.get("point", "0")), odds.get("description", ""))
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

        # Initialize market_data with proper structure
        market_data = {"over": {"odds": {}}, "under": {"odds": {}}}

        # Add Pinnacle odds first
        for pin_odd in pin_odds_list:
            side = "over" if "OVER" in pin_odd["team"].upper() else "under"
            market_data[side]["odds"]["pinnacle"] = {
                "american": decimal_to_american(pin_odd["price"]),
                "decimal": pin_odd["price"],
                "point": pin_odd.get("point"),
                "link": "https://www.pinnacle.com/en/betting",
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
                    if str(outcome.get("point", "0")) != point:
                        continue

                    # Check if this is for the same player
                    if player and player != outcome.get("description", ""):
                        continue

                    decimal_odds = outcome["price"]
                    american_odds = decimal_to_american(decimal_odds)

                    # Determine side based on outcome name (OVER/UNDER)
                    side = "over" if "OVER" in outcome["name"].upper(
                    ) else "under"

                    market_data[side]["odds"][bm_title] = {
                        "american": american_odds,
                        "decimal": decimal_odds,
                        "point": outcome.get("point"),
                        "link": bm.get("link", ""),
                    }
                    active_bookmakers.add(bm_title)

        # Ensure both sides have the same bookmakers with valid data
        for side in ["over", "under"]:
            for bm in list(
                active_bookmakers
            ):  # Create a copy to avoid modification during iteration
                if (
                    bm not in market_data[side]["odds"]
                    or market_data[side]["odds"][bm] is None
                ):
                    other_side = "under" if side == "over" else "over"
                    if (
                        bm in market_data[other_side]["odds"]
                        and market_data[other_side]["odds"][bm] is not None
                    ):
                        market_data[side]["odds"][bm] = market_data[other_side]["odds"][
                            bm
                        ].copy()
                    else:
                        # If no data available, remove from both sides
                        market_data["over"]["odds"].pop(bm, None)
                        market_data["under"]["odds"].pop(bm, None)
                        if bm in active_bookmakers:
                            active_bookmakers.remove(bm)

        # Validate player name for player props
        validated_player = None
        if player:
            validated_player = validate_player_prop(market_type, player)

        # Find +EV opportunities
        plus_ev_opportunities.extend(
            _check_ev_opportunities(
                market_type=market_type,
                game=game,
                market_data=market_data,
                fair_odds=fair_odds,
                pin_odds_list=pin_odds_list,
                point=point,
                player=validated_player,
                ev_threshold=ev_threshold,
            )
        )

    return plus_ev_opportunities


def handle_ev_h2h(
    market_type: str,
    game: dict,
    pinnacle_odds: dict,
    all_bookmakers: List[dict],
    ev_threshold: float,
) -> List[dict]:
    """Handle EV opportunities for head-to-head (moneyline) markets

    Args:
        market_type: The type of market (e.g., 'h2h')
        game: Game data dictionary
        pinnacle_odds: Dictionary of Pinnacle odds by market type
        all_bookmakers: List of all bookmaker data
        ev_threshold: Minimum EV percentage threshold

    Returns:
        List of +EV opportunities
    """
    plus_ev_opportunities = []

    if market_type not in pinnacle_odds:
        return plus_ev_opportunities

    pin_odds = pinnacle_odds[market_type]

    # In h2h, we don't have points to group by, just match up the two teams
    home_team = game.get("home_team", "")
    away_team = game.get("away_team", "")

    # Split Pinnacle odds by team
    pin_odds_home = [odds for odds in pin_odds if odds["team"] == home_team]
    pin_odds_away = [odds for odds in pin_odds if odds["team"] == away_team]

    # We need exactly one odds entry for each team
    if len(pin_odds_home) != 1 or len(pin_odds_away) != 1:
        return plus_ev_opportunities

    # Combine the two lists
    pin_odds_list = [pin_odds_home[0], pin_odds_away[0]]

    # Devig Pinnacle odds
    pin_american = [odds["american"] for odds in pin_odds_list]
    fair_odds = power_devig(pin_american)

    # Initialize market_data with proper structure
    market_data = {"over": {"odds": {}}, "under": {"odds": {}}}

    # Add Pinnacle odds first - home team is "over", away team is "under" for convention
    market_data["over"]["odds"]["pinnacle"] = {
        "american": decimal_to_american(pin_odds_list[0]["price"]),
        "decimal": pin_odds_list[0]["price"],
        "point": 0,  # No point spread in h2h
        "link": "https://www.pinnacle.com/en/betting",
    }

    market_data["under"]["odds"]["pinnacle"] = {
        "american": decimal_to_american(pin_odds_list[1]["price"]),
        "decimal": pin_odds_list[1]["price"],
        "point": 0,  # No point spread in h2h
        "link": "https://www.pinnacle.com/en/betting",
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
                decimal_odds = outcome["price"]
                american_odds = decimal_to_american(decimal_odds)

                # Determine side based on team name
                if outcome["name"] == home_team:
                    side = "over"  # Home team is "over"
                elif outcome["name"] == away_team:
                    side = "under"  # Away team is "under"
                else:
                    continue  # Skip if team name doesn't match

                market_data[side]["odds"][bm_title] = {
                    "american": american_odds,
                    "decimal": decimal_odds,
                    "point": 0,  # No point spread in h2h
                    "link": bm.get("link", ""),
                }
                active_bookmakers.add(bm_title)

    # Ensure both sides have the same bookmakers with valid data
    for side in ["over", "under"]:
        for bm in list(
            active_bookmakers
        ):  # Create a copy to avoid modification during iteration
            if (
                bm not in market_data[side]["odds"]
                or market_data[side]["odds"][bm] is None
            ):
                other_side = "under" if side == "over" else "over"
                if (
                    bm in market_data[other_side]["odds"]
                    and market_data[other_side]["odds"][bm] is not None
                ):
                    market_data[side]["odds"][bm] = market_data[other_side]["odds"][
                        bm
                    ].copy()
                else:
                    # If no data available, remove from both sides
                    market_data["over"]["odds"].pop(bm, None)
                    market_data["under"]["odds"].pop(bm, None)
                    if bm in active_bookmakers:
                        active_bookmakers.remove(bm)

    # Find +EV opportunities
    plus_ev_opportunities.extend(
        _check_ev_opportunities(
            market_type=market_type,
            game=game,
            market_data=market_data,
            fair_odds=fair_odds,
            pin_odds_list=pin_odds_list,
            point="0",  # No point spread in h2h
            player=None,  # No player for h2h
            ev_threshold=ev_threshold,
        )
    )

    return plus_ev_opportunities


def _check_ev_opportunities(
    market_type: str,
    game: dict,
    market_data: dict,
    fair_odds: list,
    pin_odds_list: list,
    point: str,
    player: str,
    ev_threshold: float,
) -> List[dict]:
    """Helper function to check for +EV opportunities

    Args:
        market_type: The type of market
        game: Game data dictionary
        market_data: Market data with odds from all bookmakers
        fair_odds: Fair odds calculated from Pinnacle
        pin_odds_list: List of Pinnacle odds
        point: The point/line value
        player: Player name (for player props)
        ev_threshold: Minimum EV percentage threshold

    Returns:
        List of +EV opportunities
    """
    plus_ev_opportunities = []

    # Check over side
    for bm_title, odds_data in market_data["over"]["odds"].items():
        if bm_title == "pinnacle":
            continue

        american_odds = odds_data["american"]

        # Match the outcome to the correct fair odds
        fair_odd = fair_odds[0]  # Use first fair odd for over

        # Check if odds are +EV
        is_better = (
            (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd)
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
                fair_prob = abs(fair_odd) / (abs(fair_odd) + 100)

            ev_percentage = (decimal_odds * fair_prob - 1) * 100

            if ev_percentage >= ev_threshold:
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
        fair_odd = fair_odds[1]  # Use second fair odd for under

        # Check if odds are +EV
        is_better = (
            (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd)
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
                fair_prob = abs(fair_odd) / (abs(fair_odd) + 100)

            ev_percentage = (decimal_odds * fair_prob - 1) * 100

            if ev_percentage >= ev_threshold:
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
