-- Runs only on an empty data volume (the image skips /docker-entrypoint-initdb.d otherwise).
-- The entrypoint runs psql as POSTGRES_USER, so the new database is owned by that user.
CREATE DATABASE mtg_stats_test;
-- Test speed: commits do not wait for the WAL flush (crash-safe, may lose the last commits).
ALTER DATABASE mtg_stats_test SET synchronous_commit = off;
