# syntax=docker/dockerfile:1
# API and worker image. Build context: apps/api. Only a `dev` target exists for now (S-1.3.2);
# a production target arrives with the deploy epic (E-11).
#
# Dependencies are installed from pyproject.toml + uv.lock before the source is copied, so a
# source edit never invalidates the dependency layer. The virtualenv lives in /opt/venv, outside
# /app, so the dev bind mount of apps/api over /app cannot hide it.
FROM python:3.12.12-slim-bookworm AS dev

# Keep this uv version in step with .github/workflows/ci.yml (the one that wrote uv.lock).
COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 1000 app \
    && mkdir /opt/venv /app \
    && chown app:app /opt/venv /app

USER app
WORKDIR /app

COPY --chown=app:app pyproject.toml uv.lock .python-version ./
# Dev dependencies are included (the default group), so pytest and mypy run in the container.
RUN uv sync --locked --no-install-project --no-cache

COPY --chown=app:app . .

EXPOSE 8000
# Compose overrides this for the worker and the migrate step. The factory keeps `import app.main`
# free of side effects.
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
