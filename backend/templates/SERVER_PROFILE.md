# SERVER_PROFILE: <server name>

> Copy this file into the root of a server project as `SERVER_PROFILE.md` and fill it in.
> HostSpawn and any AI helper use it to understand the server quickly.
> **Never write secret values here** (passwords, RCON passwords, Steam logins, tokens, keys). Only say *where* they are.
> Keep IP addresses and SteamIDs out if the project is shared.

Last updated: YYYY-MM-DD

## Basics
| | |
|---|---|
| Game | Rust / Arma 3 / Arma Reforger / ... |
| Status | live (public) / testing / dev |
| Max players | |
| Project folder | `/home/ubuntu/Desktop/...` |
| Game install folder | |
| Steam app id + login | e.g. 258550, anonymous |
| Mod framework / mods | e.g. Oxide/uMod, Workshop ids, local addons |

## Ports
| Port | Protocol | Purpose | Public? |
|---|---|---|---|
| | UDP | game | yes |
| | UDP | query (A2S) | yes |
| | TCP/UDP | RCON | **no** (localhost/firewalled) |

## Who keeps it running
Supervisor (cron watchdog / systemd unit / HostSpawn) and how to tell it the stop is on purpose (maintenance flag).

## Commands
| Action | Command | Notes (warnings, downtime, needs OK?) |
|---|---|---|
| start | | |
| stop | | |
| restart | | |
| status | | |
| update | | |
| backup | | where backups go, how many are kept |
| wipe | | Rust only; **needs owner OK** |
| send console command | | |
| health check | | e.g. A2S on port X |

## Logs
| Log | Path |
|---|---|
| server console | |
| control script | |
| update / steamcmd | |

## Secrets (where, never the values)
| Secret | File |
|---|---|
| e.g. RCON password | `config/secrets.env` (chmod 600, git-ignored) |

## Schedules
Restarts, updates, wipes (times in UK time), and where they are configured.

## Data that must survive (never delete)
Player data, permissions, saves, history...

## Open issues / known risks
- 
