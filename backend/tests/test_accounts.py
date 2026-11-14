"""Accounts and sign-in security: storage, setup, lock-out, sessions, roles, cookies. No real users touched."""
import http.client
import json
import stat
import threading
import time

from manager import accounts as A
from manager import util
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import FakeAdapter, Sandbox

GOOD = "correct horse battery staple"


class TestStore(Sandbox):
    def acc(self):
        return A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")

    def test_password_never_stored_and_files_private(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        tok = a.login("owner", GOOD)
        for f in ("accounts.json", "sessions.json"):
            text = (self.tmp / f).read_text()
            self.assertNotIn(GOOD, text)
            self.assertNotIn(tok, text)  # only a hash of the session token is kept
            self.assertEqual(stat.S_IMODE((self.tmp / f).stat().st_mode), 0o600, f)
        self.assertTrue(a.users["owner"]["hash"].startswith("scrypt$"))

    def test_setup_once_even_when_racing(self):
        a = self.acc()
        errs = []

        def go(i):
            try:
                a.setup_owner(f"owner{i}", GOOD + str(i))
            except PermissionError as e:
                errs.append(str(e))
        ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(a.users), 1)
        self.assertEqual(len(errs), 9)

    def test_weak_and_bad_inputs_refused(self):
        a = self.acc()
        for user, pw in (("ab", GOOD), ("ok_user", "short"), ("ok_user", "aaaaaaaaaaaa"), ("bob123", "bob123isgreat"),
                         ("ok_user", "password123"), ("bad name", GOOD), ("x" * 40, GOOD)):
            with self.assertRaises(ValueError, msg=(user, pw)):
                a.create(user, pw)
        self.assertEqual(a.users, {})

    def test_lockout_after_five_wrong_and_unknown_user_same_message(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        with self.assertRaises(ValueError) as e1:
            a.login("nobody", "x", ip="1.1.1.1")
        with self.assertRaises(ValueError) as e2:
            a.login("owner", "x", ip="2.2.2.2")
        self.assertEqual(str(e1.exception), str(e2.exception))  # doesn't reveal which usernames exist
        for _ in range(4):
            with self.assertRaises(ValueError):
                a.login("owner", "wrong", ip="3.3.3.3")
        with self.assertRaises(PermissionError):
            a.login("owner", GOOD, ip="9.9.9.9")  # the username is locked, even with the right password
        a._fails.clear()
        self.assertTrue(a.login("owner", GOOD))

    def test_sessions_expire_logout_and_password_change(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        t1, t2 = a.login("owner", GOOD), a.login("owner", GOOD, remember=True)
        self.assertEqual(a.session(t1)["role"], "admin")
        self.assertIsNone(a.session("made-up-token"))
        a.logout(t1)
        self.assertIsNone(a.session(t1))
        a.change_password("owner", GOOD, GOOD + "!")
        self.assertIsNone(a.session(t2))  # other devices signed out
        t3 = a.login("owner", GOOD + "!")
        a.sessions[A._digest(t3)]["exp"] = time.time() - 1
        self.assertIsNone(a.session(t3))
        self.assertIsNotNone(A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json").users)  # reloads

    def test_last_admin_protected(self):
        a = self.acc()
        a.setup_owner("owner", GOOD)
        with self.assertRaises(ValueError):
            a.set_role("owner", "viewer")
        with self.assertRaises(ValueError):
            a.remove("owner")
        a.create("second", GOOD + "2", "admin")
        a.set_role("owner", "viewer")
        self.assertEqual(a.users["owner"]["role"], "viewer")

    def test_role_rules(self):
        self.assertTrue(A.allowed("admin", "/api/delete"))
        for p, act in (("/api/delete", ""), ("/api/deploy", ""), ("/api/settings", ""), ("/api/users", ""),
                       ("/api/deps/install", ""), ("/api/action", "wipe"), ("/api/action", "oxide"), ("/api/job", "discard")):
            self.assertFalse(A.allowed("moderator", p, act), (p, act))
        for act in ("start", "stop", "restart", "update", "backup", "send"):
            self.assertTrue(A.allowed("moderator", "/api/action", act))
        self.assertTrue(A.allowed("moderator", "/api/job", "cancel"))
        self.assertFalse(A.allowed("viewer", "/api/action", "start"))
        self.assertTrue(A.allowed("viewer", "/api/logout"))


class TestSignInOverHttp(Sandbox):
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

    def req(self, method, path, body=None, cookie=""):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"X-Manager": "1", "Content-Type": "application/json"}
        if cookie:
            h["Cookie"] = cookie
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (json.loads(data) if data and r.getheader("Content-Type", "").startswith("application/json") else data), r.getheader("Set-Cookie") or ""

    def signin(self, user, pw):
        code, body, setc = self.req("POST", "/api/login", {"username": user, "password": pw})
        return code, setc.split(";")[0], setc

    def test_full_flow(self):
        self.assertEqual(self.req("GET", "/")[0], 200)  # the page itself loads, to show the setup/sign-in screen
        self.assertEqual(self.req("GET", "/api/overview")[0], 401)
        st = self.req("GET", "/api/setup/status")[1]
        self.assertTrue(st["setup_needed"])
        code, body, setc = self.req("POST", "/api/setup", {"username": "owner", "password": GOOD})
        self.assertEqual((code, body["role"]), (200, "admin"))
        self.assertIn("HttpOnly", setc)
        self.assertIn("SameSite=Strict", setc)
        self.assertNotIn(setc.split(";")[0].split("=", 1)[1], json.dumps(body))  # token only in the cookie
        self.assertEqual(self.req("POST", "/api/setup", {"username": "evil", "password": GOOD + "x"})[0], 403)  # once only
        ck = setc.split(";")[0]
        self.assertEqual(self.req("GET", "/api/overview", cookie=ck)[0], 200)
        # add a viewer and a moderator
        for name, role in (("watcher", "viewer"), ("mod1", "moderator")):
            self.assertEqual(self.req("POST", "/api/users", {"action": "add", "username": name, "password": GOOD + str(len(name)), "role": role}, ck)[0], 200)
        _, vck, _ = self.signin("watcher", GOOD + "7")
        _, mck, _ = self.signin("mod1", GOOD + "4")
        self.assertEqual(self.req("GET", "/api/overview", cookie=vck)[0], 200)
        self.assertEqual(self.req("POST", "/api/action", {"server": "srv", "action": "start"}, vck)[0], 403)
        self.assertEqual(self.req("POST", "/api/action", {"server": "srv", "action": "start"}, mck)[0], 202)
        self.assertEqual(self.req("POST", "/api/settings", {"community": "Hacked"}, mck)[0], 403)
        self.assertEqual(self.req("GET", "/api/users", cookie=mck)[0], 403)
        self.assertEqual(self.req("POST", "/api/users", {"action": "add", "username": "x1x", "password": GOOD + "q", "role": "admin"}, mck)[0], 403)
        time.sleep(0.3)
        self.assertTrue(any("(by mod1)" in e["message"] for e in util.read_events()))  # audit: who did it
        # sign out
        code, _, setc = self.req("POST", "/api/logout", {}, mck)
        self.assertIn("Max-Age=0", setc)
        self.assertEqual(self.req("GET", "/api/overview", cookie=mck)[0], 401)
        # header still required on POST (cross-site protection)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/action", json.dumps({"server": "srv", "action": "start"}), {"Cookie": ck})
        self.assertEqual(c.getresponse().status, 403)

    def test_lockout_over_http(self):
        self.req("POST", "/api/setup", {"username": "owner", "password": GOOD})
        for _ in range(5):
            self.assertEqual(self.signin("owner", "nope")[0], 400)
        self.assertEqual(self.signin("owner", GOOD)[0], 429)

    def test_forged_cookie_refused(self):
        self.req("POST", "/api/setup", {"username": "owner", "password": GOOD})
        for ck in ("mgr_session=abc", "mgr_session=" + "A" * 43, "mgr_session=; other=1", 'mgr_session="x'):
            self.assertEqual(self.req("GET", "/api/overview", cookie=ck)[0], 401, ck)
