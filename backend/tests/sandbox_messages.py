"""In-game messages end to end, against a RUNNING app sandbox (backend/tests/app_sandbox.py; SANDBOX_PORT picks it):
a Rust server gets a scheduled message at the next minute and a restart two minutes ahead with a 1-minute
countdown. The fake game writes what was said to server/said.txt. About 4 minutes.
Usage: SANDBOX_PORT=8198 python3 -I backend/tests/sandbox_messages.py"""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import sandbox_multi_servers as m  # noqa: E402

check, call = m.check, m.call
UK = ZoneInfo("Europe/London")


def wait(f, secs):
    end = time.time() + secs
    while time.time() < end:
        if f():
            return True
        time.sleep(1)
    return False


def game_started(exe: str) -> float | None:
    """Start time (unix) of the process whose argv[0] is exe, like Manager finds its servers."""
    boot = time.time() - float(Path("/proc/uptime").read_text().split()[0])
    for d in Path("/proc").iterdir():
        try:
            if d.name.isdigit() and (d / "cmdline").read_bytes().split(b"\0", 1)[0].decode() == exe:
                return boot + int((d / "stat").read_text().rsplit(")", 1)[1].split()[19]) / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            continue
    return None


def main():
    tok = m.sign_in()
    job = m.finish(tok, m.create(tok, "rust", "vanilla", {"name": "Messages Rust", "recommended_restarts": False,
                                                          "restart_times": []}, f"msg-{os.getpid()}"))
    sid = job["server_id"]
    said = Path(job["root"]) / "server" / "said.txt"
    try:
        check(call(f"server?id={sid}", tok=tok)["can_say"], "Rust server can show messages")
        now = datetime.now(UK)
        if now.second > 40:  # don't race the minute boundary
            time.sleep(62 - now.second)
            now = datetime.now(UK)
        msg_at = (now + timedelta(minutes=1)).strftime("%H:%M")
        restart_at = (now + timedelta(minutes=2)).strftime("%H:%M")
        call("server/settings", {"server": sid, "messages": [{"text": "Hello {server}: {players} on", "times": [msg_at]}],
                                 "restart_times": [restart_at], "restart_warn_s": 60}, tok)
        print(f"  message at {msg_at}, restart at {restart_at} with a 1-minute countdown")
        check(wait(lambda: said.exists() and "Hello Messages Rust:" in said.read_text(), 100),
              "scheduled message said in game, placeholders filled")
        text = lambda: said.read_text() if said.exists() else ""
        check(wait(lambda: "Server restart in 1 minute." in text(), 100), "countdown: 1 minute")
        check(wait(lambda: "Server restart in 10 seconds." in text(), 70), "countdown: 30 and 10 seconds")
        events = lambda: [e["message"] for e in call("events", tok=tok) if e["server"] == sid]
        check(any(f"scheduled restart (with update) at {restart_at} UK: countdown started" in e for e in events()),
              "restart event says when and that the countdown started")
        exe = str(Path(job["root"]) / "server" / "RustDedicated")
        restart_t = datetime.strptime(f"{now:%Y-%m-%d} {restart_at}", "%Y-%m-%d %H:%M").replace(tzinfo=UK).timestamp()
        if restart_t < now.timestamp():
            restart_t += 86400
        check(wait(lambda: (game_started(exe) or 0) >= restart_t, 120),
              f"the game was restarted at {restart_at}, not later ({datetime.fromtimestamp(game_started(exe) or 0, UK):%H:%M:%S})")
        print("    said in game:\n      " + "\n      ".join(text().splitlines()))
        r = call("players", {"action": "say", "text": "manual hello"}, tok)
        check(sid in r["servers"] and wait(lambda: "manual hello" in text(), 10), "message to every server reaches it")
    finally:
        call("delete", {"server": sid, "confirm": job["name"], "keep_backups": False}, tok)
    print(f"\n{m.passed} passed, {len(m.problems)} failed")
    for p in m.problems:
        print("  FAIL", p)
    sys.exit(1 if m.problems else 0)


if __name__ == "__main__":
    main()
