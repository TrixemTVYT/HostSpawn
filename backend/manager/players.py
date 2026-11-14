"""The player database (state/players.db, SQLite, stays on this machine): everyone seen on any server, their names,
sessions and playtime, notes, one ban list for all servers, a watchlist and an audit trail of moderation.
A player's key is "steam:<id>" (Rust) or "be:<guid>" (BattlEye games). No IP addresses are stored.
The supervisor feeds it once a minute with observe(); the web API reads and changes it."""
import sqlite3
import threading
import time
from pathlib import Path

from . import util

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (key TEXT PRIMARY KEY, name TEXT, first_seen REAL, last_seen REAL,
                                    playtime_s REAL DEFAULT 0, sessions INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS names (key TEXT, name TEXT, last_seen REAL, PRIMARY KEY (key, name));
CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, key TEXT, server TEXT, start REAL, end REAL);
CREATE TABLE IF NOT EXISTS online (server TEXT, key TEXT, ref TEXT, name TEXT, ping INTEGER, since REAL, seen REAL,
                                   PRIMARY KEY (server, key));
CREATE TABLE IF NOT EXISTS bans (id INTEGER PRIMARY KEY, key TEXT, name TEXT, reason TEXT, by TEXT, at REAL,
                                 until REAL, lifted_by TEXT, lifted_at REAL);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, key TEXT, text TEXT, by TEXT, at REAL);
CREATE TABLE IF NOT EXISTS watch (key TEXT PRIMARY KEY, reason TEXT, by TEXT, at REAL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, at REAL, who TEXT, action TEXT, key TEXT, server TEXT,
                                  detail TEXT);
CREATE INDEX IF NOT EXISTS sessions_key ON sessions (key);
CREATE INDEX IF NOT EXISTS bans_key ON bans (key);
"""


class PlayerDB:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or util.STATE / "players.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
        if new:
            self.path.chmod(0o600)

    def close(self):
        self._db.close()

    def _q(self, sql: str, args=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # ---- fed by the supervisor ----
    def observe(self, server: str, players: list[dict], now: float | None = None) -> tuple[list[dict], list[dict]]:
        """Who is on this server now. Returns (joined, left) since the last call; keeps sessions and playtime."""
        now = now or time.time()
        seen = {p["key"]: p for p in players if p.get("key")}
        with self._lock, self._db:
            db = self._db
            before = {r["key"]: dict(r) for r in db.execute("SELECT * FROM online WHERE server=?", (server,))}
            joined = [p for k, p in seen.items() if k not in before]
            left = [r for k, r in before.items() if k not in seen]
            for p in seen.values():
                name = str(p.get("name") or "")[:64]
                db.execute("INSERT INTO players (key, name, first_seen, last_seen) VALUES (?,?,?,?) "
                           "ON CONFLICT(key) DO UPDATE SET name=excluded.name, last_seen=excluded.last_seen",
                           (p["key"], name, now, now))
                db.execute("INSERT INTO names VALUES (?,?,?) ON CONFLICT(key, name) DO UPDATE SET last_seen=excluded.last_seen",
                           (p["key"], name, now))
                if p["key"] in before:
                    db.execute("UPDATE online SET ref=?, name=?, ping=?, seen=? WHERE server=? AND key=?",
                               (str(p.get("ref", "")), name, p.get("ping"), now, server, p["key"]))
                    gap = now - before[p["key"]]["seen"]
                    if 0 < gap < 600:  # a long gap means the agent was off: don't count it as play
                        db.execute("UPDATE players SET playtime_s = playtime_s + ? WHERE key=?", (gap, p["key"]))
                else:
                    db.execute("INSERT INTO online VALUES (?,?,?,?,?,?,?)",
                               (server, p["key"], str(p.get("ref", "")), name, p.get("ping"), now, now))
                    db.execute("INSERT INTO sessions (key, server, start, end) VALUES (?,?,?,?)", (p["key"], server, now, now))
                    db.execute("UPDATE players SET sessions = sessions + 1 WHERE key=?", (p["key"],))
                db.execute("UPDATE sessions SET end=? WHERE id = (SELECT max(id) FROM sessions WHERE key=? AND server=?)",
                           (now, p["key"], server))
            for r in left:
                db.execute("DELETE FROM online WHERE server=? AND key=?", (server, r["key"]))
        return joined, left

    def gone(self, server: str, key: str) -> None:
        """Kicked or banned: off this server now (before the next observe sees it)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM online WHERE server=? AND key=?", (server, key))

    def forget_server(self, server: str) -> None:
        """A server was deleted or stopped: nobody is online there any more (their history stays)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM online WHERE server=?", (server,))

    # ---- reading ----
    def online(self, server: str | None = None) -> list[dict]:
        if server:
            return self._q("SELECT o.*, p.playtime_s FROM online o JOIN players p USING (key) WHERE server=? ORDER BY o.since",
                           (server,))
        return self._q("SELECT o.*, p.playtime_s FROM online o JOIN players p USING (key) ORDER BY o.server, o.since")

    def search(self, q: str = "", limit: int = 100) -> list[dict]:
        like = f"%{q.strip()}%"
        rows = self._q("SELECT DISTINCT p.* FROM players p LEFT JOIN names n USING (key) "
                       "WHERE p.name LIKE ? OR n.name LIKE ? OR p.key = ? ORDER BY p.last_seen DESC LIMIT ?",
                       (like, like, q.strip(), max(1, min(500, limit))))
        banned = {b["key"] for b in self.bans()}
        watched = {w["key"] for w in self._q("SELECT key FROM watch")}
        online = {o["key"] for o in self.online()}
        for r in rows:
            r.update(banned=r["key"] in banned, watched=r["key"] in watched, online=r["key"] in online)
        return rows

    def profile(self, key: str) -> dict | None:
        p = self._q("SELECT * FROM players WHERE key=?", (key,))
        if not p:
            return None
        out = p[0]
        out["names"] = self._q("SELECT name, last_seen FROM names WHERE key=? ORDER BY last_seen DESC", (key,))
        out["history"] = self._q("SELECT server, start, end FROM sessions WHERE key=? ORDER BY start DESC LIMIT 50", (key,))
        out["notes"] = self._q("SELECT id, text, by, at FROM notes WHERE key=? ORDER BY at DESC", (key,))
        out["bans"] = self._q("SELECT * FROM bans WHERE key=? ORDER BY at DESC", (key,))
        out["ban"] = self.active_ban(key)
        w = self._q("SELECT reason, by, at FROM watch WHERE key=?", (key,))
        out["watch"] = w[0] if w else None
        out["online"] = self._q("SELECT server, since FROM online WHERE key=?", (key,))
        return out

    # ---- bans: one list for every server ----
    def active_ban(self, key: str, now: float | None = None) -> dict | None:
        now = now or time.time()
        r = self._q("SELECT * FROM bans WHERE key=? AND lifted_at IS NULL AND (until IS NULL OR until > ?) "
                    "ORDER BY at DESC LIMIT 1", (key, now))
        return r[0] if r else None

    def bans(self, include_old: bool = False, now: float | None = None) -> list[dict]:
        if include_old:
            return self._q("SELECT * FROM bans ORDER BY at DESC")
        return self._q("SELECT * FROM bans WHERE lifted_at IS NULL AND (until IS NULL OR until > ?) ORDER BY at DESC",
                       (now or time.time(),))

    def ban(self, key: str, minutes: int, reason: str, by: str, name: str = "") -> dict:
        now = time.time()
        until = now + minutes * 60 if minutes and minutes > 0 else None
        if not name:
            p = self._q("SELECT name FROM players WHERE key=?", (key,))
            name = p[0]["name"] if p else ""
        with self._lock, self._db:
            self._db.execute("UPDATE bans SET lifted_by=?, lifted_at=? WHERE key=? AND lifted_at IS NULL",
                             (f"replaced by {by}", now, key))  # one active ban per player
            self._db.execute("INSERT INTO bans (key, name, reason, by, at, until) VALUES (?,?,?,?,?,?)",
                             (key, name, reason, by, now, until))
        self.audit(by, "ban", key, "", f"{'permanent' if until is None else f'{minutes} min'}: {reason}")
        return self.active_ban(key)

    def unban(self, key: str, by: str) -> bool:
        with self._lock, self._db:
            n = self._db.execute("UPDATE bans SET lifted_by=?, lifted_at=? WHERE key=? AND lifted_at IS NULL",
                                 (by, time.time(), key)).rowcount
        self.audit(by, "unban", key, "", "")
        return n > 0

    # ---- notes, watchlist, audit ----
    def note(self, key: str, text: str, by: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO notes (key, text, by, at) VALUES (?,?,?,?)", (key, text[:1000], by, time.time()))
        self.audit(by, "note", key, "", "")

    def watch(self, key: str, on: bool, by: str, reason: str = "") -> None:
        with self._lock, self._db:
            if on:
                self._db.execute("INSERT OR REPLACE INTO watch VALUES (?,?,?,?)", (key, reason[:200], by, time.time()))
            else:
                self._db.execute("DELETE FROM watch WHERE key=?", (key,))
        self.audit(by, "watch" if on else "unwatch", key, "", reason[:200])

    def watched(self, key: str) -> dict | None:
        r = self._q("SELECT * FROM watch WHERE key=?", (key,))
        return r[0] if r else None

    def audit(self, who: str, action: str, key: str, server: str, detail: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO audit (at, who, action, key, server, detail) VALUES (?,?,?,?,?,?)",
                             (time.time(), who, action, key, server, detail[:300]))

    def audit_log(self, limit: int = 200) -> list[dict]:
        return self._q("SELECT a.*, p.name FROM audit a LEFT JOIN players p USING (key) ORDER BY a.at DESC LIMIT ?",
                       (max(1, min(1000, limit)),))
