"""The sandbox's fake SteamCMD and fake games: they must never reach Steam, and must look enough like the real thing
(A2S answers, the game's own process name, Ctrl-C stops them) for a whole setup to run against them."""
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from manager import a2s

FAKES = Path(__file__).parent / "fakes"
STEAMCMD = FAKES / "fake_steamcmd.sh"


def free_udp() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def free_tcp() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TestFakeSteamcmd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="manager-fakes-"))
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait(5)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self, app: str) -> Path:
        out = subprocess.run([str(STEAMCMD), "+force_install_dir", str(self.tmp / app), "+login", "anonymous",
                              "+app_update", app, "validate", "+quit"], capture_output=True, text=True, timeout=30).stdout
        self.assertIn(f"Success! App '{app}' fully installed.", out)
        self.assertIn("progress: 100.00", out)
        return self.tmp / app

    def start_and_query(self, exe: Path, args: list[str], port: int):
        p = subprocess.Popen([str(exe), *args])
        self.procs.append(p)
        end, answer = time.time() + 15, None
        while time.time() < end and answer is None:
            answer = a2s.info("127.0.0.1", port, timeout=1)
        self.assertIsNotNone(answer, f"{exe.name} didn't answer A2S on {port}")
        cmd0 = open(f"/proc/{p.pid}/cmdline", "rb").read().split(b"\0")[0].decode()
        self.assertEqual(cmd0, str(exe))  # Manager finds the server by its path, like the real one
        p.send_signal(signal.SIGINT)
        self.assertIsNotNone(p.wait(10))

    def test_rust(self):
        q = free_udp()
        self.start_and_query(self.install("258550") / "RustDedicated", ["-batchmode", "+server.queryport", str(q)], q)

    def test_reforger_reads_its_config(self):
        q = free_udp()
        d = self.install("1874900")
        cfg = self.tmp / "config.json"
        cfg.write_text(json.dumps({"a2s": {"address": "0.0.0.0", "port": q}}))
        self.start_and_query(d / "ArmaReforgerServer", ["-config", str(cfg), "-maxFPS", "60"], q)

    def test_arma3(self):
        q = free_udp()
        self.start_and_query(self.install("233780") / "arma3server_x64", [f"-port={q - 1}", "-config=manager/server.cfg"], q)

    def test_rust_answers_rcon_with_fake_players(self):
        from manager.rcon import WebRcon
        q, r = free_udp(), free_tcp()
        exe = self.install("258550") / "RustDedicated"
        p = subprocess.Popen([str(exe), "+server.queryport", str(q), "+rcon.port", str(r), "+rcon.password", "sbx-pw"])
        self.procs.append(p)
        rows = self.until(lambda: json.loads(WebRcon("127.0.0.1", r, "sbx-pw", timeout=1).command("playerlist")))
        self.assertEqual(len(rows), 3)
        sid = rows[0]["SteamID"]
        WebRcon("127.0.0.1", r, "sbx-pw").command(f'banid {sid} "x" "test"')
        rows = json.loads(WebRcon("127.0.0.1", r, "sbx-pw").command("playerlist"))
        self.assertNotIn(sid, [x["SteamID"] for x in rows])

    def test_arma3_answers_battleye_with_fake_players(self):
        from manager.battleye import BattlEyeRcon
        q, r = free_udp(), free_udp()
        d = self.install("233780")
        (d / "battleye").mkdir()
        (d / "battleye/beserver_x64.cfg").write_text(f"RConPassword sbx-be\nRConIP 127.0.0.1\nRConPort {r}\n")
        p = subprocess.Popen([str(d / "arma3server_x64"), f"-port={q - 1}"], cwd=d)
        self.procs.append(p)
        c = BattlEyeRcon("127.0.0.1", r, "sbx-be", timeout=0.5)
        players = self.until(c.players)
        self.assertEqual(len(players), 3)
        c.command(f"addBan {players[0]['guid']} 0 test")
        self.assertEqual(len(c.players()), 2)

    def until(self, f, secs=15):
        end = time.time() + secs
        while True:
            try:
                return f()
            except Exception:
                if time.time() > end:
                    raise
                time.sleep(0.3)

    def test_login_succeeds_and_unknown_app_fails(self):
        out = subprocess.run([str(STEAMCMD), "+login", "someone", "+quit"], capture_output=True, text=True).stdout
        self.assertIn("Logging in user 'someone' to Steam Public...OK", out)
        r = subprocess.run([str(STEAMCMD), "+force_install_dir", str(self.tmp / "x"), "+app_update", "1", "+quit"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_never_contacts_steam(self):
        text = STEAMCMD.read_text() + (FAKES / "fake_any_game.py").read_text()
        for word in ("curl", "wget", "steampowered", "http"):
            self.assertNotIn(word, text)


class TestDeployUsesFakeOnlyWhenAsked(unittest.TestCase):
    def test_switch_is_read_from_the_environment_only(self):
        src = (Path(__file__).parents[1] / "manager" / "deploy.py").read_text()
        self.assertIn('os.environ.get("MANAGER_TEST_STEAMCMD")', src)
        self.assertNotIn("MANAGER_TEST_STEAMCMD", os.environ)  # tests themselves run with it unset
