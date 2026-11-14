"""Phase 1: rename/community setting, Rust WebRCON, the 4-way health check, backups on by default. Fakes only."""
import time
import unittest

from manager import util
from manager.adapters import base as base_mod
from manager.adapters.arma3 import Arma3Adapter
from manager.adapters.rust import RustAdapter
from manager.rcon import RconError, WebRcon
from manager.supervisor import Supervisor
from tests.fakes.fake_webrcon import FakeWebRcon
from tests.test_manager import FakeAdapter, Sandbox, free_port, write_exec


class TestWebRcon(unittest.TestCase):
    def test_serverinfo(self):
        f = FakeWebRcon(password="pa ss/wörd")
        try:
            info = WebRcon("127.0.0.1", f.port, "pa ss/wörd").serverinfo()
            self.assertEqual((info["Players"], info["Framerate"]), (7, 99))
            self.assertEqual(f.commands, ["serverinfo"])
            self.assertNotIn(" ", f.paths[0])  # password is URL-encoded
        finally:
            f.close()

    def test_skips_other_console_lines_and_bad_json(self):
        f = FakeWebRcon(noise=True)
        try:
            self.assertEqual(WebRcon("127.0.0.1", f.port, "pw").command("status"), "ran status")
        finally:
            f.close()

    def test_fragmented_reply_with_ping(self):
        f = FakeWebRcon(fragment=True)
        try:
            self.assertEqual(WebRcon("127.0.0.1", f.port, "pw").serverinfo()["MaxPlayers"], 50)
        finally:
            f.close()

    def test_wrong_password_is_clear_and_never_leaks(self):
        f = FakeWebRcon(password="right")
        try:
            with self.assertRaises(RconError) as e:
                WebRcon("127.0.0.1", f.port, "wrong-secret-123").serverinfo()
            self.assertIn("wrong password", str(e.exception))
            self.assertNotIn("wrong-secret-123", str(e.exception))
        finally:
            f.close()

    def test_silent_server_times_out(self):
        f = FakeWebRcon(silent=True)
        try:
            t = time.time()
            with self.assertRaises(RconError):
                WebRcon("127.0.0.1", f.port, "pw", timeout=1).serverinfo()
            self.assertLess(time.time() - t, 3)
        finally:
            f.close()

    def test_nothing_listening_and_no_password(self):
        with self.assertRaises(RconError):
            WebRcon("127.0.0.1", free_port(), "pw", timeout=1).serverinfo()
        with self.assertRaises(RconError) as e:
            WebRcon("127.0.0.1", 1, "").serverinfo()
        self.assertIn("no RCON password", str(e.exception))


class TestCommunity(Sandbox):
    def setUp(self):
        super().setUp()
        self._cfg = util.CONFIG
        util.CONFIG = self.tmp

    def tearDown(self):
        util.CONFIG = self._cfg
        super().tearDown()

    def test_default_and_setting(self):
        self.assertEqual(util.community(), "My Community")
        (self.tmp / "settings.toml").write_text('[community]\nname = "Riverside Gaming"\n')
        self.assertEqual(util.community(), "Riverside Gaming")
        (self.tmp / "settings.toml").write_text("this is [not toml")
        self.assertEqual(util.community(), "My Community")

    def test_arma3_defaults_use_community(self):
        (self.tmp / "settings.toml").write_text('[community]\nname = "Night Ops"\n')
        a = Arma3Adapter("arma3", {"root": str(self.tmp / "a3"), "port": 2302})
        a.write_configs()
        text = (self.tmp / "a3/server/manager/server.cfg").read_text()
        self.assertIn('hostname = "Night Ops Arma 3";', text)
        self.assertIn('motd[] = {"Welcome to Night Ops"};', text)
        self.assertEqual(a.session, "manager-arma3")
        self.assertIn("BattlEye = 1;", text)  # on by default
        Arma3Adapter("arma3", {"root": str(self.tmp / "a3"), "port": 2302, "battleye": False}).write_configs()
        self.assertIn("BattlEye = 0;", (self.tmp / "a3/server/manager/server.cfg").read_text())


class TestRustRcon(Sandbox):
    def test_rcon_check_reads_rust_secret_and_fills_status(self):
        f = FakeWebRcon(password="rust-rcon-pw")
        try:
            root = self.tmp / "rust"
            (root / "config").mkdir(parents=True)
            (root / "logs").mkdir()
            (root / "config/secrets.env").write_text("RCON_PASSWORD=rust-rcon-pw\n")
            write_exec(root / "rustctl.sh", '#!/usr/bin/env bash\n[ "$1" = status ] && echo "running pid=1 uptime=1:00 mem=1.0GB"\n')
            a = RustAdapter("rust", {"root": str(root), "rcon_port": f.port})
            self.assertEqual(a.rcon_check()["players"], 7)
            snap = Supervisor({"rust": a}, config={}).check("rust")
            self.assertTrue(snap["checks"]["rcon"]["ok"])
            self.assertEqual((snap["status"]["players"], snap["status"]["fps"]), (7, 99))  # from RCON
            self.assertTrue(snap["checks"]["process"])
            self.assertIsNone(RustAdapter("rust", {"root": str(root)}).rcon_check())  # not set up
        finally:
            f.close()


class Health(FakeAdapter):
    actions = ("start", "stop", "send", "backup")

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.rcon = {"ok": True}
        self.age = 5
        self.backups = 0
        self.newest = time.time()

    def rcon_check(self): return self.rcon
    def log_age(self): return self.age
    def last_backup(self): return self.newest
    def backup(self):
        self.backups += 1
        return "ok"


class TestHealthAlerts(Sandbox):
    def events(self):
        return [e["message"] for e in util.read_events()]

    def test_rcon_alert_after_three_and_recovery(self):
        a = Health("srv", {})
        sup = Supervisor({"srv": a}, config={})
        sup.tick()
        a.rcon = {"ok": False, "error": "refused"}
        for _ in range(5):
            sup.tick()
        self.assertEqual(self.events().count("RCON not answering for 3 min (refused)"), 1)
        a.rcon = {"ok": True}
        sup.tick()
        self.assertIn("RCON answering again", self.events())

    def test_quiet_log_alert_once(self):
        a = Health("srv", {})
        a.log_alert_s = 600
        sup = Supervisor({"srv": a}, config={})
        a.age = 700
        sup.tick(); sup.tick()
        self.assertEqual(sum("console log hasn't changed" in m for m in self.events()), 1)
        a.age = 10
        sup.tick()
        a.age = 900
        sup.tick()
        self.assertEqual(sum("console log hasn't changed" in m for m in self.events()), 2)

    def test_server_deleted_during_a_round_is_not_reported_as_an_error(self):
        # Delete in HostSpawn removes the server from the supervisor while a round of checks may be running.
        sup = None

        class DeletesOther(Health):
            def rcon_check(self):
                sup.adapters.pop("b", None)  # "b" is deleted before its turn
                sup.adapters.pop("a", None)  # and "a" halfway through its own check
                return {"ok": True}

        a, b, c = DeletesOther("a", {}), Health("b", {}), Health("c", {})
        sup = Supervisor({"a": a, "b": b, "c": c}, config={})
        sup.tick()
        self.assertFalse([m for m in self.events() if "error" in m])
        self.assertIn("c", sup.snapshot)  # the servers that are left are still checked
        self.assertNotIn("b", sup.snapshot)

    def test_old_backup_warns_once_a_day(self):
        a = Health("srv", {})
        a.newest = time.time() - 48 * 3600
        sup = Supervisor({"srv": a}, config={"backups": {"max_age_hours": 36}})
        sup.tick(); sup.tick()
        self.assertEqual(self.events().count("newest backup is 48 h old"), 1)
        a.newest = None
        Supervisor({"srv": a}, config={}).tick()  # same day: still only one warning
        self.assertEqual(sum("backup" in m for m in self.events()), 1)

    def test_backups_can_be_turned_off(self):
        a = Health("srv", {})
        a.newest = None
        Supervisor({"srv": a}, config={"backups": {"enabled": False}}).tick()
        self.assertFalse(any("backup" in m for m in self.events()))

    def test_daily_backup_runs_once_and_not_in_dev(self):
        a = Health("srv", {})
        now = f"{util.uk_now():%H:%M}"
        Supervisor({"srv": a}, control=False, config={"backups": {"time": now}}).tick()
        time.sleep(0.2)
        self.assertEqual(a.backups, 0)  # dev copy: never
        sup = Supervisor({"srv": a}, config={"backups": {"time": now}})
        sup.tick(); sup.tick()
        Supervisor({"srv": a}, config={"backups": {"time": now}}).tick()  # manager restarted: still once
        time.sleep(0.3)
        self.assertEqual(a.backups, 1)
        self.assertIn("daily backup: ok", self.events())

    def test_snapshot_has_all_checks(self):
        a = Health("srv", {"query_port": free_port()})
        ck = Supervisor({"srv": a}, config={}).check("srv")["checks"]
        self.assertEqual(set(ck), {"process", "query", "rcon", "log_age_s", "last_backup"})
        self.assertEqual((ck["process"], ck["query"], ck["rcon"]["ok"]), (True, False, True))


if __name__ == "__main__":
    unittest.main()


class TestRestartWarnings(Sandbox):
    def test_rust_counts_down_over_rcon_then_supervisor_updates(self):
        from manager.adapters import base as b, rust_server
        f = FakeWebRcon(password="pw")
        try:
            util.set_secret("SRV_X_RCON", "pw")
            a = rust_server.RustServer("x", {"root": str(self.tmp / "r"), "rcon_port": f.port, "rcon_password_key": "SRV_X_RCON",
                                             "managed": True})
            calls = []
            a.update = lambda: calls.append("update")
            real_sleep, b._sleep = b._sleep, lambda s_: None  # don't really wait a minute
            try:
                Supervisor._restart_with_update(a, 60)  # the scheduler starts it 60 s early so the restart is on time
            finally:
                b._sleep = real_sleep
            self.assertEqual(calls, ["update"])
            says = [c for c in f.commands if c.startswith("say ")]
            self.assertEqual(len(says), 3)  # 60 s, 30 s, 10 s
            self.assertIn("1 minute", says[0])
        finally:
            f.close()
