# Manager: roadmap, tool ideas, names (2026-11-06)

## Done so far
- An engine that keeps servers up: crash and freeze recovery, crash-loop backoff, update retries, daily backups,
  4-way health checks (process, query, RCON, log), alerts and an event log.
- Rust (RCON), Arma Reforger and Arma 3 adapters.
- A create-server flow: game → server type → settings → deploy, with a live loading screen and guided steps.
- A panel for each server, with Delete and Install/Update Oxide.
- A dark dashboard, live and dev copies, desktop launchers, 85 unit tests and a 48-scenario sandbox.

## Still to do (in order)
| # | Step | What it means |
|---|---|---|
| 1 | **Altis Life** | **Built** (2026-11-06). Still needs one real run with your Steam login and `sudo apt install mariadb-server libtbbmalloc2` |
| 2 | **More game modes** | A3Wasteland, Antistasi (CBA), Carbon for Rust, Reforger Workshop scenarios |
| 3 | **Mods** | Browse and pick mods (Steam Workshop, Reforger Workshop, uMod plugins); update checks; an "update mods at next restart" button |
| 4 | **RCON for Arma** | **Arma 3 built** (2026-11-06): BattlEye RCon client, players, kick/ban, say. Reforger: its RCON commands still need checking against a real server |
| 5 | **Agent API + pairing** | Agent runs as a service; the desktop app pairs with a one-time code; encrypted connection |
| 6 | **Desktop app (.exe)** | Tauri window holding today's screens; Windows, macOS, Linux; built automatically by GitHub |
| 7 | **Windows agent** | A per-OS process layer (replacing tmux/signals/`/proc`); Windows service; Windows Server 2019–2025, Windows 10/11 |
| 8 | **Installer: "Manager Setup", our own, custom, smart and trusted** (code-signed in your name; every download checked against official checksums/signatures, official sources only, nothing bundled you didn't choose; detects what's installed, repair, resume; clean uninstaller; a **terms page with "I accept"** before anything installs) | Our own installer with our look: welcome → folder → choose components (dependencies with sizes) → progress screen → Launch. Windows `Setup.exe` (also installs DirectX June 2010, VC++, MySQL/MariaDB when needed); Linux package with the same screens. Replaces the temporary launcher message box. Was: Windows installer that can fetch extras (MariaDB/MySQL, Visual C++ runtime); Linux one-line install script |
| 9 | **More games** | DayZ, Valheim, Project Zomboid, Palworld, 7 Days to Die |
| 10 | **Alerts out** | Discord webhook, phone push (home-screen app over Tailscale), email |
| 11 | **Release** | Name, domain, website, licence, code signing (removes the Windows warning), docs |
| 12 | **Service (maybe)** | Accounts, several machines per account, billing; only if you decide to |

## Tool ideas for server management
**Keeping it running**
- *Restart planner*: warning messages in game at 15/5/1 min; skip restarts when players are mid-raid/peak hours.
- *Health score* (0–100) per server from FPS, crashes, RAM and query time, with a 7-day graph.
- *Smart update window*: update only when the player count is low, or at a set hour.
- *Resource guard*: a memory/CPU cap per server; warns before the machine runs out of RAM or disk.

**Players**
- *A player list across all servers* (name, SteamID, playtime, last seen, notes).
- *Ban once, ban everywhere*, plus temporary bans, with a reason and evidence notes.
- *Whitelist and priority queue* (VIP slots).
- *Chat log search* and a bad-word filter.
- *Admin audit log*: who pressed what, and when.

**Content**
- *PBO Manager* (built-in tool, offered to anyone using the .exe): view, unpack and pack `.pbo` files by dragging them in. The engine exists (`backend/manager/pbo.py`, `./manager.sh pbo list|unpack|pack`); the screen comes with the .exe.
- *Config editor* with history and one-click rollback (and a check before saving).
- *Mod/plugin manager* with update alerts and "is this mod compatible with the new game version?".
- *Wipe planner* (Rust): calendar, countdown, what's kept or wiped, announcements.
- *Map/seed picker* (Rust): preview, size, seed history.

**Safety**
- *Backup browser*: restore a single server or a single file; a monthly "test restore" check.
- *Off-site backups* (S3 / Backblaze B2).
- *Port checker*: tests from outside whether players can reach the server ("is my firewall open?").
- *Secrets vault*: passwords never shown; rotate with one click.

**Community**
- *Discord bot*: server status, player count, restart and wipe announcements.
- *Server status page / widget* for your website.
- *Staff roles*: moderators can kick/ban but not delete servers.

## Name ideas + domains to check
DNS check on 2026-11-06: "maybe free" means no DNS record was found, which often means unregistered.
**Always confirm on a registrar** (Namecheap, Cloudflare, Porkbun) before buying.

| Name | Idea | Domain ideas (DNS check) |
|---|---|---|
| **Fortline** | your servers' front line, always held | fortline.gg (maybe free) · fortline.io (taken) · getfortline.com |
| **Ironhold** | strong, keeps servers up | ironhold.gg (maybe free) · ironholdhq.com (maybe free) |
| **Helmsman** | steers all your servers | helmsman.gg (maybe free) · gethelmsman.com |
| **Command Post** | one place to command every server | commandpost.gg (maybe free) · commandposthq.com |
| **Rackline** | a rack of servers, in one line | rackline.gg (maybe free) · rackline.io (taken) |
| **ServerForge** | forges new servers in minutes | serverforge.gg (maybe free) |
| **Lobbyline** | from lobby to live server | lobbyline.gg (maybe free) |
| **MSM** (Multi Server Manager) | your first idea (note: an old Minecraft tool also uses "MSM") | msm.games (maybe free) · usemsm.com (maybe free) |

Already taken: garrison.gg, garrisonhq.com, outpost.gg, outposthq.io, quartermaster.gg, keepalive.gg, stronghold.gg.

**Owner's choice (2026-11-06): ServerForge**, as a game-server manager.
- Domains that look free: serverforge.gg, serverforgehq.com, forgeservers.gg.
- Taken: serverforge.com, .io, .games, .app, getserverforge.com and gameserverforge.com. That suggests someone
  already uses the name, so **check for a trademark** (UK IPO and USPTO search) and existing companies before
  buying.
- Possible tagline: "ServerForge — forge and run your game servers".
- Avoid "GameForge": that's a big games publisher.

Earlier pick: **Fortline** (fortline.gg). It's short, easy to say and spell, fits gaming, and .gg is the gamer
domain. Also check fortline.com and the name on GitHub and in the Steam/Discord communities before deciding.
