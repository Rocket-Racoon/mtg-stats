"""Pydantic models and constants that mirror ``packages/game-core/CONTRACT.md``.

Models are frozen and strict (no coercion: ``True`` is not an integer, ``1.0`` is not an int).
Lists inside a model are never mutated in place by the reducer; a handler builds new ones.
Pure: no clock, randomness or I/O.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from app.games.core.canonical import MAX_SAFE_INTEGER

SCHEMA_VERSION: Final = 1
SUPPORTED_VERSIONS: Final = frozenset({SCHEMA_VERSION})

EVENT_ID_PATTERN: Final = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

EVENT_TYPES: Final[tuple[str, ...]] = (
    "game_created",
    "settings_changed",
    "seat_joined",
    "seat_left",
    "seat_moved",
    "seat_claimed",
    "deck_assigned",
    "commanders_set",
    "control_mode_changed",
    "game_started",
    "turn_passed",
    "life_changed",
    "commander_damage",
    "poison_changed",
    "counter_changed",
    "commander_cast",
    "monarch_set",
    "initiative_set",
    "drain",
    "group_drain",
    "player_conceded",
    "player_eliminated",
    "player_revived",
    "value_set",
    "game_end_requested",
    "game_ended",
    "game_abandoned",
    "game_resumed",
    "event_reverted",
)
"""The closed event registry (section 4)."""

ReducerErrorCode = Literal[
    "unsupported_version",
    "unknown_event_type",
    "invalid_event",
    "game_mismatch",
    "out_of_order",
    "invalid_payload",
    "game_closed",
    "invalid_phase",
    "invalid_setting",
    "invalid_amount",
    "unknown_seat",
    "unknown_commander",
    "seat_eliminated",
    "seat_not_eliminated",
    "seat_taken",
    "table_full",
    "not_enough_players",
    "not_revertible",
    "already_reverted",
]
REDUCER_ERROR_CODES: Final[tuple[str, ...]] = get_args(ReducerErrorCode)
"""Every code a reducer may return (section 6.2)."""

SERVER_ONLY_REJECT_CODES: Final = ("forbidden", "undo_window_expired")
"""Codes only the server's authorization layer returns; a reducer MUST NOT (section 6.3)."""

WIN_CONDITION_CODES: Final = (
    "combat_damage",
    "commander_damage",
    "combo",
    "alternate_win",
    "poison",
    "mill",
    "life_drain",
    "concession",
    "last_standing",
    "other",
)
"""D5 win-condition codes (section 3.4)."""

SETTING_RANGES: Final = {
    "starting_life": (1, 999),
    "commander_damage_threshold": (1, 99),
    "poison_threshold": (1, 99),
}
"""Inclusive ranges of the integer settings (section 3.2)."""
TRUST_MODES: Final = ("casual", "strict")
DEFAULT_SETTINGS: Final = {
    "starting_life": 40,
    "commander_damage_threshold": 21,
    "poison_threshold": 10,
    "trust_mode": "casual",
}

SafeInt = Annotated[StrictInt, Field(ge=-MAX_SAFE_INTEGER, le=MAX_SAFE_INTEGER)]
"""An integer in the contract's range (section 1); booleans and floats are rejected."""
SeatId = Annotated[StrictInt, Field(ge=1, le=8)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0, le=MAX_SAFE_INTEGER)]
NonEmptyStr = Annotated[str, Field(min_length=1)]

Phase = Literal["lobby", "active", "ending", "finished", "abandoned"]
ResumePhase = Literal["lobby", "active", "ending"]
ControlMode = Literal["own_seat", "host_all"]
EliminationReason = Literal["life", "commander_damage", "poison", "concession", "declared"]


class _Frozen(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class Settings(_Frozen):
    """Game settings (section 3.2).

    Values are not range-checked here: ``initial_state`` does not validate (section 6.1) and the
    ``game_created`` / ``settings_changed`` handlers do (S-6.1.6, ``invalid_setting``).
    """

    starting_life: SafeInt
    commander_damage_threshold: SafeInt
    poison_threshold: SafeInt
    trust_mode: str


class Commander(_Frozen):
    """One commander slot of a seat."""

    card_id: str | None
    name: str


class Elimination(_Frozen):
    """Why and when a seat left the game (section 3.3)."""

    reason: EliminationReason
    by: SeatId | None
    at_seq: SafeInt
    cause: str | None


class Seat(_Frozen):
    """A seat at the table (section 3.3)."""

    seat: SeatId
    player_id: str | None
    name: str
    deck_version_id: str | None
    commanders: Annotated[list[Commander], Field(min_length=1, max_length=2)]
    life: SafeInt
    poison: NonNegativeInt
    energy: NonNegativeInt
    experience: NonNegativeInt
    commander_cast: list[NonNegativeInt]
    elimination: Elimination | None


class GameState(_Frozen):
    """The game state (section 3.1). Every key is always present; "no value" is ``None``."""

    v: SafeInt
    game_id: NonEmptyStr
    phase: Phase
    resume_phase: ResumePhase | None
    settings: Settings
    control_mode: ControlMode
    seats: Annotated[list[Seat], Field(max_length=8)]
    order: list[SeatId]
    active_seat: SeatId | None
    turn: NonNegativeInt
    round: NonNegativeInt
    monarch: SeatId | None
    initiative: SeatId | None
    commander_damage: dict[str, dict[str, SafeInt]]
    no_winner: StrictBool
    winner_seat: SeatId | None
    win_condition: str | None
    win_condition_note: str
    started_at: str | None
    voided: list[NonNegativeInt]
    last_seq: NonNegativeInt

    def to_json_value(self) -> dict[str, Any]:
        """Return the state as plain JSON values, ready for ``canonical_json``."""
        return self.model_dump(mode="json")


class Actor(_Frozen):
    """Who sent an event (section 2)."""

    kind: Literal["player", "system"]
    player_id: str | None

    @model_validator(mode="after")
    def _player_id_matches_kind(self) -> Actor:
        if self.kind == "player" and not self.player_id:
            raise ValueError("a player actor needs a non-empty player_id")
        if self.kind == "system" and self.player_id is not None:
            raise ValueError("a system actor has player_id null")
        return self


class EventEnvelope(_Frozen):
    """An event as sent and logged (section 2). ``seq`` and ``ts`` are absent until assigned."""

    event_id: Annotated[str, Field(pattern=EVENT_ID_PATTERN)]
    game_id: NonEmptyStr
    seq: Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)] | None = None
    type: str
    payload: dict[str, Any]
    actor: Actor
    on_behalf_of_seat: SeatId | None
    ts: str | None = None
    v: SafeInt

    @model_validator(mode="before")
    @classmethod
    def _absent_is_not_null(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("seq", "ts"):
                if key in data and data[key] is None:
                    raise ValueError(f"{key} is absent until assigned, never null")
        return data

    def to_wire(self) -> dict[str, Any]:
        """Return the envelope as JSON values, omitting an unassigned ``seq`` and ``ts``."""
        data = self.model_dump(mode="json")
        for key in ("seq", "ts"):
            if data[key] is None:
                del data[key]
        return data
