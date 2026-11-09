"""factory_boy base for model factories (decision A3).

Factories use the *build* strategy (no I/O). Persist through :func:`create`, which adds the
instances to the test's session and flushes, so they are rolled back with it.
"""

from typing import Any

import factory
from sqlalchemy.ext.asyncio import AsyncSession


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
