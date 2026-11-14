"""Setup helper (deploy jobs), guided steps, ports, delete. Fakes only: a fake SteamCMD that prints real-looking
progress and installs a fake game which answers A2S; nothing is downloaded and no real server is touched."""
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import threading
import time
import tomllib
import unittest
import zipfile
from unittest import mock
from pathlib import Path

from manager import catalog, deploy, util
from manager.adapters import base as base_mod
from tests.test_manager import FAKES, Sandbox, write_exec

FAKE_RUST = f'''#!/usr/bin/env bash
# fake RustDedicated: answers A2S on +server.queryport (fake_game listens on -port + 1)
q=0; while [ $# -gt 0 ]; do [ "$1" = "+server.queryport" ] && q=$2; shift; done
export FAKE_EXE="$0"
exec -a "$0" python3 -I "{FAKES / "fake_game.py"}" -port=$((q - 1))
'''


def wait_for(cond, secs):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False



def deploy_ports() -> int:
    """A base port where port, port + 1 and port + 5 are all free (TCP and UDP), the way step_check tests them.
    Picked below Linux's range for short-lived connections (32768+): there, the tests' own HTTP and RCON connections
    could take port + 1 or port + 5 between picking and checking, and a job failed with "Port ... is in use"."""
    import random
    while True:
        p = random.randint(20000, 32000)
        if all(deploy.port_free(p + d) for d in (0, 1, 5)):
            return p


class DeploySandbox(Sandbox):
    def setUp(self):
        super().setUp()
        self._saved_d = (util.CONFIG, deploy.CONFIG, deploy.JOBS, deploy.STATE, deploy.servers_dir)
        util.CONFIG = deploy.CONFIG = self.tmp / "config"
        util.CONFIG.mkdir()
        (util.CONFIG / "settings.toml").write_text("[limits]\nmax_instances = 3\n")  # room for tests making several
        deploy.STATE = self.tmp / "state"
        deploy.JOBS = deploy.STATE / "jobs"
        self.base = self.tmp / "servers"
        deploy.servers_dir = lambda: self.base
        self._orig_alloc = deploy.allocate_ports
        port = deploy_ports()
        deploy.allocate_ports = lambda game, servers=None: (
            {"port": port, "rcon_port": port + 5, "query_port": port + 1} if game == "rust" else
            {"port": port} if game == "arma3" else {"port": port, "query_port": port + 1, "rcon_port": port + 5})
        self.steam_out = self.tmp / "steam_out"
        self.steam_out.write_text("ok")
        self.steam_sleep = self.tmp / "steam_sleep"
        self.exe_script = FAKE_RUST
        self._write_steamcmd(self.base / deploy.SHARED_STEAMCMD)
        self._sizes = {g: catalog.GAMES[g]["size_gb"] for g in ("rust", "reforger", "arma3")}
        for g in self._sizes:
            catalog.GAMES[g]["size_gb"] = 0.001  # fake games are tiny (and /tmp is small)
        self._orig_mem = deploy.mem_available_gb
        deploy.mem_available_gb = lambda: 64.0  # fake games use no real memory; a busy machine mustn't fail tests
        self.d = deploy.Deployer(None)
        create = self.d.create  # tests accept the current terms unless they say otherwise
        self.d.create = lambda *a, **k: create(*a, **{"accepted_terms": catalog.TERMS_VERSION, **k})

    def _write_steamcmd(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_exec(path, f'''#!/usr/bin/env bash
dir=""; app=""; while [ $# -gt 0 ]; do case "$1" in +force_install_dir) dir=$2;; +app_update) app=$2;; esac; shift; done
echo "Logging in user 'anonymous' to Steam Public...OK"
[ -f "{self.steam_sleep}" ] && sleep "$(cat "{self.steam_sleep}")"
if [ "$(cat "{self.steam_out}")" = guard ]; then echo "Please enter the Steam Guard code:"; exit 5; fi
for p in 10 50 90; do echo " Update state (0x61) downloading, progress: $p.00 ($((p*10000000)) / 1000000000)"; done
if [ -n "$dir" ] && [ -n "$app" ]; then mkdir -p "$dir"; cat > "$dir/RustDedicated" <<'EOF'
{FAKE_RUST}
EOF
chmod +x "$dir/RustDedicated"; echo "Success! App '$app' fully installed."; fi
''')

    def tearDown(self):
        # let setup threads finish before the real paths come back (one once registered a test server for real)
        for j in self.d.jobs.values():
            j._cancel.set()
        for t in threading.enumerate():
            if t.name.startswith("job-"):
                t.join(30)
        for sid in list(util.load_servers().keys()):
            util.run(["tmux", "kill-session", "-t", f"=manager-{sid}"])
        for j in self.d.jobs.values():
            util.run(["tmux", "kill-session", "-t", f"=manager-{j.d['server_id']}"])
        util.run(["pkill", "-f", str(self.base)])
        util.CONFIG, deploy.CONFIG, deploy.JOBS, deploy.STATE, deploy.servers_dir = self._saved_d
        deploy.allocate_ports = self._orig_alloc
        deploy.mem_available_gb = self._orig_mem
        for g, v in self._sizes.items():
            catalog.GAMES[g]["size_gb"] = v
        super().tearDown()

    def wait_status(self, job, statuses, secs=30):
        end = time.time() + secs
        while time.time() < end:
            if job.d["status"] in statuses:
                return True
            time.sleep(0.1)
        print(f"\n[wait_status] wanted {statuses}, job is {job.d['status']}: {job.d.get('error', '')}\n"
              + "\n".join(job.d.get("log", [])[-6:]))
        return False


class TestDeployFlow(DeploySandbox):
    def test_full_rust_vanilla_flow_then_delete(self):
        job = self.d.create("rust", "vanilla", {"name": "Test Rust", "max_players": 20, "world_size": 1500})
        self.assertTrue(self.wait_status(job, ("waiting",), 60), job.d)
        steps = {s["key"]: s["state"] for s in job.d["steps"]}
        self.assertEqual([k for k, v in steps.items() if v == "done"],
                         ["check", "folders", "steamcmd", "download", "configure", "start", "wait", "register"])
        self.assertEqual(job.step()["key"], "ports")
        self.assertTrue(any(c.startswith("sudo ufw allow") for c in job.step()["guide"]["commands"]))
        dl = next(s for s in job.d["steps"] if s["key"] == "download")
        self.assertEqual(dl["percent"], 100)
        # registered: Manager now lists and supervises it
        sid = job.d["server_id"]
        servers = util.load_servers()
        self.assertTrue(servers[sid]["managed"])
        self.assertEqual((servers[sid]["max_players"], servers[sid]["world_size"]), (20, 1500))
        cfgfile = Path(servers[sid]["root"]) / "server/server/server/cfg/server.cfg"
        self.assertIn("server.maxplayers 20", cfgfile.read_text())
        # passwords exist but are never in the job record or the config file
        sec = util.secrets()
        rcon = sec[servers[sid]["rcon_password_key"]]
        self.assertGreater(len(rcon), 16)
        self.assertNotIn(rcon, json.dumps(job.public()))
        self.assertNotIn(rcon, (util.CONFIG / "servers.d" / f"{sid}.toml").read_text())
        self.d.action(job.id, "continue")
        self.assertTrue(self.wait_status(job, ("done",), 20), job.d)
        self.assertIn(":", job.d["connect"])
        # neat layout: servers/<game>/<readable name>-<id end>/ with a README and a private manager/ folder
        root = Path(servers[sid]["root"])
        self.assertEqual(root.parent, self.base / "rust")
        self.assertEqual(root.name, "test-rust-" + sid.rsplit("-", 1)[-1])
        self.assertEqual(sorted(p.name for p in root.iterdir()),
                         sorted([".manager-server", "README.txt", "backups", "logs", "manager", "server"]))
        readme = (root / "README.txt").read_text()
        self.assertIn("Test Rust", readme)
        self.assertIn(str(job.d["ports"]["port"]), readme)
        self.assertFalse((root / "manager/setup").exists())  # setup downloads cleaned up
        self.assertEqual(stat.S_IMODE((root / "manager").stat().st_mode), 0o700)
        self.assertNotIn(rcon, readme)
        # a backup, then delete keeping backups
        from manager.adapters import build
        a = build(util.load_servers())[sid]
        a.backup()
        root = Path(servers[sid]["root"])
        out = self.d.delete_server(sid, keep_backups=True)
        self.assertIn("backups kept", out)
        self.assertFalse(root.exists())
        self.assertNotIn(sid, util.load_servers())
        self.assertFalse(any(k.startswith("SRV_" + sid.upper().replace("-", "_")) for k in util.secrets()))
        self.assertEqual(len(list((self.base / "_kept_backups").glob(f"{sid}_*/rust_*.tar.gz"))), 1)
        self.assertIsNone(a.pid())
        self.assertFalse((self.base / "rust").exists())  # the empty game folder is tidied away too

    def test_folder_names(self):
        self.assertEqual(deploy.folder_name("Riverside 2x Main!", "rust-vanilla-3fa2"), "riverside-2x-main-3fa2")
        self.assertEqual(deploy.folder_name("../../etc", "rust-vanilla-3fa2"), "etc-3fa2")  # can't climb out
        self.assertEqual(deploy.folder_name("Ünïcødé ★", "arma3-vanilla-00aa"), "unicde-00aa")
        self.assertEqual(deploy.folder_name("!!!", "arma3-vanilla-00aa"), "server-00aa")
        self.assertLessEqual(len(deploy.folder_name("x" * 200, "a-b-ffff")), 37)

    def test_old_steamcmd_reused_new_installs_shared(self):
        self.assertEqual(deploy.steamcmd_path(), self.base / deploy.SHARED_STEAMCMD)
        shutil.rmtree(self.base / "_shared")
        (self.base / ".steamcmd").mkdir()
        (self.base / deploy.LEGACY_STEAMCMD).write_text("old")
        self.assertEqual(deploy.steamcmd_path(), self.base / deploy.LEGACY_STEAMCMD)  # no second 200 MB download

    def test_skip_ports_and_back_to_settings(self):
        job = self.d.create("rust", "vanilla", {})
        self.assertTrue(self.wait_status(job, ("waiting",), 60))
        job2_back = self.d.action(job.id, "back")  # no earlier guided step: back to the settings
        self.assertEqual((job2_back.d["status"], job2_back.d.get("back_to_settings")), ("cancelled", True))
        self.d.action(job.id, "retry")
        self.assertTrue(self.wait_status(job, ("waiting",), 30))
        self.d.action(job.id, "skip")
        self.assertTrue(self.wait_status(job, ("done",), 20))
        self.assertEqual(next(s for s in job.d["steps"] if s["key"] == "ports")["state"], "skipped")

    def test_cancel_during_download_then_retry(self):
        self.steam_sleep.write_text("4")
        job = self.d.create("rust", "vanilla", {})
        end = time.time() + 15
        while job.step()["key"] != "download" and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.5)
        self.d.action(job.id, "cancel")
        self.assertTrue(self.wait_status(job, ("cancelled",), 10), job.d)
        self.assertEqual(job.step()["key"], "download")
        self.steam_sleep.unlink()
        self.d.action(job.id, "retry")
        self.assertTrue(self.wait_status(job, ("waiting",), 60), job.d)

    def test_first_start_crash_fails_with_log_then_discard(self):
        (self.tmp / "crash").touch()
        orig = deploy.Deployer.step_start

        def crashing_start(self_, job):
            (Path(job.d["root"]) / "server/CRASH").touch()  # fake game exits at once
            orig(self_, job)
        deploy.Deployer.step_start = crashing_start
        try:
            job = self.d.create("rust", "vanilla", {})
            self.assertTrue(self.wait_status(job, ("failed",), 60), job.d)
        finally:
            deploy.Deployer.step_start = orig
        self.assertEqual(job.step()["key"], "wait")
        self.assertIn("stopped during its first start", job.d["error"])
        root = Path(job.d["root"])
        self.assertTrue(root.exists())
        self.d.discard_job(job.id)
        self.assertFalse(root.exists())
        self.assertNotIn(job.id, self.d.jobs)

    def test_interrupted_job_shows_retry_after_restart(self):
        self.steam_sleep.write_text("3")
        job = self.d.create("rust", "vanilla", {})
        time.sleep(1)
        d2 = deploy.Deployer(None)  # as if Manager restarted now
        j2 = d2.jobs[job.id]
        self.assertEqual(j2.d["status"], "failed")
        self.assertIn("Interrupted", j2.d["error"])
        self.wait_status(job, ("waiting", "failed"), 30)

    def test_steam_guard_during_download_fails_clearly(self):
        self.steam_out.write_text("guard")
        job = self.d.create("rust", "vanilla", {})
        self.assertTrue(self.wait_status(job, ("failed",), 30))
        self.assertIn("Steam wants a login", job.d["error"])


class TestTerms(DeploySandbox):
    def test_deploy_needs_the_current_terms_accepted(self):
        for given in ("", "old-version", "yes"):
            with self.assertRaises(ValueError) as e:
                self.d.create("rust", "vanilla", {}, accepted_terms=given)
            self.assertIn("accept the terms", str(e.exception))
        self.assertEqual(self.d.jobs, {})

    def test_acceptance_is_recorded(self):
        self.steam_sleep.write_text("30")
        j = self.d.create("arma3", "altislife", {"steam_user": "pilot"})
        acc = j.d["accepted_terms"]
        self.assertEqual(acc["version"], catalog.TERMS_VERSION)
        self.assertIn("Steam Subscriber Agreement", acc["items"])
        self.assertIn("Altis Life (AsYetUntitled) licence", acc["items"])
        self.d.action(j.id, "cancel")


class TestRestartSchedules(DeploySandbox):
    def test_recommended_by_default_and_per_mode(self):
        self.steam_sleep.write_text("30")
        j = self.d.create("arma3", "altislife", {"steam_user": "pilot"})
        self.assertEqual(self.d.cfg(j)["restart_times"], catalog.restarts_for("arma3", "altislife")["times"])
        self.assertEqual(len(self.d.cfg(j)["restart_times"]), 6)  # every 4 hours
        self.d.action(j.id, "cancel")
        self.assertTrue(self.wait_status(j, ("cancelled",), 10))
        j2 = self.d.create("rust", "vanilla", {"oxide": True})  # becomes the hidden oxide type: falls back to Rust's
        self.assertEqual(self.d.cfg(j2)["restart_times"], ["05:00"])
        self.d.action(j2.id, "cancel")

    def test_opt_out_and_custom(self):
        self.steam_sleep.write_text("30")
        j = self.d.create("rust", "vanilla", {"recommended_restarts": False, "restart_times": ["07:00", "07:00", "03:00"]})
        self.assertEqual(self.d.cfg(j)["restart_times"], ["03:00", "07:00"])
        self.d.action(j.id, "cancel")
        self.assertTrue(self.wait_status(j, ("cancelled",), 10))
        for bad in (["7am"], ["01:00"] * 7):
            with self.assertRaises(ValueError):
                self.d.create("rust", "vanilla", {"recommended_restarts": False, "restart_times": bad})


class TestDeployGuards(DeploySandbox):
    """After a page bug re-sent one Deploy 705 times and ran the machine out of memory (2026-11-06)."""

    def test_same_request_id_never_makes_a_second_job(self):
        self.steam_sleep.write_text("30")
        a = self.d.create("rust", "vanilla", {}, request_id="abc-123")
        for _ in range(20):
            self.assertIs(self.d.create("rust", "vanilla", {}, request_id="abc-123"), a)
        self.assertEqual(len(self.d.jobs), 1)
        self.d.action(a.id, "cancel")

    def test_only_one_active_setup(self):
        self.steam_sleep.write_text("30")
        a = self.d.create("rust", "vanilla", {}, request_id="one")
        with self.assertRaises(ValueError) as e:
            self.d.create("rust", "vanilla", {}, request_id="two")
        self.assertIn("still being set up", str(e.exception))
        self.d.action(a.id, "cancel")

    def test_rate_limit(self):
        self.d._recent = [time.time()] * deploy.MAX_DEPLOYS_PER_MINUTE
        with self.assertRaises(ValueError) as e:
            self.d.create("rust", "vanilla", {})
        self.assertIn("Too many", str(e.exception))

    def test_memory_check(self):
        orig = deploy.mem_available_gb
        deploy.mem_available_gb = lambda: 1.0
        try:
            with self.assertRaises(ValueError) as e:
                self.d.create("rust", "vanilla", {})
            self.assertIn("Not enough free memory", str(e.exception))
        finally:
            deploy.mem_available_gb = orig

    def test_sandbox_with_fake_games_skips_the_real_memory_check(self):
        # The app sandbox's fake games use a few MB; a real Rust needs 10 GB free. Only the sandbox switch relaxes it.
        orig = deploy.mem_available_gb
        deploy.mem_available_gb = lambda: 1.0
        try:
            with mock.patch.dict(os.environ, {"MANAGER_TEST_STEAMCMD": str(Path(__file__).parent / "fakes/fake_steamcmd.sh")}):
                job = self.d.create("rust", "vanilla", {})
            self.d.action(job.id, "cancel")
        finally:
            deploy.mem_available_gb = orig

    def test_flood_of_requests_creates_one_job(self):
        self.steam_sleep.write_text("30")
        import threading as th
        made, errors = [], []

        def hit(i):
            try:
                made.append(self.d.create("rust", "vanilla", {}, request_id=f"r{i}"))
            except ValueError as e:
                errors.append(str(e))
        ts = [th.Thread(target=hit, args=(i,)) for i in range(50)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(self.d.jobs), 1)
        self.assertEqual(len(made), 1)
        self.assertEqual(len(errors), 49)
        self.d.action(made[0].id, "cancel")


class TestInstanceLimit(DeploySandbox):
    """At most 3 instances by default (settings.toml [limits] max_instances); numbers stay with their server."""

    def setUp(self):
        super().setUp()
        (util.CONFIG / "settings.toml").unlink()  # the real default

    def made(self, *sids):
        d = util.CONFIG / "servers.d"
        d.mkdir(exist_ok=True)
        for sid in sids:
            (d / f"{sid}.toml").write_text(f'[servers.{sid}]\nname = "{sid}"\nadapter = "rust_server"\nroot = "/nowhere"\n')

    def test_fourth_instance_refused_until_the_limit_is_raised(self):
        self.made("a", "b", "c")
        self.assertEqual(self.d.instances_used(), 3)
        with self.assertRaises(ValueError) as e:
            self.d.create("rust", "vanilla", {})
        self.assertIn("limit of 3 instances.", str(e.exception))
        self.assertEqual(self.d.jobs, {})
        (util.CONFIG / "settings.toml").write_text("[limits]\nmax_instances = 4\n")
        self.steam_sleep.write_text("30")
        job = self.d.create("rust", "vanilla", {})
        self.assertEqual(self.d.instances_used(), 4)  # a setup in progress holds its place
        self.d.action(job.id, "cancel")
        self.assertTrue(self.wait_status(job, ("cancelled",)))
        self.assertEqual(self.d.instances_used(), 3)  # cancelled: the place is free again

    def test_retrying_a_cancelled_setup_checks_the_limit(self):
        self.made("a", "b")
        self.steam_sleep.write_text("3")  # short: a cancel that lands before the download starts waits for it
        job = self.d.create("rust", "vanilla", {})
        self.d.action(job.id, "cancel")
        self.assertTrue(self.wait_status(job, ("cancelled",)))
        self.made("c")
        with self.assertRaises(ValueError):
            self.d.action(job.id, "retry")

    def test_numbers_stay_with_their_server(self):
        from manager import instances
        self.assertEqual([instances.number(s) for s in ("x", "y", "z")], [1, 2, 3])
        self.assertEqual(instances.number("x"), 1)  # asked again: same number
        instances.release("x")  # Instance 1 deleted: the others keep theirs
        self.assertEqual((instances.number("y"), instances.number("z")), (2, 3))
        self.assertEqual(instances.number("new"), 1)  # the next one fills the gap
        self.assertEqual(instances.label("z", "Arma 3"), "Instance 3 · Arma 3 Server")

    def test_default_name_doesnt_repeat_the_game(self):
        (util.CONFIG / "settings.toml").write_text('[community]\nname = "Riverside"\n')
        self.steam_sleep.write_text("30")
        job = self.d.create("rust", "vanilla", {"oxide": True})
        self.assertNotIn("Rust Rust", job.d["name"])
        self.assertTrue(job.d["name"].startswith("Riverside Rust"))
        self.d.action(job.id, "cancel")

    def test_bad_limit_setting_falls_back_to_3(self):
        from manager import instances
        (util.CONFIG / "settings.toml").write_text('[limits]\nmax_instances = "lots"\n')
        self.assertEqual(instances.max_instances(), 3)


class TestOxideAction(DeploySandbox):
    def test_install_oxide_on_vanilla_switches_to_modded(self):
        from manager.adapters import build, rust_server
        job = self.d.create("rust", "vanilla", {})
        self.assertTrue(self.wait_status(job, ("waiting",), 60))
        sid = job.d["server_id"]
        a = build(util.load_servers())[sid]
        old_pid = a.pid()
        calls = []
        orig = rust_server.install_oxide
        rust_server.install_oxide = lambda d: calls.append(d) or "Oxide installed (1 KB)"
        try:
            out = a.oxide()
        finally:
            rust_server.install_oxide = orig
        self.assertEqual(calls, [a.server_dir])
        self.assertIn("now modded", out)
        self.assertIn("restarted", out)
        self.assertNotEqual(a.pid(), old_pid)
        again = util.load_servers()[sid]
        self.assertEqual((again["framework"], again["mode"]), ("oxide", "oxide"))
        self.assertFalse(a.has("maintenance"))
        self.assertIn("oxide", a.confirm)


class TestValidationAndSafety(DeploySandbox):
    def test_cancel_stops_download_and_its_children(self):
        self.steam_sleep.write_text("30")  # the fake SteamCMD script waits in a child process, like the real one
        job = self.d.create("rust", "vanilla", {})
        self.assertTrue(wait_for(lambda: len(job._procs) == 1, 20))
        p = job._procs[0]
        t0 = time.time()
        self.d.action(job.id, "cancel")
        self.assertTrue(self.wait_status(job, ("cancelled",), 20))
        self.assertLess(time.time() - t0, 20)  # well before the 30 s download would have ended
        alive = lambda: subprocess.run(["pgrep", "-g", str(p.pid)], capture_output=True, text=True).stdout.split()
        self.assertTrue(wait_for(lambda: alive() == [], 10), alive())  # nothing left in its process group

    def test_process_started_after_cancel_is_stopped(self):
        job = self.d.create("rust", "vanilla", {})
        self.d.action(job.id, "cancel")
        late = subprocess.Popen(["sleep", "30"], start_new_session=True)
        job._procs.append(late)
        self.assertIsNotNone(late.wait(5))

    def test_late_job_writes_stay_in_the_deployers_folders(self):
        """A job thread still running after the paths change (a test ending) must not write to the real folders."""
        job = self.d.create("rust", "vanilla", {})
        sandbox_jobs = self.d.jobs_dir
        saved = deploy.JOBS
        deploy.JOBS = self.tmp / "elsewhere"
        try:
            job.save()
        finally:
            deploy.JOBS = saved
        self.assertTrue((sandbox_jobs / f"{job.id}.json").exists())
        self.assertFalse((self.tmp / "elsewhere").exists())

    def test_register_refuses_folder_outside_servers(self):
        job = self.d.create("rust", "vanilla", {})
        job._cancel.set()
        job.d["root"] = str(self.tmp / "not-servers" / "x")
        with self.assertRaises(deploy.StepFailed):
            self.d.step_register(job)
        self.assertFalse((util.CONFIG / "servers.d" / f"{job.d['server_id']}.toml").exists())

    def test_bad_requests(self):
        for game, mode, opts, msg in (("nope", "x", {}, "unknown"), ("arma3", "wasteland", {}, "isn't available yet"),
                                      ("rust", "vanilla", {"max_players": 0}, "1–500"),
                                      ("rust", "vanilla", {"world_size": "big"}, "must be a number"),
                                      ("arma3", "vanilla", {"steam_user": "bad user; rm"}, "Steam username"),
                                      ("arma3", "kpliberation", {"steam_user": "u", "map": "Mars"}, "unknown map")):
            with self.assertRaises(ValueError) as e:
                self.d.create(game, mode, opts)
            self.assertIn(msg, str(e.exception), (game, mode))

    def test_install_oxide_question(self):
        self.steam_sleep.write_text("30")
        yes = self.d.create("rust", "vanilla", {"oxide": True})
        self.d.action(yes.id, "cancel")
        self.assertTrue(self.wait_status(yes, ("cancelled",), 10))
        no = self.d.create("rust", "vanilla", {"oxide": False})
        self.assertEqual((yes.d["mode"], yes.d["mode_name"]), ("oxide", "Modded (Oxide / uMod)"))
        self.assertIn("oxide", [st["key"] for st in yes.d["steps"]])
        self.assertEqual(no.d["mode"], "vanilla")
        self.assertNotIn("oxide", [st["key"] for st in no.d["steps"]])
        self.assertNotIn("oxide", yes.d["options"])  # a question, not a server setting
        self.assertEqual(self.d.cfg(yes)["framework"], "oxide")
        with self.assertRaises(ValueError):
            self.d.create("rust", "oxide", {})  # hidden type: only reachable through the question
        for j in (yes, no):
            self.d.action(j.id, "cancel")

    def test_name_is_cleaned(self):
        self.steam_sleep.write_text("30")
        job = self.d.create("rust", "vanilla", {"name": 'Evil"\n\\name'})
        self.assertEqual(job.d["name"], "Evilname")
        self.d.action(job.id, "cancel")

    def test_delete_refuses_unmanaged_and_unmarked(self):
        (util.CONFIG / "servers.toml").write_text('[servers.mine]\nname = "Mine"\nadapter = "arma3"\nroot = "/tmp"\n')
        with self.assertRaises(ValueError) as e:
            self.d.delete_server("mine")
        self.assertIn("Only servers HostSpawn created", str(e.exception))
        (util.CONFIG / "servers.d").mkdir()
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (util.CONFIG / "servers.d/x.toml").write_text(f'[servers.x]\nname = "X"\nadapter = "rust-server"\nroot = "{outside}"\n')
        with self.assertRaises(ValueError) as e:
            self.d.delete_server("x")
        self.assertIn("Safety check failed", str(e.exception))
        self.assertTrue(outside.exists())
        inside = self.base / "y"
        inside.mkdir(parents=True)  # inside servers/ but no marker file
        (util.CONFIG / "servers.d/y.toml").write_text(f'[servers.y]\nname = "Y"\nadapter = "rust-server"\nroot = "{inside}"\n')
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.d.delete_server("y")
        self.assertTrue(inside.exists())

    def test_toml_dump_round_trips(self):
        data = {"s1": {"name": 'A "quoted" name', "port": 2001, "sandbox": True, "times": ["05:00", "17:00"],
                       "_file": "skip", "none": None}}
        back = tomllib.loads(deploy.toml_dump(data))
        self.assertEqual(back, {"servers": {"s1": {"name": 'A "quoted" name', "port": 2001, "sandbox": True,
                                                   "times": ["05:00", "17:00"]}}})

    def test_ports_avoid_used_and_bound(self):
        deploy.allocate_ports = self._orig_alloc
        p = deploy.allocate_ports("reforger", {"live": {"port": 2001, "query_port": 17777}})
        self.assertNotIn(p["port"], (2001,))
        self.assertNotIn(p["query_port"], (17777,))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("0.0.0.0", 0))
            busy = s.getsockname()[1]
            self.assertFalse(deploy.port_free(busy))
        r = deploy.allocate_ports("rust", {"a": {"port": 28015}})
        self.assertGreaterEqual(r["port"], 28115)
        self.assertEqual((r["rcon_port"], r["query_port"]), (r["port"] + 1, r["port"] + 2))


class TestArma3Steps(DeploySandbox):
    def test_steam_login_check_and_kp_mission(self):
        a3_steam = self.tmp / "a3steam.sh"
        reply = self.tmp / "a3reply"
        reply.write_text("Logging in user 'pilot' to Steam Public...OK\nWaiting for user info...OK\n")
        write_exec(a3_steam, f'#!/usr/bin/env bash\ncat "{reply}"\n')
        job = deploy.Job({"id": "j1", "server_id": "arma3-kpliberation-abcd", "game": "arma3", "mode": "kpliberation",
                          "name": "KP", "options": {"max_players": 10, "map": "Altis", "release": ""},
                          "ports": {"port": 2402}, "root": str(self.base / "arma3-kpliberation-abcd"), "steps": [{"key": "x"}],
                          "current": 0, "log": []}, self.d)
        util.set_secret("SRV_ARMA3_KPLIBERATION_ABCD_STEAM_USER", "pilot")
        orig_adapter = self.d.adapter
        self.d.adapter = lambda j: type(orig_adapter(j))(j.d["server_id"], {**self.d.cfg(j), "steamcmd": str(a3_steam)})
        guide = self.d.step_steam_login(job, check=False)
        self.assertIn("+login pilot +quit", guide["commands"][0])
        self.assertIn("never saves it", guide["text"])
        self.assertEqual(guide["login"], {"target": "job:j1", "user": "pilot"})
        self.assertEqual(self.d.step_steam_login(job, check=True), (True, ""))
        reply.write_text("Logging in user 'pilot' to Steam Public...\npassword:\n")
        ok, msg = self.d.step_steam_login(job, check=True)
        self.assertFalse(ok)
        self.assertIn("still asks for a login", msg)
        # KP Liberation: release lookup and download are faked; extraction is real
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("README.md", "x")
            z.writestr("kp_liberation.Altis.pbo", b"PBO")
        orig_rel, orig_dl = catalog.releases, deploy._download
        catalog.releases = lambda g, m: [{"tag": "v9", "date": "2020-01-01", "assets": {"kp_liberation.Altis.zip": "http://x"}}]
        deploy._download = lambda url, j, label: buf.getvalue()
        try:
            self.d.step_mission(job)
        finally:
            catalog.releases, deploy._download = orig_rel, orig_dl
        mp = Path(job.d["root"]) / "server/mpmissions"
        self.assertEqual(sorted(p.name for p in mp.iterdir()), ["kp_liberation.Altis.pbo"])
        self.assertEqual(self.d.cfg(job)["mission"], "kp_liberation.Altis")
        self.assertEqual(job.d["options"]["release_used"], "v9")

    def test_zip_with_unsafe_path_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../evil.txt", "x")
        with self.assertRaises(deploy.StepFailed):
            deploy._safe_extract_zip(buf.getvalue(), self.tmp / "dest")
        self.assertFalse((self.tmp.parent / "evil.txt").exists())


if __name__ == "__main__":
    unittest.main()
