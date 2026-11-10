"""``system`` domain: health endpoints. The reference example of the domain layout (S-1.4.4).

Layout every domain copies: ``models.py`` (tables), ``schemas.py`` (API shapes), ``router.py``
(thin HTTP layer), ``services/`` (logic; ``services/__init__.py`` is the only public interface),
``repository.py`` (queries on this domain's own tables) and ``tests/``.
"""
