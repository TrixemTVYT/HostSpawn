#!/usr/bin/env bash
# Build a release into dist/ (or $OUT):
#   HostSpawn-Setup-<version>-linux-x64.run  the installer most people use: one file that opens the HostSpawn Setup
#                                          window (or the text installer with --terminal / no screen)
#   hostspawn-<version>-linux-x64.tar.gz   the same release as an archive (engine, desktop app, Setup, installer)
#   release.json                           version, date and each file's SHA-256 (the app's updater reads it)
#   SHA256SUMS                             checksums of the downloads
#   SHA256SUMS.sig, release.json.sig       Ed25519 signatures, when signed (--sign)
# Inside the archive, MANIFEST.sha256 lists every file's checksum (+ MANIFEST.sha256.sig when signed); the installer
# checks it before installing. Only product files go in: no tests, secrets, settings, accounts, state, logs or servers.
#
# Usage: packaging/build_release.sh [--version X.Y.Z] [--sign <private key>] [--no-app] [--channel stable|beta]
#   version defaults to the VERSION file. --no-app skips building the desktop app (needs Flutter otherwise).
#   The private key comes from packaging/release/keygen.sh and should live offline (e.g. a USB stick).
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(cat VERSION)"; KEY=""; APP=1; CHANNEL=stable
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift ;;
    --sign) KEY="$2"; shift ;;
    --no-app) APP=0 ;;
    --channel) CHANNEL="$2"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "refusing: version must look like 1.2.3 (got $VERSION)"; exit 1; }
[[ "$CHANNEL" =~ ^(stable|beta)$ ]] || { echo "refusing: channel must be stable or beta"; exit 1; }
if [ -n "$KEY" ]; then
  [ -f "$KEY" ] || { echo "refusing: no private key at $KEY"; exit 1; }
  [ "$(stat -c %a "$KEY")" = 600 ] || { echo "refusing: $KEY must be private (chmod 600)"; exit 1; }
fi
NAME="hostspawn-$VERSION"
ARCHIVE="$NAME-linux-x64.tar.gz"
OUT="${OUT:-dist}"; STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT" "$STAGE/$NAME/config" "$STAGE/$NAME/docs" "$STAGE/$NAME/backend" "$STAGE/$NAME/frontend" "$STAGE/$NAME/packaging"
cp -r backend/manager backend/games backend/manager_app.py "$STAGE/$NAME/backend/"   # no tests
cp -r frontend/web "$STAGE/$NAME/frontend/"
cp -r packaging/desktop packaging/installer packaging/release "$STAGE/$NAME/packaging/"
rm -f "$STAGE/$NAME/packaging/release/"*.key   # never a private key, even by mistake
cp manager.sh README.md VERSION LICENSE NOTICE TRADEMARKS.md "$STAGE/$NAME/"
echo "$VERSION" > "$STAGE/$NAME/VERSION"
cp config/servers.example.toml config/settings.example.toml config/secrets.env.example "$STAGE/$NAME/config/"
cp docs/TERMS_DRAFT.md docs/DEVELOPERS.md docs/BUILDING.md "$STAGE/$NAME/docs/"
if [ "$APP" = 1 ]; then   # the HostSpawn desktop app (Flutter, Linux)
  command -v flutter >/dev/null || PATH="$HOME/development/flutter/bin:$PATH"
  command -v flutter >/dev/null || { echo "refusing: Flutter isn't installed (or use --no-app)"; exit 1; }
  (cd frontend/app && flutter build linux --release >/dev/null)
  cp -r frontend/app/build/linux/x64/release/bundle "$STAGE/$NAME/app"
  (cd frontend/setup && flutter build linux --release >/dev/null)   # the HostSpawn Setup window
  cp -r frontend/setup/build/linux/x64/release/bundle "$STAGE/$NAME/setup"
fi
find "$STAGE/$NAME" \( -name "__pycache__" -o -name "*.bak*" \) -prune -exec rm -rf {} +
chmod +x "$STAGE/$NAME"/packaging/installer/*.sh "$STAGE/$NAME"/packaging/desktop/*.sh "$STAGE/$NAME"/packaging/release/*.sh "$STAGE/$NAME/manager.sh"
# never ship anything secret or personal (belt and braces: these shouldn't be in the copied folders anyway)
if find "$STAGE/$NAME" \( -name "secrets.env" -o -name "accounts.json" -o -name "servers.toml" -o -name "settings.toml" \
    -o -name "instance.env" -o -name "*.key" -o -name "sessions.json" -o -name "players.db" \) | grep -q .; then
  echo "refusing: a private file got into the release"; exit 1
fi
sign() { openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$1" -out "$1.sig"; }
( cd "$STAGE/$NAME" && find . -type f ! -name 'MANIFEST.sha256*' -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256 )
[ -n "$KEY" ] && sign "$STAGE/$NAME/MANIFEST.sha256"
rm -f "$OUT/SHA256SUMS" "$OUT/SHA256SUMS.sig" "$OUT/release.json" "$OUT/release.json.sig"
tar -C "$STAGE" -czf "$OUT/$ARCHIVE" "$NAME"
# The Setup file: a short script, then the archive. It unpacks itself to a temporary folder and runs Setup.
SETUP="HostSpawn-Setup-$VERSION-linux-x64.run"
sed "s/__VERSION__/$VERSION/g" packaging/installer/setup-run.sh > "$STAGE/head.sh"
sed -i "s/__SKIP__/$(( $(wc -l < "$STAGE/head.sh") + 1 ))/" "$STAGE/head.sh"
cat "$STAGE/head.sh" "$OUT/$ARCHIVE" > "$OUT/$SETUP"; chmod +x "$OUT/$SETUP"
( cd "$OUT" && sha256sum "$SETUP" "$ARCHIVE" > SHA256SUMS )
python3 -I - "$OUT" "$VERSION" "$CHANNEL" "$SETUP" "$ARCHIVE" <<'PY'
import datetime, hashlib, json, os, sys
out, version, channel, *names = sys.argv[1:]
files = [{"name": n, "kind": "setup" if n.endswith(".run") else "archive", "os": "linux", "arch": "x64",
          "sha256": hashlib.sha256(open(os.path.join(out, n), "rb").read()).hexdigest(),
          "size": os.path.getsize(os.path.join(out, n))} for n in names]
json.dump({"name": "HostSpawn", "version": version, "channel": channel,
           "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "notes": "docs/CHANGELOG.md", "files": files}, open(os.path.join(out, "release.json"), "w"), indent=2)
PY
if [ -n "$KEY" ]; then sign "$OUT/SHA256SUMS"; sign "$OUT/release.json"; fi
echo "built $OUT/$SETUP and $OUT/$ARCHIVE ($(du -h "$OUT/$ARCHIVE" | cut -f1)), $(wc -l < "$STAGE/$NAME/MANIFEST.sha256") files, app and Setup window: $([ "$APP" = 1 ] && echo yes || echo no)"
[ -n "$KEY" ] && echo "signed with key $(packaging/release/fingerprint.sh <(openssl pkey -in "$KEY" -pubout))" || echo "NOT signed (use --sign <private key> for a real release)"
cat "$OUT/SHA256SUMS"
