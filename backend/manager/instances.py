"""Instance numbers and the instance limit. Every server is shown as "Instance 1 · Arma 3 Server". A number stays with
its server until the server is deleted, so deleting Instance 1 doesn't renumber the others. The numbers live in
state/instances.json (git-ignored); the limit is settings.toml [limits] max_instances (default 3)."""
import json
import os
import threading

from . import util

DEFAULT_MAX = 3
_lock = threading.Lock()


def _load() -> dict:
    try:
        return {str(k): int(v) for k, v in json.loads((util.STATE / "instances.json").read_text()).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _save(d: dict):
    util.STATE.mkdir(parents=True, exist_ok=True)
    tmp = util.STATE / "instances.json.tmp"
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    os.replace(tmp, util.STATE / "instances.json")


def number(sid: str) -> int:
    """This server's number; a server seen for the first time gets the lowest free one."""
    with _lock:
        d = _load()
        if sid not in d:
            used, n = set(d.values()), 1
            while n in used:
                n += 1
            d[sid] = n
            _save(d)
        return d[sid]


def release(sid: str):
    """The server was deleted: its number is free again."""
    with _lock:
        d = _load()
        if d.pop(sid, None) is not None:
            _save(d)


def label(sid: str, game: str) -> str:
    return f"Instance {number(sid)} · {game} Server"


def max_instances() -> int:
    try:
        return max(0, int(util.settings().get("limits", {}).get("max_instances", DEFAULT_MAX)))
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_MAX
