"""Server Doctor: a sample log line for every rule gives the right finding; a clean server gives "all good".
Everything is made up: no real server, machine readings or ports are used."""
import socket
import unittest

from manager import doctor

BIG = {"os": "Linux", "arch": "x86_64", "cores": 8, "ram_total_gb": 32.0, "ram_free_gb": 28.0, "swap_gb": 4.0,
       "disk_free_gb": 200.0, "disk_total_gb": 500.0}

# One real-looking line per rule: (rule id, game, log name, line, text the title must contain).
SAMPLES = [
    ("port_in_use", "rust", "console", "Couldn't bind to 0.0.0.0:28015: Address already in use", "port"),
    ("disk_full_log", "reforger", "console", "write failed: No space left on device", "disk is full"),
    ("out_of_memory", "arma3", "console", "Killed", "memory"),
    ("missing_library", "arma3", "console",
     "./arma3server_x64: error while loading shared libraries: libtbb.so.2: cannot open shared object file", "libtbb.so.2"),
    ("permission_denied", "rust", "console", "open('server/cfg/server.cfg'): Permission denied", "allowed"),
    ("segfault", "reforger", "console", "Segmentation fault (core dumped)", "crashed"),
    ("steam_login", "arma3", "steamcmd", "Logging in user 'x' to Steam Public...FAILED (Invalid Password)", "log in"),
    ("steam_no_sub", "arma3", "steamcmd", "ERROR! Failed to install app '233780' (No subscription)", "can't download"),
    ("steam_disk", "rust", "steamcmd", "Error! App '258550' state is 0x202 after update job.", "disk space"),
    ("steam_failed", "rust", "steamcmd", "ERROR! Failed to install app '258550' (Timeout)", "Timeout"),
    ("rust_plugin_compile", "rust", "console", "Error while compiling: Kits.cs(12,5): error CS1002", "Kits"),
    ("rust_plugin_load", "rust", "console", "Failed to initialize plugin 'BetterChat v5.2.1'", "BetterChat"),
    ("rust_rcon", "rust", "console", "RCON Error: couldn't start listener on port 28016", "RCON"),
    ("rust_map", "rust", "console", "World generation failed: out of range", "map"),
    ("reforger_mod_download", "reforger", "console", "RESOURCES (E): Addon 5965550F24A0C152 download failed", "mod"),
    ("reforger_config", "reforger", "console", "BACKEND (E): JSON is invalid at line 14", "config.json"),
    ("reforger_scenario", "reforger", "console", "Scenario {ECC61978EDCC2B5A} not found", "scenario"),
    ("reforger_backend", "reforger", "console", "BACKEND (E): Backend not available, retrying", "online service"),
    ("arma3_be_kick", "arma3", "console", "Player cc kicked off by BattlEye: Game restart required",
     "game restart required"),
    ("arma3_spyglass", "arma3", "console", '"SPYGLASS-FLAG: cc : 1 : [""A3_Data_F_AoW_Loadorder""]"', "spyglass"),
    ("arma3_extdb","arma3", "console", "extDB3: Failed to Connect to Database: Access denied for user", "database"),
    ("arma3_mission", "arma3", "console", "Mission Altis_Life.Altis not found", "mission"),
    ("arma3_addon", "arma3", "console", "Warning Message: Addon 'life_server' requires addon 'A3_Data_F'",
     "life_server"),
    ("arma3_mod_case", "arma3", "console", "Cannot open @ExtDB3/extDB3_x64.so", "@ExtDB3"),
    ("arma3_battleye", "arma3", "console", "Failed to initialize BattlEye Server", "BattlEye"),
]


NAMES = {"rust": "Rust", "reforger": "Arma Reforger", "arma3": "Arma 3"}  # what the real adapters call their game


class FakeServer:
    def __init__(self, game, logs=None, cfg=None, sid="s1"):
        self.id, self.game, self._logs, self.cfg = sid, NAMES[game], logs or {}, cfg or {}

    def logs(self):
        return self._logs


def run(a, snap=None, **k):
    k.setdefault("machine", BIG)
    k.setdefault("events", [])
    k.setdefault("busy", lambda port, udp: False)
    return doctor.diagnose(a, snap or {"status": {"state": "running", "flags": []}}, **k)


class TestRules(unittest.TestCase):
    def test_every_rule_has_a_sample(self):
        self.assertEqual({r["id"] for r in doctor.RULES}, {s[0] for s in SAMPLES})

    def test_each_sample_gives_its_finding(self):
        for rid, game, log, line, words in SAMPLES:
            with self.subTest(rid):
                flags = ["Last update failed"] if log == "steamcmd" else []  # steam logs only count while failing
                a = FakeServer(game, {log: "Server starting\n" + line + "\nsome later line"})
                r = run(a, {"status": {"state": "running", "flags": flags}})
                ids = [f["id"] for f in r["findings"]]
                self.assertIn(rid, ids)
                f = next(f for f in r["findings"] if f["id"] == rid)
                self.assertIn(words.lower(), f["title"].lower(), f)
                self.assertNotIn("{m}", f["title"] + f["fix"])
                self.assertIn(line.strip()[:40], f["where"])

    def test_a_clean_server_is_all_good(self):
        a = FakeServer("rust", {"console": "Server startup complete\nSaved 1234 ents"}, {"port": 28015})
        r = run(a)
        self.assertEqual(r["findings"], [])
        self.assertTrue(r["ok"])
        self.assertGreater(r["checked"], 10)

    def test_rules_only_apply_to_their_game(self):
        a = FakeServer("reforger", {"console": "Error while compiling: Kits.cs"})
        self.assertEqual(run(a)["findings"], [])

    def test_old_steam_log_is_ignored_while_updates_work(self):
        a = FakeServer("arma3", {"steamcmd": "FAILED (Invalid Password)"})
        self.assertEqual(run(a)["findings"], [])

    def test_spyglass_finding_goes_once_fixed_or_paused(self):
        line = '"SPYGLASS-FLAG: cc : 1 : [""A3_New_F""]"'
        for st, shown in (({"on": True, "official": ["A3_New_F"]}, True), ({"on": True, "official": []}, False),
                          ({"on": False, "official": ["A3_New_F"]}, False)):
            a = FakeServer("arma3", {"console": line})
            a.spyglass_status = lambda st=st: st
            self.assertEqual("arma3_spyglass" in [f["id"] for f in run(a)["findings"]], shown, st)


class TestState(unittest.TestCase):
    def test_missing_game_offers_repair_first(self):
        r = run(FakeServer("rust"), {"status": {"state": "not_installed", "flags": []}})
        self.assertEqual(r["findings"][0]["id"], "not_installed")
        self.assertEqual(r["findings"][0]["action"], {"kind": "install", "op": "repair", "label": "Repair"})
        self.assertFalse(r["ok"])

    def test_crash_loop_offers_start(self):
        flags = ["Crash loop: watchdog stopped trying. Check the log, then press Start"]
        r = run(FakeServer("arma3"), {"status": {"state": "maintenance", "flags": flags}})
        self.assertEqual(r["findings"][0]["id"], "crash_loop")

    def test_many_restarts_in_an_hour(self):
        import time
        evs = [{"t": time.time() - 60 * i, "server": "s1", "message": "server not running - restarting"}
               for i in range(4)]
        evs.append({"t": time.time(), "server": "other", "message": "server not running - restarting"})
        r = run(FakeServer("rust"), events=evs)
        self.assertEqual(r["findings"][0]["title"], "Restarted 4 times in the last hour")

    def test_taken_port_is_named_and_replaces_the_log_finding(self):
        a = FakeServer("rust", {"console": "Address already in use"}, {"port": 28015, "rcon_port": 28016})
        r = run(a, {"status": {"state": "stopped", "flags": []}}, busy=lambda p, udp: p == 28015)
        ids = [f["id"] for f in r["findings"]]
        self.assertEqual(ids, ["port_taken"])
        self.assertIn("28015", r["findings"][0]["title"])

    def test_ports_are_not_checked_while_running(self):
        a = FakeServer("rust", {}, {"port": 28015})
        r = run(a, busy=lambda p, udp: True)  # our own running server holds the port: that's fine
        self.assertEqual(r["findings"], [])

    def test_low_disk_and_memory(self):
        r = run(FakeServer("rust"), machine={**BIG, "disk_free_gb": 0.5, "ram_free_gb": 0.4})
        ids = {f["id"]: f["level"] for f in r["findings"]}
        self.assertEqual(ids, {"disk_low": "problem", "ram_low": "warning"})

    def test_problems_come_before_warnings(self):
        a = FakeServer("rust", {"console": "Error while compiling: Kits.cs\nNo space left on device"})
        levels = [f["level"] for f in run(a)["findings"]]
        self.assertEqual(levels, sorted(levels, key=["problem", "warning", "tip"].index))

    def test_real_port_check(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", 0))
        s.listen()
        try:
            self.assertTrue(doctor.port_busy(s.getsockname()[1], udp=False))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
