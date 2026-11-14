"""Installation management end to end, against a RUNNING app sandbox (backend/tests/app_sandbox.py; SANDBOX_PORT
picks it): a fake Rust instance is checked for updates, updated, checked and repaired, cleaned up and reinstalled
through the API, as the app does it, with a save file that must survive. About 2 minutes.
Usage: SANDBOX_PORT=8198 python3 -I backend/tests/sandbox_install.py"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import sandbox_multi_servers as m  # noqa: E402

check, call = m.check, m.call


def run(tok, sid, action, **extra):
    call("install", {"server": sid, "action": action, **extra}, tok)
    end = time.time() + 120
    while time.time() < end:
        j = call(f"install?server={sid}", tok=tok)["job"]
        if j and j["state"] != "running":
            return j
        time.sleep(1)
    return {"result": "timed out", "summary": ""}


def main():
    tok = m.sign_in()
    job = m.finish(tok, m.create(tok, "rust", "vanilla", {"name": "Install Rust", "recommended_restarts": False,
                                                          "restart_times": []}, f"inst-{os.getpid()}"))
    sid, root = job["server_id"], Path(job["root"])
    save = root / "server" / "server" / "server" / "my.sav"  # in the Rust identity folder (kept and backed up)
    try:
        info = call(f"install?server={sid}", tok=tok)
        check(info["installed"] and info["build"] == "1000", f"installed, build {info['build']} recorded")
        check(info["sizes"]["total"] > 0 and info["disk_free_gb"] > 0, "sizes and free disk shown")
        call("install", {"server": sid, "action": "check"}, tok)
        end = time.time() + 30
        while time.time() < end and call(f"install?server={sid}", tok=tok)["update_available"] is None:
            time.sleep(1)
        check(call(f"install?server={sid}", tok=tok)["update_available"] is False, "update check: up to date")
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text("precious")
        j = run(tok, sid, "update")
        check(j["result"] == "ok" and "running" in j["summary"], f"update: {j['summary']}")
        check(m.wait_state(tok, sid, "running", 30), "running after the update")
        (root / "server" / "RustDedicated").unlink()  # a broken install
        j = run(tok, sid, "repair")
        check(j["result"] == "ok" and (root / "server" / "RustDedicated").exists(), f"repair: {j['summary']}")
        j = run(tok, sid, "cleanup")
        check(j["result"] == "ok", f"clean up: {j['summary']}")
        (root / "server" / "junk.bin").write_text("old")
        try:
            call("install", {"server": sid, "action": "reinstall", "confirm": "nope"}, tok)
            check(False, "reinstall with the wrong name is refused")
        except m.Refused as e:
            check("to confirm" in str(e), "reinstall with the wrong name is refused")
        j = run(tok, sid, "reinstall", confirm=job["name"])
        check(j["result"] == "ok" and not (root / "server" / "junk.bin").exists(), f"reinstall: {j['summary']}")
        check(save.exists() and save.read_text() == "precious", "the save survived update, repair and reinstall")
        check(m.wait_state(tok, sid, "running", 30), "running after the reinstall")
        h = call(f"install?server={sid}", tok=tok)["history"]
        check([x["op"] for x in h[:4]] == ["reinstall", "cleanup", "repair", "update"], "history lists all four")
    finally:
        call("delete", {"server": sid, "confirm": job["name"], "keep_backups": False}, tok)
    print(f"\n{m.passed} passed, {len(m.problems)} failed")
    for p in m.problems:
        print("  FAIL", p)
    sys.exit(1 if m.problems else 0)


if __name__ == "__main__":
    main()
