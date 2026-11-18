"""Rust's server.cfg as HostSpawn writes it (sections, tags, safe text, no RCON password) and its settings over the API."""
import http.client
import json
import threading

from manager import accounts as A
from manager import util
from manager.adapters.rust_server import RustServer
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import Sandbox


class TestServerCfg(Sandbox):
    def cfg_text(self, **cfg):
        a = RustServer("r", {"root": str(self.tmp / "r"), **cfg})
        a.write_configs()
        return (self.tmp / "r/server/server/server/cfg/server.cfg").read_text()

    def test_sections_values_and_tags(self):
        t = self.cfg_text(hostname="Riverside 2x", max_players=100, world_size=4250, seed=123456, region="EU",
                          url="https://example.com", header_image="https://example.com/banner.png")
        for line in ('server.hostname "Riverside 2x"', "server.maxplayers 100", "server.worldsize 4250",
                     "server.seed 123456", 'server.url "https://example.com"',
                     'server.headerimage "https://example.com/banner.png"', 'server.tags "monthly,vanilla,EU"',
                     "server.pve false", "server.globalchat true", "// --- Identity"):
            self.assertIn(line, t)
        self.assertIn(f"Wipes monthly (Thursdays, 20:00 {util.local_now():%Z}).", t)  # the wipe schedule goes in the description
        self.assertNotIn("server.logoimage", t)  # not set: left out
        for line in t.splitlines():  # comments only on their own lines (Rust reads one command per line)
            self.assertTrue(line == "" or line.startswith("//") or "//" not in line.split('"')[0], line)

    def test_modded_pve_weekly(self):
        t = self.cfg_text(framework="oxide", pve=True, wipe_mode="weekly", description="Chill PvE, wipes Fridays")
        self.assertIn('server.tags "weekly,pve"', t)
        self.assertIn("server.pve true", t)
        self.assertIn('server.description "Chill PvE, wipes Fridays"', t)  # mentions wipes itself: not added twice

    def test_text_cannot_break_out_of_its_line(self):
        t = self.cfg_text(hostname='Evil"\nrcon.password hacked', description="a\\nb")
        self.assertNotIn("\nrcon.password", t)
        self.assertIn("server.hostname \"Evil' rcon.password hacked\"", t)

    def test_rcon_password_never_in_the_file(self):
        (self.tmp / "secrets.env").write_text("SRV_R_RCON=very-secret-rcon\n")
        t = self.cfg_text(rcon_password_key="SRV_R_RCON")
        self.assertNotIn("very-secret-rcon", t)
        self.assertNotIn("rcon.", t.replace("RCON", ""))


class TestRustSettingsApi(Sandbox):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        f = self.tmp / "r.toml"
        f.write_text('[servers.r]\nname = "R"\n')
        self.a = RustServer("r", {"root": str(self.tmp / "r"), "managed": True, "_file": str(f), "name": "R"})
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", "correct horse battery staple")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Supervisor({"r": self.a}, config={}), "", "live",
                                                                        None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = self.acc.login("owner", "correct horse battery staple", device="test")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def save(self, **body):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        c.request("POST", "/api/v1/server/settings", json.dumps({"server": "r", **body}),
                  {"X-Manager": "1", "Content-Type": "application/json", "Authorization": "Bearer " + self.tok})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def test_saves_and_refuses(self):
        self.assertEqual(self.save(description="Be nice", url="https://riverside.example", pve=True,
                                   global_chat=False, region="EU"), 200)
        self.assertEqual((self.a.cfg["description"], self.a.cfg["url"], self.a.cfg["pve"], self.a.cfg["region"]),
                         ("Be nice", "https://riverside.example", True, "EU"))
        for bad in ({"url": "http://plain.example"}, {"url": "javascript:alert(1)"}, {"header_image": "https://a b"},
                    {"region": "MARS"}, {"description": "x" * 401}):
            self.assertEqual(self.save(**bad), 400, bad)
        self.assertEqual(self.save(url=""), 200)  # clearing is fine
