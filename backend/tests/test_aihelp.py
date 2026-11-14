"""AI help: the "Copy for AI" guide and AGENTS.md never hold a password, a player's IP or SteamID; they say where the
mission, configs and logs are, which files not to open, and the owner's own AGENTS.md/CLAUDE.md is never replaced.
Everything is a fake Altis Life instance in a temp folder: no real server, tmux or game is used."""
import http.client
import json
import threading
from pathlib import Path
from unittest import mock

from manager import accounts as A
from manager import aihelp, util
from manager.adapters import managed
from manager.adapters.arma3 import Arma3Adapter
from manager.files import Files
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import Sandbox

SECRET = "adminsecret99"                  # ARMA3_ADMIN_PASSWORD in the Sandbox's secrets
IP, STEAMID, GUID = "203.0.113.7", "76561198000000001", "0123456789abcdef0123456789abcdef"  # made up


class AltisSandbox(Sandbox):
    def setUp(self):
        super().setUp()
        util.secrets()  # what the engine does at start: redact() learns the secret values
        self.root = self.tmp / "inst"
        for rel, text in {
            "server/mpmissions/Altis_Life.Altis/core/fn_test.sqf": 'hint "hi";\n',
            "server/mpmissions/Altis_Life.Altis/config/Config_Vehicles.hpp": "class X {};\n",
            "server/mpmissions/Altis_Life.Altis/mission.sqm": "version=54;\n",
            "server/@extdb3/extdb3-conf.ini": f"[Database]\nPassword = {SECRET}\n",
            "server/manager/server.cfg": f'passwordAdmin = "{SECRET}";\n',
            "manager/db.cnf": f"password={SECRET}\n",
            "logs/arma3_2026-11-08.log": (f"Player Bob connected (id={STEAMID}).\nBattlEye: GUID {GUID}\n"
                                          f"Loading {self.tmp}/inst/server/keys and {Path.home()}/.local/x\n"
                                          f"Bob joined from {IP}:2304\nadmin login {SECRET}\nMission read.\n"),
        }.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        (self.root / "server/arma3server_x64").write_text("")
        self.cfg = {"root": str(self.root), "managed": True, "name": "Test Life", "mode_name": "Altis Life",
                    "mission": "Altis_Life.Altis", "server_mods": ["@life_server", "@extdb3"],
                    "framework_release": "v5.0.0", "max_players": 64}
        self.a = Arma3Adapter("a3", self.cfg)


class TestGuide(AltisSandbox):
    def test_no_secrets_or_player_details(self):
        text = aihelp.guide(self.a, doctor={"findings": [{"level": "problem", "title": f"Bad {SECRET}",
                                                          "why": "x", "fix": "y"}]})
        for bad in (SECRET, IP, STEAMID, GUID, str(self.tmp), str(Path.home())):
            self.assertNotIn(bad, text)
        self.assertIn("Mission read.", text)       # the log lines are there, just cleaned
        self.assertIn("<steamid>", text)
        self.assertIn("<ip>", text)
        self.assertIn("<instance folder>/server/keys", text)

    def test_says_where_things_are_and_what_not_to_open(self):
        text = aihelp.guide(self.a)
        for want in ("Arma 3 (Altis Life)", "Altis_Life.Altis", "life_server.pbo", "Config_*.hpp", "Database tab",
                     "./manager.sh a3 restart", "Pack", "`server/manager`"):
            self.assertIn(want, text)
        private = aihelp.private_files(self.a)
        for p in ("manager/", "server/@extdb3/extdb3-conf.ini", "server/manager/server.cfg"):
            self.assertIn(p, private)
        self.assertNotIn("server/mpmissions/Altis_Life.Altis/core/fn_test.sqf", private)
        self.assertIn("server/mpmissions/Altis_Life.Altis/core/", text)  # folder list

    def test_owner_notes_are_added_but_redacted(self):
        (self.root / "AI_NOTES.md").write_text(f"We use custom shops. Old password {SECRET}.\n")
        text = aihelp.guide(self.a)
        self.assertIn("We use custom shops.", text)
        self.assertNotIn(SECRET, text)

    def test_server_set_up_by_hand_gets_a_short_guide(self):
        a = Arma3Adapter("hand", {"root": str(self.root)})  # not managed: no file list, nothing written
        text = aihelp.guide(a)
        self.assertIn("Arma 3", text)
        self.assertNotIn("## Folders", text)
        self.assertIsNone(aihelp.write(a))
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_rust_oxide(self):
        from manager.adapters.rust_server import RustServer
        r = RustServer("r", {"root": str(self.tmp / "r"), "managed": True, "framework": "oxide"})
        text = aihelp.guide(r)
        self.assertIn("server/oxide/plugins/*.cs", text)
        self.assertIn("oxide.reload", text)


class TestAgentsFile(AltisSandbox):
    def test_written_and_kept_up_to_date(self):
        self.assertEqual(aihelp.write(self.a), "wrote AGENTS.md, CLAUDE.md")
        agents = (self.root / "AGENTS.md").read_text()
        self.assertTrue(agents.startswith(aihelp.MARKER))
        self.assertNotIn(SECRET, agents)
        self.assertNotIn("Newest console", agents)   # no logs in the file: they go out of date
        self.assertIn("@AGENTS.md", (self.root / "CLAUDE.md").read_text())
        self.assertIsNone(aihelp.write(self.a))      # nothing changed, nothing written
        # The Files editor shows HostSpawn's copies read-only
        ro = {x["path"]: x["read_only"] for x in Files(self.a).list()}
        self.assertTrue(ro["AGENTS.md"])
        self.assertTrue(ro["CLAUDE.md"])

    def test_owners_own_files_are_never_replaced(self):
        (self.root / "AGENTS.md").write_text("my own rules\n")
        (self.root / "CLAUDE.md").write_text("my claude notes\n")
        self.assertIsNone(aihelp.write(self.a))
        self.assertEqual((self.root / "AGENTS.md").read_text(), "my own rules\n")
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "my claude notes\n")
        self.assertFalse(Files(self.a).generated(self.root / "AGENTS.md"))

    def test_start_writes_it_and_a_failure_never_stops_a_start(self):
        calls = []
        fake_run = lambda args, *k, **kw: (calls.append(args), (0, ""))[1]  # no real tmux
        with mock.patch.object(managed, "run", fake_run), mock.patch.object(Arma3Adapter, "pid", return_value=None):
            self.assertEqual(self.a._start(), "started")
            self.assertTrue((self.root / "AGENTS.md").exists())
            with mock.patch.object(aihelp, "write", side_effect=RuntimeError("boom")):
                self.assertEqual(self.a._start(), "started")
        self.assertTrue(all(c[0] == "tmux" for c in calls))


class TestApi(AltisSandbox):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", "correct horse battery staple")
        self.acc.create("mod", "quiet river lantern stone", "moderator", by="owner")
        sup = Supervisor({"a3": self.a}, config={})
        sup.diagnose = lambda sid: {"findings": [], "checked": 1, "ok": True}  # no real health check or ports
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sup, "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = {"owner": self.acc.login("owner", "correct horse battery staple", device="t"),
                    "mod": self.acc.login("mod", "quiet river lantern stone", device="t")}

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, who="owner"):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        c.request("GET", "/api/v1/" + path, None, {"X-Manager": "1", "Authorization": "Bearer " + self.tok[who]})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_admins_only(self):
        self.assertEqual(self.call("aihelp?server=a3", who="mod")[0], 403)
        self.assertEqual(self.call("aihelp?server=nope")[0], 404)
        code, d = self.call("aihelp?server=a3")
        self.assertEqual(code, 200)
        self.assertIn("Nothing wrong found", d["text"])
        self.assertNotIn(SECRET, d["text"])
