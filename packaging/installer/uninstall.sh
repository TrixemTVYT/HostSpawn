#!/usr/bin/env bash
# HostSpawn uninstaller. By default it keeps your data (settings, accounts, servers, backups) so you can reinstall
# without losing anything. --remove-data deletes everything, after you type a confirmation.
# Options: --prefix DIR (default ~/.local/share/hostspawn)   --remove-data   --yes (automation; still needs
#          HOSTSPAWN_CONFIRM="delete everything" for --remove-data)
set -euo pipefail
PREFIX="${HOME}/.local/share/hostspawn"; REMOVE_DATA=0; YES=0
SYSTEMCTL="${HOSTSPAWN_SYSTEMCTL:-systemctl}"
while [ $# -gt 0 ]; do
  case "$1" in --prefix) PREFIX="$2"; shift ;; --remove-data) REMOVE_DATA=1 ;; --yes) YES=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;; *) echo "Unknown option: $1"; exit 2 ;; esac
  shift
done
[ -f "$PREFIX/.hostspawn-install" ] || { echo "No HostSpawn install found at $PREFIX (nothing removed)."; exit 1; }
echo "Uninstalling HostSpawn from $PREFIX"
if [ "$YES" = 0 ]; then read -r -p "Continue? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || { echo "Cancelled."; exit 0; }; fi

# stop the dashboard and anything it started
if command -v "$SYSTEMCTL" >/dev/null && "$SYSTEMCTL" --user show-environment >/dev/null 2>&1; then
  "$SYSTEMCTL" --user disable --now hostspawn.service >/dev/null 2>&1 || true
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hostspawn.service"
  "$SYSTEMCTL" --user daemon-reload >/dev/null 2>&1 || true
fi
python3 - "$PREFIX" <<'PY'
# Stop this install's own programs only: the HostSpawn app (manager_app.py inside it) and game servers or
# SteamCMD running from its servers/ folder. Never this uninstaller or anything else.
import os, signal, sys
base = os.path.realpath(sys.argv[1]) + "/"
skip = {os.getpid(), os.getppid()}
for d in os.listdir("/proc"):
    if not d.isdigit() or int(d) in skip:
        continue
    try:
        argv = open(f"/proc/{d}/cmdline", "rb").read().split(b"\0")
        exe = os.path.realpath(os.readlink(f"/proc/{d}/exe"))
        cwd = os.path.realpath(os.readlink(f"/proc/{d}/cwd")) + "/"
    except OSError:
        continue
    a = [x.decode(errors="ignore") for x in argv if x]
    # the app, started with its full path (service, icon) or from inside the install ("backend/manager_app.py serve");
    # manager_app.py at the top is where it was before 2026-11-07
    app = any(x in (base + "backend/manager_app.py", base + "manager_app.py")
              or (x in ("backend/manager_app.py", "manager_app.py") and cwd == base) for x in a)
    game = exe.startswith(base + "servers/") or any(x.startswith(base + "servers/") for x in a[:2])
    if app or game:
        try:
            os.kill(int(d), signal.SIGTERM)
        except OSError:
            pass
PY
for s in $(tmux ls -F '#S' 2>/dev/null | grep '^manager-' || true); do
  # only sessions whose server lives inside this install
  pane_path=$(tmux display-message -p -t "$s" '#{pane_current_path}' 2>/dev/null || true)
  case "$pane_path" in "$PREFIX"/*) tmux kill-session -t "$s" ;; esac
done
rm -f "$HOME/.local/share/applications/hostspawn.desktop" "$HOME/Desktop/hostspawn.desktop"

if [ "$REMOVE_DATA" = 1 ]; then
  if [ "$YES" = 1 ]; then CONF="${HOSTSPAWN_CONFIRM:-}"; else read -r -p 'This deletes your servers, backups and accounts. Type "delete everything": ' CONF; fi
  [ "$CONF" = "delete everything" ] || { echo "Not confirmed: your data was kept in $PREFIX."; REMOVE_DATA=0; }
fi
if [ "$REMOVE_DATA" = 1 ]; then
  rm -rf "$PREFIX"
  echo "HostSpawn and all its data were removed."
else
  for d in backend frontend packaging docs app manager web games desktop installer; do rm -rf "$PREFIX/$d"; done
  rm -f "$PREFIX/LICENSE" "$PREFIX/NOTICE" "$PREFIX/TRADEMARKS.md"
  rm -f "$PREFIX/manager_app.py" "$PREFIX/manager.sh" "$PREFIX/README.md" "$PREFIX/VERSION"
  printf 'HostSpawn was uninstalled; your data is kept here. Reinstall to use it again.\n' > "$PREFIX/KEPT-DATA.txt"
  echo "HostSpawn was removed. Your data (settings, accounts, servers, backups) is kept in $PREFIX."
fi
