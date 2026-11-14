#!/usr/bin/env bash
# Manager. ./manager.sh help
#   keepalive        start the dashboard if it isn't running (for cron, every minute)
#   restart-manager  restart the dashboard/supervisor (game servers keep running)
#   stop-manager     stop the dashboard/supervisor (cron restarts it unless its line is removed)
#   deploy           (development copy only) run the tests, then update the live copy and restart it
DIR="$(cd "$(dirname "$0")" && pwd)"
LIVE_DIR="${MANAGER_LIVE_DIR:-$HOME/Desktop/Manager-live}"
cd "$DIR" || exit 1
APP="$DIR/backend/manager_app.py"
PATTERN="python3 -I $APP serve"

case "${1:-}" in
  keepalive)
    pgrep -u "$(id -u)" -f "$PATTERN" >/dev/null && exit 0
    mkdir -p logs
    nohup python3 -I "$APP" serve >>logs/manager.log 2>&1 &
    exit 0 ;;
  restart-manager)
    pkill -u "$(id -u)" -f "$PATTERN"; sleep 1
    exec "$DIR/manager.sh" keepalive ;;
  stop-manager)   # cron starts it again within a minute unless its crontab line is removed
    pkill -u "$(id -u)" -f "$PATTERN" && echo "manager stopped" || echo "manager was not running"
    exit 0 ;;
  deploy)
    [ "$DIR" != "$LIVE_DIR" ] || { echo "Run deploy from the development copy."; exit 1; }
    [ -z "$(git status --porcelain)" ] || { echo "Commit your changes first (git status shows uncommitted work)."; exit 1; }
    echo "Running tests..."
    python3 -I -m unittest discover -s backend/tests -t backend -q || { echo "Tests failed: live copy NOT updated."; exit 1; }
    before=$(git -C "$LIVE_DIR" rev-parse --short HEAD)
    git -C "$LIVE_DIR" merge --ff-only main || { echo "Live copy could not fast-forward: NOT updated."; exit 1; }
    after=$(git -C "$LIVE_DIR" rev-parse --short HEAD)
    echo "Live copy $before -> $after. Restarting the live manager (game servers are not touched)."
    "$LIVE_DIR/manager.sh" restart-manager
    exit 0 ;;
esac
exec python3 -I "$APP" "$@"
