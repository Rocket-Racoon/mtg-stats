"""ORM models of the ``playgroups`` domain: ``Invite``."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, UtcDateTime, _utcnow

INVITE_MAX_USES_LIMIT = 20


class Invite(IdMixin, Base):
    """An invite link. Only the SHA-256 hex of the token is stored, never the token."""

    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(f"max_uses BETWEEN 1 AND {INVITE_MAX_USES_LIMIT}", name="max_uses_range"),
        CheckConstraint("use_count >= 0 AND use_count <= max_uses", name="use_count_valid"),
        # Serves the keyset list; its leading column also covers FK lookups and cascades.
        Index("ix_invites_created_by_created_at_id", "created_by", "created_at", "id"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    max_uses: Mapped[int]
    use_count: Mapped[int] = mapped_column(default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)
