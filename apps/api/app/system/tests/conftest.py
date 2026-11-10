"""Domain test configuration: reuse the shared env and event-loop setup of ``tests/conftest.py``."""

import importlib

# Side effects only: forces APP_ENV/DATABASE_URL and sets the Windows event loop policy.
importlib.import_module("tests.conftest")
