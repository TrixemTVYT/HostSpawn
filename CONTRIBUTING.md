# Contributing to HostSpawn

Thanks for wanting to help! HostSpawn by TrixemTV is open source (GPL-3.0, see `NOTICE`).

## How changes get in
- **Bugs:** open an issue with the bug form (HostSpawn's account menu → **Report a bug** fills in safe details).
- **Ideas:** the idea form, or `/suggest` in the HostSpawn Discord.
- **Code:** fork, make your change on a branch, and open a pull request. **Every change is reviewed by TrixemTV**
  before it's merged: it's checked to be safe (no hidden network calls, nothing touching people's passwords or files
  it shouldn't, no new dependencies without a good reason) and tested. Small, focused pull requests are reviewed fastest.

## The rules HostSpawn keeps
- **Safety first.** Never log, show or send a password, token or Steam login (`manager/util.py: redact`). Risky
  actions ask first and back up first. Firewall and system settings are always the owner's job: show the commands,
  don't run them.
- **The engine uses Python's standard library only** (nothing to `pip install`).
- **Tests never touch real servers**: use the fakes in `backend/tests/`. Run them before you send a change:
  ```bash
  python3 -I -m unittest discover -s backend/tests -t backend
  (cd frontend/app && flutter test && flutter analyze)
  ```
- **Plain English** in everything people see.
- **New game server types start as "preview"** (`backend/manager/catalog.py`): they show as Coming soon until
  they've run on real servers.

## The name
Changes to HostSpawn itself are very welcome. A separate copy you publish must use a different name and logo and
credit "HostSpawn by TrixemTV" (see `TRADEMARKS.md`).
