"""The one place that imports every domain's models (S-1.4.3).

``migrations/env.py`` imports this module so that ``Base.metadata`` holds every table when
Alembic autogenerates or checks. Each domain adds a line here when its ``models`` module lands.
It lives at the app root, not in ``core``, because ``core`` may not import domains
(architecture §4.2). Domain ``models`` are imported directly (not through ``services``): this is
a registration side effect, and importing ``models`` is the whole point.
"""

from sqlalchemy import MetaData

import app.cards.models
import app.identity.models
import app.playgroups.models
import app.system.models  # noqa: F401  (registration side effect)
from app.core.db import Base

# Domain models register here, in dependency order (e.g. `import app.playgroups.models`).

metadata: MetaData = Base.metadata
