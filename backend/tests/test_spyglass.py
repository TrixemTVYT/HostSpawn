"""Spyglass: flagged official parts are added to a made-up mission's allowed list; anything else is only listed.
Everything happens in a temp folder: no real server or mission is touched."""
import tempfile
import unittest
from pathlib import Path

from manager import pbo, spyglass
from manager import util
from tests.test_manager import Sandbox

SCRIPT = 'private _patchList = [\n    "A3_Data_F","life_server"\n];\nif (true) then {};\n'
LOG = ('10:14:27 "SPYGLASS-FLAG: cc : 1 : [""A3_Data_F_AoW_Loadorder"",""A3_Data_F"",""cheat_menu""]"\n'
       '10:15:00 "SPYGLASS-FLAG: cc : 1 : [""A3_Data_F_AoW_Loadorder"",""A3_Props_F_AoW""]"\n')


def rapified(patches: list[str]) -> bytes:
    """A tiny binarized config.bin like Arma's: a value, an array, CfgVehicles, then CfgPatches with the given classes."""
    import struct
    z = lambda t: t.encode() + b"\0"
    empty = z("") + b"\0"  # a class body: no parent, 0 entries

    def body(entries):  # entries: list of bytes, with class offsets filled in later
        return z("") + bytes([len(entries)]) + b"".join(entries)
    # layout: header(16) root, then CfgVehicles body, CfgPatches body, then each patch body
    root_entries_fixed = [b"\x01\x00" + z("version") + z("1.0"),
                          b"\x02" + z("list") + b"\x02" + b"\x00" + z("a") + b"\x02" + struct.pack("<i", 5)]
    root_len = len(body(root_entries_fixed + [b"\0" + z("CfgVehicles") + b"xxxx", b"\0" + z("CfgPatches") + b"xxxx"]))
    veh_at = 16 + root_len
    patch_at = veh_at + len(empty)
    patch_body_len = len(body([b"\0" + z(n) + b"xxxx" for n in patches]))
    first = patch_at + patch_body_len
    patch_entries = [b"\0" + z(n) + struct.pack("<I", first + i * len(empty)) for i, n in enumerate(patches)]
    root = body(root_entries_fixed + [b"\0" + z("CfgVehicles") + struct.pack("<I", veh_at),
                                      b"\0" + z("CfgPatches") + struct.pack("<I", patch_at)])
    data = root + empty + body(patch_entries) + empty * len(patches)
    return b"\0raP" + struct.pack("<III", 0, 8, 16 + len(data)) + data + b"\0" * 4


class FakeArma:
    def __init__(self, root: Path, spy=True):
        self.root, self.server_dir = root, root / "server"
        self.cfg = {"mission": "Altis_Life.Tanoa"}
        src = root / "src"
        (src / "SpyGlass").mkdir(parents=True)
        (src / "description.ext").write_text("x")
        (src / "config").mkdir()
        (src / "config" / "Config_Master.hpp").write_text(
            "class Life_Settings {\n    spyGlass_toggle = true; //Spyglass On/Off Toggle\n    other = 1;\n};\n")
        if spy:
            (src / "SpyGlass" / "fn_initSpy.sqf").write_text(SCRIPT)
        (self.server_dir / "mpmissions").mkdir(parents=True)
        pbo.pack(src, self.server_dir / "mpmissions" / "Altis_Life.Tanoa.pbo")

    def master(self):
        files = pbo.read(self.server_dir / "mpmissions" / "Altis_Life.Tanoa.pbo")[1]
        return next(v for k, v in files.items() if k.replace("\\", "/") == "config/Config_Master.hpp").decode()

    def script(self):
        files = pbo.read(self.server_dir / "mpmissions" / "Altis_Life.Tanoa.pbo")[1]
        return next(v for k, v in files.items() if k.replace("\\", "/") == "SpyGlass/fn_initSpy.sqf").decode()


class TestSpyglass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reported_reads_doubled_quotes_without_repeats(self):
        self.assertEqual(spyglass.reported(LOG),
                         ["A3_Data_F_AoW_Loadorder", "A3_Data_F", "cheat_menu", "A3_Props_F_AoW"])

    def test_allow_adds_only_new_official_parts_and_backs_up(self):
        a = FakeArma(self.root)
        msg = spyglass.allow(a, LOG)
        self.assertIn("2 more", msg)
        self.assertIn("cheat_menu", msg)  # listed for the owner, not added
        allowed = spyglass.allowed(a.script())
        self.assertTrue({"A3_Data_F_AoW_Loadorder", "A3_Props_F_AoW", "A3_Data_F", "life_server"} <= allowed)
        self.assertNotIn("cheat_menu", allowed)
        self.assertEqual(a.script().count('"A3_Data_F"'), 1)
        self.assertIn("if (true) then {};", a.script())  # the rest of the script is untouched
        self.assertEqual(len(list((self.root / "backups" / "missions").glob("*.pbo"))), 1)

    def test_second_run_changes_nothing(self):
        a = FakeArma(self.root)
        spyglass.allow(a, LOG)
        self.assertIn("already allows", spyglass.allow(a, LOG))
        self.assertEqual(len(list((self.root / "backups" / "missions").glob("*.pbo"))), 1)

    def test_nothing_flagged(self):
        a = FakeArma(self.root)
        before = (a.server_dir / "mpmissions" / "Altis_Life.Tanoa.pbo").read_bytes()
        self.assertIn("nothing to change", spyglass.allow(a, "Server started\n"))
        self.assertEqual(before, (a.server_dir / "mpmissions" / "Altis_Life.Tanoa.pbo").read_bytes())

    def test_has_spyglass(self):
        self.assertTrue(spyglass.has_spyglass(FakeArma(self.root / "a")))
        self.assertFalse(spyglass.has_spyglass(FakeArma(self.root / "b", spy=False)))

    def test_official_parts_come_from_the_servers_game_files(self):
        a = FakeArma(self.root)
        for folder, sub, names in (("addons", "", ["A3_Data_F", "A3_Props_F_New"]),
                                   ("aow/addons", "a3/props_f_aow/military/", ["A3_Props_F_AoW", "NotOfficial"])):
            src = self.root / "addon_src" / folder
            (src / sub).mkdir(parents=True, exist_ok=True)
            (src / sub / "config.bin").write_bytes(rapified(names))
            (src / "model.p3d").write_bytes(b"x" * 10)
            (a.server_dir / folder).mkdir(parents=True, exist_ok=True)
            pbo.pack(src, a.server_dir / folder / "thing.pbo")
        self.assertEqual(spyglass.official_parts(a.server_dir), {"A3_Data_F", "A3_Props_F_New", "A3_Props_F_AoW"})
        self.assertEqual(spyglass.status(a, "")["missing"], 2)  # A3_Data_F is already allowed
        msg = spyglass.allow(a, "")  # nobody thrown out yet: still fixes everything at once
        self.assertIn("2 more official", msg)
        self.assertTrue({"A3_Props_F_New", "A3_Props_F_AoW"} <= spyglass.allowed(a.script()))
        self.assertNotIn("NotOfficial", spyglass.allowed(a.script()))
        self.assertEqual(spyglass.status(a, "")["missing"], 0)

    def test_bad_names_are_ignored(self):
        self.assertEqual(spyglass.reported('SPYGLASS-FLAG: x : 1 : ["A3_ok","bad\\"];hack","A3 space"]'), ["A3_ok"])

    def test_turn_off_and_on_uses_the_framework_switch(self):
        a = FakeArma(self.root)
        self.assertIn("now OFF", spyglass.set_on(a, False))
        self.assertIn("spyGlass_toggle = false; //Spyglass On/Off Toggle", a.master())
        self.assertIn("other = 1;", a.master())
        self.assertIn("already off", spyglass.set_on(a, False))
        self.assertIn("now ON", spyglass.set_on(a, True))
        self.assertIn("spyGlass_toggle = true;", a.master())
        self.assertEqual(len(list((self.root / "backups" / "missions").glob("*.pbo"))), 2)
        self.assertEqual(a.script(), SCRIPT)  # the allowed list is untouched

    def test_no_spyglass_means_nothing_changes(self):
        a = FakeArma(self.root, spy=False)
        self.assertIn("no Spyglass", spyglass.set_on(a, False))
        self.assertIn("no Spyglass", spyglass.allow(a, LOG))

    def test_status(self):
        a = FakeArma(self.root)
        s = spyglass.status(a, LOG)
        self.assertEqual((s["present"], s["on"], s["allowed"]), (True, True, 2))
        self.assertEqual(s["official"], ["A3_Data_F_AoW_Loadorder", "A3_Props_F_AoW"])  # A3_Data_F is already allowed
        self.assertEqual(s["other"], ["cheat_menu"])
        self.assertEqual([(f["time"], f["player"]) for f in s["flags"]], [("10:14:27", "cc"), ("10:15:00", "cc")])
        spyglass.allow(a, LOG)
        spyglass.set_on(a, False)
        s = spyglass.status(a, LOG)
        self.assertEqual((s["on"], s["official"]), (False, []))
        self.assertFalse(spyglass.status(FakeArma(self.root / "b", spy=False))["present"])


class TestSpyglassApi(Sandbox):
    """GET /api/spyglass is for admins; turning it off goes through the normal confirmed action. All in a temp folder,
    with state and events in the sandbox (never the real event list)."""

    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer
        from manager import accounts as A
        from manager.adapters.arma3 import Arma3Adapter
        from manager.supervisor import Supervisor
        from manager.web import make_handler
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name) / "inst"
        self.fake = FakeArma(root)
        self.a = Arma3Adapter("sg-test", {"root": str(root), "managed": True, "mission": "Altis_Life.Tanoa"})
        self.a.log_dir.mkdir(parents=True, exist_ok=True)
        (self.a.log_dir / "arma3_2026-11-08_1000.log").write_text("Server started\n" + LOG)
        self.acc = A.Accounts(Path(self.td.name) / "accounts.json", Path(self.td.name) / "sessions.json")
        self.acc.setup_owner("owner", "correct horse battery staple")
        self.acc.create("mod", "quiet river lantern stone", "moderator", by="owner")
        sup = Supervisor({"sg-test": self.a}, config={})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sup, "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = {"owner": self.acc.login("owner", "correct horse battery staple", device="t"),
                    "mod": self.acc.login("mod", "quiet river lantern stone", device="t")}

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.td.cleanup()
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

    def test_status_admins_only_and_turn_off(self):
        import time
        self.assertEqual(self.call("spyglass?server=sg-test", who="mod")[0], 403)
        code, d = self.call("spyglass?server=sg-test")
        self.assertEqual((code, d["on"], d["official"]), (200, True, ["A3_Data_F_AoW_Loadorder", "A3_Props_F_AoW"]))
        self.assertIn("spyglass_off", self.a.actions)
        self.assertEqual(self.call("action", {"server": "sg-test", "action": "spyglass_off"})[0], 400)  # not confirmed
        self.assertEqual(self.call("action", {"server": "sg-test", "action": "spyglass_off"}, who="mod")[0], 403)
        code, _ = self.call("action", {"server": "sg-test", "action": "spyglass_off", "confirm": "spyglass_off"})
        self.assertEqual(code, 202)
        for _ in range(50):
            if "spyGlass_toggle = false;" in self.fake.master():
                break
            time.sleep(0.1)
        self.assertIn("spyGlass_toggle = false;", self.fake.master())
        self.assertFalse(self.call("spyglass?server=sg-test")[1]["on"])
        self.assertTrue(any(e["server"] == "sg-test" for e in util.read_events(50)))  # logged in the sandbox

    def test_change_restarts_a_running_instance_and_waits_for_a_stopped_one(self):
        from unittest import mock
        calls = []
        with mock.patch.object(type(self.a), "pid", return_value=None):
            msg = self.a.spyglass_off()
        self.assertIn("applies the next time the instance starts", msg)
        self.assertNotIn("Restart the instance", msg)
        with mock.patch.object(type(self.a), "pid", return_value=12345), \
                mock.patch.object(type(self.a), "_stop", side_effect=lambda **k: calls.append("stop")), \
                mock.patch.object(type(self.a), "_start", side_effect=lambda: calls.append("start") or "started"):
            msg = self.a.spyglass_on()
            self.assertIn("restarted to apply it (started)", msg)
            self.assertEqual(calls, ["stop", "start"])
            self.assertEqual(self.a.spyglass_on(), "Spyglass is already on.")  # no change: no restart
            self.assertEqual(calls, ["stop", "start"])
            msg = self.a.spyglass()
            self.assertIn("2 more", msg)
            self.assertEqual(calls, ["stop", "start", "stop", "start"])


if __name__ == "__main__":
    unittest.main()
