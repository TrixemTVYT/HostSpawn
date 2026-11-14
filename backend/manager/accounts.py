"""User accounts for Manager: owner set up by the first-run wizard, then more people with roles.

- Passwords are stored only as scrypt hashes (salted, slow); never the password itself.
- Sessions: a random token in an HttpOnly, SameSite=Strict cookie. Only a SHA-256 of each token is stored
  (state/sessions.json), so a stolen file can't be used to log in. Sessions expire (12 h, or 30 days with
  "remember me") and survive a Manager restart.
- Lock-out: 5 wrong passwords for a username or from one address lock it for 5 minutes, doubling each time.
- App sign-in (the HostSpawn program, later a phone): a device token sent as "Authorization: Bearer ...", kept
  hashed like a session but marked with a device name. It lasts 90 days and renews itself while in use. Devices
  can be listed and signed out one by one. A new device can also be paired with a one-time 6-digit code
  (5 minutes, one use, created by a signed-in user or `./manager.sh pair <user>` on this machine).
- Roles: admin (everything), moderator (run servers, no deleting/wiping/creating/settings), viewer (look only).
Files are chmod 600 and git-ignored (config/accounts.json, state/sessions.json)."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import util

ROLES = ("admin", "moderator", "viewer")
SESSION_S, REMEMBER_S, DEVICE_S = 12 * 3600, 30 * 86400, 90 * 86400
PAIR_S, PAIR_FAILS = 300, 10  # a pairing code lasts 5 min; 10 wrong codes in a row pause pairing for 5 min
DEVICE_RE = re.compile(r"[^\x00-\x1f]{1,40}")
MAX_FAILS, LOCK_S = 5, 300
N, R, P = 2 ** 14, 8, 1
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{3,32}")


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, want = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk.hex(), want)
    except (ValueError, TypeError):
        return False


def password_problem(pw: str, username: str = "") -> str | None:
    """None if acceptable, else a plain reason."""
    if len(pw) < 10:
        return "Use at least 10 characters."
    if username and username.lower() in pw.lower():
        return "Don't put your username in the password."
    if len(set(pw)) < 5:
        return "Use a less repetitive password."
    common = {"password12", "password123", "1234567890", "qwertyuiop", "letmein123", "admin12345"}
    if pw.lower() in common:
        return "That password is too common."
    return None


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.touch(mode=0o600)
    os.chmod(tmp, 0o600)
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)
    os.chmod(path, 0o600)


class Accounts:
    def __init__(self, users_file: Path | None = None, sessions_file: Path | None = None, pair_file: Path | None = None):
        self.users_file = users_file or util.CONFIG / "accounts.json"
        self.sessions_file = sessions_file or util.STATE / "sessions.json"
        # pending pairing codes (hashed) live in a file so `./manager.sh pair` can add one while Manager runs
        self.pair_file = pair_file or self.sessions_file.with_name("pairing.json")
        self._pair_fails, self._pair_paused = 0, 0.0
        self._lock = threading.Lock()
        self._fails: dict[str, list] = {}  # key -> [count, locked_until, lock_seconds]
        self.users = self._load(self.users_file).get("users", {})
        self.sessions = {k: v for k, v in self._load(self.sessions_file).items() if v.get("exp", 0) > time.time()}

    @staticmethod
    def _load(path: Path) -> dict:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_users(self):
        _write_private(self.users_file, {"users": self.users})

    def _save_sessions(self):
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if v.get("exp", 0) > now}
        _write_private(self.sessions_file, self.sessions)

    @property
    def setup_needed(self) -> bool:
        return not self.users

    # ---- users ----
    def create(self, username: str, password: str, role: str = "admin", by: str = "", only_if_first: bool = False) -> None:
        with self._lock:
            if only_if_first and self.users:
                raise PermissionError("Setup is already done.")
            if not USERNAME_RE.fullmatch(username or ""):
                raise ValueError("Username: 3–32 letters, numbers, dots, dashes or underscores.")
            if username.lower() in (u.lower() for u in self.users):
                raise ValueError("That username is taken.")
            if role not in ROLES:
                raise ValueError("Unknown role.")
            problem = password_problem(password, username)
            if problem:
                raise ValueError(problem)
            self.users[username] = {"hash": hash_password(password), "role": role, "created": time.time(), "by": by}
            self._save_users()

    def setup_owner(self, username: str, password: str) -> None:
        self.create(username, password, "admin", by="setup", only_if_first=True)

    def set_role(self, username: str, role: str) -> None:
        with self._lock:
            if username not in self.users or role not in ROLES:
                raise ValueError("Unknown user or role.")
            if self.users[username]["role"] == "admin" and role != "admin" and self._admins() == 1:
                raise ValueError("There must always be at least one admin.")
            self.users[username]["role"] = role
            self._save_users()
            self._drop_sessions(username)  # new rights take effect at the next login

    def remove(self, username: str) -> None:
        with self._lock:
            if username not in self.users:
                raise ValueError("Unknown user.")
            if self.users[username]["role"] == "admin" and self._admins() == 1:
                raise ValueError("You can't remove the last admin.")
            del self.users[username]
            self._save_users()
            self._drop_sessions(username)

    def change_password(self, username: str, old: str, new: str) -> None:
        with self._lock:
            u = self.users.get(username)
            if not u or not check_password(old, u["hash"]):
                raise ValueError("Your current password is wrong.")
            problem = password_problem(new, username)
            if problem:
                raise ValueError(problem)
            u["hash"] = hash_password(new)
            self._save_users()
            self._drop_sessions(username)  # signs out other devices

    def _admins(self) -> int:
        return sum(1 for u in self.users.values() if u["role"] == "admin")

    def list(self) -> list[dict]:
        return [{"username": n, "role": u["role"], "created": u.get("created"), "last_login": u.get("last_login")}
                for n, u in sorted(self.users.items())]

    # ---- login / sessions ----
    def _locked(self, key: str) -> float:
        f = self._fails.get(key)
        return max(0.0, f[1] - time.time()) if f else 0.0

    def _fail(self, key: str) -> None:
        if len(self._fails) > 10_000:  # someone trying many made-up names: forget old, unlocked entries
            now = time.time()
            self._fails = {k: v for k, v in self._fails.items() if v[1] > now}
        f = self._fails.setdefault(key, [0, 0.0, LOCK_S])
        f[0] += 1
        if f[0] >= MAX_FAILS:
            f[1] = time.time() + f[2]
            f[0], f[2] = 0, min(f[2] * 2, 6 * 3600)

    def login(self, username: str, password: str, ip: str = "", remember: bool = False, device: str = "") -> str:
        """Returns a session token. Raises ValueError (wrong) or PermissionError (locked)."""
        keys = [f"user:{username.lower()}", f"ip:{ip}"]
        wait = max(self._locked(k) for k in keys)
        if wait:
            raise PermissionError(f"Too many wrong attempts. Try again in {int(wait // 60) + 1} min.")
        name = next((n for n in self.users if n.lower() == username.lower()), None)
        ok = check_password(password, self.users[name]["hash"]) if name else check_password(password, _DUMMY)
        if not ok:
            for k in keys:
                self._fail(k)
            raise ValueError("Wrong username or password.")
        for k in keys:
            self._fails.pop(k, None)
        if device:
            return self._new_token(name, device)
        token = secrets.token_urlsafe(32)
        with self._lock:
            self.sessions[_digest(token)] = {"user": name, "exp": time.time() + (REMEMBER_S if remember else SESSION_S),
                                             "created": time.time()}
            self.users[name]["last_login"] = time.time()
            self._save_sessions()
            self._save_users()
        return token

    def _new_token(self, name: str, device: str) -> str:
        device = device.strip()
        if not DEVICE_RE.fullmatch(device):
            raise ValueError("Device name: 1–40 characters.")
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self.sessions[_digest(token)] = {"user": name, "exp": now + DEVICE_S, "created": now, "device": device,
                                             "last_used": now}
            self.users[name]["last_login"] = now
            self._save_sessions()
            self._save_users()
        return token

    def session(self, token: str) -> dict | None:
        """{"user", "role"} for a valid session token, else None."""
        if not token:
            return None
        s = self.sessions.get(_digest(token))
        if not s or s["exp"] < time.time() or s["user"] not in self.users:
            return None
        if s.get("device"):
            now = time.time()
            s["last_used"] = now
            if s["exp"] - now < DEVICE_S / 2:  # in use: renew (writes at most a few times per 90 days)
                with self._lock:
                    s["exp"] = now + DEVICE_S
                    self._save_sessions()
        return {"user": s["user"], "role": self.users[s["user"]]["role"], **({"device": s["device"]} if s.get("device") else {})}

    # ---- devices (app sign-ins) ----
    def devices(self, username: str | None = None) -> list[dict]:
        """Signed-in devices; the id is a short part of the token's hash (useless for signing in)."""
        now = time.time()
        return sorted(({"id": k[:16], "user": v["user"], "device": v["device"], "created": v.get("created"),
                        "last_used": v.get("last_used"), "expires": v["exp"]}
                       for k, v in self.sessions.items()
                       if v.get("device") and v["exp"] > now and (username is None or v["user"] == username)),
                      key=lambda d: d["created"] or 0)

    def revoke_device(self, device_id: str, username: str | None = None) -> None:
        """Sign one device out. username=None (admins) may revoke anyone's device."""
        with self._lock:
            keys = [k for k, v in self.sessions.items() if v.get("device") and len(device_id) == 16
                    and k.startswith(device_id) and (username is None or v["user"] == username)]
            if not keys:
                raise ValueError("Unknown device.")
            for k in keys:
                del self.sessions[k]
            self._save_sessions()

    # ---- pairing codes ----
    def new_pair_code(self, username: str) -> str:
        """A one-time 6-digit code that signs a new device in as this user. Only its hash is stored."""
        if username not in self.users:
            raise ValueError("Unknown user.")
        code = f"{secrets.randbelow(10 ** 6):06d}"
        with self._lock:
            now = time.time()
            pending = {k: v for k, v in self._load(self.pair_file).items() if v.get("exp", 0) > now}
            mine = sorted((k for k, v in pending.items() if v["user"] == username), key=lambda k: pending[k]["exp"])
            for k in mine[:-2]:  # at most 3 codes waiting per user
                del pending[k]
            pending[_digest("pair:" + code)] = {"user": username, "exp": now + PAIR_S}
            _write_private(self.pair_file, pending)
        return code

    def pair(self, code: str, device: str) -> str:
        """Swap a pairing code for a device token. Raises ValueError (wrong/expired) or PermissionError (paused)."""
        with self._lock:
            now = time.time()
            if self._pair_paused > now:
                raise PermissionError(f"Too many wrong codes. Try again in {int((self._pair_paused - now) // 60) + 1} min.")
            pending = {k: v for k, v in self._load(self.pair_file).items() if v.get("exp", 0) > now}
            hit = pending.pop(_digest("pair:" + str(code).strip()), None) if re.fullmatch(r"\d{6}", str(code).strip()) else None
            if not hit or hit["user"] not in self.users:
                self._pair_fails += 1
                if self._pair_fails >= PAIR_FAILS:  # counted across every address: 10 guesses out of a million
                    self._pair_paused, self._pair_fails = now + LOCK_S, 0
                    _write_private(self.pair_file, {})  # and every waiting code is cancelled
                raise ValueError("That code is wrong or has expired.")
            if not DEVICE_RE.fullmatch(device.strip()):
                raise ValueError("Device name: 1–40 characters.")
            self._pair_fails = 0
            _write_private(self.pair_file, pending)  # used once
        return self._new_token(hit["user"], device)

    def logout(self, token: str) -> None:
        with self._lock:
            if self.sessions.pop(_digest(token), None) is not None:
                self._save_sessions()

    def _drop_sessions(self, username: str) -> None:
        self.sessions = {k: v for k, v in self.sessions.items() if v["user"] != username}
        self._save_sessions()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# Compared against when the username doesn't exist, so a wrong username takes as long as a wrong password.
_DUMMY = hash_password(secrets.token_urlsafe(16))

# What each role may do (POST paths and server actions). Viewers can only look.
MODERATOR_ACTIONS = {"start", "stop", "restart", "update", "backup", "send"}  # rotate, wipe, oxide: admins only
MODERATOR_PLAYER_ACTIONS = {"kick", "ban", "unban", "note", "watch", "unwatch", "say"}
MODERATOR_INSTALL_ACTIONS = {"update", "check"}  # repair, reinstall and clean up are admin only


def allowed(role: str, path: str, action: str = "") -> bool:
    if role == "admin":
        return True
    if role == "moderator":
        if path == "/api/action":
            return action in MODERATOR_ACTIONS
        if path == "/api/job":
            return action != "discard"  # discarding deletes files: admins only
        if path == "/api/players":
            return action in MODERATOR_PLAYER_ACTIONS
        if path == "/api/install":
            return action in MODERATOR_INSTALL_ACTIONS
        return path in ("/api/steam/login", *EVERYONE)
    return path in EVERYONE


EVERYONE = ("/api/me/password", "/api/logout", "/api/devices", "/api/pair/new")  # own account and own devices
