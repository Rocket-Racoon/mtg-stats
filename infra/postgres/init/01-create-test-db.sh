#!/bin/sh
# Runs once, on an empty data volume. Creates the test database next to the dev one,
# owned by the same user. Credentials come from the container environment.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-SQL
	CREATE DATABASE mtg_stats_test OWNER "$POSTGRES_USER";
SQL
