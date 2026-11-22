"""The initial state (CONTRACT.md section 3.5)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.games.core.canonical import has_lone_surrogate
from app.games.core.contract import DEFAULT_SETTINGS, SCHEMA_VERSION, GameState, Settings


def _settings_values(settings: Settings | Mapping[str, Any] | None) -> dict[str, Any]:
    if settings is None:
        given: dict[str, Any] = {}
    elif isinstance(settings, Settings):
        given = {name: getattr(settings, name) for name in Settings.model_fields}
    elif isinstance(settings, Mapping):
        given = dict(settings)
    else:
        raise TypeError("settings must be a mapping")
    unknown = sorted(str(key) for key in given if key not in DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"unknown setting: {', '.join(unknown)}")
    return {**DEFAULT_SETTINGS, **given}


def initial_state(game_id: str, settings: Settings | Mapping[str, Any] | None = None) -> GameState:
    """Return the empty lobby for ``game_id`` (CONTRACT.md section 3.5).

    ``settings`` is optional and may be partial: a missing key takes its default. Values are
    copied as given, with neither types nor ranges validated (the ``game_created`` and
    ``settings_changed`` handlers validate, S-6.1.6).

    Raises:
        TypeError: ``game_id`` is not a string, or ``settings`` is not a mapping.
        ValueError: ``game_id`` is empty or has a lone surrogate, or ``settings`` has a key that
            is not a setting name. These are not reducer error codes.
    """
    if not isinstance(game_id, str):
        raise TypeError("game_id must be a string")
    if game_id == "" or has_lone_surrogate(game_id):
        raise ValueError("game_id must be a non-empty well-formed string")
    return GameState(
        v=SCHEMA_VERSION,
        game_id=game_id,
        phase="lobby",
        resume_phase=None,
        settings=Settings.model_construct(**_settings_values(settings)),
        control_mode="own_seat",
        seats=[],
        order=[],
        active_seat=None,
        turn=0,
        round=0,
        monarch=None,
        initiative=None,
        commander_damage={},
        no_winner=False,
        winner_seat=None,
        win_condition=None,
        win_condition_note="",
        started_at=None,
        voided=[],
        reverted=[],
        last_seq=0,
    )
