import asyncio
from typing import List, Dict


def validate_player_prop(market_type: str, player_name: str) -> str:
    """
    Validate that a player name exists for player props.
    Raises an error if player name is missing for a player prop.

    Args:
        market_type: The type of market
        player_name: The player name to validate

    Returns:
        The validated player name

    Raises:
        ValueError: If player name is missing for a player prop
    """
    if "player_" in market_type and (not player_name or player_name == "Unknown"):
        error_msg = f"Missing player name for player prop: {market_type}"
        print(error_msg)
        raise ValueError(error_msg)
    return player_name
