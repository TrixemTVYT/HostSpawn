"""Shared helpers: paths, config, secrets, redaction, events."""
import json
import os
import re
import secrets as _secrets
import subprocess
import threading
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.environ.get("MANAGER_ROOT", Path(__file__).resolve().parents[2]))  # the repo or install folder
CONFIG = ROOT / "config"
STATE = ROOT / "state"
LOGS = ROOT / "logs"
UK = ZoneInfo("Europe/London")
SECRETS_FILE = CONFIG / "secrets.env"

_known_secrets: set[str] = set()
_events_lock = threading.Lock()


def instance() -> dict:
    """config/instance.env (git-ignored): MODE=live|dev, PORT. Missing file = dev, the safe default.
    dev: sees every server but can't control them, and runs no watchdog or scheduled restarts."""
    env = {}
    try:
        for line in (CONFIG / "instance.env").read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.startswith("#"):
                env[k.strip()] = v.strip()
    except OSError:
        pass
    mode = "live" if env.get("MODE") == "live" else "dev"
    hosts = [h.strip() for h in env.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
    return {"mode": mode, "port": int(env.get("PORT") or (8100 if mode == "live" else 8101)),
            "interval": float(env.get("INTERVAL") or 60), "allowed_hosts": hosts}


def settings() -> dict:
    """config/settings.toml (git-ignored): this install's own choices, e.g. the community name."""
    try:
        with open(CONFIG / "settings.toml", "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_settings(changes: dict) -> dict:
    """Merge {section: {key: value}} into config/settings.toml (backed up first). Returns the new settings."""
    import json as _json
    cur = settings()
    for sec, vals in changes.items():
        cur.setdefault(sec, {}).update(vals)
    path = CONFIG / "settings.toml"
    if path.exists():
        (CONFIG / "settings.toml.bak").write_text(path.read_text())
    def val(v):
        return ("true" if v else "false") if isinstance(v, bool) else str(v) if isinstance(v, (int, float)) else _json.dumps(str(v))
    lines = ["# HostSpawn settings for this install (git-ignored). Changed from the Settings page."]
    for sec, vals in cur.items():
        if isinstance(vals, dict):
            lines += ["", f"[{sec}]"] + [f"{k} = {val(v)}" for k, v in vals.items()]
    path.write_text("\n".join(lines) + "\n")
    return cur


def community() -> str:
    """The server owner's community name (shown in the dashboard, used for default server names)."""
    return str(settings().get("community", {}).get("name") or "My Community")


def load_servers(path: Path | None = None) -> dict:
    """config/servers.toml (servers you set up by hand) + config/servers.d/*.toml (servers Manager created;
    each file is one server, `[servers.<id>]`). Created servers are marked managed = true."""
    path = path or CONFIG / "servers.toml"
    servers = {}
    try:
        with open(path, "rb") as f:
            servers.update(tomllib.load(f).get("servers", {}))
    except FileNotFoundError:
        pass
    for f in sorted((path.parent / "servers.d").glob("*.toml")):
        try:
            with open(f, "rb") as fh:
                for sid, cfg in tomllib.load(fh).get("servers", {}).items():
                    if sid not in servers:
                        servers[sid] = {**cfg, "managed": True, "_file": str(f)}
        except (OSError, tomllib.TOMLDecodeError):
            continue
    return servers


def servers_dir() -> Path:
    """Where created servers live (settings.toml [paths] servers; default: <HostSpawn>/servers)."""
    p = settings().get("paths", {}).get("servers")
    return Path(p).expanduser() if p else ROOT / "servers"


def read_env(path: Path, remember: bool = True) -> dict:
    """KEY=value lines. remember=True (secret files only) makes redact() hide the values anywhere they appear."""
    out = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
            if remember and len(v) >= 4:
                _known_secrets.add(v)
    except OSError:
        pass
    return out


def secrets() -> dict:
    return read_env(SECRETS_FILE)


def redact(text: str) -> str:
    for s in sorted(_known_secrets, key=len, reverse=True):
        text = text.replace(s, "***")
    return text


def init_secrets() -> list[str]:
    """Create config/secrets.env (chmod 600) and fill any empty generated values. Never prints values."""
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SECRETS_FILE.exists():
        SECRETS_FILE.touch(mode=0o600)
        lines = (CONFIG / "secrets.env.example").read_text().splitlines()
    else:
        lines = SECRETS_FILE.read_text().splitlines()
    generated = {"MANAGER_PASSWORD", "ARMA3_ADMIN_PASSWORD"}
    filled = []
    for i, line in enumerate(lines):
        k, sep, v = line.partition("=")
        if sep and k in generated and not v.strip():
            lines[i] = f"{k}={_secrets.token_urlsafe(18)}"
            filled.append(k)
    os.chmod(SECRETS_FILE, 0o600)
    SECRETS_FILE.write_text("\n".join(lines) + "\n")
    os.chmod(SECRETS_FILE, 0o600)
    return filled


_secrets_lock = threading.Lock()


def set_secret(key: str, value: str) -> None:
    with _secrets_lock:
        lines = SECRETS_FILE.read_text().splitlines() if SECRETS_FILE.exists() else []
        for i, line in enumerate(lines):
            if line.partition("=")[0] == key:
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
        SECRETS_FILE.touch(mode=0o600)
        os.chmod(SECRETS_FILE, 0o600)
        SECRETS_FILE.write_text("\n".join(lines) + "\n")
        if len(value) >= 4:
            _known_secrets.add(value)


def new_secret(key: str) -> str:
    """Generate and store a random password under key (keeps an existing one). Returns the key, never the value."""
    if not secrets().get(key):
        set_secret(key, _secrets.token_urlsafe(18))
    return key


def remove_secrets(prefix: str) -> None:
    with _secrets_lock:
        if not SECRETS_FILE.exists():
            return
        lines = [l for l in SECRETS_FILE.read_text().splitlines() if not l.startswith(prefix)]
        SECRETS_FILE.write_text("\n".join(lines) + "\n")
        os.chmod(SECRETS_FILE, 0o600)


def run(cmd: list[str], timeout: float = 30, cwd=None) -> tuple[int, str]:
    """Run without a shell. Returns (exit code, combined output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 127, str(e)


def run_background(server_id: str, action: str, cmd: list[str], cwd=None) -> None:
    """Long actions (countdowns, updates) run detached; output goes to logs/actions.log."""
    LOGS.mkdir(exist_ok=True)
    log = open(LOGS / "actions.log", "a")
    log.write(f"{now_utc()} [{server_id}] {action}: {' '.join(cmd)}\n")
    log.flush()
    subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL, cwd=cwd, start_new_session=True)


def tail(path: Path, lines: int = 100) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", "replace")
        return redact("\n".join(data.splitlines()[-lines:]))
    except OSError:
        return ""


def newest(pattern_dir: Path, glob: str) -> Path | None:
    files = sorted(pattern_dir.glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def uk_now() -> datetime:
    return datetime.now(UK)


def clean_command(text: str, limit: int = 300) -> str:
    """Console commands: strip control characters (they could inject extra lines into a console)."""
    return re.sub(r"[\x00-\x1f\x7f]", "", text)[:limit].strip()


def add_event(server_id: str, level: str, message: str) -> dict:
    """Append to state/events.jsonl. level: info | warn | alert."""
    ev = {"t": time.time(), "server": server_id, "level": level, "message": redact(message)}
    STATE.mkdir(exist_ok=True)
    with _events_lock, open(STATE / "events.jsonl", "a") as f:
        f.write(json.dumps(ev) + "\n")
    return ev


def read_events(limit: int = 200) -> list[dict]:
    try:
        lines = (STATE / "events.jsonl").read_text().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
