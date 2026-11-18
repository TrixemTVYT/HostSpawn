"""The Database tab: look at (and carefully change) an instance's own databases from the app.

Sources, and only these: the instance's MariaDB database (Altis Life, through its own login file manager/db.cnf, so
the password never leaves the machine or reaches the app), and SQLite files inside the instance's folder (for example
Rust plugin data). Nothing else on the machine is reachable.

Reading is the default: one statement, read-only (a read-only transaction or a read-only SQLite connection), at most
MAX_ROWS rows and TIME_LIMIT_S seconds. Changing needs the person to type CHANGE; a backup of the database is made
first and the change is written to the event log (who, what kind, how many rows; never the data)."""
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

from .adapters.managed import ManagedServer
from .util import add_event, redact, unique_path

PAGE_ROWS = 50
MAX_ROWS = 500
TIME_LIMIT_S = 10
KEEP_BACKUPS = 10
MAX_CELL = 2000
SQLITE_EXT = (".db", ".sqlite", ".sqlite3", ".db3")
SKIP_DIRS = {"manager", "backups", "steamcmd", "logs", "Steam", "steamapps"}
READ_WORDS = {"select", "with", "show", "describe", "desc", "explain"}


class DbError(Exception):
    pass


# ---- SQL text checks ----
def one_statement(sql: str) -> str:
    """The statement without a trailing ';'. Refuses two statements (a ';' outside quotes and comments)."""
    s = str(sql or "").strip()
    i, n, quote = 0, len(s), None
    while i < n:
        c = s[i]
        if quote:
            if c == "\\" and quote != "`":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif s.startswith("--", i) or c == "#":
            j = s.find("\n", i)
            i = n if j < 0 else j
            continue
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        elif c == ";":
            if s[i + 1:].strip(" \t\r\n;"):
                raise DbError("Only one statement at a time, please.")
            s = s[:i]
            break
        i += 1
    s = s.strip()
    if not s:
        raise DbError("Type a query first.")
    return s


def first_word(sql: str) -> str:
    s = re.sub(r"(--|#)[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).lstrip(" \t\r\n(")
    m = re.match(r"[A-Za-z]+", s)
    return m.group(0).lower() if m else ""


def is_read(sql: str) -> bool:
    return first_word(sql) in READ_WORDS


def cell(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<binary, {len(bytes(v))} bytes>"
    if isinstance(v, str) and len(v) > MAX_CELL:
        return v[:MAX_CELL] + "…"
    return v


# ---- SQLite ----
class Sqlite:
    kind = "sqlite"

    def __init__(self, path: Path):
        self.path = path

    def _ro(self):
        con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        con.execute("PRAGMA query_only = ON")
        deadline = time.monotonic() + TIME_LIMIT_S
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        return con

    @staticmethod
    def ident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def tables(self) -> list[dict]:
        with closing(self._ro()) as con:
            names = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            return [{"name": t, "rows": con.execute(f"SELECT COUNT(*) FROM {self.ident(t)}").fetchone()[0]}
                    for t in names]

    def columns(self, table: str) -> tuple[list[str], list[str]]:
        with closing(self._ro()) as con:
            info = con.execute(f"PRAGMA table_info({self.ident(table)})").fetchall()
        cols = [r[1] for r in info]
        key = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
        return cols, key

    def rows(self, table, cols, key, page, search, order, desc) -> dict:
        t = self.ident(table)
        rowid = not key
        sel = ('rowid AS "rowid", ' if rowid else "") + "*"
        where, args = "", []
        if search:
            where = " WHERE " + " OR ".join(f"CAST({self.ident(c)} AS TEXT) LIKE ?" for c in cols)
            args = [f"%{search}%"] * len(cols)
        ob = f" ORDER BY {self.ident(order)} {'DESC' if desc else 'ASC'}" if order else ""
        with closing(self._ro()) as con:
            total = con.execute(f"SELECT COUNT(*) FROM {t}{where}", args).fetchone()[0]
            cur = con.execute(f"SELECT {sel} FROM {t}{where}{ob} LIMIT ? OFFSET ?", [*args, PAGE_ROWS, page * PAGE_ROWS])
            names = [d[0] for d in cur.description]
            data = [[cell(v) for v in r] for r in cur.fetchall()]
        return {"columns": names, "key": ["rowid"] if rowid else key, "rows": data, "total": total}

    def query(self, sql: str) -> dict:
        with closing(self._ro()) as con:
            cur = con.execute(sql)
            names = [d[0] for d in cur.description or []]
            data = [[cell(v) for v in r] for r in cur.fetchmany(MAX_ROWS + 1)]
        return {"columns": names, "rows": data[:MAX_ROWS], "truncated": len(data) > MAX_ROWS}

    def write(self, sql: str, args=()) -> int:
        con = sqlite3.connect(self.path, timeout=5)
        try:
            cur = con.execute(sql, list(args))
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    def lit(self, v):
        return "?", [v]

    def backup(self, dest: Path) -> Path:
        out = unique_path(dest / f"{self.path.stem}_{time.strftime('%Y%m%d-%H%M%S')}.sqlite")
        src, dst = sqlite3.connect(self.path, timeout=5), sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        os.chmod(out, 0o600)
        return out


# ---- MariaDB / MySQL (through the mysql client and the instance's own login file) ----
class MariaDb:
    kind = "mariadb"

    def __init__(self, db: str, cnf: Path):
        self.db, self.cnf = db, cnf

    @staticmethod
    def ident(name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    @staticmethod
    def literal(v) -> str:
        """A value as SQL text that can't break out of its quotes: strings go in as hex."""
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, (int, float)):
            return repr(v)
        return f"CONVERT(X'{str(v).encode().hex()}' USING utf8mb4)" if str(v) else "''"

    def lit(self, v):
        return self.literal(v), []

    def _run(self, script: str, timeout: float = TIME_LIMIT_S + 5) -> tuple[list[str], list[list]]:
        exe = shutil.which("mysql") or shutil.which("mariadb")
        if not exe:
            raise DbError("The MariaDB client (mysql) isn't installed on this machine.")
        try:
            p = subprocess.run([exe, f"--defaults-extra-file={self.cnf}", "--batch", "--default-character-set=utf8mb4",
                                self.db], input=script, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise DbError(f"The database took longer than {int(timeout)} seconds.")
        if p.returncode != 0:
            msg = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "the database refused it"
            msg = re.sub(r"^ERROR \d+ \(\w+\)( at line \d+)?: ", "", msg)
            if "Can't connect" in msg or "Access denied" in msg:
                msg += " (is MariaDB running? sudo systemctl start mariadb)"
            raise DbError(redact(msg))
        lines = p.stdout.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return [], []
        split = [[self._unescape(x) for x in ln.split("\t")] for ln in lines]
        return [str(c) for c in split[0]], split[1:]

    @staticmethod
    def _unescape(s: str):
        if s == "NULL":
            return None
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", "0": "\0", "\\": "\\"}.get(m.group(1), m.group(1)), s)

    _READ = (f"SET SESSION max_statement_time={TIME_LIMIT_S};\nSET SESSION sql_select_limit={MAX_ROWS + 1};\n"
             "START TRANSACTION READ ONLY;\n")

    def tables(self) -> list[dict]:
        _, rows = self._run("SELECT table_name, table_rows FROM information_schema.tables WHERE "
                            "table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name;")
        return [{"name": r[0], "rows": int(r[1]) if r[1] not in (None, "") else None, "approx": True} for r in rows]

    def columns(self, table: str) -> tuple[list[str], list[str]]:
        _, rows = self._run("SELECT column_name, column_key FROM information_schema.columns WHERE table_schema = "
                            f"DATABASE() AND table_name = {self.literal(table)} ORDER BY ordinal_position;")
        return [r[0] for r in rows], [r[0] for r in rows if r[1] == "PRI"]

    def rows(self, table, cols, key, page, search, order, desc) -> dict:
        t = self.ident(table)
        where = ""
        if search:
            where = (" WHERE CONCAT_WS(CHAR(31), " + ", ".join(self.ident(c) for c in cols) + ") LIKE "
                     + self.literal(f"%{search}%"))
        ob = f" ORDER BY {self.ident(order)} {'DESC' if desc else 'ASC'}" if order else ""
        _, tot = self._run(self._READ + f"SELECT COUNT(*) FROM {t}{where};\nROLLBACK;")
        names, data = self._run(self._READ + f"SELECT * FROM {t}{where}{ob} LIMIT {PAGE_ROWS} OFFSET {page * PAGE_ROWS};"
                                "\nROLLBACK;")
        return {"columns": names or cols, "key": key, "rows": [[cell(v) for v in r] for r in data],
                "total": int(tot[0][0]) if tot else 0}

    def query(self, sql: str) -> dict:
        names, data = self._run(self._READ + sql + ";\nROLLBACK;")
        return {"columns": names, "rows": [[cell(v) for v in r] for r in data[:MAX_ROWS]],
                "truncated": len(data) > MAX_ROWS}

    def write(self, sql: str, args=()) -> int:
        _, rows = self._run(f"SET SESSION max_statement_time={TIME_LIMIT_S * 3};\n{sql};\nSELECT ROW_COUNT();",
                            timeout=TIME_LIMIT_S * 3 + 5)
        try:
            return int(rows[-1][0])
        except (IndexError, TypeError, ValueError):
            return 0

    def backup(self, dest: Path) -> Path:
        exe = shutil.which("mysqldump") or shutil.which("mariadb-dump")
        if not exe:
            raise DbError("Can't back up first: mysqldump isn't installed, so nothing was changed.")
        out = unique_path(dest / f"{self.db}_{time.strftime('%Y%m%d-%H%M%S')}.sql")
        with open(out, "w") as f:
            os.chmod(out, 0o600)
            p = subprocess.run([exe, f"--defaults-extra-file={self.cnf}", "--single-transaction", self.db],
                               stdout=f, stderr=subprocess.PIPE, text=True, timeout=600)
        if p.returncode != 0:
            out.unlink(missing_ok=True)
            raise DbError("The backup failed, so nothing was changed: " + redact(p.stderr.strip()[:200]))
        return out


# ---- the instance's databases ----
class Database:
    def __init__(self, a):
        if not isinstance(a, ManagedServer) or not a.cfg.get("managed"):
            raise DbError("HostSpawn didn't create this instance, so its databases are opened where they live.")
        self.a = a
        self.root = a.root.resolve()

    def sources(self) -> list[dict]:
        out = []
        login = self.a.db_login() if hasattr(self.a, "db_login") else None
        if login:
            out.append({"id": "mariadb", "kind": "mariadb", "name": login[0],
                        "label": f"{self.a.cfg.get('framework') or 'Server'} database (MariaDB)",
                        "note": "Players are saved here while they play: change money or items when they're offline."})
        for p in self._sqlite_files():
            rel = p.relative_to(self.root).as_posix()
            out.append({"id": "sqlite:" + rel, "kind": "sqlite", "name": p.name, "label": p.name, "path": rel,
                        "note": "A plugin may overwrite changes while the server runs: stop it first to be safe."})
        return out

    def _sqlite_files(self) -> list[Path]:
        found = []
        for d, dirs, files in os.walk(self.root):
            here = Path(d)
            dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not (here == self.root and x in SKIP_DIRS)
                             and x not in ("steamapps", "Steam"))
            for f in sorted(files):
                if f.lower().endswith(SQLITE_EXT) and not f.startswith("."):
                    p = here / f
                    try:
                        with open(p, "rb") as fh:
                            if fh.read(16) == b"SQLite format 3\x00" and not p.is_symlink():
                                found.append(p)
                    except OSError:
                        pass
            if len(found) >= 50:
                break
        return found

    def source(self, sid: str):
        sid = str(sid or "")
        if sid == "mariadb":
            login = self.a.db_login() if hasattr(self.a, "db_login") else None
            if not login:
                raise DbError("This instance has no MariaDB database.")
            return MariaDb(*login)
        if sid.startswith("sqlite:"):
            rel = sid[len("sqlite:"):]
            p = (self.root / rel).resolve()
            if p.parent == p or not p.is_relative_to(self.root) or p not in self._sqlite_files():
                raise DbError("That database isn't part of this instance.")
            return Sqlite(p)
        raise DbError("Unknown database.")

    def tables(self, sid: str) -> list[dict]:
        return self._safe(lambda: self.source(sid).tables())

    def _table(self, src, table: str) -> tuple[list[str], list[str]]:
        names = {t["name"] for t in src.tables()}
        if table not in names:
            raise DbError("No such table.")
        return src.columns(table)

    def rows(self, sid: str, table: str, page: int = 0, search: str = "", order: str = "", desc: bool = False) -> dict:
        def go():
            src = self.source(sid)
            cols, key = self._table(src, table)
            o = order if order in cols or (order == "rowid" and src.kind == "sqlite") else ""
            return src.rows(table, cols, key, max(0, int(page)), str(search or "")[:200], o, bool(desc))
        return self._safe(go)

    def query(self, sid: str, sql: str) -> dict:
        s = one_statement(sql)
        if not is_read(s):
            raise DbError("That query changes data. Turn on Changes to run it (a backup is made first).")
        return self._safe(lambda: self.source(sid).query(s))

    def change(self, sid: str, by: str, confirm: str, sql: str = "", edit: dict | None = None) -> dict:
        if confirm != "CHANGE":
            raise DbError("Type CHANGE to confirm.")
        src = self.source(sid)
        if edit:
            stmt, args, what = self._edit_sql(src, edit)
        else:
            stmt, args = one_statement(sql), []
            what = f"{first_word(stmt).upper()} query"
        dest = self.root / "backups" / "database"
        dest.mkdir(parents=True, exist_ok=True)
        os.chmod(dest, 0o700)
        bak = self._safe(lambda: src.backup(dest))
        for old in sorted(dest.glob(bak.stem.rsplit("_", 1)[0] + "_*" + bak.suffix))[:-KEEP_BACKUPS]:
            old.unlink(missing_ok=True)
        n = self._safe(lambda: src.write(stmt, args))
        add_event(self.a.id, "info", f"database change by {by}: {what} in {src.kind} ({n} row{'s' if n != 1 else ''}), "
                                     f"backup {bak.name}")
        return {"changed": n, "backup": bak.name}

    def _edit_sql(self, src, e: dict) -> tuple[str, list, str]:
        table, op = str(e.get("table", "")), str(e.get("op", ""))
        cols, key = self._table(src, table)
        if src.kind == "sqlite" and not key:
            key, cols = ["rowid"], cols + ["rowid"]
        t, q, args, parts = src.ident(table), src.ident, [], []

        def val(v):
            text, a = src.lit(v)
            args.extend(a)
            return text

        def where():
            k = e.get("key") or {}
            if not key or set(k) != set(key):
                raise DbError("This table has no key column, so rows can't be picked safely. Use a SQL query.")
            return " AND ".join(f"{q(c)} = {val(k[c])}" for c in key)

        if op == "update":
            sets = e.get("set") or {}
            if not sets or any(c not in cols for c in sets):
                raise DbError("Unknown column.")
            parts = [f"{q(c)} = {val(v)}" for c, v in sets.items()]
            return f"UPDATE {t} SET {', '.join(parts)} WHERE {where()}", args, f"edit in {table}"
        if op == "delete":
            return f"DELETE FROM {t} WHERE {where()}", args, f"delete from {table}"
        if op == "insert":
            vals = e.get("values") or {}
            if not vals or any(c not in cols for c in vals):
                raise DbError("Unknown column.")
            cs = ", ".join(q(c) for c in vals)
            return f"INSERT INTO {t} ({cs}) VALUES ({', '.join(val(v) for v in vals.values())})", args, \
                f"new row in {table}"
        raise DbError("Unknown change.")

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except DbError:
            raise
        except sqlite3.Error as e:
            raise DbError(redact(str(e)) or "the database refused it")
        except OSError as e:
            raise DbError(f"Couldn't open the database ({e.strerror or e}).")
