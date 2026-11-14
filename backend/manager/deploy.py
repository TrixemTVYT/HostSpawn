"""Setup helper back end: create a server from the catalogue as a job of steps the dashboard can follow live.

A job runs its steps in order. "Auto" steps report progress (percent, detail, speed). "Guided" steps are things the
person must do themselves (a Steam login, opening firewall ports): the job pauses, the dashboard shows what to do,
and Continue runs a check before moving on (Back returns to the previous guided step, or to the settings).
Jobs are saved to state/jobs/<id>.json, so a Manager restart shows them as interrupted, with Retry.

Delete removes a server Manager created: stop it, delete its folder, settings and secrets (backups can be kept)."""
import io
import json
import os
import re
import secrets as _rand
import shutil
import signal
import socket
import tarfile
import threading
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

from . import catalog, instances
from .adapters import ADAPTERS
from .adapters.managed import LOGIN_PROBLEMS
from .util import (CONFIG, STATE, add_event, community, instance, load_servers, new_secret, remove_secrets,
                   run, secrets, servers_dir, set_secret)

JOBS = STATE / "jobs"
MARKER = ".manager-server"   # in every folder Manager creates; delete refuses folders without it
# Layout of a created server (servers/<game>/<name>-<4 letters>/):
#   server/ game files · logs/ · backups/ · manager/ (private, 700: database login, setup files) · README.txt
PRIVATE = "manager"
SHARED_STEAMCMD = "_shared/steamcmd/steamcmd.sh"  # one SteamCMD for every server
LEGACY_STEAMCMD = ".steamcmd/steamcmd.sh"         # where it was before 2026-11-06


def folder_name(name: str, sid: str) -> str:
    """A readable, safe folder name: "Riverside 2x Main!" -> "riverside-2x-main-3fa2" (the end comes from the ID)."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()  # "Ünï" -> "Uni"
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:32].strip("-") or "server"
    return f"{slug}-{sid.rsplit('-', 1)[-1]}"


def steamcmd_path() -> Path:
    """The shared SteamCMD. An older download in the old place is reused rather than fetched again."""
    new, old = servers_dir() / SHARED_STEAMCMD, servers_dir() / LEGACY_STEAMCMD
    return old if old.exists() and not new.exists() else new


def _private(job) -> Path:
    p = Path(job.d["root"]) / PRIVATE
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def _work(job) -> Path:
    p = _private(job) / "setup"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_readme(job, cfg: dict) -> None:
    ports = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in job.d["ports"].items())
    text = f"""{cfg['name']}
{'=' * len(cfg['name'])}
Game: {job.d['game_name']} ({job.d['mode_name']})
Created by HostSpawn on {cfg.get('created', time.strftime('%Y-%m-%d %H:%M'))}. Server ID: {job.d['server_id']}
Ports: {ports}

Folders
  server/    the game's own files. Mods, missions and configs live here.
  logs/      the server's logs (HostSpawn shows them in Console & logs).
  backups/   backups HostSpawn makes before updates, wipes and on schedule.
  manager/   HostSpawn's private files for this server (only your user can open it). Don't share it.
{"  steamcmd/  this server's own SteamCMD (it remembers the Steam login used to download the game)." + chr(10) if job.d["game"] == "arma3" else ""}{"  tmp/       the game's temporary add-on downloads (safe to empty while the server is stopped)." + chr(10) if job.d["game"] == "reforger" else ""}
Start, stop, restart, update and back up this server from HostSpawn. Editing files in server/ is fine:
restart the server afterwards. Don't delete the hidden {MARKER} file: it's how HostSpawn knows this folder is
safe to manage. To remove the server, use Delete server in HostSpawn (it can keep the backups).
"""
    (Path(job.d["root"]) / "README.txt").write_text(text)


def _remove_empty_game_folder(root: Path, base: Path) -> None:
    """After a delete: tidy away servers/<game>/ if it's now empty (never anything outside servers/)."""
    parent = root.parent
    if parent != base and str(parent).startswith(str(base) + "/"):
        try:
            parent.rmdir()  # only succeeds when empty
        except OSError:
            pass
STEAMCMD_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
EXTDB3_URL = "https://github.com/SteezCram/extDB3/releases/download/v1033.1/@extDB3.v1032-linux.zip"


def mariadb_listening(host: str = "127.0.0.1", port: int = 3306) -> bool:
    """True if a MySQL/MariaDB server sends its greeting (protocol 10) on host:port."""
    try:
        with socket.create_connection((host, port), timeout=3) as s:
            s.settimeout(3)
            data = s.recv(64)
        return len(data) > 4 and data[4] == 10
    except OSError:
        return False


class Cancelled(Exception):
    pass


class StepFailed(Exception):
    pass


# ---------------------------------------------------------------- ports
def _used_ports(servers: dict) -> set[int]:
    used = set()
    for cfg in servers.values():
        for k in ("port", "query_port", "rcon_port", "app_port"):
            if isinstance(cfg.get(k), int) and cfg[k] > 0:
                used.add(cfg[k])
        if cfg.get("adapter") == "arma3":
            used.update(range(int(cfg.get("port", 2302)), int(cfg.get("port", 2302)) + 5))
    return used


def port_free(port: int) -> bool:
    for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM):
        with socket.socket(socket.AF_INET, kind) as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                return False
    return True


def allocate_ports(game: str, servers: dict | None = None) -> dict:
    """Free ports for a new server, away from every configured server and anything already listening."""
    used = _used_ports(load_servers() if servers is None else servers)
    ok = lambda ps: all(p not in used and 1024 < p < 65535 and port_free(p) for p in ps)
    if game == "rust":
        for base in range(28015, 60000, 100):
            if ok([base, base + 1, base + 2]):
                return {"port": base, "rcon_port": base + 1, "query_port": base + 2}
    if game == "arma3":
        for base in range(2302, 60000, 100):
            if ok([*range(base, base + 5), base + 7]):  # game, query, Steam, VON, BattlEye; RCon on +7
                return {"port": base, "rcon_port": base + 7}
    if game == "reforger":
        out = {}
        for key, start in (("port", 2001), ("query_port", 17777), ("rcon_port", 19999)):
            p = start
            while not ok([p]) or p in out.values():
                p += 1
            out[key] = p
        return out
    raise ValueError(f"no port plan for {game}")


# ---------------------------------------------------------------- small helpers
def toml_dump(servers: dict) -> str:
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        if isinstance(v, dict):  # inline table, e.g. a scheduled message {text = "...", every_min = 30}
            return "{ " + ", ".join(f"{json.dumps(str(k))} = {val(x)}" for k, x in v.items() if x is not None) + " }"
        return json.dumps(str(v))  # a JSON string is a valid TOML basic string
    out = []
    for sid, cfg in servers.items():
        out.append(f"[servers.{sid}]")
        out += [f"{k} = {val(v)}" for k, v in cfg.items() if not k.startswith("_") and v is not None]
        out.append("")
    return "\n".join(out)


def _download(url: str, job, label: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "HostSpawn"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf, got, t0 = io.BytesIO(), 0, time.time()
        while True:
            job.check_cancel()
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            job.progress(got * 100 / total if total else None, f"{label}: {got // 1048576} MB",
                         speed=got / max(time.time() - t0, 0.1), done=got, total=total or None)
        return buf.getvalue()


def _safe_extract_zip(data: bytes, dest: Path, only_suffix: str = "") -> list[str]:
    dest = dest.resolve()
    names = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.infolist():
            if m.is_dir() or (only_suffix and not m.filename.endswith(only_suffix)):
                continue
            name = Path(m.filename).name if only_suffix else m.filename
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest) + "/"):
                raise StepFailed(f"unsafe file name in download: {m.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(m))
            names.append(name)
    return names


# ---------------------------------------------------------------- jobs
def _kill_tree(p) -> None:
    """Stop a process and everything it started (SteamCMD's script starts the real SteamCMD as a child)."""
    if p.returncode is not None:
        return  # already finished and collected: its number may belong to something else now
    try:
        os.killpg(p.pid, signal.SIGKILL)  # its own process group (started with start_new_session)
    except (OSError, ProcessLookupError):
        try:
            p.kill()
        except OSError:
            pass


class _Procs(list):
    """The job's running processes. One added after Cancel was pressed is stopped at once."""

    def __init__(self, cancel: threading.Event):
        super().__init__()
        self._cancel = cancel

    def append(self, p):
        super().append(p)
        if self._cancel.is_set():
            _kill_tree(p)


class Job:
    def __init__(self, data: dict, manager):
        self.d = data
        self.mgr = manager
        self._cancel = threading.Event()
        self._procs = _Procs(self._cancel)
        self._lock = threading.Lock()
        self._save_lock = threading.RLock()

    @property
    def id(self): return self.d["id"]

    def save(self):
        with self._save_lock:  # the worker thread and API calls may save at the same moment
            jobs = self.mgr.jobs_dir
            jobs.mkdir(parents=True, exist_ok=True)
            tmp = jobs / f"{self.id}.json.tmp"
            tmp.write_text(json.dumps(self.d, indent=1))
            tmp.replace(jobs / f"{self.id}.json")

    def log(self, line: str):
        with self._lock:
            self.d["log"] = (self.d.get("log", []) + [f"{time.strftime('%H:%M:%S')} {line}"])[-400:]
        self.save()

    def step(self):
        return self.d["steps"][self.d["current"]]

    def progress(self, percent=None, detail=None, speed=None, done=None, total=None):
        st = self.step()
        if percent is not None:
            st["percent"] = round(max(0.0, min(100.0, percent)), 1)
        if detail is not None:
            st["detail"] = detail
        st["speed"], st["done"], st["total"] = speed, done, total
        now = time.time()
        if now - self.d.get("_saved", 0) > 0.5:  # don't hammer the disk on fast progress
            self.d["_saved"] = now
            self.save()

    def check_cancel(self):
        if self._cancel.is_set():
            raise Cancelled()

    def public(self) -> dict:
        return {k: v for k, v in self.d.items() if not k.startswith("_")}


ACTIVE = ("queued", "running", "checking")  # a job in these states is using the machine (downloading, starting)
MAX_DEPLOYS_PER_MINUTE = 3


def mem_available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        pass
    return float("inf")


class Deployer:
    """Owns the jobs and runs them. supervisor: the running Supervisor, so new servers are picked up at once.
    Guards (added after a client re-sent one Deploy hundreds of times and the machine ran out of memory):
    one active setup at a time, the same request id never creates two jobs, at most 3 deploys a minute, and a
    free-memory check."""

    def __init__(self, supervisor=None):
        self.sup = supervisor
        # Folders fixed when the Deployer is made: a job thread still running later never writes anywhere else.
        self.jobs_dir, self.config_dir = JOBS, CONFIG
        self.jobs: dict[str, Job] = {}
        self._create_lock = threading.Lock()
        self._recent: list[float] = []
        self._by_request: dict[str, str] = {}
        for f in sorted(self.jobs_dir.glob("*.json")) if self.jobs_dir.exists() else []:
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            if d.get("status") in ("running", "queued"):
                d["status"] = "failed"
                d["error"] = "Interrupted (HostSpawn restarted). Press Retry to carry on from this step."
                d["steps"][d["current"]]["state"] = "failed"
            self.jobs[d["id"]] = Job(d, self)

    # ---- the instance limit ----
    def instances_used(self) -> int:
        """Instances HostSpawn made, plus ones still being set up (or failed, waiting for Retry). Servers it only
        watches (added by hand, a sandbox's practice servers) don't count."""
        made = {sid for sid, c in load_servers().items() if c.get("managed")}
        made |= {j.d["server_id"] for j in self.jobs.values() if j.d["status"] not in ("done", "cancelled")}
        return len(made)

    def _check_limit(self):
        limit = instances.max_instances()
        if self.instances_used() >= limit:
            raise ValueError(f"You've reached the limit of {limit} instance{'s' if limit != 1 else ''}. "
                             "Delete one to make a new one.")

    # ---- create ----
    def create(self, game: str, mode: str, options: dict, request_id: str = "", accepted_terms: str = "") -> Job:
        if accepted_terms != catalog.TERMS_VERSION:
            raise ValueError("Please read and accept the terms before deploying.")
        with self._create_lock:  # one create at a time, so the checks below can't race
            rid = re.sub(r"[^A-Za-z0-9-]", "", str(request_id))[:64]
            if rid and rid in self._by_request and self._by_request[rid] in self.jobs:
                return self.jobs[self._by_request[rid]]  # the same click sent again: same job, nothing new
            self._check_limit()
            busy = [j for j in self.jobs.values() if j.d["status"] in ACTIVE]
            if busy:
                raise ValueError(f"{busy[0].d['name']} is still being set up. Wait for it to finish (or cancel it) first.")
            now = time.time()
            self._recent = [t for t in self._recent if now - t < 60]
            if len(self._recent) >= MAX_DEPLOYS_PER_MINUTE:
                raise ValueError("Too many new servers in a minute. Wait a moment and try again.")
            need = catalog.GAMES.get(game, {}).get("ram_gb", 2) + 2
            if os.environ.get("MANAGER_TEST_STEAMCMD"):
                need = 0.2  # sandbox: the fake game needs a few MB, not a real server's RAM
            if mem_available_gb() < need:
                raise ValueError(f"Not enough free memory: {game} needs about {need} GB free. Stop something first.")
            job = self._create(game, mode, options)
            job.d["accepted_terms"] = {"version": accepted_terms, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                       "items": [t["name"] for t in catalog.terms_for(game, job.d["mode"])]}
            job.save()
            self._recent.append(now)
            if rid:
                self._by_request[rid] = job.id
            return job

    def _create(self, game: str, mode: str, options: dict) -> Job:
        g = catalog.GAMES.get(game)
        m = (g or {}).get("modes", {}).get(mode)
        if not g or not m:
            raise ValueError("unknown game or server type")
        if not catalog.ready(m):
            raise ValueError(f"{g['name']} {m['name']} isn't available yet: {catalog.shown(m)['reason'] or ''}")
        if game == "rust" and mode == "vanilla" and options.get("oxide") in (True, "true", "yes", 1):
            mode, m = "oxide", g["modes"]["oxide"]  # "Install Oxide?" = Yes
        elif not catalog.ready(m):
            raise ValueError(f"{g['name']} {m['name']} isn't available yet: {m.get('reason', '')}")
        default = f"{community()} {m['name'] if m['name'].startswith(g['name']) else g['name'] + ' ' + m['name']}"  # not "Rust Rust"
        name = re.sub(r"[\x00-\x1f\"\\]", "", str(options.get("name") or default))[:80]
        sid = f"{game}-{mode}-{_rand.token_hex(2)}"
        opts = {}
        for key, spec in g.get("options", {}).items():
            if spec.get("type") == "bool":
                continue  # yes/no questions decide the steps (above), they aren't server settings
            v = options.get(key, spec["default"])
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{spec['label']} must be a number")
            if not spec["min"] <= v <= spec["max"]:
                raise ValueError(f"{spec['label']} must be {spec['min']}–{spec['max']}")
            opts[key] = v
        if m.get("maps") or m.get("scenarios"):
            choices = m.get("maps") or m["scenarios"]
            mp = str(options.get("map") or next(iter(choices)))
            if mp not in choices:
                raise ValueError("unknown map")
            opts["map"] = mp
        if m.get("releases"):
            opts["release"] = str(options.get("release", ""))[:40]
        if game == "reforger" and options.get("rotation") in (True, "true", "yes", 1) and m.get("scenarios"):
            ids = list(m["scenarios"].values())
            first = m["scenarios"][opts["map"]]  # start on the chosen map, then the others in order
            opts["rotation"] = [first] + [x for x in ids if x != first]
            opts["rotation_on"] = len(ids) >= 2
        if game == "rust":
            from .adapters.rust_server import BP_POLICIES, WIPE_MODES
            wm, bp = str(options.get("wipe_mode", "monthly")), str(options.get("wipe_bp", "forced"))
            if wm not in WIPE_MODES or bp not in BP_POLICIES:
                raise ValueError("Unknown wipe schedule")
            opts.update(wipe_mode=wm, wipe_bp=bp, wipe_time="20:00", wipe_new_seed=True)
        # Restarts: the recommended schedule unless they chose their own (or none)
        if options.get("recommended_restarts", True) in (True, "true", "yes", 1):
            opts["restart_times"] = list(catalog.restarts_for(game, mode)["times"])
        else:
            rt = options.get("restart_times") if isinstance(options.get("restart_times"), list) else []
            if len(rt) > 6 or not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(t)) for t in rt):
                raise ValueError("Restart times must be HH:MM, at most 6")
            opts["restart_times"] = sorted(set(map(str, rt)))
        if g["login"] == "steam":
            user = str(options.get("steam_user", "")).strip()
            if not re.fullmatch(r"[A-Za-z0-9_.@-]{2,64}", user):
                raise ValueError("Enter the Steam username for the server's download account")
            set_secret(f"SRV_{sid.upper().replace('-', '_')}_STEAM_USER", user)
        ports = allocate_ports(game)
        root = servers_dir() / game / folder_name(name, sid)
        steps = self._steps(game, mode)
        d = {"id": _rand.token_hex(6), "server_id": sid, "game": game, "mode": mode, "name": name,
             "game_name": g["name"], "mode_name": m["name"], "options": opts, "ports": ports, "root": str(root),
             "status": "queued", "current": 0, "created": time.time(), "log": [],
             "practice": bool(os.environ.get("MANAGER_TEST_STEAMCMD")),  # sandbox: a pretend game, nothing downloaded
             "steps": [{"key": k, "title": t, "kind": kind, "state": "pending"} for k, t, kind in steps]}
        job = Job(d, self)
        self.jobs[job.id] = job
        job.save()
        self._run(job)
        return job

    @staticmethod
    def _steps(game: str, mode: str) -> list[tuple[str, str, str]]:
        s = [("check", "Check disk space and ports", "auto"), ("folders", "Create folders", "auto"),
             ("steamcmd", "Download SteamCMD", "auto")]
        if mode == "altislife":
            s.append(("licence", "Read the licence", "guided"))
        if game == "arma3":
            s.append(("steam_login", "Log in to Steam", "guided"))
        s.append(("download", "Download the server", "auto"))
        if mode == "altislife":
            s += [("framework", "Download and build Altis Life", "auto"), ("extdb", "Install extDB3", "auto"),
                  ("mariadb", "Install the database (MariaDB)", "guided"), ("database", "Create the database", "guided")]
        if game == "rust" and mode == "oxide":
            s.append(("oxide", "Install Oxide (uMod)", "auto"))
        if mode == "kpliberation":
            s.append(("mission", "Download KP Liberation", "auto"))
        s += [("configure", "Write settings", "auto"), ("start", "First start", "auto"),
              ("wait", "Wait for the server to answer", "auto"), ("register", "Add to HostSpawn", "auto"),
              ("ports", "Open the ports for players", "guided"), ("ready", "Ready", "auto")]
        return s

    # ---- control ----
    def action(self, job_id: str, action: str) -> Job:
        job = self.jobs.get(job_id)
        if not job:
            raise KeyError("no such job")
        st = job.d["status"]
        if action == "cancel":
            if st in ("done", "cancelled"):
                return job
            job._cancel.set()
            for p in list(job._procs):
                _kill_tree(p)
            if st != "running":
                job.d["status"] = "cancelled"
                job.save()
            return job
        if action == "retry" and st in ("failed", "cancelled"):
            if st == "cancelled":  # a cancelled attempt didn't count towards the limit; trying again does
                self._check_limit()
            job._cancel.clear()
            job.d.pop("error", None)
            self._run(job)
            return job
        if action in ("continue", "skip") and st == "waiting":
            step = job.step()
            if action == "skip" and not step.get("skippable"):
                raise ValueError("this step can't be skipped")
            job.d["status"] = "checking" if action == "continue" else "running"
            job.d["skip"] = action == "skip"
            job.save()
            self._run(job)
            return job
        if action == "back" and st == "waiting":
            prev = [i for i in range(job.d["current"] - 1, -1, -1) if job.d["steps"][i]["kind"] == "guided"]
            if prev:
                for i in range(prev[0], job.d["current"] + 1):
                    job.d["steps"][i]["state"] = "pending"
                job.d["current"] = prev[0]
                self._run(job)
            else:  # nothing to go back to: cancel and let the dashboard reopen the settings
                job.d["status"] = "cancelled"
                job.d["back_to_settings"] = True
                job.save()
            return job
        raise ValueError(f"can't {action} a job that is {st}")

    def _run(self, job: Job):
        job.d["status"] = job.d["status"] if job.d["status"] == "checking" else "running"
        job.save()
        threading.Thread(target=self._worker, args=(job,), daemon=True, name=f"job-{job.id}").start()

    def _worker(self, job: Job):
        try:
            while job.d["current"] < len(job.d["steps"]):
                step = job.step()
                step["state"] = "running"
                step.setdefault("started", time.time())
                job.save()
                fn = getattr(self, f"step_{step['key']}")
                if step["kind"] == "guided":
                    if job.d.get("skip"):
                        job.d.pop("skip")
                        job.log(f"{step['title']}: skipped")
                        step["state"] = "skipped"
                    elif job.d["status"] == "checking":
                        ok, msg = fn(job, check=True)
                        if not ok:
                            step["state"] = "waiting"
                            step["check_message"] = msg
                            job.d["status"] = "waiting"
                            job.save()
                            return
                        step["state"] = "done"
                        step.pop("check_message", None)
                        job.d["status"] = "running"
                    else:
                        step["guide"] = fn(job, check=False)
                        step["skippable"] = step["guide"].get("skippable", False)
                        step["state"] = "waiting"
                        job.d["status"] = "waiting"
                        job.save()
                        return
                else:
                    job.check_cancel()
                    fn(job)
                    step["state"] = "done"
                    step["percent"] = 100
                step["finished"] = time.time()
                job.d["current"] += 1
                job.save()
            job.d["current"] = len(job.d["steps"]) - 1
            job.d["status"] = "done"
            job.d["finished"] = time.time()
            job.save()
            add_event(job.d["server_id"], "info", f"created: {job.d['name']} ({job.d['game_name']} {job.d['mode_name']})")
        except Cancelled:
            job.step()["state"] = "cancelled"
            job.d["status"] = "cancelled"
            job.log("cancelled")
            job.save()
        except Exception as e:
            msg = str(e) if isinstance(e, StepFailed) else f"{type(e).__name__}: {e}"
            job.step()["state"] = "failed"
            job.d["status"] = "failed"
            job.d["error"] = msg
            job.log("FAILED: " + msg)
            if not isinstance(e, StepFailed):
                job.log(traceback.format_exc(limit=3))
            job.save()

    # ---- the server this job is building ----
    def cfg(self, job: Job) -> dict:
        d, g = job.d, catalog.GAMES[job.d["game"]]
        m = g["modes"][d["mode"]]
        sid, key = d["server_id"], "SRV_" + d["server_id"].upper().replace("-", "_")
        cfg = {"name": d["name"], "adapter": g["adapter"], "game_id": d["game"], "mode": d["mode"],
               "mode_name": m["name"], "root": d["root"], "hostname": d["name"], **d["options"], **d["ports"],
               **m.get("settings", {}), "admin_password_key": f"{key}_ADMIN"}
        if m.get("scenarios"):
            cfg["scenario"] = m["scenarios"][d["options"]["map"]]
        if d["game"] in ("rust", "reforger"):
            cfg["rcon_password_key"] = f"{key}_RCON"
            cfg["steamcmd"] = str(steamcmd_path())
        if d["game"] == "arma3":
            cfg["steam_user_key"] = f"{key}_STEAM_USER"
            cfg["rcon_password_key"] = f"{key}_RCON"
            if d["mode"] == "kpliberation":
                cfg["mission"] = f"kp_liberation.{d['options']['map']}"
            if d["mode"] == "altislife":
                cfg["mission"] = f"Altis_Life.{d['options'].get('map') or 'Altis'}"
                cfg["server_mods"] = ["@life_server", "@extdb3"]
                cfg["db_name"] = cfg["db_user"] = self.db_name(job)
                cfg["db_password_key"] = f"{key}_DB"
                cfg["framework_release"] = d["options"].get("release") or "v5.0.0"
        if instance()["mode"] != "live":
            cfg["sandbox"] = True  # created by the development copy: it may control this one
        cfg.pop("release", None)
        cfg.pop("release_used", None)
        if d["game"] != "reforger":
            cfg.pop("map", None)
        cfg["created"] = time.strftime("%Y-%m-%d %H:%M")
        return {k: v for k, v in cfg.items() if v is not None}

    def adapter(self, job: Job):
        return ADAPTERS[catalog.GAMES[job.d["game"]]["adapter"]](job.d["server_id"], self.cfg(job))

    @staticmethod
    def db_name(job) -> str:
        return "al_" + re.sub(r"[^a-z0-9]", "", job.d["server_id"].split("-")[-1].lower())

    # ---- steps ----
    def step_check(self, job):
        g = catalog.GAMES[job.d["game"]]
        base = servers_dir()
        base.mkdir(parents=True, exist_ok=True)
        free_gb = shutil.disk_usage(base).free / 2**30
        need = 0.05 if os.environ.get("MANAGER_TEST_STEAMCMD") else g["size_gb"]  # sandbox: the fake game is tiny
        job.log(f"disk free: {free_gb:.0f} GB, needs about {need} GB")
        if free_gb < need * 1.3:
            raise StepFailed(f"Not enough disk space: {free_gb:.0f} GB free, {g['name']} needs about {need} GB")
        for k, p in job.d["ports"].items():
            if not port_free(p):
                raise StepFailed(f"Port {p} ({k}) is in use. Delete this attempt and create the server again.")
        job.log("ports: " + ", ".join(f"{k} {p}" for k, p in job.d["ports"].items()))

    def step_folders(self, job):
        root = Path(job.d["root"])
        for sub in ("server", "logs", "backups"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        _private(job)
        (root / MARKER).write_text(job.d["server_id"])
        _write_readme(job, self.cfg(job))  # written again when ready
        job.log(f"folder: {root}")

    def step_steamcmd(self, job):
        a = self.adapter(job)
        target = a.steamcmd
        if target.exists():
            job.log("SteamCMD already downloaded")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        fake = os.environ.get("MANAGER_TEST_STEAMCMD")  # sandboxes only (backend/tests/app_sandbox.py): never set it for real
        if fake:
            target.symlink_to(Path(fake).resolve())
            job.log("SteamCMD: using the sandbox's fake SteamCMD (MANAGER_TEST_STEAMCMD), nothing downloaded")
            return
        data = _download(STEAMCMD_URL, job, "SteamCMD")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
            t.extractall(target.parent, filter="data")
        job.log("SteamCMD downloaded")

    def step_steam_login(self, job, check: bool):
        a = self.adapter(job)
        user = a.steam_user()
        if not check:
            return {"title": "Log in to Steam once",
                    "text": ("Arma 3's server download needs a Steam account (it doesn't have to own Arma 3; a separate "
                             "account just for servers is best). Log in below: HostSpawn types the password into SteamCMD "
                             "for you and never saves it. SteamCMD remembers the login, so updates won't ask again. "
                             "Prefer a terminal? Run the command instead, then press Continue."),
                    "commands": [f"\"{a.steamcmd}\" +login {user} +quit"], "skippable": False,
                    "login": {"target": f"job:{job.id}", "user": user}}
        code, out = run([str(a.steamcmd), "+login", user, "+quit"], timeout=120)
        if any(p in out for p in LOGIN_PROBLEMS) or "OK" not in out.split("Logging in user", 1)[-1][:200]:
            return False, "Steam still asks for a login. Run the command again and finish the login, then Continue."
        job.log("Steam login cached")
        return True, ""

    def step_download(self, job):
        a = self.adapter(job)
        last = ""
        for attempt in range(1, 4):
            t0, base = time.time(), [None]

            def prog(pct, phase, done, total):
                job.check_cancel()
                base[0] = base[0] if base[0] is not None else done
                rate = (done - base[0]) / max(time.time() - t0, 0.1)
                job.progress(pct, f"{phase.capitalize()}: {done / 2**30:.1f} of {total / 2**30:.1f} GB",
                             speed=rate, done=done, total=total)
            ok, out = a.steam_update(progress=prog, proc_holder=job._procs)
            job._procs.clear()
            job.check_cancel()
            if ok:
                job.progress(100, "Downloaded and verified")
                job.log(f"{a.game} server downloaded")
                return
            last = out.strip().splitlines()[-1] if out.strip() else "no output"
            if any(p in out for p in LOGIN_PROBLEMS):
                raise StepFailed("Steam wants a login again. Press Retry after logging in (see the Steam step).")
            job.log(f"SteamCMD attempt {attempt} failed: {last}")
            time.sleep(5)
        raise StepFailed(f"SteamCMD failed 3 times ({last}). Press Retry.")

    def step_oxide(self, job):
        from .adapters.rust_server import install_oxide
        job.progress(None, "Downloading Oxide from its official GitHub release")
        job.log(install_oxide(self.adapter(job).server_dir))

    def step_mission(self, job):
        m = catalog.GAMES["arma3"]["modes"]["kpliberation"]
        rels = catalog.releases("arma3", "kpliberation")
        want = job.d["options"].get("release")
        rel = next((r for r in rels if r["tag"] == want), None) if want else None
        rel = rel or next((r for r in rels if m["releases"]["asset"].format(map=job.d["options"]["map"]) in r["assets"]), None)
        if not rel:
            raise StepFailed("No KP Liberation release has this map")
        asset = m["releases"]["asset"].format(map=job.d["options"]["map"])
        if asset not in rel["assets"]:
            raise StepFailed(f"Release {rel['tag']} has no {asset}")
        data = _download(rel["assets"][asset], job, f"KP Liberation {rel['tag']}")
        dest = self.adapter(job).server_dir / "mpmissions"
        dest.mkdir(parents=True, exist_ok=True)
        files = _safe_extract_zip(data, dest, only_suffix=".pbo")
        if not files:
            raise StepFailed("The download had no mission file")
        job.d["options"]["release_used"] = rel["tag"]
        job.log(f"KP Liberation {rel['tag']}: {', '.join(files)}")

    # ---- Altis Life ----
    def step_licence(self, job, check: bool):
        if check:
            return True, ""
        return {"title": "Altis Life licence",
                "text": ("Altis Life (AsYetUntitled) is licensed CC BY-NC-ND 4.0: you may run it, but not for commercial "
                         "use (for example, selling in-game perks with real money) and you may not share changed "
                         "copies. HostSpawn downloads it from its official GitHub for you. Press Continue if that's OK."),
                "commands": [], "skippable": False}

    def step_framework(self, job):
        tag = job.d["options"].get("release") or "v5.0.0"
        ref = "refs/heads/master" if tag == "master" else f"refs/tags/{tag}"
        data = _download(f"https://codeload.github.com/AsYetUntitled/Framework/zip/{ref}", job, f"Altis Life {tag}")
        work = _work(job) / "framework"
        shutil.rmtree(work, ignore_errors=True)
        _safe_extract_zip(data, work)
        src = next(p for p in work.iterdir() if p.is_dir())
        for need in ("Altis_Life.Altis", "life_server", "altislife.sql"):
            if not (src / need).exists():
                raise StepFailed(f"This release doesn't have {need}; pick v5.0.0 or master")
        # The mission's mission.sqm (the map, units and markers) isn't in the mission folder: the release keeps it in
        # SQMs/missionAltis.sqm. Without it Arma can't load the mission at all.
        # Altis or Tanoa: the same scripts, with that map's SQMs/mission<Map>.sqm as mission.sqm, packed as
        # Altis_Life.<Map>.pbo (Arma reads the map from the name).
        mp = job.d["options"].get("map") or "Altis"
        mission = src / "Altis_Life.Altis"
        sqm = src / "SQMs" / f"mission{mp}.sqm"
        if sqm.exists():
            shutil.copy(sqm, mission / "mission.sqm")
        elif mp != "Altis" or not (mission / "mission.sqm").exists():
            raise StepFailed(f"This release has no mission.sqm for {mp} (SQMs/mission{mp}.sqm); pick v5.0.0 or master")
        from .pbo import pack, read
        server = Path(job.d["root"]) / "server"
        job.progress(None, "Building the mission file")
        out = server / "mpmissions" / f"Altis_Life.{mp}.pbo"
        n1 = pack(mission, out)
        if "mission.sqm" not in read(out)[1]:
            raise StepFailed("The mission file was built without mission.sqm")
        prefix = (src / "life_server/PboPrefix.txt").read_text().strip() if (src / "life_server/PboPrefix.txt").exists() else "life_server"
        n2 = pack(src / "life_server", server / "@life_server" / "addons" / "life_server.pbo", prefix=prefix)
        be = server / "battleye"
        be.mkdir(parents=True, exist_ok=True)
        for f in (src / "BEFilters").glob("*.txt") if (src / "BEFilters").exists() else []:
            shutil.copy(f, be / f.name)
        (_private(job) / "altislife.sql").write_bytes((src / "altislife.sql").read_bytes())  # kept: the database's starting point
        if (src / "AL.ini").exists():  # newer code uses extDB3's SQL_CUSTOM with AL.ini; v5.0.0 doesn't
            (_work(job) / "AL.ini").write_bytes((src / "AL.ini").read_bytes())
        shutil.rmtree(work, ignore_errors=True)
        job.log(f"Altis Life {tag}: mission ({n1} files) and life_server ({n2} files) built")

    def step_extdb(self, job):
        data = _download(EXTDB3_URL, job, "extDB3")
        server = Path(job.d["root"]) / "server"
        tmp = _work(job) / "extdb"
        shutil.rmtree(tmp, ignore_errors=True)
        _safe_extract_zip(data, tmp)
        target = server / "@extdb3"  # Linux: mod folders must be lowercase
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(tmp / "@extDB3"), target)
        so = target / "extDB3_x64.so"
        if not so.exists():
            raise StepFailed("extDB3 download has no 64-bit Linux library")
        for alias in ("extdb3_x64.so",):
            if not (target / alias).exists():
                (target / alias).symlink_to(so.name)
        ini = _work(job) / "AL.ini"
        if ini.exists():
            (target / "sql_custom").mkdir(exist_ok=True)
            shutil.copy(ini, target / "sql_custom" / "AL.ini")
        shutil.rmtree(tmp, ignore_errors=True)
        job.log("extDB3 installed (@extdb3)")

    def _extdb_missing_libs(self, job) -> list[str]:
        so = Path(job.d["root"]) / "server" / "@extdb3" / "extDB3_x64.so"
        code, out = run(["ldd", str(so)])
        return [l.split()[0] for l in out.splitlines() if "not found" in l]

    def step_mariadb(self, job, check: bool):
        if not check:
            return {"title": "Install the database",
                    "text": ("Altis Life keeps players, money, houses and vehicles in a small database. Install MariaDB "
                             "(a free database) and one library extDB3 needs, by running this on the machine. It needs "
                             "sudo, so it's yours to run. Then press Continue."),
                    "commands": ["sudo apt install -y mariadb-server libtbbmalloc2"], "skippable": False}
        if not mariadb_listening():
            return False, "MariaDB isn't answering on this machine yet. Run the command, wait a few seconds, then Continue."
        missing = self._extdb_missing_libs(job)
        if missing:
            return False, f"extDB3 still can't find: {', '.join(missing)}. Run the command again, then Continue."
        job.log("MariaDB is running; extDB3 has all its libraries")
        return True, ""

    def step_database(self, job, check: bool):
        root = Path(job.d["root"])
        db = self.db_name(job)
        key = "SRV_" + job.d["server_id"].upper().replace("-", "_") + "_DB"
        new_secret(key)
        pw = secrets()[key]
        cnf = _private(job) / "db.cnf"
        cnf.touch(mode=0o600)
        os.chmod(cnf, 0o600)
        cnf.write_text(f"[client]\nuser={db}\npassword={pw}\nhost=127.0.0.1\n")
        setup = _private(job) / "db-setup.sql"
        if not check:
            sql = (_private(job) / "altislife.sql").read_text()
            changes = [("`altislife`", f"`{db}`"), ("`arma3`@`localhost`", f"`{db}`@`localhost`"),
                       ("'arma3'@'localhost'", f"'{db}'@'localhost'"), ("IDENTIFIED BY 'changeme'", f"IDENTIFIED BY '{pw}'")]
            for a, b in changes:
                if a not in sql:
                    raise StepFailed(f"altislife.sql looks different than expected (no {a}); can't set it up safely")
                sql = sql.replace(a, b)
            setup.touch(mode=0o600)
            os.chmod(setup, 0o600)
            setup.write_text(sql)
            self._write_extdb_conf(job, db, pw)
            return {"title": "Create the database",
                    "text": ("This creates the Altis Life database and its own user (with a generated password that's "
                             "never shown). Run the command below on the machine, then press Continue. The file it reads "
                             "is private to you and is deleted afterwards."),
                    "commands": [f"sudo mariadb < \"{setup}\""], "skippable": False}
        code, out = run(["mariadb", f"--defaults-extra-file={cnf}", db, "-e", "SELECT COUNT(*) FROM players"])
        if code != 0:
            return False, "The database isn't there yet (or the login didn't work). Run the command, then Continue."
        setup.unlink(missing_ok=True)
        job.log(f"database {db} ready; setup file deleted")
        return True, ""

    def _write_extdb_conf(self, job, db: str, pw: str):
        target = Path(job.d["root"]) / "server" / "@extdb3" / "extdb3-conf.ini"
        text = target.read_text() if target.exists() else "[Main]\nVersion = 1\n"
        text = re.sub(r"\n\[(Database|altislife)\][^\[]*", "\n", text)
        text += (f"\n[altislife]\nIP = 127.0.0.1\nPort = 3306\nUsername = {db}\nPassword = {pw}\nDatabase = {db}\n")
        target.touch(mode=0o600)
        os.chmod(target, 0o600)
        target.write_text(text)

    def step_configure(self, job):
        key = "SRV_" + job.d["server_id"].upper().replace("-", "_")
        new_secret(f"{key}_ADMIN")
        if job.d["game"] in ("rust", "reforger", "arma3"):
            new_secret(f"{key}_RCON")
        a = self.adapter(job)
        a.write_configs()
        job.log("settings written (passwords generated, never shown)")

    def step_start(self, job):
        a = self.adapter(job)
        with a.lock("start"):
            msg = a._start()
        job.log(f"start: {msg}")
        if msg not in ("started", "already running"):
            raise StepFailed(f"Couldn't start: {msg}")

    def step_wait(self, job):
        a = self.adapter(job)
        limit = 1800 if job.d["game"] == "rust" else 900  # Rust builds its map on the first start
        t0 = time.time()
        while time.time() - t0 < limit:
            job.check_cancel()
            if not a.pid():
                time.sleep(3)
                if not a.pid():
                    tail = (a.logs().get("console") or "").strip().splitlines()[-8:]
                    raise StepFailed("The server stopped during its first start. Last log lines:\n" + "\n".join(tail))
            if a.health():
                a.set_flag("answered")
                job.log(f"answering after {int(time.time() - t0)} s")
                return
            elapsed = int(time.time() - t0)
            hint = " (Rust is building the map: this takes a few minutes)" if job.d["game"] == "rust" else ""
            job.progress(min(95, elapsed * 100 / limit * 3), f"Starting up: {elapsed // 60} min {elapsed % 60} s{hint}")
            time.sleep(3)
        raise StepFailed(f"The server didn't answer within {limit // 60} minutes. Check its console log, then Retry.")

    def step_register(self, job):
        cfg = self.cfg(job)
        base = servers_dir().resolve()
        if not str(Path(cfg["root"]).resolve()).startswith(str(base) + "/"):
            raise StepFailed(f"Safety check: the server folder isn't inside {base}, so it wasn't added")
        d = self.config_dir / "servers.d"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{job.d['server_id']}.toml"
        if job.d.get("practice"):
            cfg["fake"] = True  # shown as Practice: players can't join a pretend game
        f.write_text(toml_dump({job.d["server_id"]: cfg}))
        instances.number(job.d["server_id"])
        cfg = {**cfg, "managed": True, "_file": str(f)}
        if self.sup is not None:
            self.sup.adapters[job.d["server_id"]] = ADAPTERS[cfg["adapter"]](job.d["server_id"], cfg)
            self.sup.check(job.d["server_id"])
        job.log("added to HostSpawn: it is now watched like your other servers")

    def step_ports(self, job, check: bool):
        g = catalog.GAMES[job.d["game"]]
        p = job.d["ports"]
        public = []
        for spec in g["ports"]:
            if not spec["public"]:
                continue
            port = p["port"] if spec["role"] == "game" else p.get(f"{spec['role']}_port")
            if port:
                span = spec.get("range", 1)
                public.append((f"{port}:{port + span - 1}" if span > 1 else str(port), spec["proto"]))
        if check:
            return True, ""
        return {"title": "Let players in",
                "text": ("Players can only join if these ports are open. On this machine run the commands below (they "
                         "need sudo, so they're yours to run). If your host has its own firewall (for example OVH's "
                         "Game firewall), add the same ports there. Then press Continue. You can also Skip and do it later."),
                "commands": [f"sudo ufw allow {port}/{proto}" for port, proto in public], "skippable": True}

    def step_ready(self, job):
        from .adapters.reforger_server import public_ip
        job.d["connect"] = f"{public_ip() or '<server-ip>'}:{job.d['ports']['port']}"
        shutil.rmtree(Path(job.d["root"]) / PRIVATE / "setup", ignore_errors=True)  # downloads used while building
        _write_readme(job, self.cfg(job))
        if job.d.get("practice"):
            job.log("ready: a practice instance (a pretend game in this sandbox; players can't join it)")
        else:
            job.log(f"ready: players connect to {job.d['connect']}")

    # ---- delete ----
    def delete_server(self, sid: str, keep_backups: bool = True) -> str:
        servers = load_servers()
        cfg = servers.get(sid)
        if not cfg or not cfg.get("managed"):
            raise ValueError("Only servers HostSpawn created can be deleted here")
        root = Path(cfg["root"]).resolve()
        base = servers_dir().resolve()
        if not str(root).startswith(str(base) + "/") or (root / MARKER).read_text().strip() != sid:
            raise ValueError("Safety check failed: that folder wasn't created by HostSpawn, so it won't be deleted")
        a = (self.sup.adapters.get(sid) if self.sup else None) or ADAPTERS[cfg["adapter"]](sid, cfg)
        with a.lock("delete"):
            a._stop()
            kept = ""
            if keep_backups and any((root / "backups").glob("*")):
                dest = base / "_kept_backups" / f"{sid}_{time.strftime('%Y%m%d-%H%M%S')}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(root / "backups"), dest)
                kept = f"; backups kept in {dest}"
            shutil.rmtree(root)
            _remove_empty_game_folder(root, base)
        Path(cfg["_file"]).unlink(missing_ok=True)
        remove_secrets("SRV_" + sid.upper().replace("-", "_") + "_")
        shutil.rmtree(STATE / sid, ignore_errors=True)
        if self.sup is not None:
            self.sup.adapters.pop(sid, None)
            self.sup.snapshot.pop(sid, None)
            self.sup.db.forget_server(sid)  # nobody is "online" on a deleted server (their history stays)
        for j in list(self.jobs.values()):
            if j.d["server_id"] == sid:
                (self.jobs_dir / f"{j.id}.json").unlink(missing_ok=True)
                self.jobs.pop(j.id, None)
        instances.release(sid)
        add_event(sid, "info", f"deleted {cfg.get('name', sid)}{kept}")
        return f"deleted{kept}"

    def discard_job(self, job_id: str) -> str:
        """Throw away a failed/cancelled attempt and its half-made folder (only if HostSpawn made it)."""
        job = self.jobs.get(job_id)
        if not job or job.d["status"] not in ("failed", "cancelled"):
            raise ValueError("only a failed or cancelled attempt can be discarded")
        root = Path(job.d["root"]).resolve()
        base = servers_dir().resolve()
        a = self.adapter(job)
        if a.pid():
            with a.lock("discard"):
                a._stop()
        if str(root).startswith(str(base) + "/") and (root / MARKER).exists():
            shutil.rmtree(root)
            _remove_empty_game_folder(root, base)
        remove_secrets("SRV_" + job.d["server_id"].upper().replace("-", "_") + "_")
        shutil.rmtree(STATE / job.d["server_id"], ignore_errors=True)
        (self.jobs_dir / f"{job_id}.json").unlink(missing_ok=True)
        self.jobs.pop(job_id, None)
        return "discarded"
