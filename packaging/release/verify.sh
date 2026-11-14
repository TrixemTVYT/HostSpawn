#!/usr/bin/env bash
# Check a HostSpawn download is really ours and undamaged, before unpacking it.
#   ./verify.sh <folder with the downloaded files> [public key]
# The folder holds SHA256SUMS, SHA256SUMS.sig and the .tar.gz files. The public key defaults to the one next to this
# script; compare its fingerprint with the one on the HostSpawn website.
set -euo pipefail
DIR="${1:?usage: verify.sh <download folder> [public key]}"
PUB="${2:-$(dirname "$0")/hostspawn-release.pub}"
[ -f "$PUB" ] || { echo "✗ No public key at $PUB: get hostspawn-release.pub from the HostSpawn website."; exit 1; }
[ -f "$DIR/SHA256SUMS" ] && [ -f "$DIR/SHA256SUMS.sig" ] || { echo "✗ SHA256SUMS or SHA256SUMS.sig is missing."; exit 1; }
echo "Key fingerprint: $("$(dirname "$0")/fingerprint.sh" "$PUB")  (compare with the website)"
if ! openssl pkeyutl -verify -pubin -inkey "$PUB" -rawin -in "$DIR/SHA256SUMS" -sigfile "$DIR/SHA256SUMS.sig" >/dev/null 2>&1; then
  echo "✗ The signature doesn't match: this download was NOT signed by HostSpawn, or was changed. Don't install it."
  exit 1
fi
echo "✓ Signed by HostSpawn"
if ! (cd "$DIR" && sha256sum --quiet -c SHA256SUMS); then
  echo "✗ A file doesn't match its checksum: download it again."
  exit 1
fi
echo "✓ Every file matches its checksum: safe to unpack and install"
