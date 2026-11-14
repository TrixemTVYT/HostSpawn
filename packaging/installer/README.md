# Installer (Linux)

```bash
packaging/build_release.sh                 # -> dist/hostspawn-<version>.tar.gz + dist/SHA256SUMS
tar -xzf dist/hostspawn-*.tar.gz && cd hostspawn-*/
./installer/install.sh                 # or --help
```

**What it does:**
1. Checks the machine: Linux, Python 3.11+, disk space, a free port from 8100.
2. Checks every file against `MANIFEST.sha256`, and refuses a damaged or altered download.
3. Shows the terms and needs `accept` typed.
4. Checks the dependencies (tmux, lib32gcc-s1, gir1.2-webkit2-4.1) and offers `sudo apt-get install` for the
   **missing ones only**. You type your own sudo password, and `--no-deps` skips this.
5. Installs for the user into `~/.local/share/hostspawn`, with no root needed. Upgrades keep config, accounts,
   servers and backups. It refuses to install into a folder that isn't a HostSpawn install.
6. Sets up a systemd **user** service (`hostspawn`), an app-menu/desktop icon, and opens the app.

**Uninstall:** `~/.local/share/hostspawn/installer/uninstall.sh`. It keeps your data by default; with
`--remove-data` it deletes everything, after you type `delete everything`. It only stops this install's own app
and game servers.

**Test it safely:** `backend/tests/installer_sandbox.sh` runs 26 checks with a fake HOME and fake
systemctl/sudo/dpkg-query (it refuses to run unless the fakes are used). It covers a tampered release, refused
terms, a fresh install, a working app, an upgrade, a foreign folder, uninstall (keep/remove data) and reinstall.

**Still to come:** a graphical installer (with the .exe), signed releases (GPG/Authenticode), Windows.
