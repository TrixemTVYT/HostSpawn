#!/usr/bin/env bash
# HostSpawn installer (Linux). Run it from an unpacked release folder:  ./packaging/installer/install.sh
#
# What it does, in order (nothing is changed until you've accepted the terms):
#   1. checks this machine (Linux, Python 3.11+, disk space, a free port)
#   2. checks the release files against MANIFEST.sha256 and its HostSpawn signature (refuses a damaged, altered or
#      unsigned-by-us download; an upgrade must be signed by the same key as the installed copy)
#   3. shows the terms and asks you to accept them
#   4. checks dependencies and offers to install only the missing ones (you type your own sudo password)
#   5. installs HostSpawn for your user (no root needed): ~/.local/share/hostspawn
#   6. starts it automatically (systemd user service), adds the HostSpawn app to the desktop and app menu, opens it
# Upgrading keeps your settings, accounts, servers and backups.
#
# Options: --prefix DIR   install somewhere else         --port N      dashboard port (default: first free from 8100)
#          --no-deps      don't check/install packages   --no-service  don't set up autostart
#          --no-desktop   no icon, don't open the app    --yes         accept terms and prompts (automation only)
#          --check        only check this machine, the download and the packages, then stop (changes nothing)
#          --sudo CMD     how to ask for the admin password for packages (default sudo; HostSpawn Setup uses pkexec)
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
PREFIX="${HOME}/.local/share/hostspawn"
PORT=""; DO_DEPS=1; DO_SERVICE=1; DO_DESKTOP=1; YES=0; CHECK=0; SUDO=sudo
SYSTEMCTL="${HOSTSPAWN_SYSTEMCTL:-systemctl}"   # tests point this at a fake
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    --no-deps) DO_DEPS=0 ;; --no-service) DO_SERVICE=0 ;; --no-desktop) DO_DESKTOP=0 ;;
    --yes) YES=1 ;;
    --check) CHECK=1 ;;
    --sudo) SUDO="$2"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

if [ -t 1 ]; then B=$'\e[1m'; D=$'\e[2m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; C=$'\e[36m'; N=$'\e[0m'; else B= D= G= Y= R= C= N=; fi
step() { printf "\n${C}${B}[%s/6]${N} ${B}%s${N}\n" "$1" "$2"; }
ok() { printf "  ${G}✓${N} %s\n" "$*"; }
warn() { printf "  ${Y}!${N} %s\n" "$*"; }
fail() { printf "  ${R}✗ %s${N}\n" "$*"; exit 1; }
ask() { [ "$YES" = 1 ] && return 0; local a; read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo dev)"
printf "\n${B}HostSpawn installer${N} ${D}(version %s)${N}\n" "$VERSION"
printf "${D}Sets up and runs game servers on this machine. Installs to %s${N}\n" "$PREFIX"

# 1. this machine
step 1 "Checking this machine"
[ "$(uname -s)" = Linux ] || fail "This installer is for Linux. (Windows comes with the Windows version.)"
if [ -r /etc/os-release ]; then . /etc/os-release; ok "System: ${PRETTY_NAME:-Linux}"
  case "${ID:-}${ID_LIKE:-}" in *debian*|*ubuntu*) ;; *) warn "Tested on Ubuntu and Debian; other systems may need packages installed by hand." ;; esac
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null || fail "Python 3.11 or newer is needed (python3 --version)."
ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
mkdir -p "$(dirname "$PREFIX")"
FREE_GB=$(df -Pk "$(dirname "$PREFIX")" | awk 'NR==2 {print int($4/1048576)}')
[ "$FREE_GB" -ge 1 ] || fail "Not enough disk space (less than 1 GB free)."
ok "Disk: ${FREE_GB} GB free (game servers need 8–15 GB each)"
port_free() { python3 -c "import socket,sys; s=socket.socket(); s.bind(('127.0.0.1', int(sys.argv[1]))); s.close()" "$1" 2>/dev/null; }
if [ -z "$PORT" ] && [ -f "$PREFIX/config/instance.env" ]; then PORT=$(sed -n 's/^PORT=//p' "$PREFIX/config/instance.env"); fi
if [ -z "$PORT" ]; then PORT=8100; while ! port_free "$PORT"; do PORT=$((PORT + 1)); [ "$PORT" -gt 8199 ] && fail "No free port between 8100 and 8199."; done; fi
ok "Dashboard port: $PORT (only reachable from this machine)"

# 2. integrity
step 2 "Checking the download"
[ -f "$SRC/MANIFEST.sha256" ] || fail "MANIFEST.sha256 is missing: download the release again."
if ! (cd "$SRC" && sha256sum --quiet -c MANIFEST.sha256 >/dev/null 2>&1); then
  (cd "$SRC" && sha256sum --quiet -c MANIFEST.sha256 2>&1 | head -5 | sed 's/^/    /') || true
  fail "Some files don't match their checksums: the download is damaged or was changed. Not installing."
fi
ok "All $(wc -l < "$SRC/MANIFEST.sha256") files match their checksums"
# The signature. An upgrade trusts the key of the copy already installed (pinned): a new key can't sneak in.
PINNED="$PREFIX/packaging/release/hostspawn-release.pub"; BUNDLED="$SRC/packaging/release/hostspawn-release.pub"
KEYFILE=""; [ -f "$PINNED" ] && KEYFILE="$PINNED" || { [ -f "$BUNDLED" ] && KEYFILE="$BUNDLED"; }
FPR() { "$SRC/packaging/release/fingerprint.sh" "$1"; }
if [ -f "$SRC/MANIFEST.sha256.sig" ] && [ -n "$KEYFILE" ]; then
  openssl pkeyutl -verify -pubin -inkey "$KEYFILE" -rawin -in "$SRC/MANIFEST.sha256" -sigfile "$SRC/MANIFEST.sha256.sig" >/dev/null 2>&1 \
    || fail "The signature doesn't match HostSpawn's key: this isn't an official release, or it was changed. Not installing."
  ok "Signed by HostSpawn (key $(FPR "$KEYFILE"))"
  [ "$KEYFILE" = "$BUNDLED" ] && echo "  ${D}First install: compare that key fingerprint with the one on the HostSpawn website.${N}"
elif [ -f "$PINNED" ]; then
  fail "This release isn't signed, but your installed HostSpawn only accepts signed updates. Not installing."
else
  warn "This release isn't signed (the file checksums above were still checked): download HostSpawn only from github.com/TrixemTVYT/HostSpawn."
fi

# 3. terms
if [ "$CHECK" = 1 ]; then step 3 "Terms of use"; ok "Shown before installing"; else
step 3 "Terms of use"
echo "  Please read them (also in docs/TERMS_DRAFT.md):"
sed 's/^/    /' "$SRC/docs/TERMS_DRAFT.md" | sed -n '1,40p'
if [ "$YES" = 1 ]; then ok "Terms accepted (--yes)"; else
  read -r -p "  Type ${B}accept${N} to accept the terms and continue: " A
  [ "$A" = accept ] || fail "Not accepted, nothing was installed."
  ok "Terms accepted"
fi
fi
TERMS_VER=$(sed -n 's/^Version: //p' "$SRC/docs/TERMS_DRAFT.md" | head -1)

# 4. dependencies
step 4 "Dependencies"
NEED=(tmux lib32gcc-s1); WHY=("runs game servers in the background" "SteamCMD (the game downloader) is 32-bit")
if [ -x "$SRC/app/hostspawn" ]; then NEED+=(libgtk-3-0); WHY+=("the HostSpawn app window")
else NEED+=(gir1.2-webkit2-4.1); WHY+=("the app window"); fi
MISSING=()
if [ "$DO_DEPS" = 0 ]; then warn "Skipped (--no-deps). Check them later on the Dependencies page."
elif ! command -v dpkg-query >/dev/null; then warn "Can't check packages on this system: install tmux, 32-bit libc/gcc libraries and WebKitGTK 4.1 yourself."
else
  for i in "${!NEED[@]}"; do
    if dpkg-query -W -f='${Status}' "${NEED[$i]}" 2>/dev/null | grep -q "install ok installed"; then ok "${NEED[$i]} (${WHY[$i]})"
    else warn "${NEED[$i]} is missing (${WHY[$i]})"; MISSING+=("${NEED[$i]}"); fi
  done
  if [ ${#MISSING[@]} -gt 0 ] && [ "$CHECK" = 1 ]; then
    echo "  Missing: ${MISSING[*]}"
  elif [ ${#MISSING[@]} -gt 0 ]; then
    echo "  To install them, this runs:  $SUDO apt-get install -y ${MISSING[*]}"
    if ask "Install the missing packages now? (asks for your password)"; then
      $SUDO apt-get install -y "${MISSING[@]}" && ok "Installed: ${MISSING[*]}" || warn "Install failed: you can retry from the Dependencies page."
    else warn "Not installed. HostSpawn will show them on its Dependencies page."; fi
  fi
fi

[ "$CHECK" = 1 ] && { printf "\n${G}${B}Check finished: nothing was changed.${N}\n"; exit 0; }

# 5. install
step 5 "Installing"
UPGRADE=0; [ -f "$PREFIX/.hostspawn-install" ] && UPGRADE=1
if [ -e "$PREFIX" ] && [ "$UPGRADE" = 0 ] && [ -n "$(ls -A "$PREFIX" 2>/dev/null)" ]; then
  fail "$PREFIX already has files that aren't a HostSpawn install. Pick another --prefix."
fi
mkdir -p "$PREFIX"
# code is replaced; config/, state/, logs/, servers/, backups/ are kept on upgrade
rm -rf "$PREFIX/manager_app.py"; for d in manager web games desktop installer; do rm -rf "$PREFIX/$d"; done   # layout before 2026-11-07
for d in backend frontend packaging docs; do rm -rf "$PREFIX/$d"; cp -r "$SRC/$d" "$PREFIX/$d"; done
if [ -d "$SRC/app" ]; then rm -rf "$PREFIX/app"; cp -r "$SRC/app" "$PREFIX/app"; chmod +x "$PREFIX/app/hostspawn"; fi
cp "$SRC/manager.sh" "$SRC/README.md" "$SRC/VERSION" "$PREFIX/"
mkdir -p "$PREFIX/config" "$PREFIX/state" "$PREFIX/logs"
cp "$SRC"/config/*.example* "$PREFIX/config/"
[ -f "$PREFIX/config/servers.toml" ] || printf '# Servers set up by hand (git-ignored). Servers HostSpawn creates are in config/servers.d/.\n' > "$PREFIX/config/servers.toml"
printf 'MODE=live\nPORT=%s\n' "$PORT" > "$PREFIX/config/instance.env"
chmod 700 "$PREFIX/config" "$PREFIX/state"
(cd "$PREFIX" && python3 -I backend/manager_app.py init-secrets >/dev/null)
if [ -n "$TERMS_VER" ] && ! grep -q '^\[terms\]' "$PREFIX/config/settings.toml" 2>/dev/null; then
  printf '\n[terms]\naccepted_version = "%s"\naccepted_by = "installer"\naccepted_at = "%s"\n' "$TERMS_VER" "$(date '+%Y-%m-%d %H:%M:%S')" >> "$PREFIX/config/settings.toml"
fi
printf 'HostSpawn %s installed %s\n' "$VERSION" "$(date -u '+%F %T UTC')" > "$PREFIX/.hostspawn-install"
chmod +x "$PREFIX/manager.sh" "$PREFIX/packaging/desktop/"*.sh "$PREFIX/packaging/installer/"*.sh 2>/dev/null || true
[ "$UPGRADE" = 1 ] && ok "Upgraded (your settings, accounts, servers and backups were kept)" || ok "Installed to $PREFIX"

# 6. start
step 6 "Starting"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if [ "$DO_SERVICE" = 1 ] && command -v "$SYSTEMCTL" >/dev/null && "$SYSTEMCTL" --user show-environment >/dev/null 2>&1; then
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/hostspawn.service" <<UNIT
[Unit]
Description=HostSpawn game server manager
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$PREFIX
ExecStart=/usr/bin/env python3 -I $PREFIX/backend/manager_app.py serve
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  "$SYSTEMCTL" --user daemon-reload && "$SYSTEMCTL" --user enable --now hostspawn.service >/dev/null 2>&1 && ok "Starts automatically (systemd user service: hostspawn)" \
    || warn "Couldn't start the service: run  systemctl --user status hostspawn"
  echo "  ${D}To keep it running when nobody is logged in: sudo loginctl enable-linger $USER${N}"
elif [ "$DO_SERVICE" = 1 ]; then
  warn "No systemd user session here: start it with  $PREFIX/manager.sh keepalive  (or add that to cron)."
else warn "Autostart skipped (--no-service). Start it with: $PREFIX/manager.sh serve"; fi
if [ -x "$PREFIX/app/hostspawn" ]; then OPEN="env HOSTSPAWN_AGENT=http://127.0.0.1:$PORT $PREFIX/app/hostspawn"
else OPEN="$PREFIX/packaging/desktop/launch.sh $PORT"; fi
if [ "$DO_DESKTOP" = 1 ]; then
  for dir in "$HOME/.local/share/applications" "$HOME/Desktop"; do
    [ "$dir" = "$HOME/Desktop" ] && [ ! -d "$dir" ] && continue
    mkdir -p "$dir"
    printf '%s\n' "[Desktop Entry]" "Version=1.0" "Type=Application" "Name=HostSpawn" "Comment=Set up and run your game servers" \
      "Exec=$OPEN" "Icon=$PREFIX/packaging/desktop/icon.svg" "Terminal=false" "Categories=System;" "StartupWMClass=hostspawn" \
      > "$dir/hostspawn.desktop"
    chmod +x "$dir/hostspawn.desktop"; gio set "$dir/hostspawn.desktop" metadata::trusted true 2>/dev/null || true
  done
  ok "Added HostSpawn to the app menu and desktop"
fi

printf "\n${G}${B}HostSpawn is installed.${N}\n"
echo "OPEN: $OPEN"   # HostSpawn Setup's "Open HostSpawn" button runs this
printf "  Open it from the app menu, or go to ${B}http://127.0.0.1:%s${N} on this machine.\n" "$PORT"
printf "  The first time, a setup wizard creates your owner account.\n"
printf "  ${D}Uninstall: %s/packaging/installer/uninstall.sh${N}\n\n" "$PREFIX"
if [ "$DO_DESKTOP" = 1 ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ "$YES" = 0 ]; then
  (nohup $OPEN >/dev/null 2>&1 &)
fi
