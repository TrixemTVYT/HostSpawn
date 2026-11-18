"""In-game messages: countdowns before scheduled restarts (they land on time), scheduled messages with placeholders,
and their settings over the API. Fake servers only; countdowns run instantly (base._sleep replaced)."""
import http.client
import json
import threading
import time
from datetime import timedelta

from manager import accounts as A
from manager import util
from manager.adapters import base
from manager.adapters.base import countdown_text
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import Sandbox
from tests.test_players import Game

GOOD = "correct horse battery staple"


class Talky(Game):
    supervised_by = "manager"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.updates = []
        self.update = lambda: self.updates.append(time.time()) or "updated"


class Instant(Sandbox):
    def setUp(self):
        super().setUp()
        self._sleep, self.slept = base._sleep, []
        base._sleep = lambda s: self.slept.append(s)

    def tearDown(self):
        base._sleep = self._sleep
        super().tearDown()

    def wait(self, f, secs=5):
        end = time.time() + secs
        while time.time() < end and not f():
            time.sleep(0.02)
        return f()


class TestCountdown(Instant):
    def test_text(self):
        self.assertEqual(countdown_text(None, "restart", 300), "Server restart in 5 minutes.")
        self.assertEqual(countdown_text(None, "wipe", 60), "Server wipe in 1 minute.")
        self.assertEqual(countdown_text(None, "restart", 10), "Server restart in 10 seconds.")
        self.assertEqual(countdown_text("Restarting in {time}!", "restart", 120), "Restarting in 2 minutes!")
        self.assertEqual(countdown_text("Oops {nope}", "restart", 30), "Server restart in 30 seconds.")  # typo: still warns

    def test_marks_and_total_time(self):
        a = Talky("t", {})
        a.countdown(300, "restart")
        self.assertEqual(a.said, ["Server restart in 5 minutes.", "Server restart in 2 minutes.", "Server restart in 1 minute.",
                                  "Server restart in 30 seconds.", "Server restart in 10 seconds."])
        self.assertEqual(sum(self.slept), 300)  # it ends exactly when the time is up

    def test_server_that_cannot_talk_just_waits(self):
        a = Talky("t", {}, managed=False)  # watched only: never sends anything
        a.countdown(60)
        self.assertEqual((a.said, sum(self.slept)), ([], 60))


class TestScheduledRestart(Instant):
    def test_countdown_starts_early_so_restart_lands_on_time(self):
        at = util.uk_now() + timedelta(minutes=6)  # 5-6 min away: the first check in that minute starts the countdown
        a = Talky("t", {"restart_times": [f"{at:%H:%M}"], "restart_warn_s": 300})
        sup = Supervisor({"t": a}, config={})
        sup.tick(); sup.tick()
        self.assertTrue(self.wait(lambda: a.updates))
        self.assertEqual(len(a.updates), 1)
        self.assertEqual(a.said[0], "Server restart in 5 minutes.")
        self.assertTrue(any(f"at {at:%H:%M %Z}: countdown started" in e["message"] for e in util.read_events()))

    def test_restart_set_inside_the_warning_time_still_happens(self):
        at = util.uk_now() + timedelta(minutes=2)  # less than the 5-minute countdown away (the e2e sandbox caught this)
        a = Talky("t", {"restart_times": [f"{at:%H:%M}"], "restart_warn_s": 300})
        Supervisor({"t": a}, config={}).tick()
        self.assertTrue(self.wait(lambda: a.updates))
        self.assertIn("Server restart in 1 minute.", a.said)  # a shorter countdown
        self.assertNotIn("Server restart in 5 minutes.", a.said)
        self.assertLessEqual(sum(self.slept), 120)

    def test_far_restart_not_started(self):
        at = util.uk_now() + timedelta(minutes=30)
        a = Talky("t", {"restart_times": [f"{at:%H:%M}"], "restart_warn_s": 300})
        Supervisor({"t": a}, config={}).tick()
        time.sleep(0.2)
        self.assertEqual((a.updates, a.said), ([], []))

    def test_no_countdown_when_it_cannot_talk(self):
        now = util.uk_now()
        a = Talky("t", {"restart_times": [f"{now:%H:%M}"]}, managed=False)
        a.can_moderate = lambda: False
        sup = Supervisor({"t": a}, config={})
        sup.tick()
        self.assertTrue(self.wait(lambda: a.updates))  # restarts at the minute itself, as before
        self.assertEqual(a.said, [])

    def test_countdown_off(self):
        now = util.uk_now()
        a = Talky("t", {"restart_times": [f"{now:%H:%M}"], "restart_warn_s": 0})
        Supervisor({"t": a}, config={}).tick()
        self.assertTrue(self.wait(lambda: a.updates))
        self.assertEqual(a.said, [])


class TestScheduledMessages(Instant):
    def test_every_minute_once_with_placeholders(self):
        a = Talky("t", {"name": "My Rust", "max_players": 50, "restart_times": ["23:59"],
                        "messages": [{"text": "Welcome to {server}: {players}/{max} on, next restart {next_restart}", "every_min": 1}]})
        a.join("steam:76561190000000001", "Alice")
        sup = Supervisor({"t": a}, config={})
        sup.tick(); sup.tick(); sup.tick()
        self.assertEqual(len(a.said), 1)
        self.assertRegex(a.said[0], r"^Welcome to My Rust: 1/50 on, next restart \d\d:\d\d$")
        Supervisor({"t": a}, config={}).tick()  # agent restarted in the same minute: still once
        self.assertEqual(len(a.said), 1)

    def test_at_set_times_only(self):
        now = util.uk_now()
        a = Talky("t", {"messages": [{"text": "now", "times": [f"{now:%H:%M}"]},
                                     {"text": "later", "times": [f"{now + timedelta(hours=1):%H:%M}"]}]})
        Supervisor({"t": a}, config={}).tick()
        self.assertEqual(a.said, ["now"])

    def test_not_when_stopped_or_watched(self):
        msgs = [{"text": "hi", "every_min": 1}]
        stopped = Talky("s", {"messages": msgs})
        stopped.status = lambda: {"state": "stopped", "flags": []}
        watched = Talky("w", {"messages": msgs}, managed=False)
        Supervisor({"s": stopped, "w": watched}, config={}).tick()
        self.assertEqual((stopped.said, watched.said), ([], []))


class TestMessageSettingsApi(Sandbox):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", GOOD)
        self.g = Talky("g", {"name": "G"})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Supervisor({"g": self.g}, config={}), "", "live",
                                                                        None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = self.acc.login("owner", GOOD, device="test")

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        super().tearDown()

    def save(self, **body):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        c.request("POST", "/api/v1/server/settings", json.dumps({"server": "g", **body}),
                  {"X-Manager": "1", "Content-Type": "application/json", "Authorization": "Bearer " + self.tok})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_saves_messages_and_countdown(self):
        code, _ = self.save(messages=[{"text": " Rules: be nice\n", "every_min": 30},
                                      {"text": "Wipe Thursday", "times": ["18:00", "12:00", "18:00"]}],
                            restart_warn_s=600, countdown_message="Restart in {time}")
        self.assertEqual(code, 200)
        self.assertEqual(self.g.cfg["messages"], [{"text": "Rules: be nice", "every_min": 30},
                                                  {"text": "Wipe Thursday", "times": ["12:00", "18:00"]}])
        self.assertEqual((self.g.cfg["restart_warn_s"], self.g.cfg["countdown_message"]), (600, "Restart in {time}"))

    def test_bad_settings_refused_and_nothing_saved(self):
        for bad in ({"messages": [{"text": "", "every_min": 30}]}, {"messages": [{"text": "x", "every_min": 1}]},
                    {"messages": [{"text": "x", "times": ["6pm"]}]}, {"messages": [{"text": "x", "every_min": 30}] * 11},
                    {"messages": "hello"}, {"restart_warn_s": 99999}, {"restart_warn_s": "soon"},
                    {"countdown_message": "no placeholder"}):
            self.assertEqual(self.save(**bad)[0], 400, bad)
        self.assertNotIn("messages", self.g.cfg)


if __name__ == "__main__":
    import unittest
    unittest.main()
