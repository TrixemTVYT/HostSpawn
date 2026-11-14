#!/bin/sh
# HostSpawn Setup __VERSION__ (Linux, 64-bit). To run it: double-click it (if asked, choose Run), or in a terminal:
#   chmod +x HostSpawn-Setup-*.run && ./HostSpawn-Setup-*.run
# It unpacks HostSpawn into a temporary folder and opens the HostSpawn Setup window. Nothing is installed until you
# accept the terms there, and every file is checked against HostSpawn's signature first.
#   --terminal   use the text installer instead of the window (also used when there's no screen)
#   anything else after the file name goes to the installer (try --terminal --help)
T=$(mktemp -d "${TMPDIR:-/tmp}/hostspawn-setup.XXXXXX") || exit 1
trap 'rm -rf "$T"' EXIT INT TERM
if ! tail -n +__SKIP__ "$0" | tar -xz -C "$T" 2>/dev/null; then
  echo "This Setup file is damaged or incomplete: please download it again."; exit 1
fi
REL="$T/hostspawn-__VERSION__"
if [ "${1:-}" != "--terminal" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ -x "$REL/setup/hostspawn-setup" ]; then
  HOSTSPAWN_RELEASE_DIR="$REL" "$REL/setup/hostspawn-setup"
else
  [ "${1:-}" = "--terminal" ] && shift
  bash "$REL/packaging/installer/install.sh" "$@"
fi
exit $?
