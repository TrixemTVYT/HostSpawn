#!/usr/bin/env bash
# Fake SteamCMD for sandboxes (MANAGER_TEST_STEAMCMD): never contacts Steam. "+app_update <id>" installs a tiny fake
# server for Rust, Reforger or Arma 3 that answers real A2S queries (tests/fakes/fake_any_game.py). Logins succeed.
# "+app_info_print <id>" reports build FAKE_STEAM_BUILD (default 1000), and installs record that build, so the
# Installation tab shows "Up to date"; start the sandbox with a higher FAKE_STEAM_BUILD later to see an update.
FAKES="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
dir=""; app=""; user=""; info=""
build="${FAKE_STEAM_BUILD:-1000}"
while [ $# -gt 0 ]; do
  case "$1" in +force_install_dir) dir=$2 ;; +app_update) app=$2 ;; +login) user=$2 ;; +app_info_print) info=$2 ;; esac
  shift
done
echo "Steam Console Client (fake, sandbox only)"
[ -n "$user" ] && echo "Logging in user '$user' to Steam Public...OK" && echo "Waiting for user info...OK"
if [ -n "$info" ]; then
  printf '"%s"\n{\n "depots"\n {\n  "branches"\n  {\n   "public"\n   {\n    "buildid"\t\t"%s"\n   }\n  }\n }\n}\n' "$info" "$build"
fi
if [ -n "$dir" ] && [ -n "$app" ]; then
  case "$app" in 258550) exe=RustDedicated ;; 1874900) exe=ArmaReforgerServer ;; 233780) exe=arma3server_x64 ;;
    *) echo "ERROR! Unknown app $app (fake SteamCMD)"; exit 1 ;; esac
  for p in 10 35 60 85 100; do
    echo " Update state (0x61) downloading, progress: $p.00 ($((p * 1000000)) / 100000000)"; sleep 0.4
  done
  mkdir -p "$dir"
  printf '#!/usr/bin/env bash\nexport FAKE_EXE="$0"\nexec python3 -I "%s/fake_any_game.py" "$@"\n' "$FAKES" > "$dir/$exe"
  chmod +x "$dir/$exe"
  mkdir -p "$dir/steamapps"
  printf '"AppState"\n{\n "appid" "%s"\n "buildid" "%s"\n "LastUpdated" "%s"\n}\n' "$app" "$build" "$(date +%s)" \
    > "$dir/steamapps/appmanifest_$app.acf"
  echo "Success! App '$app' fully installed."
fi
exit 0
