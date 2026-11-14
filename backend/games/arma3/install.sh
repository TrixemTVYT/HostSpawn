#!/usr/bin/env bash
# Manager, Arma 3: install (or re-login) the dedicated server, Steam app 233780.
# Run this yourself in a terminal: it asks for your Steam username, then SteamCMD asks for the password and the
# Steam Guard code itself. Nothing you type is saved by this script except the username (config/secrets.env).
# SteamCMD caches the login, so later updates from the manager don't need the password.
#
# Usage: backend/games/arma3/install.sh [install_root]   (default: root from config/servers.toml, else ~/Desktop/Arma3)
set -euo pipefail
MANAGER="$(cd "$(dirname "$0")/../../.." && pwd)"
ROOT="${1:-$(python3 -I -c "import tomllib;print(tomllib.load(open('$MANAGER/config/servers.toml','rb'))['servers']['arma3']['root'])" 2>/dev/null || echo "$HOME/Desktop/Arma3")}"
STEAMCMD="$ROOT/steamcmd"
SECRETS="$MANAGER/config/secrets.env"

echo "Arma 3 server will be installed in: $ROOT/server"
echo "Tip: use a separate Steam account just for the server (it doesn't need to own Arma 3)."
mkdir -p "$STEAMCMD" "$ROOT/server" "$ROOT/logs"
if [ ! -x "$STEAMCMD/steamcmd.sh" ]; then
  echo "Downloading SteamCMD..."
  curl -sSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz -C "$STEAMCMD"
fi

read -r -p "Steam username for the Arma 3 server: " STEAM_USER
[ -n "$STEAM_USER" ] || { echo "No username given."; exit 1; }

# The password and Steam Guard prompts come from SteamCMD itself, straight from your keyboard.
"$STEAMCMD/steamcmd.sh" +force_install_dir "$ROOT/server" +login "$STEAM_USER" +app_update 233780 validate +quit

if [ ! -x "$ROOT/server/arma3server_x64" ]; then
  echo "Install did not finish (arma3server_x64 missing). Run this script again."; exit 1
fi

# Save the username (not the password) so the manager can run updates with the cached login.
[ -f "$SECRETS" ] || (cd "$MANAGER" && ./manager.sh init-secrets >/dev/null)
python3 -I - "$SECRETS" "$STEAM_USER" <<'PY'
import os, sys
path, user = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
lines = [l for l in lines if not l.startswith("ARMA3_STEAM_USER=")] + [f"ARMA3_STEAM_USER={user}"]
open(path, "w").write("\n".join(lines) + "\n"); os.chmod(path, 0o600)
PY
rm -f "$MANAGER/state/arma3/login_needed" "$MANAGER/state/arma3/update_failed"
echo
echo "Done. Arma 3 is installed. Start it from the dashboard or: ./manager.sh arma3 start"
