"""Rust: wraps the existing rustctl.sh. rustctl's own watchdog + scheduler (cron) keep the server up;
this adapter never supervises it, it only calls rustctl and reads its files."""
import json
import re
from datetime import date, datetime, timedelta

from ..rcon import RconError, WebRcon
from ..util import UK, newest, read_env, run, run_background, tail, clean_command
from .base import Adapter, next_daily
from .moderation import RustRcon


def first_thursday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(3 - d.weekday()) % 7)


class RustAdapter(RustRcon, Adapter):
    game = "Rust"
    actions = ("start", "stop", "restart", "update", "backup", "wipe", "send")
    confirm = frozenset({"stop", "restart", "update", "wipe"})
    supervised_by = "rustctl"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        # Read-only. Also makes Rust's own secrets (RCON, panel, Tebex) hidden if they ever show up in a log.
        self._rcon_password = read_env(self.root / "config/secrets.env").get("RCON_PASSWORD", "")

    @property
    def rcon_port(self): return int(self.cfg.get("rcon_port") or 0)
    def rcon_password(self): return self._rcon_password

    def rcon_check(self):
        port = self.cfg.get("rcon_port")
        if not port:
            return None
        try:
            info = WebRcon("127.0.0.1", int(port), self._rcon_password).serverinfo()
            return {"ok": True, "players": info.get("Players"), "fps": info.get("Framerate"),
                    "queued": info.get("Queued"), "max_players": info.get("MaxPlayers")}
        except RconError as e:
            return {"ok": False, "error": str(e)}

    def log_file(self):
        return newest(self.root / "logs", "server_*.log") if (self.root / "logs").is_dir() else None

    def backup_files(self):
        return list((self.root / "backups").glob("*.tar.gz"))

    @property
    def ctl(self) -> str:
        return str(self.root / "rustctl.sh")

    def _bg(self, action: str, *args: str) -> str:
        run_background(self.id, action, [self.ctl, *args], cwd=self.root)
        return f"{action} started (rustctl.sh {' '.join(args)}); progress in the rustctl log"

    @property
    def warn(self) -> str:
        return str(int(self.cfg.get("warn_seconds", 300)))

    def start(self): return self._bg("start", "start")
    def stop(self): return self._bg("stop", "stop", self.warn)
    def restart(self): return self._bg("restart", "restart", self.warn)
    def update(self): return self._bg("update", "update", self.warn)
    def backup(self): return self._bg("backup", "backup")

    def wipe(self, kind):
        if kind not in ("map", "full"):
            raise ValueError("wipe kind must be map or full")
        return self._bg(f"wipe {kind}", "wipe", kind, self.warn)

    def send(self, command):
        command = clean_command(command)
        if not command:
            raise ValueError("empty command")
        code, out = run([self.ctl, "cmd", command], timeout=15)
        return "sent" if code == 0 and "sent" in out else "not sent (server not running?)"

    def status(self):
        logs = self.root / "logs"
        if not (self.root / "rustctl.sh").exists():
            return {"state": "not_installed"}
        code, out = run([self.ctl, "status"], timeout=15)
        s = {"state": "unknown", "flags": []}
        m = re.search(r"running pid=(\d+) uptime=(\S+) mem=([\d.]+)GB", out)
        if m:
            s.update(state="running", pid=int(m[1]), uptime=m[2], mem_mb=int(float(m[3]) * 1024))
        elif out.startswith("stopped"):
            s["state"] = "maintenance" if "maintenance" in out else "stopped"
        if (logs / ".update_failed").exists():
            s["flags"].append("Last update failed: rustctl retries every 15 min")
        crashes = (logs / ".crashes").read_text().strip() if (logs / ".crashes").exists() else ""
        if crashes.isdigit() and int(crashes) >= 2:
            s["flags"].append(f"{crashes} failed starts in a row")
        try:
            # Optional: a plugin that writes live stats as JSON (Fps, MaxPlayers), path relative to root.
            live = json.loads((self.root / self.cfg["live_json"]).read_text()) if self.cfg.get("live_json") else {}
            s["fps"] = live.get("Fps")
            s["max_players"] = live.get("MaxPlayers")
        except (OSError, ValueError):
            pass
        return s

    def logs(self):
        out = {"rustctl": tail(self.root / "logs/rustctl.log", 150)}
        latest = newest(self.root / "logs", "server_*.log")
        if latest:
            out["console"] = tail(latest, 150)
        return out

    def schedule(self):
        conf = read_env(self.root / "config/wipe.conf", remember=False)
        events = []
        if conf.get("AUTO_RESTART_ENABLED") == "1":
            times = [t.strip() for t in conf.get("RESTART_TIMES", "").split(",") if t.strip()]
            events += next_daily(times)
        if conf.get("AUTO_WIPE_ENABLED") == "1":
            now = datetime.now(UK)
            h, m = map(int, conf.get("FORCED_WIPE_TIME", "20:00").split(":"))
            for add in (0, 1):
                y, mo = now.year + (now.month + add - 1) // 12, (now.month + add - 1) % 12 + 1
                d = first_thursday(y, mo)
                when = datetime(d.year, d.month, d.day, h, m, tzinfo=UK)
                if when > now:
                    bp = " + blueprints" if conf.get("BLUEPRINT_WIPE_ON_FORCED") == "1" else ""
                    events.append({"when": when.isoformat(), "what": f"forced wipe (map{bp})"})
                    break
        try:
            for line in (self.root / "config/schedule_once.conf").read_text().splitlines():
                parts = line.split()
                if len(parts) >= 3 and not line.startswith("#"):
                    when = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M").replace(tzinfo=UK)
                    if when > datetime.now(UK):
                        events.append({"when": when.isoformat(), "what": f"one-off {parts[2]}"})
        except (OSError, ValueError):
            pass
        return sorted(events, key=lambda e: e["when"])
