"""ORM models of the ``playgroups`` domain: ``Playgroup``, ``Membership`` and ``Invite``."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, IdMixin, TimestampMixin, UtcDateTime, _utcnow

INVITE_MAX_USES_LIMIT = 20

PLAYGROUP_NAME_MIN_LENGTH = 3
PLAYGROUP_NAME_MAX_LENGTH = 50
PLAYGROUP_DESCRIPTION_MAX_LENGTH = 500

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES: tuple[str, ...] = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)
"""The roles a membership may hold. There is no cap on playgroups per player (decision C3)."""

_ROLE_LIST = ", ".join(f"'{role}'" for role in ROLES)


class Playgroup(IdMixin, TimestampMixin, Base):
    """A group of players who track games together."""

    __tablename__ = "playgroups"
    __table_args__ = (
        CheckConstraint(
            f"char_length(name) BETWEEN {PLAYGROUP_NAME_MIN_LENGTH} "
            f"AND {PLAYGROUP_NAME_MAX_LENGTH}",
            name="name_length",
        ),
        CheckConstraint(
            f"char_length(description) <= {PLAYGROUP_DESCRIPTION_MAX_LENGTH}",
            name="description_length",
        ),
    )

    name: Mapped[str] = mapped_column(String(PLAYGROUP_NAME_MAX_LENGTH))
    description: Mapped[str] = mapped_column(
        String(PLAYGROUP_DESCRIPTION_MAX_LENGTH), default="", server_default=""
    )


class Membership(IdMixin, Base):
    """A player's membership of a playgroup. ``left_at`` set means a former member."""

    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_LIST})", name="role_valid"),
        # At most one active owner per playgroup.
        Index(
            "uq_memberships_playgroup_id_owner",
            "playgroup_id",
            unique=True,
            postgresql_where=text("role = 'owner' AND left_at IS NULL"),
        ),
        # At most one active membership per player and playgroup.
        Index(
            "uq_memberships_playgroup_id_player_id_active",
            "playgroup_id",
            "player_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
        # Covers every row (the two above are partial): former-member lists and the cascade.
        Index("ix_memberships_playgroup_id", "playgroup_id"),
        # Serves "my playgroups" (keyset by joined_at, id) and the player FK.
        Index("ix_memberships_player_id_joined_at_id", "player_id", "joined_at", "id"),
    )

    playgroup_id: Mapped[UUID] = mapped_column(ForeignKey("playgroups.id", ondelete="CASCADE"))
    player_id: Mapped[UUID] = mapped_column(ForeignKey("players.id"))
    role: Mapped[str] = mapped_column(String(16))
    joined_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
    left_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)

    playgroup: Mapped[Playgroup] = relationship(lazy="raise")


class Invite(IdMixin, Base):
    """An invite link. Only the SHA-256 hex of the token is stored, never the token."""

    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(f"max_uses BETWEEN 1 AND {INVITE_MAX_USES_LIMIT}", name="max_uses_range"),
        CheckConstraint("use_count >= 0 AND use_count <= max_uses", name="use_count_valid"),
        # Serves the keyset list; its leading column also covers FK lookups and cascades.
        Index("ix_invites_created_by_created_at_id", "created_by", "created_at", "id"),
        # Serves the playgroup's keyset list; its leading column also covers the FK cascade.
        Index("ix_invites_playgroup_id_created_at_id", "playgroup_id", "created_at", "id"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    max_uses: Mapped[int]
    use_count: Mapped[int] = mapped_column(default=0)
    playgroup_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("playgroups.id", ondelete="CASCADE"), default=None
    )
    """The playgroup this invite joins its user to; None for a site-level invite (S-2.1.2)."""
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
