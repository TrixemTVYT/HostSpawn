"""Players and moderation in the adapters, against fake RCON servers: Rust WebRCON and BattlEye (Arma 3)."""
import stat

from manager import util
from manager.adapters.arma3 import Arma3Adapter
from manager.adapters.base import Unsupported
from manager.adapters.moderation import ModerationError, clean_text
from manager.adapters.rust import RustAdapter
from manager.adapters.rust_server import RustServer
from tests.fakes.fake_battleye import FakeBattlEye
from tests.fakes.fake_webrcon import FakeWebRcon
from tests.test_manager import FakeAdapter, Sandbox, free_port

S1, S2 = "76561190000000001", "76561190000000002"  # made-up Steam ids (test values)
G1 = "c" * 32


class TestRustModeration(Sandbox):
    def setUp(self):
        super().setUp()
        self.f = FakeWebRcon(password="rust-pw", players=[
            {"SteamID": S1, "DisplayName": "Alice", "Ping": 30, "ConnectedSeconds": 600, "Address": "10.0.0.1:5000"},
            {"SteamID": S2, "DisplayName": "Bob", "Ping": 80, "ConnectedSeconds": 60, "Address": "10.0.0.2:5000"}])
        util.set_secret("SRV_R_RCON", "rust-pw")

    def tearDown(self):
        self.f.close()
        super().tearDown()

    def server(self, **cfg):
        return RustServer("r", {"root": str(self.tmp), "rcon_port": self.f.port, "rcon_password_key": "SRV_R_RCON",
                                "managed": True, **cfg})

    def test_players_have_one_shape_and_no_ip(self):
        ps = self.server().players()
        self.assertEqual([(p["key"], p["name"], p["ref"]) for p in ps], [(f"steam:{S1}", "Alice", S1), (f"steam:{S2}", "Bob", S2)])
        self.assertEqual((ps[0]["ping"], ps[0]["connected_s"]), (30, 600))
        self.assertNotIn("10.0.0.1", str(ps))  # IP addresses are dropped

    def test_kick_ban_unban_say(self):
        a = self.server()
        self.assertEqual(a.kick(S2, 'Be "nice"\nsay hacked'), "kicked")
        self.assertIn(f'kick {S2} "Be nicesay hacked"', self.f.commands)  # quotes and new lines removed
        self.assertEqual(a.ban(f"steam:{S1}", S1, "Alice", 0, "cheating"), "banned")
        self.assertIn(S1, self.f.banned)
        self.assertEqual(a.players(), [])
        self.assertEqual(a.unban(f"steam:{S1}"), "unbanned")
        self.assertNotIn(S1, self.f.banned)
        a.say("Restart in 5 minutes")
        self.assertIn('say "Restart in 5 minutes"', self.f.commands)

    def test_temporary_ban_kicks_but_is_kept_by_hostspawn(self):
        a = self.server()
        self.assertIn("temporary", a.ban(f"steam:{S2}", S2, "Bob", 60, "rdm"))
        self.assertNotIn(S2, self.f.banned)  # Rust's own banid is permanent: HostSpawn keeps temporary ones
        self.assertEqual([p["name"] for p in a.players()], ["Alice"])

    def test_bad_ids_refused(self):
        a = self.server()
        for bad in ("123", f"{S1}; quit", "steam:abc"):
            with self.assertRaises(ModerationError):
                a.kick(bad, "x")
            with self.assertRaises(ModerationError):
                a.ban(bad, None, "x", 0, "x")
        self.assertFalse(any("quit" in c for c in self.f.commands))

    def test_watched_server_is_read_only(self):
        # A server HostSpawn only watches (like a rustctl server): player list yes, kick/ban no.
        root = self.tmp / "rustctl"
        (root / "config").mkdir(parents=True)
        (root / "config/secrets.env").write_text("RCON_PASSWORD=rust-pw\n")
        a = RustAdapter("live", {"root": str(root), "rcon_port": self.f.port})
        self.assertEqual(len(a.players()), 2)
        self.assertFalse(a.can_moderate())
        for call in (lambda: a.kick(S1, "x"), lambda: a.ban(f"steam:{S1}", S1, "A", 0, "x"),
                     lambda: a.unban(f"steam:{S1}"), lambda: a.say("hi")):
            with self.assertRaises(ModerationError):
                call()
        self.assertEqual(self.f.commands, ["playerlist"])  # nothing else was sent
        a.cfg["moderation"] = True  # the owner turned it on for this server
        self.assertTrue(a.can_moderate())

    def test_rcon_down_is_a_clear_error(self):
        a = self.server(rcon_port=free_port())  # nothing listens there
        with self.assertRaises(ModerationError) as e:
            a.players()
        self.assertTrue(str(e.exception).startswith("RCON:"))
        self.assertNotIn("rust-pw", str(e.exception))


class TestArma3Moderation(Sandbox):
    def setUp(self):
        super().setUp()
        self.f = FakeBattlEye(password="be-pw", players=[{"name": "Carl", "guid": G1, "ip": "10.0.0.3"}])
        util.set_secret("SRV_A_RCON", "be-pw")
        self.a = Arma3Adapter("a", {"root": str(self.tmp / "a3"), "rcon_port": self.f.port, "rcon_password_key": "SRV_A_RCON",
                                    "managed": True})

    def tearDown(self):
        self.f.close()
        super().tearDown()

    def test_players_kick_ban_unban_say(self):
        self.assertEqual([(p["key"], p["name"], p["ref"]) for p in self.a.players()], [(f"be:{G1}", "Carl", "0")])
        self.a.say("hello all")
        self.assertEqual(self.f.said, ["hello all"])
        self.a.ban(f"be:{G1}", "0", "Carl", 30, "griefing")
        self.assertEqual(self.a.players(), [])
        self.assertEqual([(b["guid"], b["minutes"]) for b in self.f.bans], [(G1, 30)])
        self.assertFalse(self.f.add("Carl", G1))
        self.a.unban(f"be:{G1}")
        self.assertEqual(self.f.bans, [])
        self.assertTrue(self.f.add("Carl", G1))
        self.a.kick("0", "afk")
        self.assertEqual(self.f.players, [])

    def test_no_rcon_configured(self):
        a = Arma3Adapter("b", {"root": str(self.tmp / "b"), "managed": True})
        with self.assertRaises(ModerationError):
            a.players()

    def test_battleye_config_written_private_and_local(self):
        self.a.write_configs()
        f = self.a.server_dir / "battleye" / "beserver_x64.cfg"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        text = f.read_text()
        self.assertIn("RConIP 127.0.0.1", text)
        self.assertIn(f"RConPort {self.f.port}", text)
        self.assertIn("RConPassword be-pw", text)


class TestDefaults(Sandbox):
    def test_other_adapters_say_unsupported(self):
        a = FakeAdapter("x", {})
        self.assertFalse(a.can_moderate())
        with self.assertRaises(Unsupported):
            a.players()

    def test_clean_text(self):
        self.assertEqual(clean_text('a"b\\c\nd\x00e' + "x" * 200), "abcde" + "x" * 115)


if __name__ == "__main__":
    import unittest
    unittest.main()
