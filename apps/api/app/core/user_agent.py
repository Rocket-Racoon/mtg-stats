"""The User-Agent of every call to an external API (architecture §9, S-3.1.2).

Scryfall, the deck sites and Commander Spellbook all get ``MTGStats/<version> (+<repo URL>)``:
the version is the API package's, the URL comes from ``REPO_URL``, and there is never an email.
"""

from importlib import metadata

from app.core.config import Settings, get_settings

PACKAGE_NAME = "mtg-stats-api"
FALLBACK_VERSION = "0.0.0"


def api_version() -> str:
    """The installed ``mtg-stats-api`` version, or ``0.0.0`` when it is not installed."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION


def build_user_agent(settings: Settings | None = None) -> str:
    """Return ``MTGStats/<version> (+<repo URL>)``."""
    used = settings or get_settings()
    return f"MTGStats/{api_version()} (+{used.repo_url})"
