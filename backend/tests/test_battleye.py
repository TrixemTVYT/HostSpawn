"""BattlEye RCon client against a fake UDP server: packet format, lost packets, long replies, server messages."""
import time
import unittest

from manager import battleye
from manager.battleye import BattlEyeError, BattlEyeRcon, packet, parse_bans, parse_players, unpack
from tests.fakes.fake_battleye import FakeBattlEye

GUID_A, GUID_B = "a" * 32, "0123456789abcdef0123456789abcdef"


class TestPackets(unittest.TestCase):
    def test_round_trip_and_checksum(self):
        p = packet(b"\x01\x00players")
        self.assertEqual(p[:2], b"BE")
        self.assertEqual(p[6], 0xFF)
        self.assertEqual(unpack(p), b"\x01\x00players")
        broken = p[:-1] + bytes([p[-1] ^ 1])
        self.assertIsNone(unpack(broken))  # a damaged packet is ignored, not believed
        self.assertIsNone(unpack(b"XX" + p[2:]))

    def test_parse_players(self):
        text = ("Players on server:\n[#] [IP Address]:[Port] [Ping] [GUID] [Name]\n------\n"
                f"0   10.1.2.3:2304   47   {GUID_A}(OK) Some Name\n"
                "1   10.1.2.4:2316   0    -  Joining Guy (Lobby)\n"
                f"2   10.1.2.5:2304   12   {GUID_B.upper()}(?) [TAG] Odd  Spaces\n(3 players in total)")
        ps = parse_players(text)
        self.assertEqual([p["num"] for p in ps], [0, 1, 2])
        self.assertEqual((ps[0]["name"], ps[0]["guid"], ps[0]["verified"], ps[0]["ping"]), ("Some Name", GUID_A, True, 47))
        self.assertEqual((ps[1]["guid"], ps[1]["lobby"], ps[1]["name"]), (None, True, "Joining Guy"))
        self.assertEqual((ps[2]["guid"], ps[2]["verified"], ps[2]["name"]), (GUID_B, False, "[TAG] Odd  Spaces"))

    def test_parse_bans(self):
        text = f"GUID Bans:\n[#] [GUID] [Minutes left] [Reason]\n---\n0  {GUID_A} perm cheating\n1  {GUID_B} 55 rdm\n\nIP Bans:\n"
        self.assertEqual(parse_bans(text), [{"num": 0, "guid": GUID_A, "minutes": None, "reason": "cheating"},
                                            {"num": 1, "guid": GUID_B, "minutes": 55, "reason": "rdm"}])


class TestClient(unittest.TestCase):
    def setUp(self):
        self.fakes = []

    def tearDown(self):
        for f in self.fakes:
            f.close()

    def fake(self, **k):
        f = FakeBattlEye(password="be pass", players=[{"name": "Alice", "guid": GUID_A, "ip": "10.0.0.1"},
                                                      {"name": "Bob", "guid": GUID_B, "ip": "10.0.0.2"}], **k)
        self.fakes.append(f)
        return f, BattlEyeRcon("127.0.0.1", f.port, "be pass", timeout=0.5)

    def test_players_kick_ban_unban(self):
        f, c = self.fake()
        self.assertEqual([p["name"] for p in c.players()], ["Alice", "Bob"])
        c.command("kick 0 Be nice")
        self.assertEqual([p["name"] for p in c.players()], ["Bob"])
        c.command(f"addBan {GUID_A} 0 cheating")
        c.command("ban 0 60 rdm")
        self.assertEqual(c.players(), [])
        self.assertEqual([(b["guid"], b["minutes"]) for b in c.bans()], [(GUID_A, None), (GUID_B, 60)])
        self.assertFalse(f.add("Alice again", GUID_A))  # banned GUIDs can't come back
        c.command("removeBan 0")
        self.assertEqual([b["guid"] for b in c.bans()], [GUID_B])

    def test_wrong_password_is_clear_and_never_leaks(self):
        f, _ = self.fake()
        with self.assertRaises(BattlEyeError) as e:
            BattlEyeRcon("127.0.0.1", f.port, "secret-wrong-pw", timeout=0.5).command("players")
        self.assertEqual(str(e.exception), "wrong RCON password")
        self.assertNotIn("secret-wrong-pw", str(e.exception))

    def test_lost_packets_are_retried(self):
        f, c = self.fake(drop=2)  # the first login and the first retry are lost
        self.assertEqual(len(c.players()), 2)

    def test_long_reply_in_parts_out_of_order(self):
        f, c = self.fake(split=40)
        for i in range(20):
            f.add(f"Player {i}", f"{i:032x}")
        self.assertEqual(len(c.players()), 22)

    def test_server_messages_are_acknowledged(self):
        f, c = self.fake(chatter=True)
        self.assertEqual(len(c.players()), 2)
        end = time.time() + 2  # the acknowledgement is a UDP packet the fake handles on its own thread
        while not f.acks and time.time() < end:
            time.sleep(0.01)
        self.assertEqual(f.acks, [0])

    def test_nobody_listening(self):
        f, _ = self.fake()
        port = f.port
        f.close()
        with self.assertRaises(BattlEyeError) as e:
            BattlEyeRcon("127.0.0.1", port, "be pass", timeout=0.3, tries=2).command("players")
        self.assertIn("no answer", str(e.exception))

    def test_no_password(self):
        with self.assertRaises(BattlEyeError):
            BattlEyeRcon("127.0.0.1", 9, "").command("players")
        self.assertTrue(battleye.packet(b"").startswith(b"BE"))


if __name__ == "__main__":
    unittest.main()
