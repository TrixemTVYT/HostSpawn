#!/usr/bin/env bash
# Installer + uninstaller test in a sandbox: a fake HOME, and fake systemctl/sudo/dpkg-query/gio first on PATH.
# Nothing touches the real system: the script refuses to run unless the fakes are the ones that would be used.
# Usage: backend/tests/installer_sandbox.sh [sandbox_dir]
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SB="${1:-$(mktemp -d)}"; rm -rf "$SB"; mkdir -p "$SB/home/Desktop" "$SB/bin" "$SB/rel"
PASS=0; FAIL=0
check() { if eval "$2"; then echo "PASS  $1"; PASS=$((PASS+1)); else echo "FAIL  $1"; FAIL=$((FAIL+1)); fi; }

# fakes
cat > "$SB/bin/systemctl" <<EOF
#!/usr/bin/env bash
echo "\$@" >> "$SB/systemctl.calls"
exit 0
EOF
cat > "$SB/bin/sudo" <<EOF
#!/usr/bin/env bash
echo "\$@" >> "$SB/sudo.calls"
exit 0
EOF
cat > "$SB/bin/dpkg-query" <<EOF
#!/usr/bin/env bash
case "\${@: -1}" in tmux) echo "install ok installed" ;; *) exit 1 ;; esac
EOF
printf '#!/usr/bin/env bash\nexit 0\n' > "$SB/bin/gio"
chmod +x "$SB/bin/"*
export PATH="$SB/bin:$PATH" HOME="$SB/home" HOSTSPAWN_SYSTEMCTL="$SB/bin/systemctl" XDG_CONFIG_HOME="$SB/home/.config"
unset DISPLAY WAYLAND_DISPLAY
[ "$(command -v sudo)" = "$SB/bin/sudo" ] && [ "$(command -v systemctl)" = "$SB/bin/systemctl" ] || { echo "fakes not first on PATH: refusing"; exit 1; }

# a throwaway signing key (the real one stays offline with the owner), then build, sign and unpack a release
"$REPO/packaging/release/keygen.sh" "$SB/keys" > /dev/null
"$REPO/packaging/release/keygen.sh" "$SB/otherkeys" > /dev/null
check "private key is private (600)" "[ \$(stat -c %a '$SB/keys/hostspawn-release.key') = 600 ]"
check "keygen won't overwrite a key" "! '$REPO/packaging/release/keygen.sh' '$SB/keys' > /dev/null 2>&1"
(cd "$REPO" && OUT="$SB/dist" packaging/build_release.sh --version 0.9.0-test --sign "$SB/keys/hostspawn-release.key" > "$SB/build.out") \
  || { echo "build failed"; cat "$SB/build.out"; exit 1; }
TAR="$SB/dist/hostspawn-0.9.0-test-linux-x64.tar.gz"
check "release archive, release.json and signatures made" "[ -f '$TAR' ] && [ -f '$SB/dist/release.json' ] && [ -f '$SB/dist/SHA256SUMS.sig' ] && [ -f '$SB/dist/release.json.sig' ]"
check "release.json names the version and the archive's checksum" "python3 -c \"import json; d=json.load(open('$SB/dist/release.json')); assert d['version']=='0.9.0-test' and [f for f in d['files'] if f['kind']=='archive'][0]['sha256']=='\$(sha256sum '$TAR' | cut -d' ' -f1)'\""
check "verify.sh accepts the signed download" "'$REPO/packaging/release/verify.sh' '$SB/dist' '$SB/keys/hostspawn-release.pub' > '$SB/verify.out' && grep -q 'Signed by HostSpawn' '$SB/verify.out'"
check "verify.sh refuses another key" "! '$REPO/packaging/release/verify.sh' '$SB/dist' '$SB/otherkeys/hostspawn-release.pub' > /dev/null 2>&1"
cp -r "$SB/dist" "$SB/dist-bad"; echo x >> "$SB/dist-bad/hostspawn-0.9.0-test-linux-x64.tar.gz"
check "verify.sh refuses a changed archive" "! '$REPO/packaging/release/verify.sh' '$SB/dist-bad' '$SB/keys/hostspawn-release.pub' > /dev/null 2>&1"
cp "$SB/dist/SHA256SUMS" "$SB/dist-bad/SHA256SUMS"; sed -i 's/^./0/' "$SB/dist-bad/SHA256SUMS"
check "verify.sh refuses changed checksums" "! '$REPO/packaging/release/verify.sh' '$SB/dist-bad' '$SB/keys/hostspawn-release.pub' > /dev/null 2>&1"
RUN="$SB/dist/HostSpawn-Setup-0.9.0-test-linux-x64.run"
check "Setup file made, runnable and in SHA256SUMS" "[ -x '$RUN' ] && grep -q 'HostSpawn-Setup-0.9.0-test-linux-x64.run' '$SB/dist/SHA256SUMS'"
"$RUN" --terminal --check --prefix "$SB/runcheck" > "$SB/runcheck.out" 2>&1; RC=$?
check "Setup file unpacks itself and checks (changes nothing)" "[ $RC = 0 ] && grep -q 'Check finished: nothing was changed' '$SB/runcheck.out' && [ ! -e '$SB/runcheck/backend' ]"
"$RUN" --check --prefix "$SB/runcheck" > "$SB/runnoscreen.out" 2>&1
check "with no screen, the Setup file uses the text installer" "grep -q 'Checking this machine' '$SB/runnoscreen.out'"
check "Setup file cleans up its temporary folder" "! ls -d \${TMPDIR:-/tmp}/hostspawn-setup.* 2>/dev/null | grep -q ."
head -c 2000000 "$RUN" > "$SB/cut.run"; chmod +x "$SB/cut.run"
"$SB/cut.run" --terminal --check > "$SB/cut.out" 2>&1
check "a cut-off Setup file says it's damaged" "grep -q 'damaged or incomplete' '$SB/cut.out'"
tar -C "$SB/rel" -xzf "$TAR"; REL=$(ls -d "$SB/rel"/hostspawn-*)
check "the app, the Setup window, the signed manifest and no private key are in the release" "[ -x '$REL/setup/hostspawn-setup' ] && [ -x '$REL/app/hostspawn' ] && [ -f '$REL/MANIFEST.sha256.sig' ] && ! find '$REL' -name '*.key' | grep -q ."
cp "$SB/keys/hostspawn-release.pub" "$REL/packaging/release/"   # as the real release will carry it
PREFIX="$SB/home/.local/share/hostspawn"

# 1. a tampered release is refused
cp -r "$REL" "$SB/tampered"; echo "# evil" >> "$SB/tampered/backend/manager/web.py"
"$SB/tampered/packaging/installer/install.sh" --yes > "$SB/tamper.out" 2>&1
check "tampered release refused" "grep -q \"don't match their checksums\" '$SB/tamper.out' && [ ! -e '$PREFIX/backend/manager_app.py' ]"

# 2. refusing the terms installs nothing
echo "no" | "$REL/packaging/installer/install.sh" --no-deps > "$SB/noterms.out" 2>&1
check "terms not accepted: nothing installed" "grep -q 'Not accepted' '$SB/noterms.out' && [ ! -e '$PREFIX/backend/manager_app.py' ]"

# 3. fresh install
"$REL/packaging/installer/install.sh" --yes > "$SB/install.out" 2>&1; RC=$?
check "install exits OK" "[ $RC = 0 ]"
check "files installed with marker" "[ -f '$PREFIX/backend/manager_app.py' ] && [ -f '$PREFIX/.hostspawn-install' ] && [ -f '$PREFIX/frontend/web/index.html' ]"
check "missing packages offered via sudo (fake)" "grep -q 'apt-get install -y lib32gcc-s1 libgtk-3-0' '$SB/sudo.calls'"
check "signature checked on install" "grep -q 'Signed by HostSpawn' '$SB/install.out'"
check "the HostSpawn app is installed" "[ -x '$PREFIX/app/hostspawn' ]"
check "installed package not re-installed" "! grep -q 'tmux' '$SB/sudo.calls'"
PORT=$(sed -n 's/^PORT=//p' "$PREFIX/config/instance.env")
check "free port chosen ($PORT)" "python3 -c \"import socket; s=socket.socket(); s.bind(('127.0.0.1', $PORT))\""
check "live mode" "grep -q '^MODE=live' '$PREFIX/config/instance.env'"
check "secrets private (600) and config folder 700" "[ \$(stat -c %a '$PREFIX/config/secrets.env') = 600 ] && [ \$(stat -c %a '$PREFIX/config') = 700 ]"
check "terms acceptance recorded" "grep -q 'accepted_by = \"installer\"' '$PREFIX/config/settings.toml'"
check "systemd user service written and enabled" "grep -q 'ExecStart=/usr/bin/env python3 -I $PREFIX/backend/manager_app.py serve' '$SB/home/.config/systemd/user/hostspawn.service' && grep -q 'enable --now hostspawn.service' '$SB/systemctl.calls'"
check "app menu and desktop icons open the app on its port" "[ -x '$SB/home/.local/share/applications/hostspawn.desktop' ] && grep -q 'Exec=env HOSTSPAWN_AGENT=http://127.0.0.1:$PORT $PREFIX/app/hostspawn' '$SB/home/Desktop/hostspawn.desktop'"
check "no tests or private files shipped" "[ ! -e '$PREFIX/tests' ] && [ ! -e '$PREFIX/config/accounts.json' ]"

# 4. the installed app really runs and shows the setup wizard
(cd "$PREFIX" && nohup python3 -I backend/manager_app.py serve > "$SB/app.out" 2>&1 &)
for _ in $(seq 1 30); do curl -s "http://127.0.0.1:$PORT/api/setup/status" > "$SB/status.json" 2>/dev/null && break; sleep 0.3; done
check "installed app answers, setup wizard ready" "grep -q '\"setup_needed\": true' '$SB/status.json'"
check "installed app is reachable only on 127.0.0.1" "ss -ltn | grep -q '127.0.0.1:$PORT '"

# 5. upgrade keeps data
echo '{"users": {"kept": {"hash": "x", "role": "admin"}}}' > "$PREFIX/config/accounts.json"
mkdir -p "$PREFIX/servers/test-1" && echo keep > "$PREFIX/servers/test-1/save.dat"
"$REL/packaging/installer/install.sh" --yes --no-deps > "$SB/upgrade.out" 2>&1
check "upgrade keeps accounts and servers" "grep -q 'Upgraded' '$SB/upgrade.out' && grep -q kept '$PREFIX/config/accounts.json' && [ -f '$PREFIX/servers/test-1/save.dat' ]"
check "upgrade keeps the same port" "grep -q '^PORT=$PORT' '$PREFIX/config/instance.env'"

# 5b. an upgrade signed by another key, or not signed, is refused (the installed key is pinned)
cp -r "$REL" "$SB/otherkey"; openssl pkeyutl -sign -inkey "$SB/otherkeys/hostspawn-release.key" -rawin \
  -in "$SB/otherkey/MANIFEST.sha256" -out "$SB/otherkey/MANIFEST.sha256.sig"
cp "$SB/otherkeys/hostspawn-release.pub" "$SB/otherkey/packaging/release/"
"$SB/otherkey/packaging/installer/install.sh" --yes --no-deps > "$SB/otherkey.out" 2>&1
check "upgrade signed by another key refused" "grep -q \"signature doesn't match\" '$SB/otherkey.out'"
cp -r "$REL" "$SB/unsigned"; rm "$SB/unsigned/MANIFEST.sha256.sig"
"$SB/unsigned/packaging/installer/install.sh" --yes --no-deps > "$SB/unsigned.out" 2>&1
check "unsigned upgrade refused once a signed copy is installed" "grep -q 'only accepts signed updates' '$SB/unsigned.out'"

# 6. refuses a folder that isn't a HostSpawn install
mkdir -p "$SB/other" && echo mine > "$SB/other/file.txt"
"$REL/packaging/installer/install.sh" --yes --no-deps --prefix "$SB/other" > "$SB/other.out" 2>&1
check "won't install over someone else's folder" "grep -q \"aren't a HostSpawn install\" '$SB/other.out' && [ \$(ls '$SB/other') = file.txt ]"

# 7. uninstall (keep data), then reinstall, then uninstall everything
"$PREFIX/packaging/installer/uninstall.sh" --yes > "$SB/un1.out" 2>&1
sleep 1
check "uninstall stops the app" "! ss -ltn | grep -q '127.0.0.1:$PORT '"
check "uninstall keeps data" "[ ! -e '$PREFIX/backend/manager_app.py' ] && [ -f '$PREFIX/servers/test-1/save.dat' ] && [ -f '$PREFIX/config/accounts.json' ]"
check "uninstall removes service and icons" "[ ! -e '$SB/home/.config/systemd/user/hostspawn.service' ] && [ ! -e '$SB/home/Desktop/hostspawn.desktop' ]"
"$REL/packaging/installer/install.sh" --yes --no-deps > "$SB/re.out" 2>&1
check "reinstall finds the kept data" "grep -q 'Upgraded' '$SB/re.out' && grep -q kept '$PREFIX/config/accounts.json'"
HOSTSPAWN_CONFIRM="nope" "$PREFIX/packaging/installer/uninstall.sh" --yes --remove-data > "$SB/un2.out" 2>&1
check "remove-data needs the exact confirmation" "grep -q 'Not confirmed' '$SB/un2.out' && [ -f '$PREFIX/config/accounts.json' ]"
"$REL/packaging/installer/install.sh" --yes --no-deps > /dev/null 2>&1
HOSTSPAWN_CONFIRM="delete everything" "$PREFIX/packaging/installer/uninstall.sh" --yes --remove-data > "$SB/un3.out" 2>&1
check "remove-data deletes everything" "[ ! -e '$PREFIX' ]"
"$REL/packaging/installer/uninstall.sh" --yes --prefix "$SB/other" > "$SB/un4.out" 2>&1
check "uninstaller won't touch a folder that isn't an install" "grep -q 'No HostSpawn install' '$SB/un4.out' && [ -f '$SB/other/file.txt' ]"

echo; echo "$PASS passed, $FAIL failed (sandbox: $SB)"
[ "$FAIL" = 0 ]
