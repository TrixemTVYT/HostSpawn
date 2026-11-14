# Building HostSpawn from source

You can use the official download, or build HostSpawn yourself from this source. Both give you the same program.

## What you need (Linux)
- Ubuntu 22.04+ or Debian 12+ (other Linux systems usually work, with packages installed by hand)
- Python 3.11 or newer (the engine uses only Python's standard library: nothing to `pip install`)
- `tmux`, `lib32gcc-s1` (SteamCMD), `openssl`, `git`
- For the desktop app: [Flutter](https://docs.flutter.dev/get-started/install/linux) (stable channel) and its Linux
  desktop packages (`clang cmake ninja-build pkg-config libgtk-3-dev`)

## Build
```bash
git clone <the HostSpawn repository> hostspawn
cd hostspawn
python3 -I -m unittest discover -s backend/tests -t backend   # optional: run the engine tests
packaging/build_release.sh                                     # or --no-app to skip the desktop app
```
This makes `dist/hostspawn-<version>-linux-x64.tar.gz`. Install it like the official download:
```bash
tar -xzf dist/hostspawn-*-linux-x64.tar.gz
./hostspawn-*/packaging/installer/install.sh
```
Your own build isn't signed, so the installer says it's a development build. That's expected.

## Checking an official download
Every release comes with `SHA256SUMS` (checksums): `sha256sum -c SHA256SUMS` checks your download. Releases can
also be signed with HostSpawn's release key (`SHA256SUMS.sig`, Ed25519, the key kept offline); for a signed release,
check it before unpacking:
```bash
./verify.sh <folder with the downloads> hostspawn-release.pub
```
`verify.sh` is in `packaging/release/` of this repository. It prints the key's fingerprint: compare it with the one
in the HostSpawn README / Discord. It then checks the signature and every file's checksum. The installer checks the
signature again, and once HostSpawn is installed, updates must be signed with the same key.

## Making an official release (maintainers)
1. Once, on your own computer: `packaging/release/keygen.sh <a folder on a USB stick>`. Commit only
   `hostspawn-release.pub` (to `packaging/release/`); keep the `.key` offline and backed up.
2. Set the version in `VERSION` (for example `1.0.0`) and add a `docs/CHANGELOG.md` entry.
3. `packaging/build_release.sh --sign <path to hostspawn-release.key>` (add `--channel beta` for a beta).
4. Upload everything in `dist/` (archive, `SHA256SUMS`, `release.json` and the `.sig` files) to the release page.
