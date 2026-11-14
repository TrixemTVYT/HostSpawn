"""Tests run only against fakes (fake rustctl, fake Arma 3 exe, fake steamcmd). They never touch a real server.
Run: python3 -m unittest discover -s tests -t . -v"""
import base64
import json
import os
import shutil
import signal
import socket
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from manager import a2s, util
from manager.adapters import base as base_mod
from manager.adapters.arma3 import Arma3Adapter
from manager.adapters.rust import RustAdapter, first_thursday
from manager.supervisor import Supervisor
from manager.web import make_handler

FAKES = Path(__file__).parent / "fakes"


def free_port() -> int:
    # Arma 3 uses port and port + 1, so find a pair that is free.
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            p = s.getsockname()[1]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.bind(("127.0.0.1", p + 1))
            if p < 65000:
                return p
        except OSError:
            continue


def write_exec(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class Sandbox(unittest.TestCase):
    """Points the manager's state, secrets and events at a temp folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="manager-test-"))
        self._saved = (util.STATE, util.SECRETS_FILE, base_mod.STATE, util.LOGS)
        util.STATE = base_mod.STATE = self.tmp / "state"
        util.LOGS = self.tmp / "logs"
        util.SECRETS_FILE = self.tmp / "secrets.env"
        util.SECRETS_FILE.write_text("MANAGER_PASSWORD=test-pass-123\nARMA3_STEAM_USER=fakeuser\n"
                                     "ARMA3_ADMIN_PASSWORD=adminsecret99\nARMA3_SERVER_PASSWORD=\n")

    def tearDown(self):
        util.STATE, util.SECRETS_FILE, base_mod.STATE, util.LOGS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestA2S(unittest.TestCase):
    def test_query_with_challenge(self):
        port = free_port()
        exe = Path(tempfile.mkdtemp()) / "srv"
        import subprocess
        p = subprocess.Popen(["python3", str(FAKES / "fake_game.py"), f"-port={port}"], env={**os.environ, "FAKE_EXE": str(exe)})
        try:
            for _ in range(50):
                info = a2s.info("127.0.0.1", port + 1, timeout=0.2)
                if info:
                    break
            self.assertEqual(info["players"], 3)
            self.assertEqual(info["max_players"], 32)
            self.assertEqual(info["name"], "Fake Server")
        finally:
            p.kill()

    def test_no_answer(self):
        self.assertIsNone(a2s.info("127.0.0.1", free_port(), timeout=0.3))


class TestUtil(Sandbox):
    def test_redact_hides_secret_values(self):
        util.secrets()
        self.assertEqual(util.redact("login adminsecret99 ok"), "login *** ok")

    def test_clean_command_strips_control_chars(self):
        self.assertEqual(util.clean_command("say hi\r\nquit"), "say hiquit")

    def test_instance_default_is_dev(self):
        saved = util.CONFIG
        util.CONFIG = self.tmp
        try:
            self.assertEqual(util.instance()["mode"], "dev")
            (self.tmp / "instance.env").write_text("MODE=live\n")
            inst = util.instance()
            self.assertEqual((inst["mode"], inst["port"], inst["interval"]), ("live", 8100, 60.0))
        finally:
            util.CONFIG = saved


class TestRust(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "rust"
        (self.root / "logs").mkdir(parents=True)
        (self.root / "config").mkdir()
        self.calls = self.root / "calls.txt"
        write_exec(self.root / "rustctl.sh", f"""#!/usr/bin/env bash
echo "$@" >> "{self.calls}"
case "$1" in
  status) echo "running pid=4242 uptime=1-02:03:04 mem=6.1GB" ;;
  cmd) echo sent ;;
esac
""")
        (self.root / "config/wipe.conf").write_text('AUTO_RESTART_ENABLED=1\nRESTART_TIMES="05:00"\nAUTO_WIPE_ENABLED=1\n'
                                                    'FORCED_WIPE_TIME="20:00"\nBLUEPRINT_WIPE_ON_FORCED=1\n')
        self.a = RustAdapter("rust", {"root": str(self.root), "warn_seconds": 300})

    def wait_calls(self, n):
        for _ in range(50):
            if self.calls.exists() and len(self.calls.read_text().splitlines()) >= n:
                return self.calls.read_text().splitlines()
            time.sleep(0.1)
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_status_parsed(self):
        s = self.a.status()
        self.assertEqual((s["state"], s["pid"], s["uptime"]), ("running", 4242, "1-02:03:04"))
        (self.root / "logs/.update_failed").touch()
        self.assertIn("Last update failed: rustctl retries every 15 min", self.a.status()["flags"])

    def test_actions_call_rustctl_with_countdown(self):
        self.a.restart()
        self.assertIn("restart 300", self.wait_calls(1))

    def test_wipe_kind_validated(self):
        with self.assertRaises(ValueError):
            self.a.wipe("everything")

    def test_send_strips_newlines(self):
        self.assertEqual(self.a.send("say hi\nquit"), "sent")
        self.assertIn("cmd say hiquit", self.calls.read_text())

    def test_schedule_has_forced_wipe_on_first_thursday(self):
        wipe = [e for e in self.a.schedule() if "forced wipe" in e["what"]][0]
        d = wipe["when"][:10]
        y, m, day = map(int, d.split("-"))
        self.assertEqual(first_thursday(y, m).day, day)
        self.assertIn("T20:00", wipe["when"])

    def test_first_thursday(self):
        self.assertEqual(str(first_thursday(2026, 11)), "2026-11-05")
        self.assertEqual(str(first_thursday(2026, 10)), "2026-10-01")


class TestArma3(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "arma3"
        self.server = self.root / "server"
        self.server.mkdir(parents=True)
        self.exe = self.server / "arma3server_x64"
        write_exec(self.exe, f'#!/usr/bin/env bash\nexport FAKE_EXE="$0"\nexec -a "$0" python3 -I "{FAKES / "fake_game.py"}" "$@"\n')
        self.steam_out = self.tmp / "steam_out.txt"
        self.steam_out.write_text("Success! App '233780' fully installed.\n")
        self.steam_calls = self.tmp / "steam_calls.txt"
        write_exec(self.tmp / "steamcmd.sh", f'#!/usr/bin/env bash\necho x >> "{self.steam_calls}"\ncat "{self.steam_out}"\n')
        self.port = free_port()
        self.a = Arma3Adapter("arma3", {"root": str(self.root), "port": self.port, "steamcmd": str(self.tmp / "steamcmd.sh"),
                                        "session": f"manager-test-{os.getpid()}", "max_players": 12, "retry_wait_s": 0})

    def tearDown(self):
        try:
            if self.a.pid():
                os.kill(self.a.pid(), signal.SIGKILL)
        except (ProcessLookupError, TypeError):
            pass
        util.run(["tmux", "kill-session", "-t", "=" + self.a.session])
        super().tearDown()

    def wait(self, cond, secs=10):
        for _ in range(int(secs * 10)):
            if cond():
                return True
            time.sleep(0.1)
        return False

    def test_not_installed(self):
        self.exe.unlink()
        self.assertEqual(self.a.status()["state"], "not_installed")
        self.assertIn("not installed", self.a.start())

    def test_start_health_stop(self):
        self.assertEqual(self.a.start(), "started")
        self.assertTrue(self.wait(lambda: self.a.health()))
        self.assertEqual(self.a.status()["state"], "starting")  # until the watchdog sees an answer
        self.a.watchdog()
        self.assertEqual(self.a.status()["state"], "running")
        cfg = self.server / "manager/server.cfg"
        self.assertEqual(stat.S_IMODE(cfg.stat().st_mode), 0o600)
        text = cfg.read_text()
        self.assertIn('passwordAdmin = "adminsecret99";', text)
        self.assertIn("maxPlayers = 12;", text)
        self.assertEqual(self.a.stop(), "stopped")
        self.assertEqual(self.a.status()["state"], "maintenance")
        self.assertIsNone(self.a.watchdog())  # maintenance: watchdog leaves it alone

    def test_watchdog_restarts_after_crash(self):
        self.a.start()
        self.assertTrue(self.wait(lambda: self.a.health()))
        self.a.watchdog()
        os.kill(self.a.pid(), signal.SIGKILL)
        self.assertTrue(self.wait(lambda: not self.a.pid()))
        self.assertEqual(self.a.watchdog(), "restarted (crash)")
        self.assertTrue(self.wait(lambda: self.a.health()))
        self.assertTrue(any("restarting" in e["message"] for e in util.read_events()))

    def test_crash_loop_updates_then_gives_up(self):
        (self.server / "CRASH").touch()
        self.a.start()
        results = []
        for _ in range(12):  # generous: on a busy machine the fake may take a while to start and crash
            self.wait(lambda: not self.a.pid(), 20)
            results.append(self.a.watchdog())
            if results[-1] == "gave up":
                break
        self.assertEqual(results[-1], "gave up")
        self.assertEqual(results.count("restarted (crash)"), 5)  # gives up at the 6th failed start
        self.assertTrue(self.steam_calls.exists(), "an update should run at 3 failed starts")
        self.assertIn("Crash loop: watchdog stopped trying. Check the log, then press Start", self.a.status()["flags"])
        self.assertIsNone(self.a.watchdog())

    def test_update_retries_then_flags(self):
        self.steam_out.write_text("Error! App '233780' state is 0x202 after update job.\n")
        self.assertEqual(self.a.update(), "failed")
        self.assertEqual(len(self.steam_calls.read_text().splitlines()), 5)
        self.assertTrue(self.a.has("update_failed"))

    def test_steam_guard_is_not_retried(self):
        self.steam_out.write_text("This computer has not been authenticated. Please enter the Steam Guard code:\n")
        self.assertIn("Steam login needed", self.a.update())
        self.assertEqual(len(self.steam_calls.read_text().splitlines()), 1)
        self.assertIn("Steam login needed: press Log in to Steam on this server's panel", self.a.status()["flags"])
        self.assertNotIn("fakeuser", (self.root / "logs/steamcmd_update.log").read_text())

    def test_update_restarts_if_it_was_running(self):
        self.a.start()
        self.assertTrue(self.wait(lambda: self.a.health()))
        self.assertEqual(self.a.update(), "updated")
        self.assertTrue(self.wait(lambda: self.a.health()))

    def test_backup(self):
        self.a.write_configs()
        msg = self.a.backup()
        self.assertIn("backup written", msg)
        b = next((self.root / "backups").glob("arma3_*.tar.gz"))
        self.assertEqual(stat.S_IMODE(b.stat().st_mode), 0o600)


class FakeAdapter(base_mod.Adapter):
    game = "Fake"
    actions = ("start", "stop", "send")
    confirm = frozenset({"stop"})

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.done = []
        self.watched = 0

    def status(self): return {"state": "running", "flags": []}
    def start(self): self.done.append("start"); return "ok"
    def stop(self): self.done.append("stop"); return "ok"
    def send(self, c): return "sent " + c
    def watchdog(self): self.watched += 1


class TestWeb(Sandbox):
    def serve(self, mode, sandbox=False):
        from http.server import ThreadingHTTPServer
        self.fake = FakeAdapter("fake", {"name": "Fake", "sandbox": sandbox})
        sup = Supervisor({"fake": self.fake})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sup, "test-pass-123", mode))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.url = f"http://127.0.0.1:{httpd.server_address[1]}"

    def req(self, path, body=None, auth=True, header=True):
        h = {"Content-Type": "application/json"}
        if auth:
            h["Authorization"] = "Basic " + base64.b64encode(b"admin:test-pass-123").decode()
        if header:
            h["X-Manager"] = "1"
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}") if path != "/" else {}
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}") if e.code != 401 else {}

    def test_auth_header_confirm_and_allowlist(self):
        self.serve("live")
        self.assertEqual(self.req("/api/overview", auth=False)[0], 401)
        self.assertEqual(self.req("/api/action", {"server": "fake", "action": "start"}, header=False)[0], 403)
        self.assertEqual(self.req("/api/action", {"server": "fake", "action": "wipe", "arg": "full"})[0], 400)
        self.assertEqual(self.req("/api/action", {"server": "fake", "action": "stop"})[0], 400)  # no confirm
        self.assertEqual(self.req("/api/action", {"server": "fake", "action": "stop", "confirm": "stop"})[0], 202)
        self.assertEqual(self.req("/api/action", {"server": "fake", "action": "start"})[0], 202)
        time.sleep(0.3)
        self.assertEqual(self.fake.done[:2], ["stop", "start"])
        code, body = self.req("/api/overview")
        self.assertEqual((code, body["mode"], body["servers"][0]["controllable"]), (200, "live", True))

    def test_dev_copy_blocks_real_servers(self):
        self.serve("dev")
        code, body = self.req("/api/action", {"server": "fake", "action": "start"})
        self.assertEqual(code, 403)
        self.assertIn("Development copy", body["error"])
        self.assertFalse(self.req("/api/overview")[1]["servers"][0]["controllable"])
        self.assertEqual(self.fake.done, [])

    def test_dev_copy_allows_sandbox_servers(self):
        self.serve("dev", sandbox=True)
        self.assertEqual(self.req("/api/action", {"server": "fake", "action": "send", "arg": "hi"}), (200, {"message": "sent hi"}))


class TestSupervisor(Sandbox):
    def test_dev_copy_runs_no_watchdog(self):
        a = FakeAdapter("fake", {})
        a.supervised_by = "manager"
        Supervisor({"fake": a}, control=False).tick()
        self.assertEqual(a.watched, 0)
        Supervisor({"fake": a}, control=True).tick()
        self.assertEqual(a.watched, 1)

    def test_down_alert(self):
        a = FakeAdapter("fake", {})
        a.supervised_by = "systemd"
        sup = Supervisor({"fake": a})
        sup.tick()
        a.status = lambda: {"state": "stopped", "flags": []}
        sup.tick()
        self.assertTrue(any(e["level"] == "alert" and "went down" in e["message"] for e in util.read_events()))


if __name__ == "__main__":
    unittest.main()
