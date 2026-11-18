# Changelog

## 1.0.1 (2026-11-18): time zone, safer backups, simpler delete
- **New:** account menu → **Time zone**. Scheduled restarts, daily backups and Rust wipes use it, and times show the
  real zone (for example "20:00 GMT"). It starts as your computer's own zone.
- **Fixed:** backups never overwrite each other: two in the same second get `-2`, `-3`. Old backups are cleared by
  date.
- **Fixed:** a half-written backup (disk full, read error) never counts as a backup. Thanks to **IceEagle132**.
- **Fixed:** if the safety backup before a Rust wipe fails, or installing Oxide fails, the server is started again
  instead of being left off. Thanks to **IceEagle132**.
- **Changed:** a backup waits for any update, restart or wipe to finish first.
- **Changed:** deleting an instance is one question: "Are you sure you want to delete everything?" (its backups are
  kept unless you untick it).
- **Fixed:** practice servers made by the tests keep well away from real game ports.
- Updates and fixes are now posted in **#updates** in the HostSpawn Discord.

## 1.0.0 (2026-11-14): first public release
HostSpawn by TrixemTV is now free and open source (GPL-3.0, see `NOTICE`). No sign-up, no keys.

- **Ready:** Rust, and Arma 3 (Vanilla, Altis Life with its MariaDB database, Spyglass help and a BattlEye switch).
  Each has run on real servers: setup, restarts with updates, backups, days without a crash.
- **Coming soon:** Arma Reforger, Arma 3 KP Liberation, Arma 3 player tools (kick, ban, in-game messages) and the
  Windows app. They're built and tested against fakes, and switched on once proven on real servers.
- **HostSpawn Setup** for Linux: one file, checks every file before installing, installs for your user only.
- The app: instances (up to 3), Server Doctor, live console, Files editor with versions, Database tab, players,
  backups, installation (update, repair, reinstall), PC check, themes, and **Report a bug** (to GitHub).
- Updates are announced in the HostSpawn Discord's #announcements.
