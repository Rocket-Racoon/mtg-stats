#!/bin/sh
# Container entrypoint for api, worker and migrate (S-1.3.2).
#
# Builds DATABASE_URL from POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB, which Compose passes
# from the root .env (env_file), so that file is the single source of the database credentials.
# The root .env's own DATABASE_URL points at 127.0.0.1 (native dev); inside Compose the database
# is postgres:5432, so this value replaces it. The password is URL-encoded and never put on a
# command line.
set -eu

: "${POSTGRES_USER:?POSTGRES_USER is not set; the root .env must define it}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is not set; the root .env must define it}"
: "${POSTGRES_DB:?POSTGRES_DB is not set; the root .env must define it}"

DATABASE_URL="$(python -c '
import os
from urllib.parse import quote

user = quote(os.environ["POSTGRES_USER"], safe="")
password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
db = quote(os.environ["POSTGRES_DB"], safe="")
print(f"postgresql+psycopg://{user}:{password}@postgres:5432/{db}")
')"
export DATABASE_URL

exec "$@"
