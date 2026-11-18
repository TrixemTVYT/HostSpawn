# User guide

HostSpawn by TrixemTV sets up, runs and looks after your game servers from one app. It runs on a 64-bit Linux PC or
server (Ubuntu 22.04+ or Debian 12+). It's free and open source: no sign-up, nothing sent to us.

**Ready now:** Rust, and Arma 3 (Vanilla, Altis Life). **Coming soon:** Arma Reforger, more Arma 3 modes, Arma 3
player tools (kick, ban, in-game messages) and the Windows app. Each one is switched on once it has been proven on
real servers.

## Install
1. Download **HostSpawn-Setup-…-linux-x64.run** from the latest
   [release](https://github.com/TrixemTVYT/HostSpawn/releases/latest).
2. Right-click it → **Properties** → **Permissions** → tick **Allow executing file as program**, then double-click it.
   No desktop? `chmod +x HostSpawn-Setup-*.run && ./HostSpawn-Setup-*.run`
3. **HostSpawn Setup**: read and accept the terms → **Check this computer** (nothing is changed yet) → **Install** →
   **Open HostSpawn**.

HostSpawn installs for your user only, in `~/.local/share/hostspawn`, and starts by itself after a reboot. Setup
checks every file against the release's checksums before installing. It only asks for your password to add missing
system packages (for example `tmux`), and shows you which ones first.

**Update:** download the new Setup and run it the same way. Your settings, instances, players and backups are kept,
and your game servers keep running. New versions and fixes are posted in #updates in the HostSpawn Discord.

**Uninstall:** `~/.local/share/hostspawn/packaging/installer/uninstall.sh` (it keeps your servers and backups unless
you add `--remove-data`).

## First start
HostSpawn asks you to create the **owner account**. It's stored only on your machine (the password as a scrypt hash)
and stops anyone else controlling your servers. Add more people later with a role: **Admin** (everything),
**Moderator** (start, stop, restart, update, back up, console, players) or **Viewer** (look only).

## Create an instance
Each game server is an **instance** ("Instance 1 · Rust Server"). You can have up to **3**.
1. Account menu → **PC check** first: it says what this machine can run comfortably, and why.
2. **New instance** → pick the game, the server type and the options (name, players, map).
3. HostSpawn downloads the game (the official files, through Valve's SteamCMD), writes the settings, makes the
   passwords (never shown) and starts it. The setup screen shows **where your server's files are**, normally
   `~/.local/share/hostspawn/servers/<game>/<instance name>`.
4. **Open the ports for players** (the setup shows exactly which). Firewall changes are always yours to make:
   HostSpawn shows the commands, for example `sudo ufw allow 28015/udp`, and never changes the firewall itself.

Arma 3 needs a Steam account that owns Arma 3: HostSpawn asks you to log in once (you type the password and any
Steam Guard code yourself).

## Running an instance
- **Top bar:** state, players and FPS; **Start** or **Restart**, **Stop**, and **More** (update, back up now).
- **Overview:** the **Server Doctor** (it explains problems in plain English, with a fix button where it's safe),
  the game, ports and **where the server files are** (Open folder / Copy path).
- **Players**, **Console** (live), **Files** (edit configs and scripts safely, with versions), **Settings**
  (restarts, backups, the game's options, Installation: update, repair, reinstall) and **Tools**.
- HostSpawn checks every instance each minute and restarts it after a crash or a freeze.

## Help
- **A bug?** Account menu → **Report a bug** (safe details, straight to the GitHub bug form).
- **Stuck?** Press **Get help** in #help in the [HostSpawn Discord](https://discord.gg/AaBwz8szM).
- More: [Server Doctor](SERVER_DOCTOR.md) · [Files editor](FILE_EDITOR.md) · [Database tab](DATABASE.md) ·
  [AI help](AI_HELP.md) · [Building from source](BUILDING.md)
