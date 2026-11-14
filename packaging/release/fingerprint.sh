#!/usr/bin/env bash
# A short fingerprint of a public key, for comparing with the one on the website: ./fingerprint.sh key.pub
set -euo pipefail
openssl pkey -pubin -in "${1:?usage: fingerprint.sh <key.pub>}" -outform DER | sha256sum | cut -c1-32 | sed 's/.\{8\}/&-/g; s/-$//'
