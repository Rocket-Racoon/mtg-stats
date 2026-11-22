"""ORM models of the ``identity`` domain: ``User``, ``Player`` and ``RefreshToken``."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, false, func, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, UtcDateTime, _utcnow

AVATAR_COLORS: tuple[str, ...] = (
    "red",
    "orange",
    "amber",
    "yellow",
    "lime",
    "green",
    "teal",
    "cyan",
    "blue",
    "indigo",
    "violet",
    "pink",
)
"""The 12 palette tokens a player's ``avatar_color`` may hold, in stable order."""

DISPLAY_NAME_MAX_LENGTH = 32

_AVATAR_COLOR_LIST = ", ".join(f"'{color}'" for color in AVATAR_COLORS)


class User(IdMixin, Base):
    """A registered account. Created together with its :class:`Player`."""

    __tablename__ = "users"
    __table_args__ = (Index("uq_users_username_lower", func.lower(text("username")), unique=True),)

    username: Mapped[str] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(320), default="", server_default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_site_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    password_changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class Player(IdMixin, Base):
    """Who plays: a member (``user_id`` set) or a guest (``is_guest`` and no user)."""

    __tablename__ = "players"
    __table_args__ = (
        CheckConstraint("NOT (is_guest AND user_id IS NOT NULL)", name="guest_has_no_user"),
        CheckConstraint(
            f"char_length(display_name) BETWEEN 1 AND {DISPLAY_NAME_MAX_LENGTH} "
            r"AND display_name !~ '^\s|\s$'",
            name="display_name_valid",
        ),
        CheckConstraint(f"avatar_color IN ({_AVATAR_COLOR_LIST})", name="avatar_color_valid"),
        # Serves "the guests of a playgroup" (S-2.3.1).
        Index(
            "ix_players_home_playgroup_id_guest",
            "home_playgroup_id",
            postgresql_where=text("is_guest AND merged_into_id IS NULL"),
        ),
    )

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), unique=True, default=None)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH))
    avatar_color: Mapped[str] = mapped_column(String(16))
    # No foreign key to ``playgroups``: ``identity`` is the lower domain and cannot import it, so a
    # ``ForeignKey`` here would make every ``Player`` flush need ``playgroups`` in the metadata,
    # which the CLI and workers never load. Deferred until a mechanism exists (a later story).
    home_playgroup_id: Mapped[UUID | None] = mapped_column(default=None)
    merged_into_id: Mapped[UUID | None] = mapped_column(ForeignKey("players.id"), default=None)
    created_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("players.id"), default=None)
    """The player who added this guest (S-2.3.1); None for members and for older rows."""
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class RefreshToken(IdMixin, Base):
    """An issued refresh token, stored as the SHA-256 hex of the JWT; one family per sign-in."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    replaced_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), default=None, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
