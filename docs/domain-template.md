# Domain template

How to add a domain package (S-1.4.6). The worked example is `apps/api/app/system/`
(S-1.4.4). The ten domains and who may call whom are in [architecture.md](architecture.md) §4;
the layering is §3. `tests/architecture/test_domain_imports.py` enforces both.

A domain `<name>` is one of `system`, `identity`, `playgroups`, `cards`, `decks`, `brackets`,
`games`, `stats`, `collection`, `leagues`. `core`, `workflows`, `workers` and `scripts` are
**not** domains and don't follow this layout.

## Files

Create these under `apps/api/app/<name>/` (the layout test fails if one is missing):

```
app/<name>/
  __init__.py          docstring only: what the domain owns
  models.py            SQLAlchemy 2.0 models
  schemas.py           Pydantic v2 request and response shapes
  router.py            the thin HTTP layer
  services/
    __init__.py        the domain's ONLY public interface
    <topic>.py         business logic, one module per topic
  repository.py        reusable queries on this domain's own tables
  tests/
    __init__.py
    test_<topic>.py
```

| File | Role | Rules |
|---|---|---|
| `models.py` | Tables and behavior about a model's own fields | UUID v7 keys (`app/core/ids.py`), `DateTime(timezone=True)`, non-null strings default to `""`, money is `Numeric(12, 2)` plus a currency code. No ORM event listeners for business logic. Other domains never import it. |
| `schemas.py` | Pydantic v2 in/out | Errors use the envelope; list responses are `{items, next_cursor}`. |
| `router.py` | Parse, resolve auth (`core.auth`), call **one** service function, respond | Imports no `sqlalchemy` module and not its own `repository`. No transactions and no permission checks (those live in services). |
| `services/` | Business rules, transactions, permission checks | `services/__init__.py` re-exports what other code may call, with an `__all__`. A public function accepts an optional caller session and then never commits, enqueues or publishes directly (use `core.db.after_commit` / `core.jobs.enqueue_after_commit`). Other domains reach this one only through `services/__init__.py`. |
| `repository.py` | Reusable queries | Queries only this domain's tables; data from another domain comes through that domain's `services`. Every **list query eager-loads** what it serializes (`selectinload`/`joinedload`), with a test that counts queries. Cursor paging on list endpoints. |
| `tests/` | Unit tests for the domain's internals | Names describe behavior. Acceptance tests live elsewhere. |

## Import rules (architecture §4.2)

- Import another domain only as `app.<other>.services`, and only if `<other>` is reachable from
  this domain by following the §4.2 arrows (the graph is transitive). Never `app.<other>.models`,
  `.repository` or `.services.<submodule>`.
- Never import `app.workflows`, and never `app.model_registry` (only `migrations/env.py` and tests
  may).
- `workflows/` and `workers/` follow the same "only `services`" rule for every domain.
- `core/` never imports a domain, `workflows` or `workers`.
- If a story needs an upward call, §4.3 lists the five mechanisms. A new case needs a row there
  before any code.

A SQLAlchemy query API in a router means any `sqlalchemy` or `sqlalchemy.*` import, typing-only
imports included. Session types come from `core.db` (`SessionDep`, `AppSession`).

## Registering the models

Add one line to `apps/api/app/model_registry.py`, in dependency order, so Alembic sees the tables:

```python
import app.<name>.models  # noqa: F401  (registration side effect)
```

Then register the router in `app/main.py` (`api_router.include_router(<name>_router)`), and add the
domain's row to the `DEPENDENCIES` dict in `tests/architecture/test_domain_imports.py` only if
architecture §4.2 changes (it comes from §4.2, never from the code).

## Worked example: `system`

| File | What it shows |
|---|---|
| `app/system/__init__.py` | Docstring naming the layout. |
| `app/system/router.py` | `live()` and `ready()` each call one function in `services` and return a schema. No `sqlalchemy`, no repository import. |
| `app/system/services/__init__.py` | `from app.system.services.health import liveness, readiness` plus `__all__`: the public interface. |
| `app/system/services/health.py` | The logic; reaches `repository.ping_database` for the query. |
| `app/system/repository.py` | The only place with `sqlalchemy.text` for the readiness probe. |
| `app/system/schemas.py` | `LiveResponse`, `ReadyResponse`. |
| `app/system/models.py` | Tables of the domain (registered in `app/model_registry.py`). |
| `app/system/tests/test_health.py` | Unit tests beside the code. |

## Checklist for a new domain

1. Copy the layout above; fill the docstrings.
2. Add the `model_registry.py` line and the router registration.
3. Run `uv run pytest tests/architecture -q` from `apps/api`.
4. Run the full verify gate.
