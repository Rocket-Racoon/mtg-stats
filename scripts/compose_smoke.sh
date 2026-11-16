#!/usr/bin/env bash
# Compose smoke test for the dev backend stack (S-1.3.2). Run from anywhere; CI runs it in
# .github/workflows/compose-smoke.yml. It drives the real containers and checks, in order:
#
#   AC2 (refusal)  with APP_ENV=prod the migrate step exits non-zero, the database is untouched,
#                  and `api` doesn't start.
#   AC1            on a fresh stack, `api` and `worker` start only after Postgres and Redis report
#                  healthy (from `docker events`), and after `migrate` finished.
#   AC2 (dev)      the empty dev database is migrated to head exactly once, before the API starts.
#   AC4            GET /api/v1/health/ready returns 200 with database and redis both "ok".
#   AC3            saving a file under apps/api/app reloads the API within 5 s (same image), and
#                  `docker compose restart worker` restarts the worker.
#
# It needs Docker with Compose v2, python3 and curl. It writes a root .env from .env.example when
# there is none. It removes the stack's containers and volumes (`down -v`) before starting, so
# NEVER run it against a dev stack whose data you want to keep: the Compose project name is
# fixed, so it would wipe the shared dev database. CI runners are throwaway.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f infra/docker-compose.yml)
API="http://127.0.0.1:${API_PORT:-8000}"
RELOAD_LIMIT_S=5
EVENTS_LOG="$(mktemp)"
READY_JSON="$(mktemp)"
RELOAD_FILE="apps/api/app/main.py"
# The probe edits RELOAD_FILE; this copy restores it exactly, uncommitted edits included.
RELOAD_BACKUP="$(mktemp)"
cp "$RELOAD_FILE" "$RELOAD_BACKUP"

if [ "${COMPOSE_SMOKE_I_KNOW_THIS_WIPES_THE_DEV_DB:-}" != "1" ] && [ "${CI:-}" != "true" ]; then
  echo "compose-smoke: this wipes the shared dev database (down -v) and briefly appends a" >&2
  echo "comment to $RELOAD_FILE (restored on exit). Set" >&2
  echo "COMPOSE_SMOKE_I_KNOW_THIS_WIPES_THE_DEV_DB=1 to run it outside CI." >&2
  exit 2
fi

fail() {
  echo "::error::compose-smoke: $*" >&2
  exit 1
}
step() { echo; echo "=== $*"; }
now() { date +%s.%N; }
container() { "${COMPOSE[@]}" ps -a -q "$1"; }
inspect() { docker inspect -f "$2" "$(container "$1")"; }
psql_dev() {
  "${COMPOSE[@]}" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$0"' "$1"
}
wait_healthy() { # service, timeout seconds
  local deadline=$((SECONDS + $2))
  until [ "$(inspect "$1" '{{if .State.Health}}{{.State.Health.Status}}{{end}}')" = "healthy" ]; do
    [ $SECONDS -lt $deadline ] || fail "$1 not healthy after $2 s"
    sleep 1
  done
}
startup_count() { "${COMPOSE[@]}" logs --no-color api 2>&1 | grep -c "Application startup complete" || true; }
ready_body() { curl -sS -o "$READY_JSON" -w '%{http_code}' "$API/api/v1/health/ready" || echo 000; }
worker_starts() { "${COMPOSE[@]}" logs --no-color worker 2>&1 | grep -c "registered jobs" || true; }
restore_reload_file() { cmp -s "$RELOAD_BACKUP" "$RELOAD_FILE" || cp "$RELOAD_BACKUP" "$RELOAD_FILE"; }

cleanup() {
  restore_reload_file
  [ -n "${EVENTS_PID:-}" ] && kill "$EVENTS_PID" 2>/dev/null || true
}
trap cleanup EXIT

[ -f .env ] || cp .env.example .env
# The API rejects the CHANGE_ME placeholder, so give a fresh .env a generated JWT secret (never printed).
if ! grep -Eq '^JWT_SECRET=[^[:space:]]{32,}[[:space:]]*$' .env || grep -q '^JWT_SECRET=CHANGE_ME[[:space:]]*$' .env; then
  JWT_SECRET_VALUE="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  grep -v '^JWT_SECRET=' .env > .env.tmp || true
  printf 'JWT_SECRET=%s\n' "$JWT_SECRET_VALUE" >> .env.tmp
  mv .env.tmp .env
  unset JWT_SECRET_VALUE
fi

step "Build the image once"
"${COMPOSE[@]}" down -v --remove-orphans
"${COMPOSE[@]}" build api
IMAGE_BEFORE="$(docker image inspect -f '{{.Id}}' mtg-stats-api:dev)"

step "AC2: migrate refuses when APP_ENV is not dev"
"${COMPOSE[@]}" up -d postgres redis
wait_healthy postgres 120
wait_healthy redis 60
set +e
REFUSAL="$(APP_ENV=prod "${COMPOSE[@]}" run --rm --no-deps migrate 2>&1)"
RC=$?
set -e
echo "$REFUSAL"
[ "$RC" -ne 0 ] || fail "migrate exited 0 with APP_ENV=prod"
grep -q "migrate refused" <<<"$REFUSAL" || fail "migrate did not explain its refusal"
[ "$(psql_dev "select to_regclass('public.alembic_version') is null")" = "t" ] ||
  fail "the database was touched by a refused migrate"
set +e
APP_ENV=prod "${COMPOSE[@]}" up -d api
RC=$?
set -e
[ "$RC" -ne 0 ] || fail "'up api' succeeded although migrate must refuse with APP_ENV=prod"
[ "$(inspect api '{{.State.StartedAt}}' 2>/dev/null || echo none)" = "0001-01-01T00:00:00Z" ] ||
  [ -z "$(container api)" ] || fail "api started although migrate refused"
echo "refused, database untouched, api not started"

step "Fresh stack: docker compose up (api, worker)"
"${COMPOSE[@]}" down -v --remove-orphans
docker events --filter type=container --filter label=com.docker.compose.project=mtg-stats \
  --format '{{.TimeNano}} {{.Action}} {{index .Actor.Attributes "com.docker.compose.service"}}' \
  >"$EVENTS_LOG" &
EVENTS_PID=$!
sleep 1
"${COMPOSE[@]}" up -d
wait_healthy api 180
sleep 2
kill "$EVENTS_PID" 2>/dev/null || true
EVENTS_PID=""

step "AC1: api and worker start only after Postgres and Redis are healthy"
first() { # action pattern, service -> earliest TimeNano
  awk -v a="$1" -v s="$2" '$NF == s && index($0, a) { print $1; exit }' "$EVENTS_LOG"
}
PG_HEALTHY="$(first 'health_status: healthy' postgres)"
REDIS_HEALTHY="$(first 'health_status: healthy' redis)"
MIGRATE_DIE="$(first ' die ' migrate)"
for svc in api worker; do
  START="$(first ' start ' "$svc")"
  [ -n "$PG_HEALTHY" ] && [ -n "$REDIS_HEALTHY" ] && [ -n "$MIGRATE_DIE" ] && [ -n "$START" ] ||
    { cat "$EVENTS_LOG"; fail "missing events for $svc"; }
  [ "$START" -gt "$PG_HEALTHY" ] || fail "$svc started before postgres was healthy"
  [ "$START" -gt "$REDIS_HEALTHY" ] || fail "$svc started before redis was healthy"
  [ "$START" -gt "$MIGRATE_DIE" ] || fail "$svc started before migrate finished"
  echo "$svc started after postgres, redis healthy and migrate finished"
done

step "AC2: the empty dev database was migrated to head once, before the API started"
[ "$(inspect migrate '{{.State.ExitCode}}')" = "0" ] || fail "migrate did not exit 0"
[ "$(inspect migrate '{{.RestartCount}}')" = "0" ] || fail "migrate ran more than once"
[ "$(grep -c ' start migrate$' "$EVENTS_LOG")" = "1" ] || fail "migrate started more than once"
HEAD="$("${COMPOSE[@]}" exec -T api alembic heads | awk '{print $1}')"
CURRENT="$(psql_dev 'select version_num from alembic_version')"
[ -n "$HEAD" ] && [ "$HEAD" = "$CURRENT" ] || fail "database at '$CURRENT', head is '$HEAD'"
echo "database at head $HEAD"

step "AC4: GET /api/v1/health/ready"
CODE="$(ready_body)"
cat "$READY_JSON"; echo
[ "$CODE" = "200" ] || fail "/health/ready returned $CODE"
python3 - "$READY_JSON" <<'PY'
import json, sys
body = json.load(open(sys.argv[1]))
checks = body.get("checks", {})
assert body.get("status") == "ok", body
assert checks.get("database") == "ok" and checks.get("redis") == "ok", body
PY

step "AC3: saving a file under apps/api/app reloads the API within ${RELOAD_LIMIT_S} s"
BEFORE="$(startup_count)"
[ "$BEFORE" -ge 1 ] || fail "no 'Application startup complete' line in the api log"
T0="$(now)"
echo "# compose-smoke reload probe $T0" >>"$RELOAD_FILE"
RELOADED=""
while :; do
  if [ "$(startup_count)" -gt "$BEFORE" ]; then RELOADED="$(now)"; break; fi
  python3 -c "import sys; sys.exit(0 if $(now) - $T0 < $RELOAD_LIMIT_S else 1)" || break
  sleep 0.2
done
[ -n "$RELOADED" ] || fail "the API did not reload within ${RELOAD_LIMIT_S} s"
ELAPSED="$(python3 -c "print(f'{$RELOADED - $T0:.2f}')")"
echo "reloaded in ${ELAPSED} s"
for _ in $(seq 1 30); do [ "$(ready_body)" = "200" ] && break; sleep 1; done
[ "$(ready_body)" = "200" ] || fail "/health/ready not 200 after the reload"
[ "$(docker image inspect -f '{{.Id}}' mtg-stats-api:dev)" = "$IMAGE_BEFORE" ] ||
  fail "the image changed"
restore_reload_file

step "AC3: docker compose restart worker restarts the worker"
W_BEFORE="$(inspect worker '{{.State.StartedAt}}')"
W_LOGS_BEFORE="$(worker_starts)"
[ "$W_LOGS_BEFORE" -ge 1 ] || fail "the worker never logged its registered jobs"
[ "$(inspect worker '{{.RestartCount}}')" = "0" ] || fail "the worker restarted on its own (crash loop?)"
"${COMPOSE[@]}" restart worker
sleep 5
W_AFTER="$(inspect worker '{{.State.StartedAt}}')"
[ "$W_AFTER" != "$W_BEFORE" ] || fail "the worker was not restarted"
[ "$(inspect worker '{{.State.Running}}')" = "true" ] || fail "the worker is not running after restart"
sleep 3
[ "$(inspect worker '{{.State.StartedAt}}')" = "$W_AFTER" ] || fail "the worker is crash-looping"
[ "$(inspect worker '{{.RestartCount}}')" = "0" ] || fail "the worker crashed after the restart"
[ "$(worker_starts)" -gt "$W_LOGS_BEFORE" ] || fail "the restarted worker never logged its registered jobs"
echo "worker restarted at $W_AFTER and stays up"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Compose smoke: passed"
    echo ""
    echo "| Check | Result |"
    echo "|---|---|"
    echo "| AC2 migrate refuses with APP_ENV=prod, database untouched, api not started | passed |"
    echo "| AC1 api and worker start after Postgres and Redis are healthy and migrate finished | passed |"
    echo "| AC2 empty dev database migrated to head once, before the API | passed |"
    echo "| AC4 /api/v1/health/ready 200, database and redis ok | passed |"
    echo "| AC3 API reload after a save | ${ELAPSED} s (limit ${RELOAD_LIMIT_S} s) |"
    echo "| AC3 worker restarts on \`docker compose restart worker\` | passed |"
  } >>"$GITHUB_STEP_SUMMARY"
fi
echo
echo "compose-smoke: all checks passed"
