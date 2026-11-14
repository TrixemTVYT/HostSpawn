"""Installation management for instances HostSpawn runs itself: what's installed and how big, whether Steam has a
newer version, a safe update (backup first, check it comes back, put settings and saves back if it doesn't), check
and repair, reinstall keeping saves, clean up, and a history of all of it.

One installation job per instance at a time; it holds the instance's lock, so the watchdog and other actions wait.
Jobs run in a thread and save their state to state/<id>/install_job.json: if HostSpawn stops halfway, the next start
records the job as interrupted (the watchdog brings a stopped server back, and a half-done reinstall gets its kept
saves put back). Steam only offers the newest game files, so game files can't be rolled back; settings and saves can.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import tarfile
import threading
import time
from pathlib import Path

from . import util
from .adapters.managed import LOGIN_PROBLEMS, Busy, ManagedServer
from .util import add_event

OPS = ("update", "repair", "reinstall", "cleanup", "check")
CHECK_EVERY_S = 3600          # how often Steam is asked for the newest build of each game
FIRST_CHECK_S = 300           # after HostSpawn starts, wait this long before the first automatic check
SIZE_CACHE_S = 120            # folder sizes are counted at most this often (big games have many files)
KEEP_DIR = ".reinstall-keep"  # saves and settings parked here during a reinstall, inside the instance folder
DISK_FULL = ("No space left", "Not enough disk space", "Disk write failure", "disk full", "0x202")
LOG_KEEP_DAYS, LOG_KEEP_MIN = 14, 5


class InstallError(Exception):
    """A step that can't go on, with a plain-English reason for the owner."""


def dir_size(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path, followlinks=False):
        for f in files:
            try:
                st = os.lstat(os.path.join(dirpath, f))
                total += st.st_size
            except OSError:
                pass
    return total


def gb(n: float) -> float:
    return round(n / 2**30, 2)


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def installed_build(a: ManagedServer) -> dict:
    """{"build": "123", "updated": unix time} from Steam's own record in the install, or {} if there is none."""
    f = a.server_dir / "steamapps" / f"appmanifest_{a.app_id}.acf"
    try:
        text = f.read_text(errors="replace")
    except OSError:
        return {}
    build = re.search(r'"buildid"\s*"(\d+)"', text)
    updated = re.search(r'"LastUpdated"\s*"(\d+)"', text)
    return {"build": build[1] if build else None, "updated": int(updated[1]) if updated else None}


def parse_latest_build(text: str) -> str | None:
    """The public branch's buildid from `steamcmd +app_info_print <id>` output."""
    m = re.search(r'"branches"\s*\{\s*"public"\s*\{[^{}]*?"buildid"\s*"(\d+)"', text, re.S)
    return m[1] if m else None


def restore_backup(a: ManagedServer, archive: Path) -> list[str]:
    """Put an instance's settings and saves back from one of its backups. Only folders inside the instance's own
    folder are touched (Arma 3's shared profile folder is left alone). Returns the names put back."""
    done = []
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        for p in a.backup_paths():
            if not inside(p, a.root):
                continue
            mine = [m for m in members if m.name == p.name or m.name.startswith(p.name + "/")]
            if not mine:
                continue
            aside = p.with_name(p.name + ".before-restore")
            shutil.rmtree(aside, ignore_errors=True)
            if p.exists():
                p.rename(aside)
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(p.parent, members=mine, filter="data")  # refuses ../ and absolute paths
                else:
                    for m in mine:
                        if m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() or m.islnk():
                            raise InstallError(f"unsafe file in backup: {m.name}")
                    tar.extractall(p.parent, members=mine)
            except Exception:
                shutil.rmtree(p, ignore_errors=True)
                if aside.exists():
                    aside.rename(p)
                raise
            shutil.rmtree(aside, ignore_errors=True)
            done.append(p.name)
    return done


class InstallManager:
    def __init__(self, sup):
        self.sup = sup
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._sizes: dict[str, tuple[float, dict]] = {}
        self._checking = False
        # the first automatic check waits FIRST_CHECK_S after start-up (a quick restart doesn't ask Steam again)
        self._last_check = time.time() - CHECK_EVERY_S + FIRST_CHECK_S
        for sid, a in list(sup.adapters.items()):
            if isinstance(a, ManagedServer):
                self._recover(a)

    # ---- state files ----
    @staticmethod
    def _builds_file() -> Path:
        return util.STATE / "steam_builds.json"

    def _builds(self) -> dict:
        try:
            return json.loads(self._builds_file().read_text())
        except (OSError, ValueError):
            return {}

    def history(self, a, limit: int = 30) -> list[dict]:
        try:
            lines = a.flag("install_history.jsonl").read_text().splitlines()
        except OSError:
            return []
        out = []
        for line in reversed(lines[-200:]):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out[:limit]

    def _record(self, a, job: dict):
        entry = {k: job.get(k) for k in ("id", "op", "result", "summary", "by", "started", "finished",
                                         "size_before", "size_after")}
        a.state_dir.mkdir(parents=True, exist_ok=True)
        with open(a.flag("install_history.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _save(self, a, job: dict):
        a.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = a.flag("install_job.json.tmp")
        tmp.write_text(json.dumps({k: v for k, v in job.items() if not k.startswith("_")}))
        os.replace(tmp, a.flag("install_job.json"))

    def _recover(self, a):
        """A job that was running when HostSpawn stopped: record it as interrupted and undo a half-done reinstall."""
        f = a.flag("install_job.json")
        try:
            job = json.loads(f.read_text())
        except (OSError, ValueError):
            job = None
        keep = a.root / KEEP_DIR
        note = ""
        if keep.exists():
            try:
                self._unpark(a, keep)
                note = " Saves and settings that were set aside for the reinstall were put back."
            except Exception as e:
                note = f" Saves and settings are still set aside in {keep}: {e}"
                add_event(a.id, "alert", f"interrupted reinstall: {note.strip()}")
        if job and job.get("state") == "running":
            job.update(state="failed", result="interrupted", finished=time.time(),
                       summary=f"Interrupted: HostSpawn stopped while this was running.{note} The watchdog starts "
                               "the server again if it was running. Run Check and repair to make sure the files are whole.")
            self._record(a, job)
            add_event(a.id, "warn", f"{job.get('op')} was interrupted when HostSpawn stopped")
        f.unlink(missing_ok=True)
        if a.has("busy") and a.flag("busy").read_text().strip() == "install":
            a.clear("busy")

    # ---- what's installed ----
    def sizes(self, a, fresh: bool = False) -> dict:
        hit = self._sizes.get(a.id)
        if hit and not fresh and time.time() - hit[0] < SIZE_CACHE_S:
            return hit[1]
        saves = sum(dir_size(p) for p in a.backup_paths() if p.exists() and inside(p, a.root))
        game = dir_size(a.server_dir) if a.server_dir.exists() else 0
        # bytes (the app shows MB or GB)
        s = {"game": max(0, game - sum(dir_size(p) for p in a.backup_paths() if p.exists() and inside(p, a.server_dir))),
             "saves": saves,
             "logs": dir_size(a.log_dir) if a.log_dir.exists() else 0,
             "backups": dir_size(a.root / "backups") if (a.root / "backups").exists() else 0}
        s["total"] = sum(s.values())
        self._sizes[a.id] = (time.time(), s)
        return s

    def update_available(self, sid: str) -> bool | None:
        a = self.sup.adapters.get(sid)
        if not isinstance(a, ManagedServer) or not a.app_id:
            return None
        latest = self._builds().get(a.app_id, {}).get("build")
        have = installed_build(a).get("build")
        return None if not (latest and have) else latest != have

    def info(self, sid: str) -> dict:
        a = self._adapter(sid)
        du = shutil.disk_usage(a.root if a.root.exists() else a.root.parent)
        latest = self._builds().get(a.app_id, {})
        have = installed_build(a)
        job = self.jobs.get(sid)
        return {"server": sid, "game": a.game, "mode": a.cfg.get("mode_name"), "framework": a.cfg.get("framework"),
                "folder": str(a.root), "installed": a.installed(), "running": bool(a.pid()),
                "sizes": self.sizes(a), "disk_free_gb": gb(du.free),
                "build": have.get("build"), "updated": have.get("updated"),
                "latest_build": latest.get("build"), "checked": latest.get("checked"),
                "check_error": latest.get("error"), "update_available": self.update_available(sid),
                "next_restart": next((e["when"] for e in a.schedule()), None) if a.cfg.get("restart_times") else None,
                "job": {k: v for k, v in job.items() if not k.startswith("_")} if job else None,
                "history": self.history(a)}

    def _adapter(self, sid: str) -> ManagedServer:
        a = self.sup.adapters.get(sid)
        if a is None:
            raise KeyError("unknown instance")
        if not isinstance(a, ManagedServer):
            raise InstallError("HostSpawn didn't install this instance (it runs with its own scripts), so its "
                               "installation is managed there.")
        return a

    # ---- update check ----
    def latest_build(self, a: ManagedServer) -> str:
        """Ask Steam for the newest public build of this game (no download)."""
        if not a.steamcmd.exists():
            raise InstallError("SteamCMD isn't installed for this instance")
        user = a.steam_user() or "anonymous"
        try:
            out = subprocess.run([str(a.steamcmd), "+login", user, "+app_info_update", "1",
                                  "+app_info_print", a.app_id, "+quit"], capture_output=True, text=True,
                                 timeout=int(a.cfg.get("app_info_timeout_s", 120)), stdin=subprocess.DEVNULL).stdout
        except subprocess.TimeoutExpired:
            raise InstallError("Steam didn't answer in time")
        build = parse_latest_build(out)
        if not build:
            if any(p in out for p in LOGIN_PROBLEMS):
                raise InstallError("Steam login needed to check for updates")
            raise InstallError("Steam didn't say which version is newest")
        return build

    def check_updates(self, force: bool = False):
        """Once an hour (from the supervisor), one Steam question per game. Runs in its own thread."""
        if self._checking or (not force and time.time() - self._last_check < CHECK_EVERY_S):
            return
        self._checking, self._last_check = True, time.time()

        def work():
            try:
                done = {}
                for a in [x for x in list(self.sup.adapters.values()) if isinstance(x, ManagedServer) and x.app_id]:
                    if a.app_id in done or not a.installed():
                        continue
                    try:
                        done[a.app_id] = {"build": self.latest_build(a), "checked": time.time()}
                    except InstallError as e:
                        done[a.app_id] = {**self._builds().get(a.app_id, {}), "checked": time.time(), "error": str(e)}
                cur = self._builds()
                cur.update(done)
                util.STATE.mkdir(parents=True, exist_ok=True)
                tmp = self._builds_file().with_suffix(".tmp")
                tmp.write_text(json.dumps(cur))
                os.replace(tmp, self._builds_file())
                for sid, a in list(self.sup.adapters.items()):
                    if self.update_available(sid) and not a.has("update_noted"):
                        a.set_flag("update_noted")
                        add_event(sid, "info", "a game update is available (Installation tab)")
                    elif self.update_available(sid) is False:
                        a.clear("update_noted")
            finally:
                self._checking = False
        threading.Thread(target=work, daemon=True, name="install-check").start()

    # ---- jobs ----
    def start(self, sid: str, op: str, by: str = "", confirm: str = "", warn_s: int = 0) -> dict:
        if op not in OPS:
            raise InstallError(f"unknown action: {op}")
        a = self._adapter(sid)
        if op == "check":
            self._last_check = 0
            self.check_updates(force=True)
            return {"op": "check", "state": "running", "summary": "Asking Steam for the newest version…"}
        if op == "reinstall" and confirm != a.name:
            raise InstallError(f'Type the instance\'s name ("{a.name}") to confirm the reinstall.')
        if op == "update" and not a.installed():
            raise InstallError("The game program is missing, so there's nothing to update. Run Check and repair.")
        if op in ("update", "repair") and not a.steamcmd.exists():
            raise InstallError("SteamCMD isn't installed for this instance (finish or retry its setup first).")
        with self._lock:
            cur = self.jobs.get(sid)
            if cur and cur["state"] == "running":
                raise InstallError(f"{cur['op'].capitalize()} is already running on this instance.")
            job = {"id": os.urandom(4).hex(), "op": op, "state": "running", "by": by, "started": time.time(),
                   "step": "Starting", "progress": None, "log": [], "result": None, "summary": "",
                   "warn_s": max(0, min(1800, int(warn_s or 0)))}
            self.jobs[sid] = job
        self._save(a, job)
        threading.Thread(target=self._run, args=(a, job), daemon=True, name=f"install-{sid}").start()
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def _step(self, a, job, text: str, progress: float | None = None):
        job["step"], job["progress"] = text, progress
        job["log"].append(f"{time.strftime('%H:%M:%S')} {text}")
        job["log"] = job["log"][-60:]
        self._save(a, job)

    def _run(self, a: ManagedServer, job: dict):
        op = job["op"]
        job["size_before"] = self.sizes(a, fresh=True)["total"]
        try:
            with a.lock("install"):
                summary = getattr(self, f"_op_{op}")(a, job)
            job.update(state="done", result="ok", summary=summary)
            add_event(a.id, "info", f"{op}: {summary}" + (f" (by {job['by']})" if job["by"] else ""))
        except Busy:
            job.update(state="failed", result="failed",
                       summary=f"Another {a.game} action is running on this instance. Try again when it has finished.")
        except InstallError as e:
            job.update(state="failed", result="failed", summary=str(e))
            add_event(a.id, "alert", f"{op} failed: {e}")
        except Exception as e:  # never leave a job "running" forever
            job.update(state="failed", result="failed", summary=f"Something went wrong: {e}")
            add_event(a.id, "alert", f"{op} failed: {e}")
        job["finished"] = time.time()
        job["size_after"] = self.sizes(a, fresh=True)["total"]
        job["step"], job["progress"] = ("Done" if job["result"] == "ok" else "Failed"), None
        self._record(a, job)
        a.flag("install_job.json").unlink(missing_ok=True)

    # ---- shared steps ----
    def _need_disk(self, a, need_gb: float):
        free = shutil.disk_usage(a.root).free / 2**30
        if free < need_gb:
            raise InstallError(f"Not enough free disk space: this needs about {need_gb:.1f} GB and {free:.1f} GB is "
                               "free. Run Clean up or free some space, then try again.")

    def _steam(self, a, job, label: str):
        """SteamCMD (download, or check every file). Retries a network hiccup; never retries a login or a full disk."""
        tries = int(a.cfg.get("install_tries", 3))
        for attempt in range(1, tries + 1):
            def progress(pct, state, done_b, total_b):
                self._step(a, job, f"{label}: {pct:.0f}% ({gb(done_b):.1f} of {gb(total_b):.1f} GB)", pct)
            ok, out = a.steam_update(progress=progress, proc_holder=[])
            if ok:
                a.clear("update_failed")
                a.clear("login_needed")
                return
            if any(p in out for p in LOGIN_PROBLEMS):
                a.set_flag("login_needed")
                raise InstallError("Steam needs you to log in again (Steam Guard or password). Use Log in to Steam "
                                   "on the Overview tab, then try again.")
            if any(p in out for p in DISK_FULL):
                raise InstallError("The disk filled up during the download. Free some space (Clean up helps), then "
                                   "run Check and repair.")
            self._step(a, job, f"{label}: Steam didn't finish (try {attempt} of {tries})")
            if attempt < tries:
                time.sleep(int(a.cfg.get("retry_wait_s", 30)))
        a.set_flag("update_failed")
        raise InstallError(f"Steam couldn't finish after {tries} tries. The last lines are in Console & logs "
                           "(SteamCMD). Try again later.")

    def _post_update(self, a, job):
        try:
            a.post_update()
        except Exception as e:
            self._step(a, job, f"after the download: {e}")

    def _comes_back(self, a, job, timeout_s: float | None = None) -> bool:
        """Start it and wait until it answers like a game server (or crashes, or time runs out)."""
        timeout_s = float(timeout_s or a.cfg.get("install_check_s", 300))
        t0 = time.time()
        a._start()
        while time.time() < t0 + timeout_s:
            if a.health():
                a.set_flag("answered")
                a.set_flag("crashes", "0")
                return True
            if not a.pid() and time.time() - t0 > 3:
                return False  # it exited (or never started): it won't answer
            time.sleep(1)
        return False

    def _safety_backup(self, a, job) -> Path | None:
        self._step(a, job, "Backing up settings and saves first")
        try:
            a.backup()
        except Exception as e:
            raise InstallError(f"The safety backup failed ({e}), so nothing was changed.")
        files = sorted(a.backup_files(), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None

    def _warn_players(self, a, job, what: str):
        if job["warn_s"] and a.pid() and a.can_say():
            self._step(a, job, f"Warning players: {what} in {job['warn_s'] // 60 or 1} min")
            a.countdown(job["warn_s"], what)

    # ---- update ----
    def _op_update(self, a, job) -> str:
        game_gb = self.sizes(a)["game"] / 2**30
        self._need_disk(a, max(2.0, game_gb * 0.2))
        was_running = bool(a.pid())
        self._warn_players(a, job, "update")
        backup = self._safety_backup(a, job)
        before = installed_build(a).get("build")
        if was_running:
            self._step(a, job, "Stopping the server")
            a._stop(maintenance=False)
        try:
            self._steam(a, job, "Downloading the update")
        except InstallError:
            if was_running:
                self._step(a, job, "Starting the server again on the files it has")
                a._start()
            raise
        self._post_update(a, job)
        after = installed_build(a).get("build")
        changed = f"version {before} → {after}" if before and after and before != after else "already the newest version"
        if not was_running:
            return f"Updated ({changed}). It was stopped, so it wasn't started."
        self._step(a, job, "Starting and checking it answers")
        if self._comes_back(a, job):
            return f"Updated ({changed}) and running."
        self._step(a, job, "It didn't come back: putting settings and saves back")
        a._stop(maintenance=False)
        put_back = restore_backup(a, backup) if backup else []
        ok = self._comes_back(a, job)
        raise InstallError("The update downloaded, but the server didn't start with it. "
                           + (f"Settings and saves were put back from the safety backup ({', '.join(put_back)}). "
                              if put_back else "")
                           + ("It's running again. " if ok else "It still won't start: check Console & logs. ")
                           + "Steam only keeps the newest game files, so those stay on the new version.")

    # ---- check and repair ----
    def _op_repair(self, a, job) -> str:
        self._need_disk(a, 1.0)
        was_running = bool(a.pid())
        self._warn_players(a, job, "restart")
        if was_running:
            self._step(a, job, "Stopping the server (files are checked while it's off)")
            a._stop(maintenance=False)
        self._steam(a, job, "Checking every game file")
        self._post_update(a, job)
        fixed, problems = self._self_check(a, job, was_running)
        if was_running:
            self._step(a, job, "Starting and checking it answers")
            if not self._comes_back(a, job):
                problems.append("it didn't answer after starting: check Console & logs")
        parts = ["Game files checked (any broken or missing ones were downloaded again)"]
        if fixed:
            parts.append("fixed: " + "; ".join(fixed))
        if problems:
            raise InstallError(". ".join(parts) + ". Still wrong: " + "; ".join(problems) + ".")
        return ". ".join(parts) + "." + ("" if was_running else " It was stopped, so it wasn't started.")

    def _self_check(self, a, job, running: bool) -> tuple[list[str], list[str]]:
        self._step(a, job, "Checking HostSpawn's own setup")
        fixed, problems = [], []
        if a.exe.exists() and not os.access(a.exe, os.X_OK):
            a.exe.chmod(a.exe.stat().st_mode | 0o111)
            fixed.append("the server program wasn't runnable")
        if not a.exe.exists():
            problems.append("the server program is missing after the check")
        try:
            a.write_configs()
        except Exception as e:
            problems.append(f"its settings files couldn't be written ({e})")
        uid, others = os.getuid(), 0
        for dirpath, dirs, files in os.walk(a.root):
            for n in dirs + files:
                try:
                    if os.lstat(os.path.join(dirpath, n)).st_uid != uid:
                        others += 1
                except OSError:
                    pass
        if others:
            problems.append(f"{others} files belong to another user, so they can't be changed. Fix with: "
                            f"sudo chown -R $USER: '{a.root}'")
        if not running:
            for port in sorted({p for p in (a.cfg.get("port"), a.query_port()) if p}):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    try:
                        s.bind(("0.0.0.0", int(port)))
                    except OSError:
                        problems.append(f"port {port} is used by another program")
        return fixed, problems

    # ---- reinstall ----
    def _park(self, a) -> Path:
        """Move this instance's saves and settings (those inside the game folder) out of the way, keeping their paths."""
        keep = a.root / KEEP_DIR
        if keep.exists():
            raise InstallError(f"A previous reinstall left saves set aside in {keep}. Restart HostSpawn to put them back.")
        keep.mkdir()
        manifest = []
        for p in a.backup_paths():
            if p.exists() and inside(p, a.server_dir):
                rel = p.resolve().relative_to(a.server_dir.resolve())
                dest = keep / "files" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(dest))
                manifest.append(str(rel))
        (keep / "manifest.json").write_text(json.dumps(manifest))
        return keep

    def _unpark(self, a, keep: Path):
        manifest = json.loads((keep / "manifest.json").read_text()) if (keep / "manifest.json").exists() else []
        for rel in manifest:
            src, dest = keep / "files" / rel, a.server_dir / rel
            if not src.exists():
                continue
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        shutil.rmtree(keep)

    def _op_reinstall(self, a, job) -> str:
        from . import catalog
        expected = float(catalog.GAMES.get(a.cfg.get("game_id", ""), {}).get("size_gb", 10))
        have = self.sizes(a, fresh=True)["game"] / 2**30
        self._need_disk(a, max(1.0, expected - have) + 1.0)
        was_running = bool(a.pid())
        self._warn_players(a, job, "reinstall")
        self._safety_backup(a, job)
        if was_running:
            self._step(a, job, "Stopping the server")
        a._stop(maintenance=False)
        self._step(a, job, "Setting saves and settings aside")
        keep = self._park(a)
        try:
            self._step(a, job, "Removing the old game files")
            if inside(a.server_dir, a.root) and a.server_dir.exists():
                shutil.rmtree(a.server_dir)
            a.server_dir.mkdir(parents=True, exist_ok=True)
            self._steam(a, job, "Downloading fresh game files")
            self._post_update(a, job)
        finally:
            self._step(a, job, "Putting saves and settings back")
            self._unpark(a, keep)
        a.write_configs()
        if not was_running:
            return "Reinstalled: fresh game files, your saves and settings kept. It was stopped, so it wasn't started."
        self._step(a, job, "Starting and checking it answers")
        if not self._comes_back(a, job):
            raise InstallError("Reinstalled with your saves and settings kept, but it didn't answer after starting. "
                               "Check Console & logs.")
        return "Reinstalled: fresh game files, your saves and settings kept, and it's running."

    # ---- clean up ----
    def _op_cleanup(self, a, job) -> str:
        freed, what = 0, []

        def rm(p: Path):
            nonlocal freed
            if p.is_dir() and not p.is_symlink():
                freed += dir_size(p)
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                freed += p.stat().st_size
                p.unlink()

        self._step(a, job, "Old logs")
        if a.log_dir.exists():
            logs = sorted((p for p in a.log_dir.glob(f"{a.slug}_*.log") if p.is_file()), key=lambda p: p.stat().st_mtime)
            cutoff = time.time() - LOG_KEEP_DAYS * 86400
            old = [p for p in logs[:-LOG_KEEP_MIN] if p.stat().st_mtime < cutoff]
            for p in old:
                rm(p)
            if old:
                what.append(f"{len(old)} old logs")
            sc = a.log_dir / "steamcmd_update.log"
            if sc.exists() and sc.stat().st_size > 5 * 2**20:
                size = sc.stat().st_size
                with open(sc, "rb") as f:
                    f.seek(-2**20, os.SEEK_END)
                    tail = f.read()
                sc.write_bytes(tail)
                freed += size - len(tail)
                what.append("the SteamCMD log (kept its last 1 MB)")
        self._step(a, job, "Download leftovers")
        n = 0
        for d in ("downloading", "temp"):
            p = a.server_dir / "steamapps" / d
            if p.exists():
                for child in p.iterdir():
                    rm(child)
                    n += 1
        if n:
            what.append("SteamCMD download leftovers")
        self._step(a, job, "Backups beyond the number to keep")
        keep_n = int(a.cfg.get("keep_backups", 14))
        extra = sorted(a.backup_files(), key=lambda p: p.stat().st_mtime)[:-keep_n] if keep_n > 0 else []
        for p in extra:
            rm(p)
        if extra:
            what.append(f"{len(extra)} old backups (keeping {keep_n})")
        for p in [x for x in a.server_dir.rglob("*.before-restore")] if a.server_dir.exists() else []:
            rm(p)
        if not what:
            return "Nothing to clean up."
        return f"Freed {gb(freed):.2f} GB: " + ", ".join(what) + "."
