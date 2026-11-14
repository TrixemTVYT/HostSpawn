"""Arma Reforger (basic): wraps the project's systemd unit and scripts. systemd restarts it after a crash
(Restart=on-failure) and updates the game before every start (ExecStartPre=update-game.sh), so "update"
is a restart. The manager doesn't supervise it; it watches health and raises alerts."""
import re

from ..util import newest, redact, run, run_background, tail
from .base import Adapter, proc_info


def console_stats(log) -> dict:
    """Players and FPS from the newest -logStats line of a Reforger console.log."""
    m = None
    for m in re.finditer(r"FPS: ([\d.]+).*?Mem: (\d+) kB, Player: (\d+)", tail(log, 400)):
        pass
    return {"fps": float(m[1]), "players": int(m[3])} if m else {}


class ReforgerAdapter(Adapter):
    game = "Arma Reforger"
    actions = ("start", "stop", "restart", "update", "backup")
    confirm = frozenset({"stop", "restart", "update"})
    supervised_by = "systemd"
    log_alert_s = 600  # started with -logStats 60000: the console log gets an FPS line every minute

    @property
    def unit(self) -> str:
        return self.cfg.get("unit", "reforger")

    @property
    def server_dir(self):
        return self.root / "Server"

    def _systemctl(self, verb: str) -> str:
        run_background(self.id, verb, ["sudo", "-n", "systemctl", verb, self.unit])
        return f"{verb} sent to systemd unit {self.unit}"

    def start(self): return self._systemctl("start")
    def stop(self): return self._systemctl("stop")
    def restart(self): return self._systemctl("restart")

    def update(self):
        self._systemctl("restart")
        return "restarting: the game update (SteamCMD) runs before every start"

    def backup(self):
        script = self.server_dir / "scripts/backup.sh"
        run_background(self.id, "backup", [str(script)], cwd=self.root)
        return "backup started (Server/scripts/backup.sh, keeps the newest 14 in Backups/)"

    def log_file(self):
        return self._console_log()

    def backup_files(self):
        return list((self.root / "Backups").glob("profile-*.tar.gz"))

    def _console_log(self):
        d = newest(self.server_dir / "profile/logs", "logs_*")
        return d / "console.log" if d else None

    def status(self):
        code, out = run(["systemctl", "show", self.unit, "-p", "ActiveState,SubState,MainPID,NRestarts,LoadState"])
        if code != 0:
            return {"state": "unknown"}
        p = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if p.get("LoadState") == "not-found":
            return {"state": "not_installed"}
        s = {"flags": [], "restarts": int(p.get("NRestarts", 0) or 0)}
        active = p.get("ActiveState")
        s["state"] = {"active": "running", "activating": "starting", "deactivating": "stopping"}.get(active, "stopped")
        if active == "failed":
            s["flags"].append("systemd gave up restarting it (failed)")
        pid = int(p.get("MainPID", 0) or 0)
        if pid:
            s["pid"] = pid
            s.update(proc_info(pid))
        log = self._console_log()
        if log and s["state"] == "running":
            s.update(console_stats(log))
        s["max_players"] = self.cfg.get("max_players")
        return s

    def logs(self):
        out = {}
        log = self._console_log()
        if log:
            out["console"] = tail(log, 150)
        code, j = run(["journalctl", "-u", self.unit, "-n", "80", "--no-pager"], timeout=10)
        if code == 0:
            out["systemd"] = redact(j)
        return out
