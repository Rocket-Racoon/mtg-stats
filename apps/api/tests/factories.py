"""factory_boy base for model factories (decision A3).

Factories use the *build* strategy (no I/O). Persist through :func:`create`, which adds the
instances to the test's session and flushes, so they are rolled back with it.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import factory
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.identity.models import Player, User
from app.identity.services import default_avatar_color


class BaseFactory(factory.Factory):  # type: ignore[misc]
    """Base class for model factories; subclass and set ``Meta.model``."""

    class Meta:
        abstract = True
        strategy = factory.BUILD_STRATEGY


async def create[T](
    session: AsyncSession, factory_class: type[factory.Factory[T]], **kwargs: Any
) -> T:
    """Build one instance with ``factory_class``, add it to ``session`` and flush."""
    instance: T = factory_class.build(**kwargs)
    session.add(instance)
    await session.flush()
    return instance


async def create_batch[T](
    session: AsyncSession, factory_class: type[factory.Factory[T]], size: int, **kwargs: Any
) -> list[T]:
    """Build ``size`` instances, add them to ``session`` and flush once."""
    instances: list[T] = factory_class.build_batch(size, **kwargs)
    session.add_all(instances)
    await session.flush()
    return instances


class UserFactory(BaseFactory):
    """A registered user; ``username`` is unique per build and ``password_hash`` is a stub."""

    class Meta:
        model = User

    id = factory.LazyFunction(uuid7)
    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    password_hash = "not-a-real-hash"
    is_site_admin = False
    is_active = True
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))
    password_changed_at = factory.LazyFunction(lambda: datetime.now(UTC))


class PlayerFactory(BaseFactory):
    """A member's player. Persist the user first (``create(session, UserFactory)``) and pass it
    as ``user_id=user.id``; without it a fresh, unpersisted user id is used (fails the FK)."""

    class Meta:
        model = Player

    id = factory.LazyFunction(uuid7)
    user_id = factory.LazyFunction(uuid7)
    is_guest = False
    display_name = factory.Sequence(lambda n: f"Player {n}")
    avatar_color = factory.LazyAttribute(lambda o: default_avatar_color(_as_uuid(o.id)))
    created_at = factory.LazyFunction(lambda: datetime.now(UTC))


class GuestPlayerFactory(PlayerFactory):
    """A guest: ``user_id`` is NULL and ``is_guest`` is true."""

    user_id = None
    is_guest = True
    display_name = factory.Sequence(lambda n: f"Guest {n}")


def _as_uuid(value: object) -> UUID:
    assert isinstance(value, UUID)
    return value
