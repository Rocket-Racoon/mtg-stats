"""The User-Agent of every call to an external API (architecture §9, S-3.1.2).

Scryfall, the deck sites and Commander Spellbook all get ``MTGStats/<version> (+<repo URL>)``:
the version is the API package's, the URL comes from ``REPO_SLUG``, and there is never an email.
"""

import logging
import tomllib
from functools import lru_cache
from pathlib import Path

from app.core.config import Settings, get_settings

logger = logging.getLogger("app.core.user_agent")

PYPROJECT_PATH = Path(__file__).resolve().parents[2] / "pyproject.toml"
FALLBACK_VERSION = "0.0.0"


@lru_cache
def api_version() -> str:
    """The API package's version: ``[project] version`` in ``apps/api/pyproject.toml``.

    The project is not installed (``package = false``), so package metadata does not exist. The
    file sits two levels above ``app/`` in a checkout and in the Docker image (``/app``), so it is
    the single source of truth. Returns ``0.0.0`` if the file or the key is missing.
    """
    try:
        data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        logger.warning(
            "API version unreadable (%s); using %s in the User-Agent",
            type(exc).__name__,
            FALLBACK_VERSION,
        )
        return FALLBACK_VERSION


def build_user_agent(settings: Settings | None = None) -> str:
    """Return ``MTGStats/<version> (+<repo URL>)``."""
    used = settings or get_settings()
    return f"MTGStats/{api_version()} (+{used.repo_url})"
