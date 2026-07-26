#!/usr/bin/env bash
# Drives a throwaway Home Assistant for testing this integration for real.
#
#   scripts/ha-dev.sh up       start it, install the library, wait until ready
#   scripts/ha-dev.sh sync     rebuild the library and restart after a change
#   scripts/ha-dev.sh logs     follow what the integration is doing
#   scripts/ha-dev.sh state    what the entities currently report
#   scripts/ha-dev.sh stats    what reached the Energy dashboard
#   scripts/ha-dev.sh down     stop it, keeping the configured account
#   scripts/ha-dev.sh reset    throw the instance away and start over
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

CONTAINER=ha-plusportal-dev
COMPOSE="docker compose -f docker-compose.dev.yml"
URL=http://localhost:8124

step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

build_library() {
  step "Building the library"
  # Home Assistant would otherwise try to install pyplusportal from PyPI, where
  # it may not exist yet. Installing it first makes the manifest's pin
  # already-satisfied, so no download is attempted.
  rm -f dist/pyplusportal-*.whl
  uv build --wheel -q
}

install_library() {
  step "Installing it into the container"
  # Through a shell, or the wheel's glob is passed to pip literally.
  docker exec "$CONTAINER" sh -c 'pip install --no-deps --force-reinstall -q /dist/pyplusportal-*.whl' 2>&1 |
    grep -v "root user" || { echo "  installing the library failed" >&2; exit 1; }
  docker exec "$CONTAINER" python -c \
    "import importlib.metadata as m; print('  pyplusportal', m.version('pyplusportal'), 'installed')"
}

wait_for_http() {
  printf '  waiting for Home Assistant'
  until curl -sf "$URL" -o /dev/null 2>/dev/null; do printf '.'; sleep 3; done
  printf ' ready at %s\n' "$URL"
}

enable_debug_logging() {
  local config=.dev/ha-config/configuration.yaml
  [ -f "$config" ] || return 0
  grep -q "custom_components.plusportal" "$config" && return 0
  step "Turning on debug logging for the integration"
  cat >> "$config" <<'YML'

# Added by scripts/ha-dev.sh. Home Assistant logs only warnings by default,
# so a failing setup leaves no trace at all without this.
logger:
  default: warning
  logs:
    custom_components.plusportal: debug
    homeassistant.config_entries: debug
YML
  docker restart "$CONTAINER" >/dev/null
  wait_for_http
}

case "${1:-up}" in
  up)
    mkdir -p .dev/ha-config dist
    build_library
    step "Starting Home Assistant"
    $COMPOSE up -d
    wait_for_http
    install_library
    docker restart "$CONTAINER" >/dev/null
    wait_for_http
    enable_debug_logging
    cat <<EOF

Open $URL and, if this is a fresh instance:

  1. Create the owner account (throwaway; this instance is disposable).
  2. Settings -> Devices & Services -> Add Integration -> "PlusPortal".
     Tenant, username and password are the same values as in your .env.

Then: scripts/ha-dev.sh state
EOF
    ;;

  sync)
    build_library
    install_library
    step "Restarting"
    docker restart "$CONTAINER" >/dev/null
    wait_for_http
    echo "  the integration reloads on restart; give it a moment to fetch"
    ;;

  logs)
    docker exec "$CONTAINER" tail -f /config/home-assistant.log
    ;;

  state)
    docker exec -i "$CONTAINER" python - <<'PY'
import sqlite3
db = sqlite3.connect("/config/home-assistant_v2.db")
rows = list(db.execute("""
    SELECT m.entity_id, s.state FROM states s
    JOIN states_meta m ON s.metadata_id = m.metadata_id
    WHERE s.state_id IN (SELECT MAX(state_id) FROM states GROUP BY metadata_id)
      AND m.entity_id IN (
          SELECT entity_id FROM states_meta WHERE entity_id LIKE 'sensor.%')
    ORDER BY m.entity_id"""))
shown = [(e, v) for e, v in rows if "isk" in e or "plusportal" in e]
if not shown:
    print("no entities yet — is the integration configured?")
for entity, value in shown:
    print(f"  {value:>26}  {entity}")
PY
    ;;

  stats)
    docker exec -i "$CONTAINER" python - <<'PY'
import datetime as dt, sqlite3
db = sqlite3.connect("/config/home-assistant_v2.db")
meta = list(db.execute(
    "SELECT id, statistic_id, unit_of_measurement FROM statistics_meta WHERE source='plusportal'"))
if not meta:
    print("no statistics yet")
for mid, sid, unit in meta:
    n, first, last, total = db.execute(
        "SELECT COUNT(*), MIN(start_ts), MAX(start_ts), MAX(sum) FROM statistics WHERE metadata_id=?",
        (mid,)).fetchone()
    if not n:
        print(f"  {sid}: empty"); continue
    f = dt.datetime.fromtimestamp(first).strftime("%Y-%m-%d %H:%M")
    l = dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M")
    print(f"  {sid}\n    {n} hours  {f} .. {l}  total {total} {unit}")
PY
    ;;

  down)
    $COMPOSE down
    ;;

  reset)
    step "Throwing the instance away"
    $COMPOSE down -v 2>/dev/null || true
    rm -rf .dev/ha-config
    echo "  gone. 'scripts/ha-dev.sh up' starts a fresh one."
    ;;

  *)
    sed -n '2,12p' "$0" | sed 's/^# \?//'
    exit 2
    ;;
esac
