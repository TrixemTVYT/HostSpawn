#!/usr/bin/env bash
# Put Manager launchers (live + development) on the desktop and in the app menu.
# Each runs packaging/desktop/launch.sh: Manager's own app window (no web browser). Safe to re-run.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ICON="${MANAGER_ICON:-$HERE/icon.svg}"
[ -f "$ICON" ] || ICON=utilities-system-monitor

# Launchers from before the rename (2026-11-06), created by this script: replaced by the ones below.

make() {  # make <file id> <name> <port> <comment>
  for dir in "$HOME/Desktop" "$HOME/.local/share/applications"; do
    mkdir -p "$dir"
    printf '%s\n' "[Desktop Entry]" "Version=1.0" "Type=Application" "Name=$2" "Comment=$4" \
      "Exec=$HERE/launch.sh $3" "Icon=$ICON" "Terminal=false" "Categories=System;" "StartupWMClass=$1" \
      >"$dir/$1.desktop"
    chmod +x "$dir/$1.desktop"
    gio set "$dir/$1.desktop" metadata::trusted true 2>/dev/null || true
    echo "created $dir/$1.desktop"
  done
}

make manager "Manager" 8100 "Control all your game servers (live)"
make manager-dev "Manager (DEV)" 8101 "Development copy of Manager (view only)"
