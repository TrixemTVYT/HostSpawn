#!/usr/bin/env bash
# Manager launcher (what the desktop icon runs). No web browser: it opens Manager's own app window.
# If the app-window component (WebKitGTK) is missing, it offers to install it first.
# Usage: launch.sh <port>     (8100 = live copy, 8101 = development copy)
PORT="${1:-8100}"
DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# Development checkout only: port 8100 means its live copy. An installed HostSpawn always runs itself.
[ -d "$DIR/.git" ] && [ "$PORT" = 8100 ] && [ -d "$HOME/Desktop/Manager-live" ] && DIR="$HOME/Desktop/Manager-live"
"$DIR/manager.sh" keepalive   # make sure Manager itself is running
PKG="gir1.2-webkit2-4.1"

have_window() { python3 -c 'import gi; gi.require_version("Gtk","3.0"); gi.require_version("WebKit2","4.1")' 2>/dev/null; }

if ! have_window; then
  xmessage -center -title "Manager" -buttons "Install:0,Cancel:1" -default Install \
"Manager needs one component to open its app window:

  App window (WebKitGTK)  -  package $PKG

Press Install to add it now (about 60 MB)."
  [ $? = 0 ] || exit 0
  if sudo -n true 2>/dev/null; then
    xmessage -center -title "Manager" -timeout 3 "Installing the app window... this takes a minute." &
    if ! sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y "$PKG" >>"$DIR/logs/launcher.log" 2>&1; then
      xmessage -center -title "Manager" "Install failed. Details: $DIR/logs/launcher.log"
      exit 1
    fi
  else
    xmessage -center -title "Manager" "Please run this in a terminal, then open Manager again:

  sudo apt-get install -y $PKG"
    exit 0
  fi
fi
for _ in 1 2 3 4 5 6 7 8 9 10; do   # give Manager a moment to start if keepalive just launched it
  (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null && break
  sleep 1
done
exec python3 "$(cd "$(dirname "$0")" && pwd)/manager_window.py" "$DIR" "$PORT"
