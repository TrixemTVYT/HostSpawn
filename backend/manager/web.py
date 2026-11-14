"""Dashboard web server. Binds to 127.0.0.1 only (reach it by SSH tunnel now, Tailscale later).
Sign-in with accounts (manager/accounts.py; sessions in an HttpOnly cookie). POSTs need an X-Manager header, so other
websites can't trigger actions.
Apps (the HostSpawn program) use the same API under /api/v1/... and sign in with a device token
("Authorization: Bearer ..."), from a device login or a one-time pairing code. /api/... without v1 still works. Actions are each adapter's fixed allowlist; risky ones need typed confirmation."""
import base64
import hmac
import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import catalog, deps, hostcheck, instances
from .steamlogin import Logins
from .adapters import Unsupported
from .adapters.base import Adapter
from .adapters.moderation import ModerationError
import re

from .util import ROOT, add_event, community, read_events, save_settings, secrets, settings

PAGE = ROOT / "frontend" / "web" / "index.html"
RUST_TEXT = ("description", "url", "header_image", "logo_image")   # server.cfg text settings (Rust)
RUST_FLAGS = ("pve", "global_chat")
API_VERSION = 1
PLAYER_KEY = re.compile(r"^(steam:\d{17}|be:[0-9a-f]{32})$")


def app_version() -> str:
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "dev"


def host_stats() -> dict:
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0]) // 1024
    du = hostcheck.servers_disk()  # the disk instances are on, the same one the PC check measures
    return {"load": Path("/proc/loadavg").read_text().split()[:3], "mem_total_mb": mem.get("MemTotal"),
            "mem_avail_mb": mem.get("MemAvailable"), "swap_free_mb": mem.get("SwapFree"),
            "swap_total_mb": mem.get("SwapTotal"), "disk_free_gb": du.free // 2**30, "disk_total_gb": du.total // 2**30}


def make_handler(sup, password: str, mode: str = "dev", allowed_hosts: list[str] | None = None, deployer=None,
                 accounts=None):
    """accounts=None: the older single shared password (HTTP basic auth). With accounts: sign-in with sessions."""
    adapters = sup.adapters  # the same dict the supervisor uses: servers added by the setup helper appear here
    expected = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()
    # Host names the browser may use. Anything else (e.g. a DNS-rebinding site pointing at 127.0.0.1) is refused.
    hosts = {"127.0.0.1", "localhost", *(allowed_hosts or [])}
    logins = Logins()

    def login_target(target: str):
        """(steamcmd path, steam user, adapter or None) for "job:<id>" or "server:<id>"."""
        kind, _, ref = target.partition(":")
        if kind == "job" and deployer and ref in deployer.jobs:
            a = deployer.adapter(deployer.jobs[ref])
        elif kind == "server" and ref in adapters and controllable(adapters[ref]):
            a = adapters[ref]
        else:
            raise KeyError("unknown login target")
        if not getattr(a, "steam_user_key", "") or not a.steam_user():
            raise KeyError("this server has no Steam account set")
        return str(a.steamcmd), a.steam_user(), a

    def describe(sid):
        a = adapters[sid]
        snap = sup.snapshot.get(sid) or sup.check(sid)
        return {"id": sid, "name": a.name, "game": a.game, "supervised_by": a.supervised_by,
                "instance": instances.number(sid), "label": instances.label(sid, a.game),
                "update_available": sup.install.update_available(sid),
                "actions": list(a.actions), "confirm": sorted(a.confirm),
                "controllable": mode == "live" or bool(a.cfg.get("sandbox")),
                "deletable": bool(a.cfg.get("managed")) and (mode == "live" or bool(a.cfg.get("sandbox"))),
                "panel_url": a.cfg.get("panel_url"), "managed": bool(a.cfg.get("managed")),
                "practice": bool(a.cfg.get("practice") or a.cfg.get("fake")),  # a sandbox's pretend server
                "players_supported": type(a).players is not Adapter.players,
                "players_tracked": bool(a.can_moderate() or a.cfg.get("players_watch")),
                "can_moderate": sup.can_moderate(a),
                "game_id": a.cfg.get("game_id"), "mode": a.cfg.get("mode"), "mode_name": a.cfg.get("mode_name"),
                "created": a.cfg.get("created"),
                "framework": a.cfg.get("framework"),
                "settings": {k: a.cfg.get(k) for k in ("max_players", "restart_times", "backup", "keep_backups", "world_size",
                                                       "messages", "restart_warn_s", "countdown_message", *RUST_TEXT, *RUST_FLAGS, "region")}
                            | ({"battleye": a.cfg.get("battleye") is not False} if getattr(a, "is_arma3", False) else {}),
                "can_say": a.can_say(),
                "rotation": a.rotation() if hasattr(a, "rotation") else None,
                "wipes": (a.wipe_settings() | {"world_size": a.cfg.get("wipe_world_size") or 0, "next": a.next_wipes(4)})
                         if hasattr(a, "wipe_due") else None,
                "steam_user": a.steam_user() if getattr(a, "steam_user_key", "") and hasattr(a, "steam_user") else None,
                "ports": {k: a.cfg[k] for k in ("port", "query_port", "rcon_port") if isinstance(a.cfg.get(k), int)},
                **snap}

    def controllable(a):
        return mode == "live" or bool(a.cfg.get("sandbox"))

    def detail(sid):
        a = adapters[sid]
        out = describe(sid)
        files = []
        for f in sorted(a.backup_files(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:30]:
            try:
                st = f.stat()
                files.append({"name": f.name, "size": st.st_size, "time": st.st_mtime})
            except OSError:
                pass
        out["backups"] = files
        out["log_names"] = list(a.logs().keys())
        return out

    def do_action(a, action, arg, who=""):
        try:
            result = a.run_action(action, arg)
            add_event(a.id, "info", f"{action}{' ' + arg if arg else ''}: {result}{f' (by {who})' if who else ''}")
        except Exception as e:
            add_event(a.id, "warn", f"{action} failed: {e}")
        time.sleep(2)
        sup.check(a.id)

    class Handler(BaseHTTPRequestHandler):
        server_version = "HostSpawn"

        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            for c in getattr(self, "_set_cookies", []):
                self.send_header("Set-Cookie", c)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def bearer(self) -> str:
            h = self.headers.get("Authorization", "")
            return h[7:].strip() if h[:7].lower() == "bearer " else ""

        def token(self) -> str:
            if self.bearer():
                return self.bearer()
            from http.cookies import SimpleCookie
            try:
                c = SimpleCookie(self.headers.get("Cookie", ""))
                return c["mgr_session"].value if "mgr_session" in c else ""
            except Exception:
                return ""

        def set_session(self, token: str, max_age: int):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
            self._set_cookies = [f"mgr_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}"]

        def versioned(self):
            """/api/v1/x is the same as /api/x (apps use v1 so later versions can change things safely)."""
            if self.path.startswith("/api/v1/"):
                self.path = "/api/" + self.path[len("/api/v1/"):]

        def authed(self, public: bool = False):
            host = self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in hosts:
                self.send(421, {"error": "unknown host name"})
                return False
            self.user = None
            if accounts is not None:
                self.user = accounts.session(self.token())
                if self.bearer() and not self.user and not public:
                    self.send(401, {"error": "This device is signed out. Sign in again."})
                    return False
                if self.user or public:
                    return True
                self.send(401, {"error": "Please sign in", "setup_needed": accounts.setup_needed})
                return False
            if password and hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.user = {"user": "admin", "role": "admin"}
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="HostSpawn"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_GET(self):
            self.versioned()
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self.authed(public=u.path in ("/", "/api/setup/status", "/api/info")):
                return
            if u.path == "/api/info":  # what an app checks first: is this a HostSpawn agent, and which API
                return self.send(200, {"name": "HostSpawn", "api": API_VERSION, "version": app_version(),
                                       "accounts": accounts is not None,
                                       "setup_needed": bool(accounts and accounts.setup_needed)})
            if u.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html")
            if u.path == "/api/setup/status":
                return self.send(200, {"accounts": accounts is not None,
                                       "setup_needed": bool(accounts and accounts.setup_needed),
                                       "signed_in": bool(self.user)})
            if u.path == "/api/me":
                return self.send(200, self.user)
            if u.path == "/api/devices":
                if not accounts:
                    return self.send(404, {"error": "accounts are off"})
                return self.send(200, accounts.devices(None if self.user["role"] == "admin" else self.user["user"]))
            if u.path == "/api/users":
                if not accounts or self.user["role"] != "admin":
                    return self.send(403, {"error": "Only admins can see users"})
                return self.send(200, accounts.list())
            if u.path in ("/api/files", "/api/files/dir", "/api/file", "/api/file/version"):  # file editor (admins)
                from .files import FileError, Files
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if self.user["role"] != "admin":
                    return self.send(403, {"error": "Only admins can open an instance's files"})
                try:
                    f = Files(adapters[sid])
                    if u.path == "/api/files":
                        return self.send(200, {"files": f.list(), "missions": f.missions()})
                    if u.path == "/api/files/dir":
                        return self.send(200, f.browse(q.get("path", [""])[0]))
                    path = q.get("path", [""])[0]
                    if u.path == "/api/file":
                        return self.send(200, f.read(path))
                    return self.send(200, {"text": f.version_text(path, q.get("id", [""])[0])})
                except FileError as e:
                    return self.send(400, {"error": str(e)})
            if u.path == "/api/install":  # an instance's installation: sizes, version, update, job, history
                from .install import InstallError
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                try:
                    return self.send(200, sup.install.info(sid))
                except InstallError as e:
                    return self.send(400, {"error": str(e)})
            if u.path == "/api/hostcheck":  # PC check: can this machine run each game (a guide, with a disclaimer)
                return self.send(200, hostcheck.report())
            if u.path == "/api/overview":
                jobs = [j.public() | {"log": j.d.get("log", [])[-3:]} for j in deployer.jobs.values()] if deployer else []
                jobs = [j for j in jobs if j["status"] != "done" or time.time() - j.get("finished", 0) < 600]
                limits = {"instances": deployer.instances_used() if deployer else 0, "max_instances": instances.max_instances()}
                return self.send(200, {"mode": mode, "community": community(), "jobs": jobs, "limits": limits,
                                       "sandbox": bool(os.environ.get("MANAGER_TEST_STEAMCMD")),
                                       "servers": [describe(s) for s in sorted(adapters, key=instances.number)],
                                       "host": host_stats(), "events": read_events(60)})
            if u.path == "/api/logs":
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                return self.send(200, adapters[sid].logs())
            if u.path == "/api/events":
                return self.send(200, read_events(500))
            # ---- players (state/players.db) ----
            if u.path == "/api/players":
                sid = q.get("server", [""])[0]
                if sid and sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                return self.send(200, sup.db.online(sid or None))
            if u.path == "/api/players/search":
                return self.send(200, sup.db.search(q.get("q", [""])[0][:64]))
            if u.path == "/api/player":
                p = sup.db.profile(q.get("key", [""])[0])
                return self.send(200, p) if p else self.send(404, {"error": "unknown player"})
            if u.path == "/api/bans":
                return self.send(200, sup.db.bans(include_old=q.get("all", [""])[0] == "1"))
            if u.path == "/api/audit":
                if self.user["role"] not in ("admin", "moderator"):
                    return self.send(403, {"error": "Only staff can see the audit log"})
                return self.send(200, sup.db.audit_log())
            if u.path == "/api/server":
                sid = q.get("id", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                out = detail(sid)
                if self.user["role"] == "admin" and out.get("managed"):
                    out["folder"] = str(adapters[sid].root)  # the app's Open folder button (admins only)
                return self.send(200, out)
            if u.path == "/api/doctor":  # Server Doctor: what's wrong and how to fix it
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                return self.send(200, sup.diagnose(sid))
            if u.path == "/api/spyglass":  # Altis Life's anti-cheat: on/off, allowed parts, who it threw out (admins)
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if self.user["role"] != "admin":
                    return self.send(403, {"error": "Only admins can see Spyglass"})
                a = adapters[sid]
                if not hasattr(a, "spyglass_status"):
                    return self.send(200, {"present": False})
                return self.send(200, a.spyglass_status())
            if u.path == "/api/aihelp":  # Copy for AI: a guide to the instance for the owner's own AI (admins only)
                from . import aihelp
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if self.user["role"] != "admin":
                    return self.send(403, {"error": "Only admins can copy an instance's AI help"})
                return self.send(200, {"text": aihelp.guide(adapters[sid], doctor=sup.diagnose(sid))})
            if u.path in ("/api/db", "/api/db/tables", "/api/db/rows"):  # the Database tab (admins only)
                from .database import Database, DbError
                sid = q.get("server", [""])[0]
                if sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if self.user["role"] != "admin":
                    return self.send(403, {"error": "Only admins can open an instance's databases"})
                g = lambda k, d="": q.get(k, [d])[0]
                try:
                    d = Database(adapters[sid])
                    if u.path == "/api/db":
                        return self.send(200, {"sources": d.sources()})
                    if u.path == "/api/db/tables":
                        return self.send(200, {"tables": d.tables(g("source"))})
                    return self.send(200, d.rows(g("source"), g("table"), int(g("page", "0") or 0), g("search"),
                                                 g("order"), g("desc") in ("1", "true")))
                except (DbError, ValueError) as e:
                    return self.send(400, {"error": str(e)})
            if u.path == "/api/deps":
                rust = [{"id": sid, "name": a.name, "framework": a.cfg.get("framework")} for sid, a in list(adapters.items())
                        if a.cfg.get("adapter") == "rust-server"]
                return self.send(200, deps.status(rust))
            if u.path == "/api/steam/login":
                s = logins.get(q.get("target", [""])[0])
                return self.send(200, s.public() if s else {"state": "none"})
            if u.path == "/api/settings":
                cfg = settings()
                return self.send(200, {"community": community(), "backups": sup.backups, "mode": mode,
                                       "interval": sup.interval, "servers_dir": str(cfg.get("paths", {}).get("servers", ""))})
            if u.path == "/api/catalog":
                return self.send(200, catalog.public_catalog())
            if u.path == "/api/terms":
                return self.send(200, {"version": catalog.TERMS_VERSION, "text": (ROOT / "docs/TERMS_DRAFT.md").read_text()})
            if u.path == "/api/catalog/releases":
                try:
                    rels = catalog.releases(q.get("game", [""])[0], q.get("mode", [""])[0])
                except Exception as e:
                    return self.send(502, {"error": f"couldn't reach GitHub: {type(e).__name__}"})
                return self.send(200, [{"tag": r["tag"], "date": r["date"], "assets": sorted(r["assets"])} for r in rels])
            if u.path == "/api/job":
                j = deployer.jobs.get(q.get("id", [""])[0]) if deployer else None
                return self.send(200, j.public()) if j else self.send(404, {"error": "unknown job"})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            self.versioned()
            if not self.authed(public=self.path in ("/api/login", "/api/setup", "/api/pair")):
                return
            if self.headers.get("X-Manager") != "1":
                return self.send(403, {"error": "missing X-Manager header"})
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():  # we only read Content-Length bodies
                self.close_connection = True
                return self.send(411, {"error": "send the request with a Content-Length (chunked bodies aren't accepted)"})
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if not 0 <= length <= 10_000:
                return self.send(400, {"error": "bad request size"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeDecodeError):
                return self.send(400, {"error": "bad JSON"})
            if not isinstance(body, dict):
                return self.send(400, {"error": "bad JSON"})
            # ---- accounts: first-run setup, sign in/out, app devices ----
            if self.path == "/api/pair":
                if accounts is None:
                    return self.send(404, {"error": "accounts are off"})
                try:
                    tok = accounts.pair(str(body.get("code", "")), str(body.get("device", "")))
                except PermissionError as e:
                    return self.send(429, {"error": str(e)})
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                who = accounts.session(tok)
                add_event("accounts", "info", f"device \"{who['device']}\" paired for {who['user']}")
                return self.send(200, who | {"token": tok})
            if self.path in ("/api/setup", "/api/login"):
                if accounts is None:
                    return self.send(404, {"error": "accounts are off"})
                user, pw = str(body.get("username", "")).strip(), str(body.get("password", ""))
                if self.path == "/api/setup" and self.client_address[0] != "127.0.0.1":
                    return self.send(403, {"error": "The first account can only be created on the machine HostSpawn runs on."})
                try:
                    if self.path == "/api/setup":
                        accounts.setup_owner(user, pw)
                        add_event("accounts", "info", f"owner account {user} created by the setup wizard")
                    remember = bool(body.get("remember")) or self.path == "/api/setup"
                    device = str(body.get("device", "")) if body.get("device") else ""
                    tok = accounts.login(user, pw, self.client_address[0], remember, device)
                except PermissionError as e:
                    return self.send(429 if self.path == "/api/login" else 403, {"error": str(e)})
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                finally:
                    body.pop("password", None)
                if device:  # an app: the token goes in the reply (the app keeps it in the system's keychain)
                    add_event("accounts", "info", f"device \"{device.strip()}\" signed in as {user}")
                    return self.send(200, accounts.session(tok) | {"token": tok})
                from .accounts import REMEMBER_S, SESSION_S
                self.set_session(tok, REMEMBER_S if remember else SESSION_S)
                return self.send(200, accounts.session(tok))
            if self.user is None:
                return self.send(401, {"error": "Please sign in"})
            if accounts is not None:
                from .accounts import allowed
                if not allowed(self.user["role"], self.path, str(body.get("action", ""))):
                    return self.send(403, {"error": f"Your role ({self.user['role']}) can't do that. Ask an admin."})
            if self.path == "/api/logout":
                if accounts is not None:
                    accounts.logout(self.token())
                self._set_cookies = ["mgr_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"]
                return self.send(200, {"message": "Signed out"})
            if self.path == "/api/pair/new" and accounts is not None:
                from .accounts import PAIR_S
                code = accounts.new_pair_code(self.user["user"])
                add_event("accounts", "info", f"pairing code made by {self.user['user']}")
                return self.send(200, {"code": code, "expires_in": PAIR_S})
            if self.path == "/api/devices" and accounts is not None:
                if str(body.get("action", "")) != "revoke":
                    return self.send(400, {"error": "unknown device action"})
                try:
                    accounts.revoke_device(str(body.get("id", "")), None if self.user["role"] == "admin" else self.user["user"])
                except ValueError as e:
                    return self.send(404, {"error": str(e)})
                add_event("accounts", "info", f"device signed out by {self.user['user']}")
                return self.send(200, {"message": "Device signed out"})
            if self.path == "/api/me/password" and accounts is not None:
                try:
                    accounts.change_password(self.user["user"], str(body.get("old", "")), str(body.get("new", "")))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                return self.send(200, {"message": "Password changed. Other devices are signed out."})
            if self.path == "/api/users" and accounts is not None:
                act_, name = str(body.get("action", "")), str(body.get("username", ""))
                try:
                    if act_ == "add":
                        accounts.create(name, str(body.get("password", "")), str(body.get("role", "viewer")), by=self.user["user"])
                    elif act_ == "role":
                        accounts.set_role(name, str(body.get("role", "")))
                    elif act_ == "remove":
                        if name == self.user["user"]:
                            raise ValueError("You can't remove yourself.")
                        accounts.remove(name)
                    else:
                        raise ValueError("unknown user action")
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                add_event("accounts", "info", f"user {name}: {act_} (by {self.user['user']})")
                return self.send(200, {"message": "Done", "users": accounts.list()})
            if self.path == "/api/deploy" and deployer:
                opts = body.get("options") if isinstance(body.get("options"), dict) else {}
                try:
                    job = deployer.create(str(body.get("game", "")), str(body.get("mode", "")), opts,
                                          str(body.get("request_id", "")), str(body.get("accepted_terms", "")))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                return self.send(201, job.public())
            if self.path == "/api/job" and deployer:
                jid, act = str(body.get("id", "")), str(body.get("action", ""))
                try:
                    if act == "discard":
                        return self.send(200, {"message": deployer.discard_job(jid)})
                    if act not in ("continue", "back", "skip", "cancel", "retry"):
                        return self.send(400, {"error": "unknown job action"})
                    return self.send(200, deployer.action(jid, act).public())
                except KeyError:
                    return self.send(404, {"error": "unknown job"})
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if self.path == "/api/steam/login":
                # Passwords only from this machine (or an encrypted Tailscale address in ALLOWED_HOSTS).
                if self.client_address[0] != "127.0.0.1":
                    return self.send(403, {"error": "Steam logins are only accepted on this machine"})
                target, act_ = str(body.get("target", "")), str(body.get("action", ""))
                try:
                    steamcmd, user, a = login_target(target)
                except KeyError as e:
                    return self.send(404, {"error": str(e)})
                if act_ == "start":
                    return self.send(200, logins.start(target, steamcmd, user).public())
                s = logins.get(target)
                if not s or act_ not in ("password", "code"):
                    return self.send(400, {"error": "start the login first"})
                try:
                    s.submit("need_password" if act_ == "password" else "need_code", str(body.get("value", "")))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
                finally:
                    body.pop("value", None)  # drop the password as soon as it has been typed into SteamCMD
                if target.startswith("server:"):
                    def clear_when_ok(sess=s, ad=a):
                        for _ in range(120):
                            if sess.state == "ok":
                                ad.clear("login_needed")
                                add_event(ad.id, "info", "Steam login saved by SteamCMD (password not stored)")
                                return
                            if sess.state == "failed":
                                return
                            time.sleep(1)
                    threading.Thread(target=clear_when_ok, daemon=True).start()
                return self.send(200, s.public())
            if self.path in ("/api/file", "/api/file/restore", "/api/file/pack"):
                from .files import FileError, Files
                sid = body.get("server")
                if not isinstance(sid, str) or sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if not controllable(adapters[sid]):
                    return self.send(403, {"error": "This copy of HostSpawn only watches this instance."})
                try:
                    f, by = Files(adapters[sid]), self.user["user"]
                    if self.path == "/api/file/pack":
                        return self.send(200, {"message": f.pack_mission(str(body.get("folder", "")), by)})
                    path = str(body.get("path", ""))
                    if self.path == "/api/file/restore":
                        return self.send(200, f.restore(path, str(body.get("id", "")), by))
                    text = body.get("text")
                    if not isinstance(text, str):
                        return self.send(400, {"error": "no text"})
                    return self.send(200, f.save(path, text, str(body.get("sha", "")), by))
                except FileError as e:
                    return self.send(400, {"error": str(e)})
            if self.path in ("/api/db/query", "/api/db/change"):  # admins only (accounts.allowed)
                from .database import Database, DbError
                sid = body.get("server")
                if not isinstance(sid, str) or sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if not controllable(adapters[sid]):
                    return self.send(403, {"error": "This copy of HostSpawn only watches this instance."})
                try:
                    d, src = Database(adapters[sid]), str(body.get("source", ""))
                    if self.path == "/api/db/query":
                        return self.send(200, d.query(src, str(body.get("sql", ""))))
                    edit = body.get("edit")
                    return self.send(200, d.change(src, self.user["user"], str(body.get("confirm", "")),
                                                   sql=str(body.get("sql", "")),
                                                   edit=edit if isinstance(edit, dict) else None))
                except DbError as e:
                    return self.send(400, {"error": str(e)})
            if self.path == "/api/install":
                from .install import OPS, InstallError
                sid, act_ = body.get("server"), str(body.get("action", ""))
                if not isinstance(sid, str) or sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                if act_ not in OPS:
                    return self.send(400, {"error": "unknown action"})
                if not controllable(adapters[sid]):
                    return self.send(403, {"error": "This copy of HostSpawn only watches this instance."})
                try:
                    warn_s = int(body.get("warn_s") or 0)
                except (TypeError, ValueError):
                    return self.send(400, {"error": "warn_s must be a number of seconds"})
                try:
                    job = sup.install.start(sid, act_, by=self.user["user"], confirm=str(body.get("confirm", "")),
                                            warn_s=warn_s)
                except InstallError as e:
                    return self.send(400, {"error": str(e)})
                return self.send(200, {"message": "started", "job": job})
            if self.path == "/api/settings":
                errs, ch = [], {}
                if "community" in body:
                    name = re.sub(r"[\x00-\x1f\"\\\\]", "", str(body["community"])).strip()[:60]
                    if not name:
                        errs.append("Community name can't be empty")
                    ch["community"] = {"name": name}
                b = body.get("backups")
                if isinstance(b, dict):
                    nb = {}
                    if "enabled" in b:
                        nb["enabled"] = bool(b["enabled"])
                    if "time" in b:
                        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(b["time"])):
                            errs.append("Backup time must be HH:MM (24-hour)")
                        nb["time"] = str(b["time"])
                    if "max_age_hours" in b:
                        try:
                            nb["max_age_hours"] = max(1, min(720, int(b["max_age_hours"])))
                        except (TypeError, ValueError):
                            errs.append("Backup alert age must be a number of hours")
                    ch["backups"] = nb
                if "terms_accepted" in body:
                    if str(body["terms_accepted"]) != catalog.TERMS_VERSION:
                        errs.append("Those aren't the current terms")
                    ch["terms"] = {"accepted_version": catalog.TERMS_VERSION, "accepted_by": self.user["user"],
                                   "accepted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                if errs:
                    return self.send(400, {"error": "; ".join(errs)})
                save_settings(ch)
                for k, v in ch.get("backups", {}).items():
                    sup.backups[k] = float(v) if k == "max_age_hours" else v
                return self.send(200, {"message": "Settings saved"})
            if self.path == "/api/server/settings":
                sid = body.get("server")
                if not isinstance(sid, str) or sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                a = adapters[sid]
                if not controllable(a) or not a.cfg.get("managed"):
                    return self.send(403, {"error": "Only servers HostSpawn created can be changed here"})
                ch, errs = {}, []
                if "name" in body:
                    nm = re.sub(r"[\x00-\x1f\"\\\\]", "", str(body["name"])).strip()[:80]
                    if not nm:
                        errs.append("Name can't be empty")
                    ch["name"] = ch["hostname"] = nm
                if any(k in body for k in RUST_TEXT + RUST_FLAGS + ("region",)):
                    from .adapters.rust_server import RUST_REGIONS, RustServer
                    if not isinstance(a, RustServer):
                        errs.append("Those settings are for Rust instances")
                    for k in RUST_TEXT:
                        if k in body:
                            v = re.sub(r"[\x00-\x1f\"\\\\]", "", str(body[k] or "")).strip()
                            if k == "description" and len(v) > 400:
                                errs.append("Description: 400 characters at most")
                            elif k != "description" and v and not re.fullmatch(r"https://[^\s'<>]{3,200}", v):
                                errs.append(f"{k.replace('_', ' ').capitalize()}: a web address starting with https://")
                            ch[k] = v
                    for k in RUST_FLAGS:
                        if k in body:
                            ch[k] = bool(body[k])
                    if "region" in body:
                        if body["region"] not in ("", *RUST_REGIONS):
                            errs.append("Unknown region")
                        ch["region"] = str(body["region"] or "")
                if "max_players" in body:
                    try:
                        mp = int(body["max_players"])
                        if not 1 <= mp <= 500:
                            raise ValueError
                        ch["max_players"] = mp
                    except (TypeError, ValueError):
                        errs.append("Max players must be 1–500")
                if "restart_times" in body:
                    rt = body["restart_times"] if isinstance(body["restart_times"], list) else []
                    if len(rt) > 6 or not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(t)) for t in rt):
                        errs.append("Restart times must be HH:MM, at most 6")
                    ch["restart_times"] = sorted(set(str(t) for t in rt))
                if "backup" in body:
                    ch["backup"] = bool(body["backup"])
                if "battleye" in body:  # Arma 3 only: BattlEye anti-cheat on or off (server.cfg, next start)
                    if not getattr(a, "is_arma3", False):
                        errs.append("BattlEye can only be switched for Arma 3 instances")
                    else:
                        ch["battleye"] = bool(body["battleye"])
                if "rotation" in body or "rotation_on" in body:
                    if not hasattr(a, "rotation"):
                        errs.append("This server has no map rotation")
                    else:
                        valid = {x["id"] for x in catalog.scenarios("reforger")}
                        if "rotation" in body:
                            rot = body["rotation"] if isinstance(body["rotation"], list) else None
                            if rot is None or len(rot) > 20 or any(str(x) not in valid for x in rot):
                                errs.append("Rotation can only contain the game's own scenarios (at most 20)")
                            else:
                                ch["rotation"] = [str(x) for x in rot]
                        if "rotation_on" in body:
                            ch["rotation_on"] = bool(body["rotation_on"])
                if any(k in body for k in ("wipe_mode", "wipe_time", "wipe_bp", "wipe_new_seed", "wipe_world_size")):
                    if not hasattr(a, "wipe_due"):
                        errs.append("This server has no wipes")
                    else:
                        from .adapters.rust_server import BP_POLICIES, WIPE_MODES
                        if "wipe_mode" in body:
                            ch["wipe_mode"] = str(body["wipe_mode"])
                            if ch["wipe_mode"] not in WIPE_MODES:
                                errs.append("Unknown wipe frequency")
                        if "wipe_bp" in body:
                            ch["wipe_bp"] = str(body["wipe_bp"])
                            if ch["wipe_bp"] not in BP_POLICIES:
                                errs.append("Unknown blueprint choice")
                        if "wipe_time" in body:
                            ch["wipe_time"] = str(body["wipe_time"])
                            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", ch["wipe_time"]):
                                errs.append("Wipe time must be HH:MM")
                        if "wipe_new_seed" in body:
                            ch["wipe_new_seed"] = bool(body["wipe_new_seed"])
                        if "wipe_world_size" in body:
                            try:
                                ws = int(body["wipe_world_size"] or 0)
                                if ws and not 1000 <= ws <= 6000:
                                    raise ValueError
                                ch["wipe_world_size"] = ws
                            except (TypeError, ValueError):
                                errs.append("Map size must be 1000–6000")
                if "restart_warn_s" in body:
                    try:
                        ch["restart_warn_s"] = int(body["restart_warn_s"])
                        if not 0 <= ch["restart_warn_s"] <= 1800:
                            raise ValueError
                    except (TypeError, ValueError):
                        errs.append("Countdown must be 0 to 30 minutes")
                if "countdown_message" in body:
                    cm = re.sub(r"[\x00-\x1f\x7f]", "", str(body["countdown_message"])).strip()[:120]
                    if cm and ("{time}" not in cm):
                        errs.append("The countdown message needs {time} in it")
                    ch["countdown_message"] = cm
                if "messages" in body:
                    msgs, ok = body["messages"] if isinstance(body["messages"], list) else None, []
                    if msgs is None or len(msgs) > 10:
                        errs.append("At most 10 scheduled messages")
                    for m in msgs or []:
                        text = re.sub(r"[\x00-\x1f\x7f]", "", str((m or {}).get("text", ""))).strip()[:200] if isinstance(m, dict) else ""
                        if not text:
                            errs.append("A scheduled message is empty")
                            continue
                        if m.get("times"):
                            times = m["times"] if isinstance(m["times"], list) else []
                            if not times or len(times) > 6 or not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(x)) for x in times):
                                errs.append("Message times must be HH:MM, at most 6")
                                continue
                            ok.append({"text": text, "times": sorted(set(map(str, times)))})
                        else:
                            try:
                                every = int(m.get("every_min", 0))
                                if not 5 <= every <= 1440:
                                    raise ValueError
                            except (TypeError, ValueError):
                                errs.append("Repeat messages every 5 to 1440 minutes")
                                continue
                            ok.append({"text": text, "every_min": every})
                    ch["messages"] = ok
                if "keep_backups" in body:
                    try:
                        ch["keep_backups"] = max(1, min(100, int(body["keep_backups"])))
                    except (TypeError, ValueError):
                        errs.append("Backups to keep must be a number")
                if errs:
                    return self.send(400, {"error": "; ".join(errs)})
                a.save_cfg(ch)
                add_event(sid, "info", "settings changed: " + ", ".join(sorted(k for k in ch if k != "hostname")))
                sup.check(sid)
                return self.send(200, {"message": "Saved. Name, player limit and BattlEye apply at the next restart."
                                       if "battleye" in ch else "Saved. Name and player limit apply at the next restart."})
            if self.path == "/api/deps/install":
                try:
                    return self.send(200, deps.install(str(body.get("id", ""))))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if self.path == "/api/players":
                act_, by = str(body.get("action", "")), self.user["user"]
                key, sid = str(body.get("key", "")), body.get("server")
                if act_ != "say" and not PLAYER_KEY.match(key):
                    return self.send(400, {"error": "unknown player"})
                if sid is not None and (not isinstance(sid, str) or sid not in adapters):
                    return self.send(404, {"error": "unknown server"})
                reason = re.sub(r"[\x00-\x1f\x7f]", "", str(body.get("reason", ""))).strip()[:120]
                try:
                    if act_ == "kick":
                        if not sid:
                            return self.send(400, {"error": "which server?"})
                        return self.send(200, {"message": sup.kick_player(sid, key, reason, by)})
                    if act_ == "ban":
                        try:
                            minutes = int(body.get("minutes", 0))
                        except (TypeError, ValueError):
                            minutes = -1
                        if not 0 <= minutes <= 525_600:
                            return self.send(400, {"error": "Ban length must be 0 (permanent) to 525600 minutes"})
                        if not reason:
                            return self.send(400, {"error": "Give a reason for the ban"})
                        ban, applied = sup.ban_player(key, minutes, reason, by)
                        return self.send(200, {"message": "Banned on every server", "ban": ban, "applied": applied})
                    if act_ == "unban":
                        sup.unban_player(key, by)
                        return self.send(200, {"message": "Unbanned"})
                    if act_ == "note":
                        text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(body.get("text", ""))).strip()[:1000]
                        if not text:
                            return self.send(400, {"error": "The note is empty"})
                        sup.db.note(key, text, by)
                        return self.send(200, {"message": "Note added"})
                    if act_ in ("watch", "unwatch"):
                        sup.db.watch(key, act_ == "watch", by, reason)
                        return self.send(200, {"message": "Watching" if act_ == "watch" else "Not watching"})
                    if act_ == "say":
                        text = re.sub(r"[\x00-\x1f\x7f]", "", str(body.get("text", ""))).strip()[:200]
                        if not text:
                            return self.send(400, {"error": "The message is empty"})
                        sent = sup.say(sid, text, by)
                        if not sent:
                            return self.send(400, {"error": "No server could take the message"})
                        return self.send(200, {"message": f"Sent to {len(sent)} server{'s' if len(sent) != 1 else ''}",
                                               "servers": sent})
                except ModerationError as e:
                    return self.send(400, {"error": str(e)})
                except Unsupported:
                    return self.send(400, {"error": "This server can't do that"})
                return self.send(400, {"error": "unknown player action"})
            if self.path == "/api/delete" and deployer:
                sid = body.get("server")
                if not isinstance(sid, str) or sid not in adapters:
                    return self.send(404, {"error": "unknown server"})
                a = adapters[sid]
                if not controllable(a):
                    return self.send(403, {"error": "Development copy: controls are off for this server."})
                if str(body.get("confirm", "")).strip() != a.name:
                    return self.send(400, {"error": f'type the server name "{a.name}" to confirm'})
                try:
                    return self.send(200, {"message": deployer.delete_server(sid, bool(body.get("keep_backups", True)))})
                except Exception as e:
                    return self.send(400, {"error": str(e)})
            sid, action, arg = body.get("server"), str(body.get("action", "")), str(body.get("arg", ""))
            if not isinstance(sid, str):
                return self.send(404, {"error": "not found"})
            if self.path != "/api/action" or sid not in adapters:
                return self.send(404, {"error": "not found"})
            a = adapters[sid]
            if not controllable(a):
                return self.send(403, {"error": "Development copy: controls are off. Use the live manager (port 8100)."})
            if action not in a.actions:
                return self.send(400, {"error": f"{a.name} doesn't support {action}"})
            if action == "wipe" and arg not in ("map", "full"):
                return self.send(400, {"error": "wipe must be map or full"})
            if action != "send" and action != "wipe":
                arg = ""
            want = f"{action} {arg}".strip()
            if action in a.confirm and str(body.get("confirm", "")).strip().lower() != want:
                return self.send(400, {"error": f'type "{want}" to confirm'})
            if action == "send":
                try:
                    return self.send(200, {"message": a.send(arg)})
                except (Unsupported, ValueError) as e:
                    return self.send(400, {"error": str(e)})
            threading.Thread(target=do_action, args=(a, action, arg, self.user["user"]), daemon=True).start()
            return self.send(202, {"message": f"{want} accepted: watch the events list"})

    return Handler


def serve(sup, host="127.0.0.1", port=8100, mode="dev", allowed_hosts=None, deployer=None):
    from .accounts import Accounts
    return ThreadingHTTPServer((host, port), make_handler(sup, "", mode, allowed_hosts, deployer, Accounts()))
