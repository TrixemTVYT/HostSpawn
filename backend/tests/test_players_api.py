"""The players API (/api/v1/players...): online list, search, profile, kick, ban everywhere, unban, notes, watch,
say; who may do what (admin, moderator, viewer); bad input refused."""
import http.client
import json
import threading

from manager import accounts as A
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import Sandbox
from tests.test_players import Game

GOOD = "correct horse battery staple"
KEY = "steam:76561190000000001"  # made-up (test value)


class TestPlayersApi(Sandbox):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", GOOD)
        self.acc.create("mod", GOOD, "moderator", by="owner")
        self.acc.create("view", GOOD, "viewer", by="owner")
        self.g1, self.g2 = Game("g1", {"name": "One"}), Game("g2", {"name": "Two"})
        self.watched = Game("live", {"name": "Live"}, managed=False)
        self.sup = Supervisor({"g1": self.g1, "g2": self.g2, "live": self.watched}, config={})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.sup, "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]
        self.tok = {u: self.acc.login(u, GOOD, device="test") for u in ("owner", "mod", "view")}
        self.g1.join(KEY, "Alice")
        self.sup.tick()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        super().tearDown()

    def req(self, method, path, body=None, who="owner"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"X-Manager": "1", "Content-Type": "application/json", "Authorization": "Bearer " + self.tok[who]}
        c.request(method, "/api/v1/" + path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, json.loads(data) if data else None

    def act(self, who="owner", **body):
        return self.req("POST", "players", body, who)

    def test_online_search_profile(self):
        code, online = self.req("GET", "players?server=g1", who="view")
        self.assertEqual((code, [p["name"] for p in online]), (200, ["Alice"]))
        code, found = self.req("GET", "players/search?q=ali", who="view")
        self.assertEqual((found[0]["key"], found[0]["online"]), (KEY, True))
        code, p = self.req("GET", f"player?key={KEY}", who="view")
        self.assertEqual((code, p["name"], p["online"][0]["server"]), (200, "Alice", "g1"))
        self.assertEqual(self.req("GET", "player?key=steam:1")[0], 404)
        code, srv = self.req("GET", "server?id=g1")
        self.assertEqual((srv["players_supported"], srv["players_tracked"], srv["can_moderate"]), (True, True, True))
        code, srv = self.req("GET", "server?id=live")
        self.assertEqual((srv["players_tracked"], srv["can_moderate"]), (False, False))

    def test_moderator_kicks_bans_unbans_notes_watches(self):
        self.assertEqual(self.act("mod", action="kick", server="g1", key=KEY, reason="afk")[0], 200)
        self.assertEqual(self.g1.online, [])
        code, r = self.act("mod", action="ban", key=KEY, minutes=0, reason="cheating")
        self.assertEqual((code, sorted(r["applied"])), (200, ["g1", "g2"]))
        code, bans = self.req("GET", "bans", who="view")
        self.assertEqual([(b["key"], b["by"], b["reason"]) for b in bans], [(KEY, "mod", "cheating")])
        self.assertEqual(self.act("mod", action="note", key=KEY, text="second offence")[0], 200)
        self.assertEqual(self.act("mod", action="watch", key=KEY, reason="alt accounts")[0], 200)
        self.assertEqual(self.act("mod", action="unban", key=KEY)[0], 200)
        p = self.req("GET", f"player?key={KEY}")[1]
        self.assertEqual((p["ban"], p["notes"][0]["text"], p["watch"]["reason"]), (None, "second offence", "alt accounts"))
        code, log = self.req("GET", "audit", who="mod")
        self.assertEqual([a["action"] for a in log][:5], ["unban", "watch", "note", "ban", "kick"])
        self.assertEqual(self.req("GET", "audit", who="view")[0], 403)

    def test_say_to_one_or_all(self):
        code, r = self.act(action="say", text="Restart soon")
        self.assertEqual((code, sorted(r["servers"])), (200, ["g1", "g2"]))  # not the watched server
        code, r = self.act(action="say", server="g2", text="hi two")
        self.assertEqual(r["servers"], ["g2"])
        self.assertEqual(self.watched.said, [])

    def test_viewer_cannot_moderate(self):
        for body in ({"action": "kick", "server": "g1", "key": KEY}, {"action": "ban", "key": KEY, "reason": "x"},
                     {"action": "note", "key": KEY, "text": "x"}, {"action": "say", "text": "x"}):
            self.assertEqual(self.act("view", **body)[0], 403, body)
        self.assertEqual([p["key"] for p in self.g1.online], [KEY])

    def test_bad_input_refused(self):
        self.assertEqual(self.act(action="ban", key="steam:123", reason="x")[0], 400)
        self.assertEqual(self.act(action="ban", key=KEY, reason="")[0], 400)  # a reason is required
        self.assertEqual(self.act(action="ban", key=KEY, minutes=-5, reason="x")[0], 400)
        self.assertEqual(self.act(action="ban", key=KEY, minutes="soon", reason="x")[0], 400)
        self.assertEqual(self.act(action="kick", key=KEY, reason="x")[0], 400)  # which server?
        self.assertEqual(self.act(action="kick", server="nope", key=KEY)[0], 404)
        self.assertEqual(self.act(action="note", key=KEY, text="   ")[0], 400)
        self.assertEqual(self.act(action="dance", key=KEY)[0], 400)
        code, r = self.act(action="kick", server="live", key=KEY, reason="x")
        self.assertEqual(code, 400)
        self.assertIn("only watches", r["error"])
        self.assertEqual(self.sup.db.bans(), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
