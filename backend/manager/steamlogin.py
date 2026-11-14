"""Steam login for SteamCMD from inside Manager, without a terminal and without storing the password.

SteamCMD runs in its own pseudo-terminal (pty), as if a person were typing. The password and any Steam Guard
code are written straight into its prompt and then dropped: never saved to disk or settings, never put on a
command line (where other programs could read it), never logged. After a successful login SteamCMD caches its
own login token, so later updates run without a password. Steam errors are reported, never retried here, so the
account can't be locked by repeated attempts."""
import os
import pty
import re
import select
import subprocess
import threading
import time

PROMPTS = [
    ("need_password", re.compile(r"password:\s*$", re.I | re.M)),
    ("need_code", re.compile(r"(Steam Guard code|Two-factor code|two factor code|Enter the current code)[^\n]*:\s*$", re.I | re.M)),
    ("confirm_on_phone", re.compile(r"confirm the login in the Steam Mobile app", re.I)),
]
DONE = re.compile(r"Waiting for user info\.\.\.OK|Logged in OK|Logging in user .*?\.\.\.OK", re.I)
FAIL = [
    (re.compile(r"Invalid Password|InvalidPassword", re.I), "Steam says the password is wrong."),
    (re.compile(r"Rate Limit|RateLimitExceeded|too many", re.I), "Steam is limiting login attempts. Wait 30 minutes, then try once more."),
    (re.compile(r"Invalid Login Auth Code|InvalidLoginAuthCode|Two-factor code mismatch|TwoFactorCodeMismatch", re.I),
     "That Steam Guard code was wrong or expired. Start the login again and use the newest code."),
    (re.compile(r"Account Logon Denied|AccountLogonDenied", re.I), "Steam refused the login. Check the email for that account."),
    (re.compile(r"FAILED \(", re.I), "Steam login failed."),
]
IDLE_TIMEOUT = 180  # seconds of no progress before the session gives up


class LoginSession:
    def __init__(self, steamcmd: str, user: str):
        self.steamcmd, self.user = steamcmd, user
        self.state, self.message = "starting", "Connecting to Steam…"
        self._out = ""
        self._fd = None
        self._proc = None
        self._lock = threading.Lock()
        self._last = time.time()
        self._answered = set()

    def start(self):
        master, slave = pty.openpty()
        self._fd = master
        # Only the username goes on the command line; the password is typed into the prompt later.
        self._proc = subprocess.Popen([self.steamcmd, "+login", self.user], stdin=slave, stdout=slave, stderr=slave,
                                      close_fds=True, start_new_session=True)
        os.close(slave)
        threading.Thread(target=self._read, daemon=True).start()
        return self

    def _read(self):
        while self._proc.poll() is None or self._ready():
            if not self._ready():
                if time.time() - self._last > IDLE_TIMEOUT and self.state not in ("ok", "failed"):
                    self._finish("failed", "The login timed out. Start it again.")
                continue
            try:
                chunk = os.read(self._fd, 4096).decode("utf-8", "replace")
            except (OSError, TypeError):  # TypeError: closed (fd is None)
                break
            if not chunk:
                break
            with self._lock:
                self._out = (self._out + chunk)[-8000:]
                self._last = time.time()
                self._update()
        if self.state not in ("ok", "failed"):
            self._finish("failed", "SteamCMD stopped before the login finished.")

    def _ready(self) -> bool:
        if self._fd is None:
            return False
        try:
            return bool(select.select([self._fd], [], [], 0.5)[0])
        except (OSError, ValueError):
            return False

    def _update(self):
        tail = self._out[-600:]
        for pat, msg in FAIL:
            if pat.search(tail):
                return self._finish("failed", msg)
        if DONE.search(tail):
            self._write("quit")
            return self._finish("ok", "Logged in. SteamCMD will remember this login for updates.")
        for state, pat in PROMPTS:
            if pat.search(tail) and state not in self._answered:
                self.state = state
                self.message = {"need_password": "Enter the Steam password.",
                                "need_code": "Enter the Steam Guard code (from the email or the Steam app).",
                                "confirm_on_phone": "Approve the login in your Steam Mobile app, then wait here."}[state]
                return

    def _write(self, text: str):
        try:
            os.write(self._fd, text.encode() + b"\n")
        except OSError:
            pass

    def submit(self, kind: str, value: str) -> None:
        """Type a password or code into SteamCMD's prompt. The value is not kept anywhere."""
        with self._lock:
            if kind != self.state or kind not in ("need_password", "need_code"):
                raise ValueError("SteamCMD isn't asking for that right now")
            if not value or len(value) > 200 or "\n" in value or "\r" in value:
                raise ValueError("That doesn't look right")
            self._answered.add(kind)
            self._out = ""          # forget output seen so far (it can't contain the value: prompts don't echo)
            self.state, self.message = "checking", "Checking with Steam…"
            self._last = time.time()
            self._write(value)
        del value

    def _finish(self, state: str, message: str):
        self.state, self.message = state, message
        threading.Timer(3, self.close).start()

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def public(self) -> dict:
        return {"state": self.state, "message": self.message, "user": self.user}


class Logins:
    """At most one login session per target (a setup job or a server)."""

    def __init__(self):
        self.sessions: dict[str, LoginSession] = {}

    def start(self, target: str, steamcmd: str, user: str) -> LoginSession:
        old = self.sessions.get(target)
        if old and old.state not in ("ok", "failed"):
            return old
        if old:
            old.close()
        s = LoginSession(steamcmd, user).start()
        self.sessions[target] = s
        return s

    def get(self, target: str) -> LoginSession | None:
        return self.sessions.get(target)
