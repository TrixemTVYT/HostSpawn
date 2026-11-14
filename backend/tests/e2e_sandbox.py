#!/usr/bin/env python3
"""End-to-end sandbox: runs a real manager process in LIVE mode against fake servers and drives it over HTTP.
Nothing here can reach a real server:
  - Rust: a fake rustctl.sh in the sandbox (the real one would act on the live screen session, so it's never used)
  - Reforger: fake sudo/systemctl/journalctl first on PATH, fake unit name
  - Arma 3: tests/fakes/fake_game.py behind a fake arma3server_x64, fake steamcmd
Usage: python3 -I backend/tests/e2e_sandbox.py [sandbox_dir]     (about 3 minutes; prints PASS/FAIL per scenario)"""
import base64
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]  # the whole repo: copied into the sandbox
sys.path.insert(0, str(REPO / "backend"))
from tests.fakes.fake_webrcon import FakeWebRcon  # noqa: E402
FAKES = REPO / "backend/tests/fakes"
SB = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="msm-e2e-"))
PORT = int(os.environ.get("SANDBOX_PORT", "8199"))  # another port lets a second sandbox run beside the first
SESSION = "msm-e2e" if PORT == 8199 else f"msm-e2e-{PORT}"
A3_PORT = 27950 + (PORT - 8199) * 10
PW = "e2e-test-password"
AUTH = "Basic " + base64.b64encode(f"admin:{PW}".encode()).decode()
results = []


def x(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def setup(rcon_port):
    if SB.exists():
        shutil.rmtree(SB)
    app = SB / "app"
    shutil.copytree(REPO, app, ignore=shutil.ignore_patterns(".git", "state", "logs", "backups", "secrets.env",
                                                             "settings.toml", "settings.toml.bak",  # starts fresh
                                                             "servers.toml", "instance.env", "__pycache__",
                                                             # never copy real accounts, created servers or builds
                                                             "accounts.json", "servers.d", "servers", "dist",
                                                             # the app's build output: 1.6 GB, and /tmp may be RAM
                                                             ".dart_tool", "build"))
    (app / "config/secrets.env").write_text(f"MANAGER_PASSWORD={PW}\nARMA3_STEAM_USER=fakeuser\n"
                                            "ARMA3_ADMIN_PASSWORD=e2e-admin-secret\nARMA3_SERVER_PASSWORD=\n")
    os.chmod(app / "config/secrets.env", 0o600)
    (app / "config/instance.env").write_text(f"MODE=live\nPORT={PORT}\nINTERVAL=2\n")
    # Fake Rust: rustctl keeps its state in a file, records every call.
    rust = SB / "rust"
    (rust / "logs").mkdir(parents=True)
    (rust / "config").mkdir()
    (rust / "state").write_text("running")
    (rust / "config").mkdir(exist_ok=True)
    (rust / "config/secrets.env").write_text("RCON_PASSWORD=sandbox-rcon-pw\n")
    (rust / "config/wipe.conf").write_text('AUTO_RESTART_ENABLED=1\nRESTART_TIMES="05:00"\nAUTO_WIPE_ENABLED=1\n'
                                           'FORCED_WIPE_TIME="20:00"\nBLUEPRINT_WIPE_ON_FORCED=1\n')
    x(rust / "rustctl.sh", f'''#!/usr/bin/env bash
R="{rust}"
echo "$(date +%T) $*" >> "$R/calls"
case "$1" in
  status) if [ "$(cat "$R/state")" = running ]; then echo "running pid=1 uptime=10:00 mem=1.0GB"; else echo "stopped"; fi ;;
  restart|update) sleep 1; echo running > "$R/state" ;;
  stop) echo stopped > "$R/state" ;;
  start) echo running > "$R/state" ;;
  cmd) echo sent ;;
esac
''')
    # Fake systemd for Reforger.
    sd = SB / "systemd"
    sd.mkdir()
    (sd / "props").write_text("ActiveState=active\nSubState=running\nMainPID=1\nNRestarts=0\nLoadState=loaded\n")
    x(SB / "bin/sudo", '#!/usr/bin/env bash\n[ "$1" = -n ] && shift\nexec "$@"\n')
    x(SB / "bin/systemctl", f'#!/usr/bin/env bash\nif [ "$1" = show ]; then cat "{sd}/props"; exit 0; fi\n'
                            f'echo "$*" >> "{sd}/calls"\n')
    x(SB / "bin/journalctl", '#!/usr/bin/env bash\necho "fake journal"\n')
    life = SB / "life"
    (life / "Server/profile/logs/logs_2026-11-06_10-00-00").mkdir(parents=True)
    (life / "Server/profile/logs/logs_2026-11-06_10-00-00/console.log").write_text(
        "10:00 DEFAULT : FPS: 60.0, frame time (avg: 16 ms), Mem: 990000 kB, Player: 2, AI: 0\n")
    x(life / "Server/scripts/backup.sh", f'#!/usr/bin/env bash\necho backup >> "{sd}/calls"\n')
    # Fake Arma 3.
    a3 = SB / "arma3"
    x(a3 / "server/arma3server_x64", f'#!/usr/bin/env bash\nexport FAKE_EXE="$0"\nexec -a "$0" python3 -I "{FAKES}/fake_game.py" "$@"\n')
    (SB / "steam_reply").write_text("Success! App '233780' fully installed.\n")
    x(SB / "steamcmd.sh", f'#!/usr/bin/env bash\necho x >> "{SB}/steam_calls"\ncat "{SB}/steam_reply"\n')
    (app / "config/servers.toml").write_text(f'''
[servers.rust]
name = "Sandbox Rust"
practice = true
adapter = "rust"
root = "{rust}"
query_port = 1
rcon_port = {rcon_port}
warn_seconds = 300

[servers.reforger]
name = "Sandbox Reforger"
practice = true
adapter = "reforger"
root = "{life}"
unit = "fake-unit"
max_players = 64

[servers.arma3]
name = "Sandbox Arma 3"
practice = true
adapter = "arma3"
root = "{a3}"
port = {A3_PORT}
steamcmd = "{SB}/steamcmd.sh"
session = "{SESSION}"
grace_s = 0
hang_checks = 2
retry_wait_s = 0
restart_times = []
''')
    return app


def start_manager(app):
    env = {**os.environ, "PATH": f"{SB / 'bin'}:{os.environ['PATH']}"}
    log = open(SB / "manager.out", "a")
    return subprocess.Popen(["python3", "-I", str(app / "backend/manager_app.py"), "serve"], env=env, stdout=log,
                            stderr=log, start_new_session=True)


TOKEN = {"value": ""}  # a device token from the first-run setup (the way the HostSpawn app signs in)


def api(path, body=None):
    if path.startswith("/api/"):
        path = "/api/v1/" + path[len("/api/"):]  # test the app's versioned API
    h = {"X-Manager": "1", "Content-Type": "application/json"}
    if TOKEN["value"]:
        h["Authorization"] = "Bearer " + TOKEN["value"]
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body else None,
                                 headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def server(sid):
    return next(s for s in api("/api/overview")[1]["servers"] if s["id"] == sid)


def events():
    return [e["message"] for e in api("/api/events")[1]]


def wait(cond, secs=30):
    end = time.time() + secs
    while time.time() < end:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  -- ' + detail if detail and not ok else ''}", flush=True)


def a3pid(a3):
    out = subprocess.run(["pgrep", "-f", f"^{a3}/server/arma3server_x64"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def main():
    rcon = FakeWebRcon(password="sandbox-rcon-pw")
    server_rcon_port = rcon.port
    app = setup(rcon.port)
    a3 = SB / "arma3"
    print(f"sandbox: {SB}")
    mgr = start_manager(app)
    try:
        check("agent answers /api/v1/info", wait(lambda: api("/api/info")[1]["name"] == "HostSpawn", 15))
        code, me = api("/api/setup", {"username": "sandboxowner", "password": "sandbox horse battery", "device": "e2e"})
        TOKEN["value"] = me.get("token", "")
        check("first-run setup gives the app a device token", code == 200 and TOKEN["value"] and me["role"] == "admin")
        check("manager starts in live mode", wait(lambda: api("/api/overview")[1]["mode"] == "live", 15))
        ov = api("/api/overview")[1]
        check("3 servers listed", [s["id"] for s in ov["servers"]] == ["rust", "reforger", "arma3"])
        check("rust RCON check ok (fake WebRCON)", wait(lambda: (server("rust").get("checks") or {}).get("rcon", {}).get("ok") is True, 15))
        check("rust players/fps filled from RCON", (server("rust")["status"].get("players"), server("rust")["status"].get("fps")) == (7, 99))
        check("arma3/reforger show RCON as not set up", server("reforger")["checks"]["rcon"] is None)
        check("reforger players/fps read from console log",
              (server("reforger")["status"].get("players"), server("reforger")["status"].get("fps")) == (2, 60.0))

        # --- Arma 3 lifecycle ---
        check("arma3 start accepted", api("/api/action", {"server": "arma3", "action": "start"})[0] == 202)
        check("arma3 becomes running (answers A2S)", wait(lambda: server("arma3")["status"]["state"] == "running", 20))
        p1 = a3pid(a3)
        os.kill(p1, signal.SIGKILL)
        check("crash: watchdog restarts it", wait(lambda: a3pid(a3) not in (None, p1), 20))
        check("crash: alert in events", wait(lambda: "server not running - restarting" in events(), 10))
        check("crash: running again", wait(lambda: server("arma3")["status"]["state"] == "running", 20))

        (a3 / "server/HANG").touch()
        p2 = a3pid(a3)
        check("freeze: watchdog restarts a server that stops answering", wait(lambda: a3pid(a3) not in (None, p2), 30))
        (a3 / "server/HANG").unlink()
        check("freeze: alert in events", any("hung" in m for m in events()))
        check("freeze: running again", wait(lambda: server("arma3")["status"]["state"] == "running", 20))

        (a3 / "server/CRASH").touch()
        os.kill(a3pid(a3), signal.SIGKILL)
        check("crash loop: watchdog gives up after 6 failed starts", wait(lambda: any("CRASH LOOP" in m and "stopped trying" in m for m in events()), 60))
        check("crash loop: update was tried at 3 failed starts", (SB / "steam_calls").exists())
        check("crash loop: dashboard shows maintenance + flag",
              wait(lambda: server("arma3")["status"]["state"] == "maintenance" and
                   any("Crash loop" in f for f in server("arma3")["status"]["flags"]), 6), str(server("arma3")["status"]))
        time.sleep(5)
        check("crash loop: no more restart attempts after giving up", a3pid(a3) is None)
        (a3 / "server/CRASH").unlink()
        api("/api/action", {"server": "arma3", "action": "start"})
        check("recover: Start after fixing works and clears the flag",
              wait(lambda: server("arma3")["status"]["state"] == "running" and not server("arma3")["status"]["flags"], 20),
              str(server("arma3")["status"]))

        # --- Arma 3 update paths ---
        check("update needs typed confirm", api("/api/action", {"server": "arma3", "action": "update"})[0] == 400)
        (SB / "steam_reply").write_text("Please enter the Steam Guard code:\n")
        n_before = len((SB / "steam_calls").read_text().splitlines())
        api("/api/action", {"server": "arma3", "action": "update", "confirm": "update"})
        check("steam guard: alert, flagged, not retried",
              wait(lambda: any("Steam login needed" in f for f in server("arma3")["status"]["flags"]), 20)
              and len((SB / "steam_calls").read_text().splitlines()) == n_before + 1)
        check("steam guard: server started again after the failed update", wait(lambda: server("arma3")["status"]["state"] == "running", 20))
        (SB / "steam_reply").write_text("Success! App '233780' fully installed.\n")

        # --- two actions at once ---
        count = lambda: len(subprocess.run(["pgrep", "-f", f"^{a3}/server/arma3server_x64"], capture_output=True, text=True).stdout.split())
        api("/api/action", {"server": "arma3", "action": "restart", "confirm": "restart"})
        api("/api/action", {"server": "arma3", "action": "restart", "confirm": "restart"})
        seen = []
        end = time.time() + 15
        while time.time() < end:  # sample through the whole restart: a 2nd copy must never appear
            seen.append(count()); time.sleep(0.2)
        check("double restart: second one refused as busy", any("another Arma 3 action is running" in m for m in events()))
        check("double restart: never more than one server process", max(seen) == 1, f"counts seen: {sorted(set(seen))}")
        check("double restart: running again, one process",
              wait(lambda: server("arma3")["status"]["state"] == "running" and count() == 1, 30), f"now {count()}")

        # --- Rust (fake rustctl) ---
        check("rust restart needs confirm", api("/api/action", {"server": "rust", "action": "restart"})[0] == 400)
        api("/api/action", {"server": "rust", "action": "restart", "confirm": "restart"})
        check("rust restart calls rustctl with the countdown", wait(lambda: "restart 300" in (SB / "rust/calls").read_text(), 10))
        code, body = api("/api/action", {"server": "rust", "action": "send", "arg": "say hi\nquit"})
        check("rust console command sent without the newline", code == 200 and "cmd say hiquit" in (SB / "rust/calls").read_text())
        time.sleep(2)  # the fake restart takes 1 s and then marks itself running
        (SB / "rust/state").write_text("stopped")
        check("rust down: alert raised", wait(lambda: any("went down (rustctl watchdog" in m for m in events()), 15))
        (SB / "rust/state").write_text("running")
        check("rust back: info event", wait(lambda: events().count("server is running") >= 1, 15))

        # --- RCON down / back ---
        rcon.close()
        check("RCON down: alert after 3 failed checks", wait(lambda: any(m.startswith("RCON not answering for 3 min") for m in events()), 20))
        check("RCON down: dashboard shows it failing", server("rust")["checks"]["rcon"]["ok"] is False)
        rcon = FakeWebRcon(password="sandbox-rcon-pw")
        toml = app / "config/servers.toml"
        toml.write_text(toml.read_text().replace(f"rcon_port = {server_rcon_port}", f"rcon_port = {rcon.port}"))

        # --- Reforger (fake systemd) ---
        api("/api/action", {"server": "reforger", "action": "restart", "confirm": "restart"})
        check("reforger restart goes to systemctl", wait(lambda: "restart fake-unit" in (SB / "systemd/calls").read_text(), 10))
        (SB / "systemd/props").write_text("ActiveState=active\nSubState=running\nMainPID=1\nNRestarts=1\nLoadState=loaded\n")
        check("reforger crash restart by systemd: alert", wait(lambda: "systemd restarted it after a crash" in events(), 15))
        code, _ = api("/api/action", {"server": "reforger", "action": "send", "arg": "x"})
        check("reforger has no console (refused)", code == 400)

        # --- scheduled restart ---
        now = datetime.now(ZoneInfo("Europe/London"))
        nxt = (now + timedelta(minutes=1 if now.second < 45 else 2)).strftime("%H:%M")
        toml = app / "config/servers.toml"
        toml.write_text(toml.read_text().replace("restart_times = []", f'restart_times = ["{nxt}"]'))
        (app / "config/settings.toml").write_text(f'[community]\nname = "Sandbox"\n[backups]\nenabled = true\ntime = "{nxt}"\n'
                                                 '[limits]\nmax_instances = 3\n')  # the multi-server sandbox needs 3
        os.killpg(mgr.pid, signal.SIGTERM); mgr.wait(10)
        mgr = start_manager(app)
        wait(lambda: api("/api/overview")[0] == 200, 15)
        before = a3pid(a3)
        check(f"scheduled restart at {nxt} UK happens", wait(lambda: any(m.startswith("scheduled restart (with update) at ") for m in events()), 190))
        check("daily backup ran for all 3 servers", wait(lambda: all(any(e["server"] == sid and e["message"].startswith("daily backup:")
              for e in api("/api/events")[1]) for sid in ("rust", "reforger", "arma3")), 100))  # the restart event comes up to a minute before HH:MM
        check("daily backup: real Arma 3 archive written", any((a3 / "backups").glob("arma3_*.tar.gz")))
        check("RCON back: answering again after the manager restart", wait(lambda: server("rust")["checks"]["rcon"]["ok"] is True, 15))
        check("community name shown", api("/api/overview")[1]["community"] == "Sandbox")
        check("scheduled restart: running again with a new process",
              wait(lambda: server("arma3")["status"]["state"] == "running" and a3pid(a3) not in (None, before)
                   and server("arma3")["health"], 40))

        # --- manager restart doesn't touch game servers ---
        time.sleep(3)  # let the scheduled restart's thread finish
        keep = a3pid(a3)
        os.killpg(mgr.pid, signal.SIGTERM); mgr.wait(10)
        check("manager stopped: Arma 3 keeps running", a3pid(a3) == keep)
        mgr = start_manager(app)
        check("manager back: sees the same Arma 3 process, no second copy",
              wait(lambda: server("arma3")["status"]["state"] == "running", 20) and a3pid(a3) == keep
              and len(subprocess.run(["pgrep", "-f", f"^{a3}/server/arma3server_x64"], capture_output=True, text=True).stdout.split()) == 1)

        # --- dev mode on the same sandbox ---
        os.killpg(mgr.pid, signal.SIGTERM); mgr.wait(10)
        (app / "config/instance.env").write_text(f"MODE=dev\nPORT={PORT}\nINTERVAL=2\n")
        mgr = start_manager(app)
        wait(lambda: api("/api/overview")[1]["mode"] == "dev", 15)
        check("dev copy: actions refused", api("/api/action", {"server": "arma3", "action": "start"})[0] == 403)
        os.kill(a3pid(a3), signal.SIGKILL)
        time.sleep(8)
        check("dev copy: no watchdog (crashed server NOT restarted)", a3pid(a3) is None)

        # --- load ---
        import concurrent.futures as cf
        t = time.time()
        with cf.ThreadPoolExecutor(20) as ex:
            codes = list(ex.map(lambda _: api("/api/overview")[0], range(100)))
        check(f"100 dashboard loads, 20 at once ({time.time() - t:.1f}s)", codes.count(200) == 100)
        rss = int(Path(f"/proc/{mgr.pid}/status").read_text().split("VmRSS:")[1].split()[0]) // 1024
        check(f"manager memory small ({rss} MB)", rss < 100)

        # --- secrets ---
        blob = "".join(p.read_text(errors="ignore") for p in SB.rglob("*") if p.is_file() and p.suffix in (".log", ".jsonl", ".out"))
        check("no password in any sandbox log/event file", PW not in blob and "e2e-admin-secret" not in blob)
    finally:
        try:
            os.killpg(mgr.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        subprocess.run(["tmux", "kill-session", "-t", "=" + SESSION], capture_output=True)
        p = a3pid(a3)
        if p:
            os.kill(p, signal.SIGKILL)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
