"""The initial state (CONTRACT.md section 3.5)."""

from __future__ import annotations

from app.games.core.contract import SCHEMA_VERSION, GameState, Settings


def initial_state(game_id: str, settings: Settings) -> GameState:
    """Return the empty lobby for ``game_id`` with ``settings``.

    The settings are not validated here (section 6.1); the ``game_created`` and
    ``settings_changed`` handlers do.
    """
    return GameState(
        v=SCHEMA_VERSION,
        game_id=game_id,
        phase="lobby",
        resume_phase=None,
        settings=settings,
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
        last_seq=0,
    )
