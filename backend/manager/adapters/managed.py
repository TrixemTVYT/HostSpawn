"""Servers that Manager runs and supervises itself (created by the setup helper, and Arma 3).
Runs the game in tmux, restarts it after a crash or a freeze, backs off on a crash loop (update at 3 failed starts,
give up at 6), retries SteamCMD updates 5 times and never retries a Steam login problem.
Each game subclass fills in: slug, app_id, exe, launch_args(), write_configs(), backup_paths(), log_file()."""
import fcntl
import os
import shutil
import signal
import tarfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..util import UK, add_event, newest, run, secrets, tail
from .base import Adapter, next_daily, proc_info

# SteamCMD output that means a human has to log in again. Retrying would only lock the account.
LOGIN_PROBLEMS = ("Steam Guard", "Two-factor", "two-factor", "Invalid Password", "Login Failure", "password:",
                  "No subscription", "UserActionRequired", "Cached credentials not found", "FAILED (Rate Limit")
GRACE_S = 600          # time a fresh start gets before missing health checks count
HANG_CHECKS = 5        # consecutive missed health checks (1/min) before a running server counts as hung
LOOP_UPDATE_AT = 3     # failed starts in a row before an update is tried
LOOP_GIVE_UP_AT = 6    # failed starts in a row before the watchdog stops trying and alerts


class Busy(Exception):
    pass


class ManagedServer(Adapter):
    slug = "server"          # file-name prefix for logs/backups
    app_id = ""              # SteamCMD app id
    exe_name = ""
    actions = ("start", "stop", "restart", "update", "backup")
    confirm = frozenset({"stop", "restart", "update"})
    supervised_by = "manager"
    install_hint = "not installed yet"
    stdout_to_log = True     # False: the game writes its own log file (launch_args uses self.current_log)
    backup_exclude: tuple[str, ...] = ()   # folder names left out of backups (e.g. logs, downloaded addons)
    steam_user_key = ""      # secrets.env key holding the Steam username; "" = anonymous login

    # ---- paths (all overridable in the server's config, which the tests use) ----
    @property
    def server_dir(self) -> Path: return Path(self.cfg.get("server_dir", self.root / "server"))
    @property
    def exe(self) -> Path: return Path(self.cfg.get("exe", self.server_dir / self.exe_name))
    @property
    def steamcmd(self) -> Path: return Path(self.cfg.get("steamcmd", self.root / "steamcmd/steamcmd.sh"))
    @property
    def session(self) -> str: return self.cfg.get("session", f"manager-{self.id}")
    @property
    def log_dir(self) -> Path: return self.root / "logs"

    def installed(self) -> bool:
        return self.exe.exists()

    # ---- game hooks ----
    def launch_args(self) -> list[str]:
        raise NotImplementedError

    def launch_env(self) -> dict[str, str]:
        return {}

    def write_configs(self) -> None:
        pass

    def backup_paths(self) -> list[Path]:
        return []

    def generated_files(self) -> list[Path]:
        """Files or folders HostSpawn rewrites at every start (the file editor shows them read-only)."""
        from .. import aihelp
        return [self.server_dir / "manager"] + [p for p in (self.root / "AGENTS.md", self.root / "CLAUDE.md")
                                                if aihelp.ours(p)]

    def graceful_stop(self) -> None:
        """Ask the game to save and quit (e.g. over RCON). Signals follow if it doesn't."""

    def post_update(self) -> None:
        """Runs after a successful SteamCMD update (e.g. reinstall a mod framework)."""

    def console_log(self) -> Path:
        """Where this start's console output goes (tmux pipes stdout/stderr here)."""
        return self.log_dir / f"{self.slug}_{datetime.now(UK):%Y-%m-%d_%H%M}.log"

    def steam_user(self) -> str:
        return secrets().get(self.steam_user_key, "") if self.steam_user_key else "anonymous"

    # ---- locking: one thing at a time, across the dashboard, CLI and the watchdog ----
    @contextmanager
    def lock(self, action: str = ""):
        """A named action (start/stop/...) waits up to 20 s for a routine watchdog check to finish, but is refused at
        once if another named action is running. The watchdog (no name) never waits."""
        f = open(self.flag(".lock"), "w")
        deadline = time.time() + (20 if action else 0)
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not action or self.has("busy") or time.time() >= deadline:
                    f.close()
                    raise Busy(f"another {self.game} action is running")
                time.sleep(0.2)
        if action:
            self.set_flag("busy", action)
        try:
            yield
        finally:
            if action:
                self.clear("busy")
            f.close()

    def pid(self) -> int | None:
        exe = str(self.exe)
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                if d.stat().st_uid != os.getuid():
                    continue
                argv0 = (d / "cmdline").read_bytes().split(b"\0", 1)[0].decode()
            except OSError:
                continue
            if argv0 == exe:
                return int(d.name)
        return None

    # ---- actions ----
    def start(self) -> str:
        with self.lock("start"):
            return self._start()

    def _start(self) -> str:
        self.clear("maintenance")
        if not self.installed():
            return self.install_hint
        if self.pid():
            return "already running"
        self.write_configs()
        try:  # the AI help guide (AGENTS.md); never stops a start
            from .. import aihelp
            aihelp.write(self)
        except Exception:
            pass
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.current_log = self.console_log()
        log.parent.mkdir(parents=True, exist_ok=True)
        out = log if self.stdout_to_log else self.log_dir / f"{self.slug}_stdout.txt"
        args = [str(self.exe)] + self.launch_args() + list(self.cfg.get("extra_args", []))
        q = lambda a: "'" + str(a).replace("'", "'\\''") + "'"
        if shutil.which("stdbuf"):  # write each log line at once: without it the game holds lines back in a buffer
            args = ["stdbuf", "-oL", "-eL"] + args
        env = " ".join(f"{k}={q(v)}" for k, v in self.launch_env().items())
        run(["tmux", "kill-session", "-t", "=" + self.session])  # "=": exact name; tmux otherwise matches prefixes
        code, out = run(["tmux", "new-session", "-d", "-s", self.session, "-c", str(self.exe.parent),
                         f"{'export ' + env + '; ' if env else ''}exec {' '.join(q(a) for a in args)} >>{q(out)} 2>&1"])
        if code != 0:
            return f"tmux failed: {out.strip()}"
        self.set_flag("started")
        self.clear("answered")
        self.set_flag("missed", "0")
        return "started"

    def stop(self) -> str:
        with self.lock("stop"):
            return self._stop()

    def _stop(self, maintenance: bool = True) -> str:
        """maintenance=True only for an explicit Stop ("leave it down"). Restarts and updates hold the lock, which
        already keeps the watchdog away; if Manager dies halfway, the watchdog brings the server back afterwards."""
        if maintenance:
            self.set_flag("maintenance")
        pid = self.pid()
        if not pid:
            run(["tmux", "kill-session", "-t", "=" + self.session])  # "=": exact name; tmux otherwise matches prefixes
            return "not running"
        try:
            self.graceful_stop()
            for _ in range(int(self.cfg.get("graceful_wait_s", 30)) if self._has_graceful() else 0):
                if not self.pid():
                    break
                time.sleep(1)
        except Exception:
            pass
        pid = self.pid()
        if pid:
            for sig, wait in ((signal.SIGINT, 60), (signal.SIGTERM, 20), (signal.SIGKILL, 5)):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    break
                for _ in range(wait):
                    if not self.pid():
                        break
                    time.sleep(1)
                else:
                    continue
                break
        run(["tmux", "kill-session", "-t", "=" + self.session])  # "=": exact name; tmux otherwise matches prefixes
        return "stopped" if not self.pid() else "WARNING: still running after SIGKILL"

    def _has_graceful(self) -> bool:
        return type(self).graceful_stop is not ManagedServer.graceful_stop

    def restart(self) -> str:
        with self.lock("restart"):
            self._stop(maintenance=False)
            return self._start()

    def update(self) -> str:
        with self.lock("update"):
            return self._update(restart=True)

    def steam_update(self, progress=None, proc_holder=None) -> tuple[bool, str]:
        """One SteamCMD run. progress(percent, text) is called as it downloads. Returns (ok, output)."""
        import re
        import subprocess
        user = self.steam_user()
        cmd = [str(self.steamcmd), "+force_install_dir", str(self.server_dir), "+login", user,
               "+app_update", self.app_id, "validate", "+quit"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             text=True, bufsize=1, start_new_session=True)
        if proc_holder is not None:
            proc_holder.append(p)
        lines = []
        for line in p.stdout:
            lines.append(line)
            m = re.search(r"Update state \(0x[0-9a-f]+\) (\w+), progress: ([\d.]+) \((\d+) / (\d+)\)", line)
            if m and progress and int(m[4]) > 0:
                progress(float(m[2]), m[1], int(m[3]), int(m[4]))
        p.wait()
        out = "".join(lines)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_dir / "steamcmd_update.log", "a") as f:
            f.write(out.replace(user, "<steam user>") if user != "anonymous" else out)
        return f"Success! App '{self.app_id}'" in out, out

    def _update(self, restart: bool) -> str:
        if not self.steamcmd.exists() or not self.installed():
            return self.install_hint
        if not self.steam_user():
            return "no Steam login saved"
        was_running = bool(self.pid())
        if was_running:
            self._stop(maintenance=False)
        result = "failed"
        for attempt in range(1, 6):
            ok, out = self.steam_update()
            if ok:
                self.clear("update_failed")
                self.clear("login_needed")
                try:
                    self.post_update()
                except Exception as e:
                    add_event(self.id, "warn", f"after update: {e}")
                result = "updated"
                break
            if any(p in out for p in LOGIN_PROBLEMS):
                self.set_flag("login_needed")
                add_event(self.id, "alert", f"Steam login needed for {self.game} updates")
                result = "Steam login needed (not retried)"
                break
            add_event(self.id, "warn", f"update: steamcmd failed (attempt {attempt}/5)")
            if attempt < 5:
                time.sleep(int(self.cfg.get("retry_wait_s", 60)))
        if result == "failed":
            self.set_flag("update_failed")
            add_event(self.id, "alert", "update failed 5 times; the watchdog retries every 15 min while it is stopped")
        if restart and was_running:
            self._start()
        return result

    def pre_backup(self) -> None:
        """Runs before the archive is made (e.g. dump a database into a folder that gets backed up)."""

    def backup(self) -> str:
        try:
            self.pre_backup()
        except Exception as e:
            add_event(self.id, "warn", f"backup: database dump failed ({e}); files are still backed up")
        dest = self.root / "backups"
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / f"{self.slug}_{datetime.now(UK):%Y-%m-%d_%H%M%S}.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            skip = set(self.backup_exclude)
            for p in self.backup_paths():
                if p.exists():
                    tar.add(p, arcname=p.name,
                            filter=lambda t: None if skip & set(Path(t.name).parts[1:2]) else t)
        os.chmod(out, 0o600)  # may contain configs with passwords
        for old in sorted(dest.glob(f"{self.slug}_*.tar.gz"))[:-int(self.cfg.get("keep_backups", 14))]:
            old.unlink()
        return f"backup written: {out.name} (keeps {int(self.cfg.get('keep_backups', 14))})"

    # ---- status / logs / schedule ----
    def status(self) -> dict:
        if not self.installed():
            return {"state": "not_installed", "flags": [self.install_hint[:1].upper() + self.install_hint[1:]]}
        s = {"flags": [], "max_players": self.cfg.get("max_players")}
        pid = self.pid()
        if pid:
            s.update(pid=pid, **proc_info(pid))
            s["state"] = "running" if self.has("answered") else "starting"
        else:
            s["state"] = "maintenance" if self.has("maintenance") else "stopped"
        if self.has("update_failed"):
            s["flags"].append("Last update failed")
        if self.has("login_needed"):
            s["flags"].append(self.login_hint())
        if self.counter("crashes") >= 2:
            s["flags"].append(f"{self.counter('crashes')} failed starts in a row")
        if self.has("gave_up"):
            s["flags"].append("Crash loop: watchdog stopped trying. Check the log, then press Start")
        return s

    def login_hint(self) -> str:
        return "Steam login needed"

    def log_file(self):
        return newest(self.log_dir, f"{self.slug}_*.log") if self.log_dir.exists() else None

    def logs(self):
        out = {}
        log = self.log_file()
        if log:
            out["console"] = tail(log, 150)
        if (self.log_dir / "steamcmd_update.log").exists():
            out["steamcmd"] = tail(self.log_dir / "steamcmd_update.log", 60)
        return out

    def schedule(self):
        return next_daily(self.cfg.get("restart_times", []))

    def backup_files(self):
        return list((self.root / "backups").glob(f"{self.slug}_*.tar.gz"))

    # ---- watchdog: called once a minute by the supervisor ----
    def watchdog(self, now: float | None = None) -> str | None:
        """Returns what it did (for the event log), or None."""
        if not self.installed() or self.has("maintenance"):
            return None
        try:
            with self.lock():
                return self._watchdog()
        except Busy:
            return None

    def _watchdog(self) -> str | None:
        pid = self.pid()
        if pid:
            if self.health():
                self.set_flag("answered")
                self.set_flag("crashes", "0")
                self.clear("gave_up")
                self.set_flag("missed", "0")
                return None
            if self.age("started") < float(self.cfg.get("grace_s", GRACE_S)):
                return None
            missed = self.counter("missed") + 1
            self.set_flag("missed", str(missed))
            if missed < int(self.cfg.get("hang_checks", HANG_CHECKS)):
                return None
            add_event(self.id, "alert", f"not answering queries for {missed} min - restarting (hung)")
            self._stop(maintenance=False)
            self._start()
            return "restarted (hung)"
        # Not running and not in maintenance: it crashed.
        n = 1 if self.has("answered") else self.counter("crashes") + 1
        self.set_flag("crashes", str(n))
        if n >= LOOP_GIVE_UP_AT:
            self.set_flag("maintenance")
            self.set_flag("gave_up")
            add_event(self.id, "alert", f"CRASH LOOP: {n} failed starts - stopped trying. Check the console log.")
            return "gave up"
        if n >= LOOP_UPDATE_AT and self.age("recovered") >= 1200:
            self.set_flag("recovered")
            add_event(self.id, "alert", f"CRASH LOOP: {n} failed starts - updating before the next start")
            self._update(restart=False)
        elif self.has("update_failed") and self.age("update_failed") >= 900 and not self.has("login_needed"):
            self.set_flag("update_failed")
            self._update(restart=False)
        add_event(self.id, "alert", "server not running - restarting")
        self._start()
        return "restarted (crash)"
