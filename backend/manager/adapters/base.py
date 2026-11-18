"""The adapter contract: every game offers the same actions, so the engine and dashboard stay generic."""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import a2s
from ..util import STATE, tz


class Unsupported(Exception):
    pass


class Adapter:
    game = "unknown"
    # Actions this adapter supports; the web panel only allows these.
    actions: tuple[str, ...] = ()
    # Actions that need the owner to type the action name in the dashboard.
    confirm: frozenset[str] = frozenset()
    # Who restarts it after a crash: "rustctl", "systemd", "manager".
    supervised_by = "manager"

    def __init__(self, server_id: str, cfg: dict):
        self.id = server_id
        self.cfg = cfg
        self.name = cfg.get("name", server_id)
        self.root = Path(cfg.get("root", ""))
        self.state_dir = STATE / server_id
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # ---- the common actions (each returns a short message; raises Unsupported if not offered) ----
    def install(self) -> str: raise Unsupported("install")
    def start(self) -> str: raise Unsupported("start")
    def stop(self) -> str: raise Unsupported("stop")
    def restart(self) -> str: raise Unsupported("restart")
    def update(self) -> str: raise Unsupported("update")
    def backup(self) -> str: raise Unsupported("backup")
    def wipe(self, kind: str) -> str: raise Unsupported("wipe")
    def send(self, command: str) -> str: raise Unsupported("send command")

    # ---- players (adapters/moderation.py implements these per game) ----
    def can_moderate(self) -> bool: return False
    def players(self) -> list[dict]: raise Unsupported("players")
    def kick(self, ref: str, reason: str) -> str: raise Unsupported("kick")
    def ban(self, key: str, ref: str | None, name: str, minutes: int, reason: str) -> str: raise Unsupported("ban")
    def unban(self, key: str) -> str: raise Unsupported("unban")
    def say(self, text: str) -> str: raise Unsupported("say")

    # ---- in-game countdowns (before scheduled restarts and wipes) ----
    def can_say(self) -> bool:
        return type(self).say is not Adapter.say and self.can_moderate()

    def countdown(self, seconds: int, what: str = "restart") -> None:
        """Tell players at 15, 10, 5, 2 and 1 minutes and 30 and 10 seconds, then return when the time is up.
        Best effort: a message that fails never holds anything up. Text: cfg countdown_message ({what}, {time})."""
        left = max(0, int(seconds))
        for mark in (m for m in COUNTDOWN_MARKS if m <= left):
            _sleep(left - mark)
            left = mark
            if self.can_say():
                try:
                    self.say(countdown_text(self.cfg.get("countdown_message"), what, mark))
                except Exception:
                    pass
        _sleep(left)

    def status(self) -> dict:
        """state: running | starting | stopped | maintenance | not_installed | unknown"""
        raise NotImplementedError

    def logs(self) -> dict[str, str]:
        """{log name: last lines}"""
        return {}

    def schedule(self) -> list[dict]:
        """Upcoming events: [{"when": iso time in the chosen time zone, "what": text}]"""
        return []

    # ---- health: 4 checks (process via status(), query, RCON, log activity) ----
    # Alert when the console log hasn't changed for this long while running (None = only show its age).
    log_alert_s: float | None = None

    def query_port(self) -> int | None:
        return self.cfg.get("query_port")

    def health(self) -> dict | None:
        port = self.query_port()
        return a2s.info("127.0.0.1", port) if port else None

    def rcon_check(self) -> dict | None:
        """{"ok": bool, "error"?: str, "players"?: int, "fps"?: float}, or None if this server has no RCON set up."""
        return None

    def log_file(self) -> Path | None:
        return None

    def log_age(self) -> float | None:
        f = self.log_file()
        try:
            return time.time() - f.stat().st_mtime if f else None
        except OSError:
            return None

    def backup_files(self) -> list[Path]:
        return []

    def last_backup(self) -> float | None:
        """Unix time of the newest backup, or None if there is none."""
        times = []
        for f in self.backup_files():
            try:
                times.append(f.stat().st_mtime)
            except OSError:
                pass
        return max(times) if times else None

    def save_cfg(self, changes: dict) -> None:
        """Change settings of a server HostSpawn created: in memory and in its config/servers.d file."""
        self.cfg.update(changes)
        if "name" in changes:
            self.name = changes["name"]
        f = self.cfg.get("_file")
        if not f:
            return
        import tomllib
        from ..deploy import toml_dump
        data = tomllib.loads(Path(f).read_text()).get("servers", {})
        if self.id in data:
            data[self.id].update(changes)
            Path(f).write_text(toml_dump(data))

    def run_action(self, action: str, arg: str = "") -> str:
        if action not in self.actions:
            raise Unsupported(action)
        if action == "wipe":
            return self.wipe(arg)
        if action == "send":
            return self.send(arg)
        return getattr(self, action)()

    # ---- small state-file helpers (state/<server>/<name>) ----
    def flag(self, name: str) -> Path:
        return self.state_dir / name

    def has(self, name: str) -> bool:
        return self.flag(name).exists()

    def set_flag(self, name: str, value: str = "") -> None:
        self.flag(name).write_text(value)

    def clear(self, name: str) -> None:
        self.flag(name).unlink(missing_ok=True)

    def age(self, name: str) -> float:
        try:
            return time.time() - self.flag(name).stat().st_mtime
        except OSError:
            return float("inf")

    def counter(self, name: str) -> int:
        try:
            return int(self.flag(name).read_text() or 0)
        except (OSError, ValueError):
            return 0


COUNTDOWN_MARKS = (900, 600, 300, 120, 60, 30, 10)
DEFAULT_COUNTDOWN = "Server {what} in {time}."


def _sleep(s: float) -> None:  # tests replace this to run a countdown instantly
    time.sleep(s)


def countdown_text(template: str | None, what: str, seconds: int) -> str:
    left = f"{seconds // 60} minute{'s' if seconds >= 120 else ''}" if seconds >= 60 else f"{seconds} seconds"
    try:
        return (template or DEFAULT_COUNTDOWN).format(what=what, time=left)[:200]
    except (KeyError, IndexError, ValueError):  # a template with a typo still warns players
        return DEFAULT_COUNTDOWN.format(what=what, time=left)


def proc_info(pid: int) -> dict:
    """Uptime (s) and memory (MB) of a process, from /proc."""
    out = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                out["mem_mb"] = int(line.split()[1]) // 1024
        ticks = os.sysconf("SC_CLK_TCK")
        start = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]) / ticks
        boot = float(Path("/proc/uptime").read_text().split()[0])
        out["uptime_s"] = int(boot - start)
    except (OSError, ValueError, IndexError):
        pass
    return out


def next_daily(times: list[str], days: int = 2) -> list[dict]:
    now = datetime.now(tz())
    out = []
    for d in range(days + 1):
        day = now.date() + timedelta(days=d)
        for t in times:
            try:
                h, m = map(int, t.split(":"))
            except ValueError:
                continue
            when = datetime(day.year, day.month, day.day, h, m, tzinfo=tz())
            if when > now:
                out.append(when)
    return [{"when": w.isoformat(), "what": "restart"} for w in sorted(out)[:2]]
