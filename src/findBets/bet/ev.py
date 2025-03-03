from typing import List, Dict, Any
from utils import power_devig, decimal_to_american

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
from player_props import validate_player_prop


def _check_ev_opportunities_spreads(
    market_type: str,
    game: dict,
    team_a_market_data: dict,
    team_b_market_data: dict,
    fair_odds: list,
    pin_odds_list: list,
    point: str,
    ev_threshold: float,
    column: str
) -> List[dict]:
    plus_ev_opportunities = []
    market_data = team_a_market_data if column == "team_a" else team_b_market_data
    team_index = 0 if column == "team_a" else 1  # Team A fair_odds[0], Team B fair_odds[1]
    
    # Check "over" (team covers)
    for bm_title, odds_data in market_data["over"]["odds"].items():
        if bm_title == "pinnacle":
            continue
        american_odds = odds_data["american"]
        fair_odd = fair_odds[team_index]
        
        is_better = (
            (american_odds > 0 and fair_odd > 0 and american_odds > fair_odd) or
            (american_odds < 0 and fair_odd < 0 and american_odds > fair_odd) or
            (american_odds > 0 and fair_odd < 0)
        )
        
        if is_better:
            decimal_odds = (american_odds / 100) + 1 if american_odds > 0 else (100 / abs(american_odds)) + 1
            fair_prob = 100 / (fair_odd + 100) if fair_odd > 0 else abs(fair_odd) / (abs(fair_odd) + 100)
            ev_percentage = (decimal_odds * fair_prob - 1) * 100
            
            if ev_percentage >= ev_threshold:
                opportunity = {
                    "sport": game["sport_title"],
                    "market_type": market_type,
                    "market_point": point,
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
        team_a_odds = pair[0] if float(pair[0]['point']) < 0 else pair[1]
        team_b_odds = pair[1] if float(pair[1]['point']) > 0 else pair[0]
        point_abs = abs(float(team_a_odds['point']))
        
        # Devig odds
        fair_odds = power_devig([team_a_odds['american'], team_b_odds['american']])
        
        # Initialize market data
        team_a_market_data = {"over": {"odds": {}}, "under": {"odds": {}}}
        team_b_market_data = {"over": {"odds": {}}, "under": {"odds": {}}}
        
        # Pinnacle odds
        team_a_market_data["over"]["odds"]["pinnacle"] = {
            "american": team_a_odds['american'],
            "decimal": team_a_odds['price'],
            "point": team_a_odds['point'],
            "link": "https://www.pinnacle.com/en/betting"
        }
        team_a_market_data["under"]["odds"]["pinnacle"] = {
            "american": team_b_odds['american'],
            "decimal": team_b_odds['price'],
            "point": team_b_odds['point'],
            "link": "https://www.pinnacle.com/en/betting"
        }
        team_b_market_data["over"]["odds"]["pinnacle"] = {
            "american": team_b_odds['american'],
            "decimal": team_b_odds['price'],
            "point": team_b_odds['point'],
            "link": "https://www.pinnacle.com/en/betting"
        }
        team_b_market_data["under"]["odds"]["pinnacle"] = {
            "american": team_a_odds['american'],
            "decimal": team_a_odds['price'],
            "point": team_a_odds['point'],
            "link": "https://www.pinnacle.com/en/betting"
        }
        
        # Other bookmakers
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
                    outcome_point = float(outcome.get("point", 0))
                    if abs(outcome_point) != point_abs:
                        continue
                    odds_data = {
                        "american": decimal_to_american(outcome["price"]),
                        "decimal": outcome["price"],
                        "point": outcome_point,
                        "link": bm.get("link", "")
                    }
                    outcome_team = outcome["name"].lower()
                    team_a_name = team_a_odds["team"].lower()
                    team_b_name = team_b_odds["team"].lower()
                    if outcome_team in team_a_name or team_a_name in outcome_team:
                        if outcome_point < 0:
                            team_a_market_data["over"]["odds"][bm_title] = odds_data
                        else:
                            team_a_market_data["under"]["odds"][bm_title] = odds_data
                    elif outcome_team in team_b_name or team_b_name in outcome_team:
                        if outcome_point > 0:
                            team_b_market_data["over"]["odds"][bm_title] = odds_data
                        else:
                            team_b_market_data["under"]["odds"][bm_title] = odds_data
                    active_bookmakers.add(bm_title)
        
        # Check EV opportunities for both teams
        plus_ev_opportunities.extend(
            _check_ev_opportunities_spreads(
                market_type=market_type,
                game=game,
                team_a_market_data=team_a_market_data,
                team_b_market_data=team_b_market_data,
                fair_odds=fair_odds,
                pin_odds_list=[team_a_odds, team_b_odds],
                point=str(point_abs),
                ev_threshold=ev_threshold,
                column="team_a"
            )
        )
        plus_ev_opportunities.extend(
            _check_ev_opportunities_spreads(
                market_type=market_type,
                game=game,
                team_a_market_data=team_a_market_data,
                team_b_market_data=team_b_market_data,
                fair_odds=fair_odds,
                pin_odds_list=[team_a_odds, team_b_odds],
                point=str(point_abs),
                ev_threshold=ev_threshold,
                column="team_b"
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
        key = (str(odds.get("point", "0")), "")  # No player description for totals
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
                    side = "over" if "OVER" in outcome["name"].upper() else "under"

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
                    side = "over" if "OVER" in outcome["name"].upper() else "under"

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
