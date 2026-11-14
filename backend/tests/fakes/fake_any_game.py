"""Fake game server for sandboxes: works out its query port from Rust, Reforger or Arma 3 launch arguments, then runs
fake_game.py (answers A2S_INFO on that port, exits cleanly on SIGINT). If the launch sets up RCON (Rust
+rcon.port/+rcon.password, Arma 3 battleye/beserver_x64.cfg), fake_game.py also answers RCON with a few fake
players who come and go (FAKE_RCON tells it how)."""
import json
import os
import shutil
import sys

args = sys.argv[1:]


def after(flag):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


query = None
if after("+server.queryport"):  # Rust
    query = int(after("+server.queryport"))
elif after("-config"):  # Reforger: the a2s port is in its JSON config
    try:
        query = int(json.load(open(after("-config")))["a2s"]["port"])
    except (OSError, ValueError, KeyError):
        query = None
if query is None:  # Arma 3: -port=P, queries on P + 1
    p = next((a.split("=", 1)[1] for a in args if a.startswith("-port=")), None)
    query = int(p) + 1 if p else 27016
if after("+rcon.port") and after("+rcon.password"):  # Rust WebRCON
    os.environ["FAKE_RCON"] = f"rust:{after('+rcon.port')}:{after('+rcon.password')}"
elif os.path.exists("battleye/beserver_x64.cfg"):  # Arma 3 BattlEye RCon (the server starts in its own folder)
    be = dict(line.split(" ", 1) for line in open("battleye/beserver_x64.cfg").read().splitlines() if " " in line)
    if be.get("RConPort") and be.get("RConPassword"):
        os.environ["FAKE_RCON"] = f"be:{be['RConPort']}:{be['RConPassword']}"
here = os.path.dirname(os.path.abspath(__file__))
# keep the game's own path as the process name, so Manager finds it like the real server
name = os.environ.get("FAKE_EXE", sys.argv[0])
os.execv(shutil.which("python3") or "/usr/bin/python3", [name, "-I", os.path.join(here, "fake_game.py"), f"-port={query - 1}", *args])
