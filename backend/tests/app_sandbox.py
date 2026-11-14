"""A sandbox agent for trying the HostSpawn app: the end-to-end sandbox's three fake servers (fake rustctl, fake
systemd unit, fake Arma 3), a fresh copy of Manager in live mode on port 8199, nothing real touched. Servers created
from the app here use tests/fakes/fake_steamcmd.sh: no Steam downloads, just a tiny fake game that answers queries.
Usage: python3 -I backend/tests/app_sandbox.py [sandbox_dir]     (Ctrl-C stops it and its fakes; SANDBOX_PORT=8198 for a second one)
It starts empty, like a fresh install. HOSTSPAWN_PRACTICE=1 adds the three practice servers (fake Rust, Reforger, Arma 3).
Then: HOSTSPAWN_AGENT=http://127.0.0.1:8199 frontend/app/build/linux/x64/release/bundle/hostspawn"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import e2e_sandbox as e2e  # noqa: E402
from tests.fakes.fake_webrcon import FakeWebRcon  # noqa: E402


def _stop(*_):
    raise KeyboardInterrupt


def main():
    # New servers made in the sandbox use a fake SteamCMD: nothing is downloaded from Steam, the "game" is tiny.
    os.environ["MANAGER_TEST_STEAMCMD"] = str(Path(__file__).parent / "fakes" / "fake_steamcmd.sh")
    rcon = FakeWebRcon(password="sandbox-rcon-pw")
    app = e2e.setup(rcon.port)
    if os.environ.get("HOSTSPAWN_PRACTICE") != "1":  # like a fresh install: no instances until you make one
        (app / "config/servers.toml").write_text("# Instances set up by hand (none). Created ones are in servers.d/.\n")
    mgr = e2e.start_manager(app)
    print(f"sandbox agent: http://127.0.0.1:{e2e.PORT}  (folder {e2e.SB})", flush=True)
    signal.signal(signal.SIGTERM, _stop)  # clean up on TERM too, not only Ctrl-C
    try:
        while mgr.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:  # stop everything the sandbox started (same cleanup as the end-to-end run)
        try:
            os.killpg(mgr.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        subprocess.run(["tmux", "kill-session", "-t", "=" + e2e.SESSION], capture_output=True)
        # servers created from the app inside the sandbox (only sessions whose folder is in the sandbox)
        names = subprocess.run(["tmux", "ls", "-F", "#S"], capture_output=True, text=True).stdout.split()
        for n in (x for x in names if x.startswith("manager-")):
            where = subprocess.run(["tmux", "display-message", "-p", "-t", "=" + n, "#{pane_current_path}"],
                                   capture_output=True, text=True).stdout.strip()
            if where.startswith(str(e2e.SB.resolve()) + "/"):
                subprocess.run(["tmux", "kill-session", "-t", "=" + n], capture_output=True)
        p = e2e.a3pid(e2e.SB / "arma3")
        if p:
            os.kill(p, signal.SIGKILL)
        rcon.close()


if __name__ == "__main__":
    main()
