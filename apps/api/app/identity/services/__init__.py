"""Public interface of the ``identity`` domain; other code imports only from here."""

from app.identity.models import AVATAR_COLORS
from app.identity.services.players import (
    InvalidAvatarColorError,
    InvalidDisplayNameError,
    PlayerInfo,
    create_guest_player,
    create_user_with_player,
    default_avatar_color,
    get_player,
    get_player_for_user,
    get_players,
    normalize_display_name,
)

__all__ = [
    "AVATAR_COLORS",
    "InvalidAvatarColorError",
    "InvalidDisplayNameError",
    "PlayerInfo",
    "create_guest_player",
    "create_user_with_player",
    "default_avatar_color",
    "get_player",
    "get_player_for_user",
    "get_players",
    "normalize_display_name",
]
