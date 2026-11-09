"""Throwaway models for exercising ``core.db``; kept off the real ``Base.metadata``."""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, MetaData, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import NAMING_CONVENTION, Base, IdMixin, TimestampMixin
from tests.factories import BaseFactory


class SampleBase(Base):
    """Separate metadata so sample tables never leak into the real schema (Alembic)."""

    __abstract__ = True
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Widget(IdMixin, TimestampMixin, SampleBase):
    """Sample parent row with a unique, a check and an index."""

    __tablename__ = "sample_widgets"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("length(name) > 0", name="name_not_empty"),
        Index(None, "name", "kind"),
    )

    name: Mapped[str] = mapped_column(String(50), default="")
    kind: Mapped[str] = mapped_column(String(50), default="")


class Part(IdMixin, TimestampMixin, SampleBase):
    """Sample child row with a foreign key."""

    __tablename__ = "sample_parts"

    widget_id: Mapped[UUID] = mapped_column(ForeignKey("sample_widgets.id"))


class WidgetFactory(BaseFactory):
    """Build-strategy factory for :class:`Widget`."""

    class Meta:
        model = Widget

    name = "widget"
