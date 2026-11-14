"""Installation management against a fake game and a fake SteamCMD (never Steam, never a real server).
The fake SteamCMD reads files in a control folder: mode (ok / fail / login / diskfull), build (the build id it
installs and reports as newest), slow (seconds to take), broken (the update writes a bad setting, so the game won't
start until settings are put back) and crash (the update breaks the game files themselves)."""
import json
import os
import shutil
import signal
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from manager import accounts, install, util
from manager.adapters.managed import ManagedServer
from manager.install import KEEP_DIR, InstallError, InstallManager, parse_latest_build
from tests.test_manager import FAKES, Sandbox, free_port, write_exec


class FakeInstalled(ManagedServer):
    game, slug, app_id, exe_name = "Fake Game", "fake", "999", "game"

    def launch_args(self):
        return [f"-port={self.cfg['port']}"]

    def query_port(self):
        return self.cfg["port"] + 1

    def backup_paths(self):
        return [self.server_dir / "saves", self.server_dir / "settings"]


class InstallSandbox(Sandbox):
    def setUp(self):
        super().setUp()
        self.ctl = self.tmp / "steamctl"
        self.ctl.mkdir()
        (self.ctl / "build").write_text("100")
        self.root = self.tmp / "inst"
        steamcmd = self.tmp / "steamcmd.sh"
        # the game program refuses to start while settings/BAD exists, or when CRASH sits next to it
        game = (f'#!/usr/bin/env bash\n[ -f "$(dirname "$0")/settings/BAD" ] && exit 1\n'
                f'export FAKE_EXE="$0"\nexec -a "$0" python3 -I "{FAKES / "fake_game.py"}" "$@"\n')
        (self.tmp / "game.sh").write_text(game)
        write_exec(steamcmd, f'''#!/usr/bin/env bash
C="{self.ctl}"
dir=""; app=""; info=""
while [ $# -gt 0 ]; do case "$1" in +force_install_dir) dir=$2;; +app_update) app=$2;; +app_info_print) info=$2;; esac; shift; done
echo x >> "$C/calls"
mode=$(cat "$C/mode" 2>/dev/null || echo ok); build=$(cat "$C/build")
if [ "$mode" = login ]; then echo "Steam Guard code:"; exit 5; fi
if [ -n "$info" ]; then
  printf '"%s"\\n{{\\n "depots"\\n {{\\n  "branches"\\n  {{\\n   "public"\\n   {{\\n    "buildid"\\t\\t"%s"\\n   }}\\n  }}\\n }}\\n}}\\n' "$info" "$build"; exit 0
fi
case "$mode" in
  fail) echo "Error! App '$app' state is 0x602 after update job."; exit 8 ;;
  diskfull) echo "Error! App '$app' state is 0x202 after update job. Not enough disk space"; exit 8 ;;
esac
[ -f "$C/slow" ] && sleep "$(cat "$C/slow")"
mkdir -p "$dir/steamapps"
cp "{self.tmp / "game.sh"}" "$dir/game"; chmod +x "$dir/game"
printf '"AppState"\\n{{\\n "appid" "%s"\\n "buildid" "%s"\\n "LastUpdated" "1700000000"\\n}}\\n' "$app" "$build" > "$dir/steamapps/appmanifest_$app.acf"
[ -f "$C/broken" ] && mkdir -p "$dir/settings" && touch "$dir/settings/BAD"
[ -f "$C/crash" ] && touch "$dir/CRASH"
echo "Success! App '$app' fully installed."
''')
        self.port = free_port()
        self.a = FakeInstalled("f", {"name": "Fake One", "root": str(self.root), "port": self.port,
                                     "steamcmd": str(steamcmd), "session": f"manager-test-install-{os.getpid()}",
                                     "retry_wait_s": 0, "install_tries": 2, "install_check_s": 15, "keep_backups": 3})
        # installed, with a save and a setting
        assert self.a.steam_update()[0]
        (self.a.server_dir / "saves").mkdir()
        (self.a.server_dir / "saves" / "world.sav").write_text("my world")
        (self.a.server_dir / "settings").mkdir(exist_ok=True)
        (self.a.server_dir / "settings" / "server.cfg").write_text("hostname=Fake One")
        (self.ctl / "calls").unlink()
        self.m = InstallManager(SimpleNamespace(adapters={"f": self.a}))

    def tearDown(self):
        try:
            if self.a.pid():
                os.kill(self.a.pid(), signal.SIGKILL)
        except (ProcessLookupError, TypeError):
            pass
        util.run(["tmux", "kill-session", "-t", "=" + self.a.session])
        super().tearDown()

    def calls(self) -> int:
        try:
            return len((self.ctl / "calls").read_text().splitlines())
        except OSError:
            return 0

    def running(self, secs=15) -> bool:
        end = time.time() + secs
        while time.time() < end:
            if self.a.health():
                return True
            time.sleep(0.2)
        return False

    def start_game(self):
        self.a.start()
        self.assertTrue(self.running())

    def run_job(self, op, secs=60, **k) -> dict:
        self.m.start("f", op, by="tester", **k)
        end = time.time() + secs
        while time.time() < end and self.m.jobs["f"]["state"] == "running":
            time.sleep(0.1)
        return self.m.jobs["f"]

    def events(self):
        return [e["message"] for e in util.read_events(200)]


class TestInfoAndUpdateCheck(InstallSandbox):
    def test_info_sizes_version_and_check(self):
        i = self.m.info("f")
        self.assertEqual((i["installed"], i["build"], i["update_available"]), (True, "100", None))
        self.assertEqual(set(i["sizes"]), {"game", "saves", "logs", "backups", "total"})
        self.assertEqual(i["sizes"]["saves"], len("my world") + len("hostname=Fake One"))  # bytes
        self.assertEqual(i["sizes"]["total"], sum(v for k, v in i["sizes"].items() if k != "total"))
        self.m.check_updates(force=True)
        self.assertTrue(self.wait_for(lambda: self.m.update_available("f") is not None))
        self.assertFalse(self.m.update_available("f"))  # Steam's newest = what's installed
        (self.ctl / "build").write_text("101")
        self.m.check_updates(force=True)
        self.assertTrue(self.wait_for(lambda: self.m.update_available("f")))
        self.assertIn("a game update is available (Installation tab)", self.events())

    def test_check_needing_login_is_reported_not_crashed(self):
        (self.ctl / "mode").write_text("login")
        self.m.check_updates(force=True)
        self.assertTrue(self.wait_for(lambda: "error" in self.m._builds().get("999", {})))
        self.assertIn("login needed", self.m.info("f")["check_error"])

    def test_parse_latest_build(self):
        self.assertEqual(parse_latest_build('"branches" { "public" { "buildid" "123" "timeupdated" "1" } }'), "123")
        self.assertIsNone(parse_latest_build("nothing useful"))

    def wait_for(self, f, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if f():
                return True
            time.sleep(0.1)
        return False


class TestSafeUpdate(InstallSandbox):
    def test_update_backs_up_downloads_restarts_and_checks(self):
        self.start_game()
        (self.ctl / "build").write_text("101")
        job = self.run_job("update")
        self.assertEqual(job["result"], "ok", job)
        self.assertIn("version 100 → 101", job["summary"])
        self.assertTrue(self.running())
        self.assertEqual(len(self.a.backup_files()), 1)  # the safety backup
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")
        h = self.m.history(self.a)[0]
        self.assertEqual((h["op"], h["result"], h["by"]), ("update", "ok", "tester"))
        self.assertFalse(self.a.flag("install_job.json").exists())

    def test_stopped_instance_stays_stopped(self):
        job = self.run_job("update")
        self.assertEqual(job["result"], "ok")
        self.assertIn("wasn't started", job["summary"])
        self.assertIsNone(self.a.pid())

    def test_update_that_breaks_settings_is_put_back(self):
        self.start_game()
        (self.ctl / "broken").write_text("")
        job = self.run_job("update", secs=90)
        self.assertEqual(job["result"], "failed")
        self.assertIn("put back from the safety backup", job["summary"])
        self.assertIn("It's running again", job["summary"])
        self.assertFalse((self.a.server_dir / "settings" / "BAD").exists())
        self.assertEqual((self.a.server_dir / "settings" / "server.cfg").read_text(), "hostname=Fake One")
        self.assertTrue(self.running())
        self.assertTrue(any(m.startswith("update failed") for m in self.events()))

    def test_update_that_breaks_the_game_says_so(self):
        self.start_game()
        (self.ctl / "crash").write_text("")
        job = self.run_job("update", secs=90)
        self.assertEqual(job["result"], "failed")
        self.assertIn("still won't start", job["summary"])
        self.assertIn("Steam only keeps the newest game files", job["summary"])
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")

    def test_steam_failing_is_retried_then_server_started_again(self):
        self.start_game()
        (self.ctl / "mode").write_text("fail")
        job = self.run_job("update")
        self.assertEqual(job["result"], "failed")
        self.assertIn("couldn't finish after 2 tries", job["summary"])
        self.assertEqual(self.calls(), 2)
        self.assertTrue(self.running())  # back up on the files it had

    def test_steam_guard_is_not_retried(self):
        (self.ctl / "mode").write_text("login")
        job = self.run_job("update")
        self.assertIn("log in again", job["summary"])
        self.assertEqual(self.calls(), 1)
        self.assertTrue(self.a.has("login_needed"))

    def test_disk_filling_up_mid_download(self):
        (self.ctl / "mode").write_text("diskfull")
        job = self.run_job("update")
        self.assertIn("disk filled up", job["summary"])
        self.assertEqual(self.calls(), 1)

    def test_not_enough_disk_changes_nothing(self):
        self.start_game()
        tiny = shutil.disk_usage(self.tmp)._replace(free=100 * 2**20)
        with mock.patch.object(install.shutil, "disk_usage", return_value=tiny):
            job = self.run_job("update")
        self.assertIn("Not enough free disk space", job["summary"])
        self.assertEqual((self.calls(), self.a.backup_files()), (0, []))
        self.assertTrue(self.running())  # never stopped

    def test_one_job_at_a_time(self):
        (self.ctl / "slow").write_text("3")
        self.m.start("f", "update")
        with self.assertRaises(InstallError) as e:
            self.m.start("f", "repair")
        self.assertIn("already running", str(e.exception))
        end = time.time() + 30
        while self.m.jobs["f"]["state"] == "running" and time.time() < end:
            time.sleep(0.1)

    def test_players_are_warned_first(self):
        self.start_game()
        said = []
        self.a.can_say = lambda: True
        self.a.say = said.append
        from manager.adapters import base
        with mock.patch.object(base, "_sleep", lambda s: None):
            job = self.run_job("update", warn_s=120)
        self.assertEqual(job["result"], "ok")
        self.assertEqual(said[0], "Server update in 2 minutes.")


class TestRepairReinstallCleanup(InstallSandbox):
    def test_repair_fixes_files_and_its_own_setup(self):
        self.start_game()
        self.a.exe.unlink()  # a missing game file (Steam's check puts it back)
        job = self.run_job("repair")
        self.assertEqual(job["result"], "ok", job)
        self.assertTrue(self.a.exe.exists())
        self.assertTrue(self.running())

    def test_repair_makes_the_program_runnable_again(self):
        os.chmod(self.a.exe, 0o644)
        # Steam rewrites the file; make the fake keep it un-runnable to exercise HostSpawn's own fix
        with mock.patch.object(FakeInstalled, "post_update", lambda s: os.chmod(s.exe, 0o644)):
            job = self.run_job("repair")
        self.assertEqual(job["result"], "ok", job)
        self.assertIn("wasn't runnable", job["summary"])
        self.assertTrue(os.access(self.a.exe, os.X_OK))

    def test_reinstall_needs_the_name_and_keeps_saves(self):
        with self.assertRaises(InstallError):
            self.m.start("f", "reinstall", confirm="wrong")
        self.start_game()
        (self.a.server_dir / "junk.dll").write_text("old")
        job = self.run_job("reinstall", confirm="Fake One")
        self.assertEqual(job["result"], "ok", job)
        self.assertFalse((self.a.server_dir / "junk.dll").exists())  # fresh game files
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")
        self.assertEqual((self.a.server_dir / "settings" / "server.cfg").read_text(), "hostname=Fake One")
        self.assertFalse((self.root / KEEP_DIR).exists())
        self.assertTrue(self.running())

    def test_reinstall_download_failing_keeps_saves(self):
        (self.ctl / "mode").write_text("fail")
        job = self.run_job("reinstall", confirm="Fake One")
        self.assertEqual(job["result"], "failed")
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")
        self.assertFalse((self.root / KEEP_DIR).exists())

    def test_interrupted_reinstall_is_put_back_on_next_start(self):
        # as if HostSpawn died after setting saves aside and deleting the game folder
        self.m._park(self.a)
        shutil.rmtree(self.a.server_dir)
        self.a.state_dir.mkdir(parents=True, exist_ok=True)
        self.a.flag("install_job.json").write_text(json.dumps({"id": "x", "op": "reinstall", "state": "running"}))
        InstallManager(SimpleNamespace(adapters={"f": self.a}))
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")
        self.assertFalse((self.root / KEEP_DIR).exists())
        h = self.m.history(self.a)[0]
        self.assertEqual(h["result"], "interrupted")
        self.assertIn("put back", h["summary"])

    def test_cleanup(self):
        logs = self.a.log_dir
        logs.mkdir(parents=True, exist_ok=True)
        old = time.time() - 30 * 86400
        for i in range(8):
            p = logs / f"fake_2026-01-0{i + 1}_1200.log"
            p.write_text("x" * 1000)
            os.utime(p, (old + i, old + i))
        dl = self.a.server_dir / "steamapps" / "downloading" / "999"
        dl.mkdir(parents=True)
        (dl / "chunk").write_bytes(b"0" * 5000)
        self.a.cfg["keep_backups"] = 99
        for _ in range(3):
            self.a.backup()
            time.sleep(1.05)
        self.a.cfg["keep_backups"] = 1
        job = self.run_job("cleanup")
        self.assertEqual(job["result"], "ok", job)
        self.assertIn("3 old logs", job["summary"])  # 8 old ones, the newest 5 always stay
        self.assertIn("download leftovers", job["summary"])
        self.assertIn("2 old backups (keeping 1)", job["summary"])
        self.assertEqual(len(list(logs.glob("fake_*.log"))), 5)
        self.assertEqual(self.run_job("cleanup")["summary"], "Nothing to clean up.")


class TestRestoreSafety(InstallSandbox):
    def test_restore_never_touches_folders_outside_the_instance(self):
        outside = self.tmp / "shared-profiles"
        outside.mkdir()
        (outside / "other.txt").write_text("someone else's")
        self.a.backup_paths = lambda: [self.a.server_dir / "saves", outside]
        self.a.backup()
        archive = self.a.backup_files()[0]
        (outside / "other.txt").write_text("changed since")
        (self.a.server_dir / "saves" / "world.sav").write_text("changed since")
        put_back = install.restore_backup(self.a, archive)
        self.assertEqual(put_back, ["saves"])
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")
        self.assertEqual((outside / "other.txt").read_text(), "changed since")

    def test_restore_refuses_a_path_escaping_the_folder(self):
        evil = self.tmp / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            f = self.tmp / "x"
            f.write_text("x")
            tar.add(f, arcname="saves/../../../escaped")
        with self.assertRaises(Exception):
            install.restore_backup(self.a, evil)
        self.assertFalse((self.tmp / "escaped").exists())
        self.assertEqual((self.a.server_dir / "saves" / "world.sav").read_text(), "my world")  # left as it was


class TestRoles(InstallSandbox):
    def test_who_may_do_what(self):
        self.assertTrue(accounts.allowed("moderator", "/api/install", "update"))
        self.assertTrue(accounts.allowed("moderator", "/api/install", "check"))
        for op in ("repair", "reinstall", "cleanup"):
            self.assertFalse(accounts.allowed("moderator", "/api/install", op))
            self.assertTrue(accounts.allowed("admin", "/api/install", op))
        self.assertFalse(accounts.allowed("viewer", "/api/install", "update"))

    def test_instances_it_didnt_install(self):
        from manager.adapters.base import Adapter
        m = InstallManager(SimpleNamespace(adapters={"x": Adapter("x", {})}))
        with self.assertRaises(InstallError):
            m.info("x")


class TestInstallApi(InstallSandbox):
    def setUp(self):
        super().setUp()
        import threading
        from http.server import ThreadingHTTPServer

        from manager.supervisor import Supervisor
        from manager.web import make_handler
        self.acc = accounts.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", "correct horse battery staple")
        self.sup = Supervisor({"f": self.a}, config={})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.sup, "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = self.acc.login("owner", "correct horse battery staple", device="test")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        h = {"X-Manager": "1", "Content-Type": "application/json", "Authorization": "Bearer " + self.tok}
        c.request("POST" if body is not None else "GET", "/api/v1/" + path, json.dumps(body) if body else None, h)
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_info_refusals_and_an_update(self):
        code, info = self.call("install?server=f")
        self.assertEqual((code, info["build"], info["installed"]), (200, "100", True))
        self.assertEqual(self.call("install?server=nope")[0], 404)
        self.assertEqual(self.call("install", {"server": "f", "action": "format-disk"})[0], 400)
        code, err = self.call("install", {"server": "f", "action": "reinstall", "confirm": "wrong"})
        self.assertEqual(code, 400)
        self.assertIn("to confirm", err["error"])
        self.assertEqual(self.call("install", {"server": "f", "action": "update", "warn_s": "soon"})[0], 400)
        code, r = self.call("install", {"server": "f", "action": "update"})
        self.assertEqual((code, r["job"]["op"]), (200, "update"))
        end = time.time() + 30
        while time.time() < end and self.call("install?server=f")[1]["job"]["state"] == "running":
            time.sleep(0.2)
        info = self.call("install?server=f")[1]
        self.assertEqual(info["job"]["result"], "ok")
        self.assertEqual(info["history"][0]["by"], "owner")
