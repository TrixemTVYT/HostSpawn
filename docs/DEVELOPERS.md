# Developer guide

How to work on Manager (product name: HostSpawn). Read `CONTRIBUTING.md` first for the project rules: they apply to
people too.

## 1. What it is
One Python process (the **engine**) that:
- runs and supervises game servers (`backend/manager/adapters/`, `backend/manager/supervisor.py`);
- creates new servers from a catalogue (`backend/manager/catalog.py`, `backend/manager/deploy.py`);
- serves a JSON API and one page app (`backend/manager/web.py`, `frontend/web/index.html`) on `127.0.0.1`.

The desktop window (`packaging/desktop/manager_window.py`) only shows that page. It's a native GTK + WebKit window, with no
browser. Everything is **Python 3.11+ standard library only**, so there's nothing to `pip install`.

```
frontend/web/index.html  ──HTTP/JSON──>  backend/manager/web.py ──> accounts.py (sign-in, roles)
                                   │
                                   ├──> supervisor.py (every minute: checks, alerts, watchdog, restarts, wipes, backups)
                                   ├──> deploy.py (setup jobs: steps, guided steps, delete)
                                   └──> adapters/* (one class per game; same actions for all)
```

## 2. Get started
```bash
git clone <repo> && cd Manager
./manager.sh init-secrets                         # creates config/secrets.env (chmod 600)
cp config/servers.example.toml config/servers.toml   # or start empty: an empty file is fine
printf 'MODE=dev\nPORT=8101\n' > config/instance.env
./manager.sh serve                                 # http://127.0.0.1:8101 (first run shows the setup wizard)
```
- `MODE=dev`: the copy can see every server but **only controls servers it created itself** (they're marked
  `sandbox = true`). Use it for development. `MODE=live` controls everything.
- `INTERVAL=5` in `instance.env` makes the supervisor check every 5 s instead of 60 s, which helps when testing.
- Settings that aren't code live in git-ignored files: `config/settings.toml`, `config/servers.toml`,
  `config/servers.d/*.toml` (created servers), `config/secrets.env`, `config/accounts.json`, `state/`, `logs/`,
  `servers/`.

- Created servers live in `servers/<game>/<name>-<4 letters>/`, containing `server/`, `logs/`, `backups/`,
  `manager/` (private) and `README.txt`. The hidden `.manager-server` file marks a folder Manager may delete.
  Rust and Reforger share `servers/_shared/steamcmd/`.

## 3. Tests (run before every commit)
```bash
python3 -I -m unittest discover -s backend/tests -t backend      # ~140 tests, about 1.5 min
python3 -I backend/tests/e2e_sandbox.py                    # 48 end-to-end scenarios in a sandbox copy, about 3 min
```
- Tests use **fakes only**: a fake SteamCMD, fake game servers (`backend/tests/fakes/fake_game.py` answers real A2S
  queries and can CRASH/HANG/IGNORE_INT/GARBAGE), a fake WebRCON and a fake BattlEye RCon (both with made-up
  players), and fake `sudo`/`systemctl`/`apt-get` on PATH.
- App sandbox checks (need `backend/tests/app_sandbox.py` running; `SANDBOX_PORT=8198` runs a second one beside yours):
  `backend/tests/sandbox_multi_servers.py` (four servers at once), `backend/tests/sandbox_players.py` (players and bans) and
  `backend/tests/sandbox_messages.py` (scheduled message, restart countdown; the fake game writes what it was told to
  say to `server/said.txt`).
- Player data (`state/players.db`) is personal data: it stays in `state/` (git-ignored), holds no IP addresses, and
  Steam ids never go into the event log.
- **Never let a test reach the real system.** If a test fakes `sudo`, assert `shutil.which("sudo")` is the fake
  first (see `backend/tests/test_deps.py`). A test once really installed a package because a PATH was hard-coded.
- Never point a test at a real server's control script. For example, a real `rustctl.sh` acts on whatever screen
  session has the name its config gives it.

## 4. Code map
| File | What it does |
|---|---|
| `backend/manager/util.py` | Paths, settings/servers loading, secrets (read, generate, redact), events log |
| `backend/manager/accounts.py` | scrypt passwords, hashed session tokens, lock-out, roles and `allowed()` |
| `backend/manager/web.py` | HTTP server: auth, Host check, X-Manager header, all endpoints, validation |
| `backend/manager/supervisor.py` | The minute loop: health checks (process/query/RCON/log/backup), alerts, watchdog threads, scheduled restarts/wipes/backups |
| `backend/manager/catalog.py` | Games, server types, maps/scenarios, recommended restarts, terms per type |
| `backend/manager/deploy.py` | Setup jobs (steps, guided steps, retry/discard), deploy guards, delete, ports |
| `backend/manager/adapters/base.py` | The adapter contract (actions, status, logs, schedule, health hooks, `save_cfg`) |
| `backend/manager/adapters/managed.py` | Engine for servers Manager runs itself: tmux, watchdog, crash loop, SteamCMD updates, backups |
| `backend/manager/adapters/*_server.py`, `arma3.py` | Game-specific bits: launch args, config files, RCON, wipes, rotation |
| `backend/manager/adapters/rust.py`, `reforger.py` | Wrappers for servers run by their own scripts (rustctl.sh, a systemd unit) |
| `backend/manager/rcon.py` | Rust WebRCON client (stdlib WebSocket) |
| `backend/manager/battleye.py` | BattlEye RCon client (UDP, protocol v2): Arma 3 players, kick, bans, say |
| `backend/manager/adapters/moderation.py` | Players per game in one shape (`key` steam:/be:, `ref`), kick/ban/unban/say; watched servers read-only |
| `backend/manager/instances.py` | Instance numbers (`state/instances.json`) and the instance limit (`[limits] max_instances`) |
| `backend/manager/install.py` | Installation jobs: update check (hourly, Steam build ids), safe update with restore, check and repair, reinstall keeping saves, clean up, history |
| `backend/manager/hostcheck.py` | PC check: machine specs against each game's needs (`GET /api/hostcheck`) |
| `backend/manager/players.py` | Player database `state/players.db` (SQLite): sessions, playtime, names, one ban list, notes, watchlist, audit |
| `backend/manager/steamlogin.py` | Steam login through a pty (password typed into SteamCMD, never stored) |
| `backend/manager/deps.py` | Dependencies page: checks and installs |
| `backend/manager/pbo.py` | PBO pack/unpack/list (`./manager.sh pbo …`) |
| `frontend/web/index.html` | The whole UI (no framework). All text goes through `textContent`, never `innerHTML` with data |

## 5. Add a game
1. Write `backend/manager/adapters/<game>_server.py`: subclass `ManagedServer`, set `game`, `slug`, `app_id`,
   `exe_name`, and implement `launch_args()`, `write_configs()`, `backup_paths()`, `query_port()`. Optional:
   `rcon_check()`, `graceful_stop()`, `post_update()`, `log_file()`.
2. Register it in `backend/manager/adapters/__init__.py` (`ADAPTERS`).
3. Add it to `GAMES` in `backend/manager/catalog.py`: name, adapter, app_id, login (`anonymous`/`steam`), ports,
   options, modes (`status: "ready"`), plus a recommended restart schedule in `RESTARTS`.
4. If its ports differ, add a plan in `deploy.allocate_ports`.
5. Tests: a fake game binary that answers A2S (see `backend/tests/test_deploy.py`, `FAKE_RUST`), and a full setup flow.
6. **Before** marking it ready, do one real deploy in dev mode (into `servers/`, on spare ports) and check the
   game's own log. (A guessed Reforger scenario ID was wrong until a real run listed the IDs.)

## 6. API
JSON over HTTP on `127.0.0.1`. Apps should use **`/api/v1/...`** (same endpoints as `/api/...`, which stays for
the built-in page); a future v2 can change things without breaking v1 apps.
- **Page (browser):** sign in with `POST /api/login`; the session is an HttpOnly cookie.
- **Apps (the HostSpawn program, later a phone):** sign in with `POST /api/v1/login`
  `{username, password, device: "My PC"}` (or `/api/v1/setup` on first run, with `device`). The reply has a
  `token`. Send it as `Authorization: Bearer <token>`. Keep it in the system keychain, never in a plain file.
  A device token lasts 90 days and renews itself while used. A 401 saying "signed out" means sign in again.
- **Pairing a new device without typing a password on it:** a signed-in user calls `POST /api/v1/pair/new` (or
  runs `./manager.sh pair <username>` on the machine) to get a 6-digit code (5 min, one use). The new device
  sends `POST /api/v1/pair {code, device}` and gets a token with **the same rights as that user**. 10 wrong
  codes in a row pause pairing for 5 min and cancel the waiting codes.
- Every POST needs the header `X-Manager: 1`, including apps. Bodies are at most 10 kB of JSON.

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/api/info` | anyone | `{name: "HostSpawn", api: 1, version, accounts, setup_needed}`: apps check this first |
| GET | `/api/setup/status` | anyone | `{accounts, setup_needed, signed_in}` |
| POST | `/api/setup` | first run, this machine only | create the owner account |
| POST | `/api/login` · `/api/logout` | anyone · signed in | sign in/out |
| GET | `/api/me` · POST `/api/me/password` | signed in | who am I · change password (signs out every device) |
| POST | `/api/pair/new` · `/api/pair` `{code, device}` | signed in · anyone | make a pairing code · swap it for a device token |
| GET/POST | `/api/devices` · `{action: "revoke", id}` | signed in (own devices; admin: all) | signed-in apps · sign one out |
| GET/POST | `/api/users` | admin | list · add/role/remove |
| GET | `/api/overview` | signed in | servers, setups in progress, machine stats, recent events |
| GET | `/api/server?id=` · `/api/logs?server=` | signed in | one server's details · its logs (secrets redacted) |
| POST | `/api/action` `{server, action, arg?, confirm?}` | moderator: start/stop/restart/update/backup/send · admin: all | run an action; risky ones need `confirm` = the action name |
| POST | `/api/server/settings` | admin | name, players, restarts, backups, wipes, rotation; `restart_warn_s` (0–1800), `countdown_message` (needs `{time}`), `messages` (≤10: `{text, every_min 5–1440}` or `{text, times: [HH:MM]}`) |
| GET/POST | `/api/settings` | signed in / admin | community, backups, terms acceptance |
| GET | `/api/catalog` · `/api/catalog/releases` · `/api/terms` | signed in | games and types · releases of a type · terms text |
| POST | `/api/deploy` `{game, mode, options, request_id, accepted_terms}` | admin | start a setup (one at a time, rate-limited) |
| GET/POST | `/api/job` | signed in / moderator (discard: admin) | a setup's progress · continue/back/skip/cancel/retry/discard |
| POST | `/api/delete` `{server, confirm: name, keep_backups}` | admin | delete a server Manager created |
| GET/POST | `/api/deps` · `/api/deps/install` | signed in / admin | dependency status · install one |
| GET/POST | `/api/steam/login` | moderator | Steam login session for a setup or server |
| GET | `/api/events` | signed in | the event log |
| GET | `/api/install?server=` | signed in | an instance's installation: sizes, build, newest build, running job, history |
| POST | `/api/install` `{server, action: update\|repair\|reinstall\|cleanup\|check, confirm, warn_s}` | update/check: moderator; others: admin | starts a job (reinstall needs the instance's name) |
| GET | `/api/players?server=` · `/api/players/search?q=` · `/api/player?key=` · `/api/bans?all=1` | signed in | online now · everyone seen · one profile · the ban list |
| GET | `/api/audit` | moderator | moderation audit log |
| POST | `/api/players` `{action, key, server?, reason?, minutes?, text?}` | moderator | kick (needs server), ban (reason required; minutes 0 = permanent; every server), unban, note, watch, unwatch, say (server or all) |

## 7. Rules for changes (see `CONTRIBUTING.md`)
- **Safety first.**
  - Never log, show or commit a secret.
  - Validate every input on the server side; the UI's checks are only for convenience.
  - Risky actions need a typed confirmation and a role check.
  - Anything that deletes makes a backup first and only deletes inside folders Manager made (the marker file
    `.manager-server`).
- No `shell=True`, and no user text in a command line without validation (the Steam password is typed into a
  pty for exactly this reason).
- Animations: transform/opacity only (a glow filter once froze software rendering).
- Don't reuse a name that already exists in `index.html` (a Deploy handler called `go` once called itself 705
  times).
- Log every change in `docs/CHANGELOG.md`, and say how it was tested.

## 8. Releases and the installer
`packaging/build_release.sh` makes `dist/hostspawn-<version>.tar.gz` plus `SHA256SUMS`. `packaging/installer/install.sh`
installs from it (it checks the checksum first). See `packaging/installer/README.md`.
