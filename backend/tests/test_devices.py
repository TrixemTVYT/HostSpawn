"""App sign-in: /api/v1 paths, device tokens (Bearer), pairing codes, device sign-out. No real users touched."""
import http.client
import json
import stat
import threading
import time

from manager import accounts as A
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import FakeAdapter, Sandbox

GOOD = "correct horse battery staple"


class TestDeviceStore(Sandbox):
    def acc(self):
        return A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")

    def test_device_token_hashed_listed_revoked(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        tok = a.login("owner", GOOD, device="My PC")
        self.assertEqual(a.session(tok)["device"], "My PC")
        self.assertNotIn(tok, (self.tmp / "sessions.json").read_text())
        [d] = a.devices("owner")
        self.assertEqual((d["device"], len(d["id"])), ("My PC", 16))
        self.assertGreater(d["expires"] - time.time(), 89 * 86400)
        with self.assertRaises(ValueError):
            a.revoke_device(d["id"], "someone_else")  # not theirs
        with self.assertRaises(ValueError):
            a.revoke_device(d["id"][:4])  # a short prefix can't match many
        a.revoke_device(d["id"], "owner")
        self.assertIsNone(a.session(tok))
        self.assertEqual(a.devices(), [])

    def test_device_renews_when_used_and_password_change_signs_out(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        tok = a.login("owner", GOOD, device="phone")
        key = next(iter(a.sessions))
        a.sessions[key]["exp"] = time.time() + 10 * 86400  # pretend it's old
        a.session(tok)
        self.assertGreater(a.sessions[key]["exp"] - time.time(), 89 * 86400)
        a.change_password("owner", GOOD, GOOD + " two")
        self.assertIsNone(a.session(tok))

    def test_bad_device_names(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        for bad in ("   ", "x" * 41, "evil\nname"):
            with self.assertRaises(ValueError, msg=bad):
                a.login("owner", GOOD, device=bad)

    def test_pair_code_once_expires_private(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        code = a.new_pair_code("owner")
        self.assertRegex(code, r"^\d{6}$")
        self.assertNotIn(code, a.pair_file.read_text())  # only a hash
        self.assertEqual(stat.S_IMODE(a.pair_file.stat().st_mode), 0o600)
        tok = A.Accounts(a.users_file, a.sessions_file).pair(code, "phone")  # another process (CLI) made it: works
        self.assertTrue(tok)
        with self.assertRaises(ValueError):
            a.pair(code, "phone2")  # used once
        code = a.new_pair_code("owner")
        data = json.loads(a.pair_file.read_text())
        for v in data.values():
            v["exp"] = time.time() - 1
        a.pair_file.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            a.pair(code, "late")
        with self.assertRaises(ValueError):
            a.new_pair_code("nobody")

    def test_pair_guessing_pauses_and_cancels_codes(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        code = a.new_pair_code("owner")
        wrong = f"{(int(code) + 1) % 10**6:06d}"
        for _ in range(A.PAIR_FAILS):
            with self.assertRaises(ValueError):
                a.pair(wrong, "x")
        with self.assertRaises(PermissionError):
            a.pair(code, "x")  # paused, even with the right code
        a._pair_paused = 0
        with self.assertRaises(ValueError):
            a.pair(code, "x")  # and the waiting code was cancelled

    def test_at_most_three_codes_waiting(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        for _ in range(6):
            a.new_pair_code("owner")
        self.assertEqual(len(json.loads(a.pair_file.read_text())), 3)


class TestAppApi(Sandbox):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.fake = FakeAdapter("srv", {"name": "Srv"})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Supervisor({"srv": self.fake}, config={}), "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        super().tearDown()

    def req(self, method, path, body=None, token="", cookie=""):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"X-Manager": "1", "Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        if cookie:
            h["Cookie"] = cookie
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data) if data else None, r.getheader("Set-Cookie") or ""

    def test_info_is_public(self):
        code, body, _ = self.req("GET", "/api/v1/info")
        self.assertEqual((code, body["name"], body["api"], body["setup_needed"]), (200, "HostSpawn", 1, True))

    def test_app_flow(self):
        code, body, setc = self.req("POST", "/api/v1/setup", {"username": "owner", "password": GOOD, "device": "Desktop app"})
        self.assertEqual((code, body["role"], body["device"]), (200, "admin", "Desktop app"))
        self.assertEqual(setc, "")  # apps get the token in the reply, not a cookie
        tok = body["token"]
        self.assertEqual(self.req("GET", "/api/v1/overview", token=tok)[0], 200)
        self.assertEqual(self.req("GET", "/api/overview", token=tok)[0], 200)  # old paths still work
        self.assertEqual(self.req("GET", "/api/v1/overview")[0], 401)
        self.assertEqual(self.req("GET", "/api/v1/overview", token="A" * 43)[0], 401)
        self.assertEqual(self.req("POST", "/api/v1/action", {"server": "srv", "action": "start"}, token=tok)[0], 202)
        # pair a phone as a viewer account
        self.assertEqual(self.req("POST", "/api/v1/users", {"action": "add", "username": "watcher", "password": GOOD + "v", "role": "viewer"}, tok)[0], 200)
        vt = self.req("POST", "/api/v1/login", {"username": "watcher", "password": GOOD + "v", "device": "laptop"})[1]["token"]
        code, body, _ = self.req("POST", "/api/v1/pair/new", {}, vt)
        self.assertEqual(code, 200)
        self.assertEqual(self.req("POST", "/api/v1/pair", {"code": "000000" if body["code"] != "000000" else "000001", "device": "x"})[0], 400)
        code, paired, _ = self.req("POST", "/api/v1/pair", {"code": body["code"], "device": "iPhone"})
        self.assertEqual((code, paired["user"], paired["role"]), (200, "watcher", "viewer"))  # same rights, no more
        self.assertEqual(self.req("POST", "/api/v1/action", {"server": "srv", "action": "start"}, token=paired["token"])[0], 403)
        # devices: viewer sees only their own; admin sees all and can sign one out
        mine = self.req("GET", "/api/v1/devices", token=vt)[1]
        self.assertEqual(sorted(d["device"] for d in mine), ["iPhone", "laptop"])
        alld = self.req("GET", "/api/v1/devices", token=tok)[1]
        self.assertEqual(len(alld), 3)
        owner_dev = next(d for d in alld if d["user"] == "owner")
        self.assertEqual(self.req("POST", "/api/v1/devices", {"action": "revoke", "id": owner_dev["id"]}, vt)[0], 404)  # not theirs
        phone = next(d for d in alld if d["device"] == "iPhone")
        self.assertEqual(self.req("POST", "/api/v1/devices", {"action": "revoke", "id": phone["id"]}, tok)[0], 200)
        code, body, _ = self.req("GET", "/api/v1/overview", token=paired["token"])
        self.assertEqual(code, 401)
        self.assertIn("signed out", body["error"])
        # logout with a token ends that device only
        self.assertEqual(self.req("POST", "/api/v1/logout", {}, vt)[0], 200)
        self.assertEqual(self.req("GET", "/api/v1/me", token=vt)[0], 401)
        self.assertEqual(self.req("GET", "/api/v1/me", token=tok)[0], 200)

    def test_header_still_required_and_wrong_login(self):
        tok = self.req("POST", "/api/v1/setup", {"username": "owner", "password": GOOD, "device": "pc"})[1]["token"]
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/v1/action", json.dumps({"server": "srv", "action": "start"}), {"Authorization": "Bearer " + tok})
        self.assertEqual(c.getresponse().status, 403)
        code, body, _ = self.req("POST", "/api/v1/login", {"username": "owner", "password": "nope", "device": "pc"})
        self.assertEqual(code, 400)
        self.assertNotIn("token", body)


class TestPairCommand(Sandbox):
    def test_cli_code_pairs_and_unknown_user_refused(self):
        import contextlib
        import io
        from manager import util
        from manager.main import main
        saved = util.CONFIG
        util.CONFIG = self.tmp / "config"  # never the real accounts
        try:
            A.Accounts().setup_owner("owner", GOOD)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["pair", "owner"]), 0)
                self.assertEqual(main(["pair", "ghost"]), 1)
            code = out.getvalue().split("owner: ")[1][:6]
            agent = A.Accounts()  # the running Manager
            self.assertEqual(agent.session(agent.pair(code, "phone"))["user"], "owner")
        finally:
            util.CONFIG = saved


class TestChunkedRefused(TestAppApi):
    def test_chunked_body_gets_a_clear_411(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", "/api/v1/setup")
        c.putheader("X-Manager", "1")
        c.putheader("Transfer-Encoding", "chunked")
        c.endheaders()
        body = json.dumps({"username": "owner", "password": GOOD}).encode()
        c.send(b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body))
        r = c.getresponse()
        self.assertEqual(r.status, 411)
        self.assertIn("Content-Length", json.loads(r.read())["error"])
        self.assertTrue(self.acc.setup_needed)  # nothing was created from a half-read request
