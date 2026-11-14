"""Player database and moderation through the supervisor: sessions and playtime, one ban list for every server
(kicked on sight), temporary bans ending, watchlist alerts, notes, audit, and servers HostSpawn only watches."""
import stat
import time

from manager import util
from manager.adapters.moderation import ModerationError
from manager.players import PlayerDB
from manager.supervisor import Supervisor
from tests.test_manager import FakeAdapter, Sandbox

A, B = "steam:76561190000000001", "steam:76561190000000002"  # made-up ids (test values)


class Game(FakeAdapter):
    """A server with RCON: a list of players; kick/ban remove them; bans are remembered like the game would."""
    def __init__(self, *a, managed=True, **k):
        super().__init__(*a, **k)
        self.cfg["managed"] = managed
        self.online, self.kicked, self.game_bans, self.said = [], [], set(), []

    def can_moderate(self): return bool(self.cfg.get("managed") or self.cfg.get("moderation"))

    def join(self, key, name):
        if key not in self.game_bans:
            self.online.append({"key": key, "name": name, "ping": 20, "connected_s": 5, "ref": key[-4:]})

    def players(self): return list(self.online)

    def kick(self, ref, reason):
        if not self.can_moderate():
            raise ModerationError("watch only")
        self.kicked.append((ref, reason))
        self.online = [p for p in self.online if p["ref"] != ref]
        return "kicked"

    def ban(self, key, ref, name, minutes, reason):
        if not minutes:
            self.game_bans.add(key)
        if ref:
            self.kick(ref, reason)
        return "banned"

    def unban(self, key):
        self.game_bans.discard(key)
        return "unbanned"

    def say(self, text):
        self.said.append(text)
        return "said"


class TestPlayerDB(Sandbox):
    def test_sessions_playtime_and_names(self):
        db = PlayerDB()
        t = 1_000_000.0
        j, l = db.observe("s1", [{"key": A, "name": "Alice", "ref": "1"}], t)
        self.assertEqual(([p["key"] for p in j], l), ([A], []))
        db.observe("s1", [{"key": A, "name": "Alice", "ref": "1"}], t + 60)
        db.observe("s1", [{"key": A, "name": "Al1ce", "ref": "1"}], t + 120)  # renamed
        j, l = db.observe("s1", [], t + 180)
        self.assertEqual([r["key"] for r in l], [A])
        p = db.profile(A)
        self.assertEqual((p["name"], p["playtime_s"], p["sessions"]), ("Al1ce", 120, 1))
        self.assertEqual({n["name"] for n in p["names"]}, {"Alice", "Al1ce"})
        self.assertEqual(db.search("alice")[0]["key"], A)  # old names find them too
        db.observe("s1", [{"key": A, "name": "Al1ce", "ref": "1"}], t + 10_000)  # back later: a new session,
        db.observe("s1", [{"key": A, "name": "Al1ce", "ref": "1"}], t + 20_000)  # and a long gap isn't play
        self.assertEqual(db.profile(A)["sessions"], 2)
        self.assertEqual(db.profile(A)["playtime_s"], 120)
        self.assertEqual(stat.S_IMODE(db.path.stat().st_mode), 0o600)

    def test_bans_temporary_permanent_and_lifted(self):
        db = PlayerDB()
        db.observe("s1", [{"key": A, "name": "Alice", "ref": "1"}])
        db.ban(A, 0, "cheating", "owner")
        self.assertEqual(db.active_ban(A)["name"], "Alice")
        self.assertIsNone(db.active_ban(A)["until"])
        db.ban(A, 10, "shortened", "mod")  # replaces the old one
        self.assertEqual(len(db.bans()), 1)
        self.assertIsNotNone(db.active_ban(A))
        self.assertIsNone(db.active_ban(A, now=time.time() + 11 * 60))  # ended by itself
        self.assertTrue(db.unban(A, "owner"))
        self.assertIsNone(db.active_ban(A))
        self.assertEqual(len(db.bans(include_old=True)), 2)
        self.assertEqual([a["action"] for a in db.audit_log()], ["unban", "ban", "ban"])

    def test_notes_and_watch(self):
        db = PlayerDB()
        db.observe("s1", [{"key": B, "name": "Bob", "ref": "2"}])
        db.note(B, "warned for spawn camping", "mod")
        db.watch(B, True, "mod", "suspected aimbot")
        p = db.profile(B)
        self.assertEqual(p["notes"][0]["text"], "warned for spawn camping")
        self.assertEqual(p["watch"]["reason"], "suspected aimbot")
        self.assertTrue(db.search("Bob")[0]["watched"])
        db.watch(B, False, "mod")
        self.assertIsNone(db.profile(B)["watch"])


class TestModerationLoop(Sandbox):
    def setUp(self):
        super().setUp()
        self.s1, self.s2 = Game("s1", {"name": "One"}), Game("s2", {"name": "Two"})
        self.sup = Supervisor({"s1": self.s1, "s2": self.s2}, config={})

    def events(self):
        return [e["message"] for e in util.read_events()]

    def test_ban_once_banned_everywhere(self):
        self.s1.join(A, "Alice")
        self.sup.tick()
        ban, applied = self.sup.ban_player(A, 0, "cheating", "owner")
        self.assertEqual(sorted(applied), ["s1", "s2"])
        self.assertEqual(self.s1.online, [])  # kicked where they were
        self.s2.join(A, "Alice")  # the game itself refuses permanent bans
        self.assertEqual(self.s2.online, [])
        self.assertTrue(any("banned Alice permanently (by owner): cheating" in m for m in self.events()))

    def test_temporary_ban_kicks_on_sight_then_ends(self):
        self.sup.tick()
        self.sup.ban_player(A, 30, "rdm", "mod")
        self.s2.join(A, "Alice")  # temporary: the game lets them in, HostSpawn kicks them
        self.sup.tick()
        self.assertEqual(self.s2.online, [])
        self.assertEqual(self.s2.kicked[-1][1], "Banned: rdm")
        self.assertTrue(any("Alice is banned (by mod): kicked on joining" in m for m in self.events()))
        # ban over: let back in
        with self.sup.db._lock, self.sup.db._db:
            self.sup.db._db.execute("UPDATE bans SET until=?", (time.time() - 1,))
        self.s2.join(A, "Alice")
        self.sup.tick()
        self.assertEqual([p["key"] for p in self.s2.online], [A])

    def test_unban_lifts_everywhere(self):
        self.sup.ban_player(B, 0, "x", "owner")
        self.sup.unban_player(B, "owner")
        self.s1.join(B, "Bob")
        self.sup.tick()
        self.assertEqual([p["key"] for p in self.s1.online], [B])

    def test_watched_player_joining_alerts(self):
        self.sup.db.watch(B, True, "mod", "suspected aimbot")
        self.s2.join(B, "Bob")
        self.sup.tick()
        self.sup.tick()  # still there: only one alert
        self.assertEqual(sum("watched player Bob joined (suspected aimbot)" in m for m in self.events()), 1)

    def test_kick_and_say(self):
        self.s1.join(A, "Alice")
        self.sup.tick()
        self.sup.kick_player("s1", A, "afk", "mod")
        self.assertEqual(self.s1.online, [])
        with self.assertRaises(ModerationError):
            self.sup.kick_player("s1", A, "afk", "mod")  # not online any more
        self.assertEqual(sorted(self.sup.say(None, "hello", "owner")), ["s1", "s2"])
        self.assertEqual(self.sup.say("s2", "only two", "owner"), ["s2"])
        self.assertEqual(self.s1.said, ["hello"])

    def test_watched_server_is_never_polled_or_moderated(self):
        live = Game("live", {"name": "Live"}, managed=False)
        live.join(A, "Alice")
        polled = []
        live.players = lambda: polled.append(1) or []
        sup = Supervisor({"live": live}, config={})
        sup.tick()
        self.assertEqual(polled, [])  # not asked for players unless the owner turns it on
        sup.ban_player(A, 0, "x", "owner")
        self.assertEqual(live.kicked, [])
        with self.assertRaises(ModerationError):
            sup.kick_player("live", A, "x", "owner")

    def test_development_copy_moderates_only_its_sandbox_servers(self):
        sup = Supervisor({"s1": self.s1}, config={}, control=False)
        self.s1.join(A, "Alice")
        sup.tick()
        sup.ban_player(A, 0, "x", "owner")
        self.assertEqual(self.s1.kicked, [])  # dev copy: watch only
        self.s1.cfg["sandbox"] = True
        sup.ban_player(A, 0, "x", "owner")
        self.assertEqual(len(self.s1.kicked), 1)

    def test_deleted_server_has_nobody_online(self):
        self.s1.join(A, "Alice")
        self.sup.tick()
        self.sup.adapters.pop("s1")  # deleted while the agent was off
        sup2 = Supervisor(self.sup.adapters, config={})
        self.assertEqual(sup2.db.online("s1"), [])
        self.assertEqual(sup2.db.profile(A)["online"], [])
        self.assertEqual(sup2.db.profile(A)["sessions"], 1)  # the history stays

    def test_stopped_server_has_nobody_online(self):
        self.s1.join(A, "Alice")
        self.sup.tick()
        self.assertEqual(len(self.sup.db.online("s1")), 1)
        self.s1.status = lambda: {"state": "stopped", "flags": []}
        self.sup.tick()
        self.assertEqual(self.sup.db.online("s1"), [])


if __name__ == "__main__":
    import unittest
    unittest.main()
