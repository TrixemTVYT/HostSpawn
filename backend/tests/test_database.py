"""The Database tab: only the instance's own databases, read-only unless CHANGE is typed, one statement at a time,
a backup before every change, values that can't inject SQL, and the row cap. MariaDB is a fake `mysql` on PATH: the
real client and real databases are never reached."""
import os
import sqlite3
import stat
from pathlib import Path
from unittest import mock

from manager import database as D
from manager.adapters.managed import ManagedServer
from manager.util import read_events
from tests.test_manager import Sandbox


class Inst(ManagedServer):
    game, slug, app_id, exe_name = "rust", "fake", "999", "game"


class AltisLife(Inst):
    game = "arma3"

    def db_login(self):
        return ("altislife_test", self.root / "manager" / "db.cnf")


def make_sqlite(path: Path, n=3):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT, money INTEGER)")
    con.execute("CREATE TABLE log (line TEXT)")  # no key: rowid is used
    con.executemany("INSERT INTO players (name, money) VALUES (?, ?)", [(f"p{i}", i * 100) for i in range(n)])
    con.execute("INSERT INTO log VALUES ('hello')")
    con.commit()
    con.close()


class SqliteCase(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "inst"
        self.dbfile = self.root / "server/oxide/data/Economics.db"
        make_sqlite(self.dbfile)
        make_sqlite(self.root / "manager/private.db")      # HostSpawn's private folder: never listed
        make_sqlite(self.tmp / "elsewhere.db")              # outside the instance
        (self.root / "server/oxide/data/fake.db").write_text("not a database")
        self.d = D.Database(Inst("i", {"root": str(self.root), "managed": True}))
        self.sid = "sqlite:server/oxide/data/Economics.db"

    def money(self):
        con = sqlite3.connect(self.dbfile)
        try:
            return [r[0] for r in con.execute("SELECT money FROM players ORDER BY id")]
        finally:
            con.close()


class TestSources(SqliteCase):
    def test_only_real_sqlite_files_inside_the_instance(self):
        self.assertEqual([s["id"] for s in self.d.sources()], [self.sid])

    def test_cant_reach_outside_or_private_files(self):
        for bad in ("sqlite:../elsewhere.db", "sqlite:manager/private.db", "sqlite:server/oxide/data/fake.db",
                    "sqlite:/etc/passwd", "mariadb", "nope"):
            with self.assertRaises(D.DbError, msg=bad):
                self.d.tables(bad)

    def test_instances_hostspawn_didnt_make_are_refused(self):
        with self.assertRaises(D.DbError):
            D.Database(Inst("i", {"root": str(self.root)}))


class TestReading(SqliteCase):
    def test_tables_and_rows(self):
        self.assertEqual(self.d.tables(self.sid), [{"name": "log", "rows": 1}, {"name": "players", "rows": 3}])
        r = self.d.rows(self.sid, "players", order="money", desc=True)
        self.assertEqual(r["columns"], ["id", "name", "money"])
        self.assertEqual(r["key"], ["id"])
        self.assertEqual([x[2] for x in r["rows"]], [200, 100, 0])
        self.assertEqual(self.d.rows(self.sid, "log")["key"], ["rowid"])

    def test_search_and_pages(self):
        make_sqlite(self.root / "server/big.db", n=120)
        sid = "sqlite:server/big.db"
        self.assertEqual(self.d.rows(sid, "players", search="p11")["total"], 11)  # p11, p110..p119
        last = self.d.rows(sid, "players", page=2)
        self.assertEqual((last["total"], len(last["rows"])), (120, 20))

    def test_unknown_table_or_column_is_refused(self):
        with self.assertRaises(D.DbError):
            self.d.rows(self.sid, "players; DROP TABLE players")
        r = self.d.rows(self.sid, "players", order="money; DROP TABLE players")  # ignored, not run
        self.assertEqual(r["total"], 3)

    def test_read_only_query(self):
        r = self.d.query(self.sid, "SELECT name FROM players WHERE money > 50 ORDER BY money;")
        self.assertEqual(r, {"columns": ["name"], "rows": [["p1"], ["p2"]], "truncated": False})

    def test_changing_sql_is_refused_in_read_mode(self):
        for sql in ("UPDATE players SET money = 0", "DROP TABLE players", "delete from players",
                    "  /* hi */ INSERT INTO log VALUES ('x')", "PRAGMA user_version = 3"):
            with self.assertRaises(D.DbError, msg=sql):
                self.d.query(self.sid, sql)
        self.assertEqual(self.money(), [0, 100, 200])

    def test_with_query_that_writes_is_still_blocked(self):
        with self.assertRaises(D.DbError):
            self.d.query(self.sid, "WITH x AS (SELECT 1) DELETE FROM players")
        self.assertEqual(self.money(), [0, 100, 200])

    def test_two_statements_are_refused(self):
        with self.assertRaises(D.DbError):
            self.d.query(self.sid, "SELECT 1; DROP TABLE players")
        self.assertEqual(self.d.query(self.sid, "SELECT ';' AS x; ")["rows"], [[";"]])  # a ; in quotes is fine

    def test_row_cap(self):
        r = self.d.query(self.sid, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n LIMIT 900) "
                                   "SELECT i FROM n")
        self.assertEqual((len(r["rows"]), r["truncated"]), (D.MAX_ROWS, True))

    def test_long_and_binary_cells_are_shortened(self):
        r = self.d.query(self.sid, "SELECT zeroblob(10) AS b, printf('%.3000c', 'x') AS t")
        self.assertEqual(r["rows"][0][0], "<binary, 10 bytes>")
        self.assertEqual(len(r["rows"][0][1]), D.MAX_CELL + 1)


class TestChanging(SqliteCase):
    def test_needs_change_typed(self):
        with self.assertRaises(D.DbError):
            self.d.change(self.sid, "admin", "yes", sql="UPDATE players SET money = 0")
        self.assertEqual(self.money(), [0, 100, 200])

    def test_edit_cell_backs_up_first_and_logs_without_data(self):
        r = self.d.change(self.sid, "admin", "CHANGE",
                          edit={"op": "update", "table": "players", "key": {"id": 2}, "set": {"money": 5555}})
        self.assertEqual(r["changed"], 1)
        self.assertEqual(self.money(), [0, 5555, 200])
        bak = self.root / "backups/database" / r["backup"]
        self.assertTrue(bak.exists())
        self.assertEqual(stat.S_IMODE(bak.stat().st_mode), 0o600)
        con = sqlite3.connect(bak)
        self.assertEqual(con.execute("SELECT money FROM players WHERE id = 2").fetchone()[0], 100)  # the old value
        con.close()
        ev = read_events(5)[-1]["message"]
        self.assertIn("database change by admin: edit in players", ev)
        self.assertNotIn("5555", ev)

    def test_values_cant_inject_sql(self):
        evil = "x'); DROP TABLE players; --"
        self.d.change(self.sid, "admin", "CHANGE",
                      edit={"op": "update", "table": "players", "key": {"id": 1}, "set": {"name": evil}})
        r = self.d.rows(self.sid, "players")
        self.assertEqual(r["rows"][0][1], evil)
        self.assertEqual(r["total"], 3)

    def test_insert_delete_and_rowid_tables(self):
        self.d.change(self.sid, "a", "CHANGE", edit={"op": "insert", "table": "players", "values": {"name": "new", "money": 1}})
        self.d.change(self.sid, "a", "CHANGE", edit={"op": "delete", "table": "players", "key": {"id": 1}})
        self.assertEqual(self.money(), [100, 200, 1])
        self.d.change(self.sid, "a", "CHANGE", edit={"op": "update", "table": "log", "key": {"rowid": 1}, "set": {"line": "bye"}})
        self.assertEqual(self.d.query(self.sid, "SELECT line FROM log")["rows"], [["bye"]])

    def test_edit_needs_the_whole_key_and_known_columns(self):
        for e in ({"op": "update", "table": "players", "key": {}, "set": {"money": 1}},
                  {"op": "update", "table": "players", "key": {"id": 1}, "set": {"nope": 1}},
                  {"op": "delete", "table": "nope", "key": {"id": 1}},
                  {"op": "wipe", "table": "players"}):
            with self.assertRaises(D.DbError, msg=e):
                self.d.change(self.sid, "a", "CHANGE", edit=e)
        self.assertEqual(self.money(), [0, 100, 200])

    def test_free_sql_change_and_backups_are_capped(self):
        for i in range(D.KEEP_BACKUPS + 3):
            with mock.patch("manager.database.time.strftime", return_value=f"2026-{i:03d}"):
                r = self.d.change(self.sid, "a", "CHANGE", sql=f"UPDATE players SET money = {i}")
        self.assertEqual(r["changed"], 3)
        self.assertEqual(len(list((self.root / "backups/database").iterdir())), D.KEEP_BACKUPS)


FAKE_MYSQL = r'''#!/usr/bin/python3
import os, sys
script = sys.stdin.read()
with open(os.environ["FAKE_DB_LOG"], "a") as f:
    f.write("ARGS " + " ".join(sys.argv[1:]) + "\n" + script + "\n----\n")
if os.environ.get("FAKE_DB_FAIL"):
    print("ERROR 1045 (28000): Access denied for user 'altis'@'localhost'", file=sys.stderr)
    sys.exit(1)
if "information_schema.tables" in script:
    print("table_name\ttable_rows\nplayers\t2\nvehicles\t0")
elif "information_schema.columns" in script:
    print("column_name\tcolumn_key\nuid\tPRI\nname\t\ncash\t")
elif "ROW_COUNT()" in script:
    print("ROW_COUNT()\n1")
elif "COUNT(*)" in script:
    print("COUNT(*)\n2")
else:
    print("uid\tname\tcash\n1\tTab\\there\t100\n2\tNULL\t5")
'''
FAKE_DUMP = "#!/usr/bin/python3\nimport os, sys\nprint('-- fake dump of ' + sys.argv[-1])\n"


class TestMariaDb(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "inst"
        (self.root / "manager").mkdir(parents=True)
        (self.root / "manager/db.cnf").write_text("[client]\nuser=altis\n")
        bin_ = self.tmp / "fakebin"
        bin_.mkdir()
        for name, text in (("mysql", FAKE_MYSQL), ("mysqldump", FAKE_DUMP)):
            (bin_ / name).write_text(text)
            (bin_ / name).chmod(0o755)
        self.log = self.tmp / "mysql.log"
        self.env = mock.patch.dict(os.environ, {"PATH": str(bin_), "FAKE_DB_LOG": str(self.log)})
        self.env.start()
        self.d = D.Database(AltisLife("a3", {"root": str(self.root), "managed": True, "framework": "Altis Life"}))

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def test_lists_and_reads_through_the_login_file(self):
        self.assertEqual(self.d.sources()[0]["id"], "mariadb")
        self.assertEqual([t["name"] for t in self.d.tables("mariadb")], ["players", "vehicles"])
        r = self.d.rows("mariadb", "players", order="cash")
        self.assertEqual(r["rows"], [["1", "Tab\there", "100"], ["2", None, "5"]])
        self.assertEqual((r["key"], r["total"]), (["uid"], 2))
        log = self.log.read_text()
        self.assertIn(f"--defaults-extra-file={self.root}/manager/db.cnf", log)
        self.assertIn("START TRANSACTION READ ONLY", log)
        self.assertIn("ORDER BY `cash` ASC", log)

    def test_read_mode_refuses_changes(self):
        with self.assertRaises(D.DbError):
            self.d.query("mariadb", "UPDATE players SET cash = 0")
        self.assertFalse(self.log.exists())

    def test_change_dumps_first_and_values_go_in_as_hex(self):
        r = self.d.change("mariadb", "admin", "CHANGE",
                          edit={"op": "update", "table": "players", "key": {"uid": "1"}, "set": {"name": "a' OR 1=1 --"}})
        self.assertEqual(r["changed"], 1)
        self.assertIn("-- fake dump of altislife_test", (self.root / "backups/database" / r["backup"]).read_text())
        log = self.log.read_text()
        self.assertIn("UPDATE `players` SET `name` = CONVERT(X'", log)
        self.assertNotIn("OR 1=1", log)

    def test_login_problem_is_explained_and_nothing_changes(self):
        with mock.patch.dict(os.environ, {"FAKE_DB_FAIL": "1"}):
            with self.assertRaises(D.DbError) as e:
                self.d.tables("mariadb")
            self.assertIn("Access denied", str(e.exception))
            self.assertIn("systemctl start mariadb", str(e.exception))

    def test_literal(self):
        self.assertEqual(D.MariaDb.literal(None), "NULL")
        self.assertEqual(D.MariaDb.literal(5), "5")
        self.assertEqual(D.MariaDb.literal("ab"), "CONVERT(X'6162' USING utf8mb4)")


class TestSqlText(Sandbox):
    def test_one_statement(self):
        self.assertEqual(D.one_statement(" SELECT 1 ;  "), "SELECT 1")
        self.assertEqual(D.one_statement("SELECT 'a;b' -- x;y\n"), "SELECT 'a;b' -- x;y")
        for bad in ("", "  ;", "SELECT 1; SELECT 2", "SELECT 1 /* */; DROP TABLE x"):
            with self.assertRaises(D.DbError, msg=bad):
                D.one_statement(bad)

    def test_first_word(self):
        self.assertEqual(D.first_word("-- c\n /* x */ (SELECT 1)"), "select")
        self.assertFalse(D.is_read("REPLACE INTO x VALUES (1)"))


class TestApi(SqliteCase):
    """Admins only; moderators get 403 for reading and changing; the folder path and the Doctor over HTTP."""

    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        from manager import accounts as A
        from manager.supervisor import Supervisor
        from manager.web import make_handler
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", "correct horse battery staple")
        self.acc.create("mod", "quiet river lantern stone", "moderator", by="owner")
        sup = Supervisor({"i": Inst("i", {"root": str(self.root), "managed": True})}, config={})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sup, "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = {"owner": self.acc.login("owner", "correct horse battery staple", device="t"),
                    "mod": self.acc.login("mod", "quiet river lantern stone", device="t")}

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None, who="owner"):
        import http.client
        import json
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        c.request("POST" if body is not None else "GET", "/api/v1/" + path, json.dumps(body) if body else None,
                  {"X-Manager": "1", "Content-Type": "application/json", "Authorization": "Bearer " + self.tok[who]})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_admins_only(self):
        src = "source=sqlite%3Aserver%2Foxide%2Fdata%2FEconomics.db"
        self.assertEqual(self.call("db?server=i", who="mod")[0], 403)
        self.assertEqual(self.call(f"db/rows?server=i&{src}&table=players", who="mod")[0], 403)
        self.assertEqual(self.call("db/query", {"server": "i", "source": self.sid, "sql": "SELECT 1"}, "mod")[0], 403)
        self.assertEqual(self.call("db/change", {"server": "i", "source": self.sid, "confirm": "CHANGE",
                                                 "sql": "DELETE FROM players"}, "mod")[0], 403)
        self.assertEqual(self.money(), [0, 100, 200])
        code, d = self.call("db?server=i")
        self.assertEqual((code, d["sources"][0]["id"]), (200, self.sid))
        code, d = self.call(f"db/rows?server=i&{src}&table=players&order=money&desc=1")
        self.assertEqual((code, d["rows"][0][2]), (200, 200))
        code, d = self.call("db/query", {"server": "i", "source": self.sid, "sql": "DELETE FROM players"})
        self.assertEqual(code, 400)
        code, d = self.call("db/change", {"server": "i", "source": self.sid, "confirm": "CHANGE",
                                          "edit": {"op": "delete", "table": "players", "key": {"id": 1}}})
        self.assertEqual((code, d["changed"]), (200, 1))

    def test_folder_is_for_admins_and_doctor_answers_everyone(self):
        self.assertEqual(self.call("server?id=i")[1]["folder"], str(self.root))
        self.assertNotIn("folder", self.call("server?id=i", who="mod")[1])
        code, d = self.call("doctor?server=i", who="mod")
        self.assertEqual(code, 200)
        self.assertEqual(d["findings"][0]["id"], "not_installed")  # this pretend instance has no game files
