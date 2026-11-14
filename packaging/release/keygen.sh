#!/usr/bin/env bash
# Make HostSpawn's release signing key (Ed25519, with OpenSSL). Run ONCE, by the owner, on their own computer.
#   ./packaging/release/keygen.sh /media/usb/hostspawn-keys
# Writes:
#   <dir>/hostspawn-release.key   PRIVATE: signs releases. Keep it offline (USB stick / password manager), back it up,
#                                 never put it on the dedicated server, GitHub or in chat. Anyone with it can sign
#                                 updates as HostSpawn.
#   <dir>/hostspawn-release.pub   public: copy it to packaging/release/hostspawn-release.pub and commit it; publish it
#                                 (and its fingerprint) on the website so people can check downloads.
set -euo pipefail
DIR="${1:?usage: keygen.sh <folder for the keys>}"
mkdir -p "$DIR"
KEY="$DIR/hostspawn-release.key"; PUB="$DIR/hostspawn-release.pub"
[ -e "$KEY" ] && { echo "refusing: $KEY already exists (a new key would break checks of older releases)"; exit 1; }
( umask 077; openssl genpkey -algorithm ed25519 -out "$KEY" )
chmod 600 "$KEY"
openssl pkey -in "$KEY" -pubout -out "$PUB"
echo "Private key: $KEY (keep offline, back it up)"
echo "Public key:  $PUB"
echo "Fingerprint: $("$(dirname "$0")/fingerprint.sh" "$PUB")"
