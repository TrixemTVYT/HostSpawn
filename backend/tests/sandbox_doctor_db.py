"""Server Doctor and the Database tab end to end, against a RUNNING app sandbox (backend/tests/app_sandbox.py;
SANDBOX_PORT picks it). A fake Rust instance is broken on purpose (its port taken by another program, its game file
deleted, a crash while the port is taken) and the Doctor must name each cause; Repair from the Doctor's fix must work.
Then a plugin's SQLite file in the instance is browsed, a read-only query refuses a change, and an edit makes a
backup first. About 3 minutes.
Usage: SANDBOX_PORT=8198 python3 -I backend/tests/sandbox_doctor_db.py"""
import os
import signal
import socket
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import sandbox_multi_servers as m  # noqa: E402
from tests.sandbox_install import run  # noqa: E402

check, call = m.check, m.call


def doctor(tok, sid):
    return call(f"doctor?server={sid}", tok=tok)


def ids(d):
    return [f["id"] for f in d["findings"]]


def main():
    tok = m.sign_in()
    job = m.finish(tok, m.create(tok, "rust", "vanilla", {"name": "Doctor Rust", "recommended_restarts": False,
                                                          "restart_times": []}, f"doc-{os.getpid()}"))
    sid, root = job["server_id"], Path(job["root"])
    holder = None
    try:
        check(m.wait_state(tok, sid, "running", 60), "instance running")
        d = doctor(tok, sid)
        check(d["findings"] == [] and d["checked"] > 10, f"Doctor on a healthy instance: all good ({d['checked']} checks)")
        s = call(f"server?id={sid}", tok=tok)
        check(s.get("folder") == str(root), "admins get the instance folder for Open folder")
        port = s["ports"]["port"]

        # 1. Another program takes the game port while the instance is stopped.
        call("action", {"server": sid, "action": "stop", "confirm": "stop"}, tok)
        check(m.wait_state(tok, sid, "maintenance", 30) or m.wait_state(tok, sid, "stopped", 5), "stopped")
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("0.0.0.0", port))
        d = doctor(tok, sid)
        check(ids(d)[:1] == ["port_taken"] and str(port) in d["findings"][0]["title"],
              f"port taken: \"{d['findings'][0]['title'] if d['findings'] else 'nothing found'}\"")
        holder.close()
        holder = None
        check("port_taken" not in ids(doctor(tok, sid)), "port free again: the finding goes away")

        # 2. The game file is deleted: the Doctor offers Repair, and the fix works.
        (root / "server" / "RustDedicated").unlink()
        call("action", {"server": sid, "action": "start"}, tok)
        time.sleep(3)
        d = doctor(tok, sid)
        f = next((f for f in d["findings"] if f["id"] == "not_installed"), None)
        check(f is not None and f["action"]["op"] == "repair", "missing game file: Doctor offers Repair")
        j = run(tok, sid, f["action"]["op"]) if f else {"result": "skipped", "summary": ""}
        check(j["result"] == "ok" and (root / "server" / "RustDedicated").exists(), f"Repair from the Doctor: {j['summary']}")
        check("not_installed" not in ids(doctor(tok, sid)), "after Repair the finding is gone")

        # 3. A crash while another program holds the port: the alert says why.
        if not m.wait_state(tok, sid, "running", 60):
            call("action", {"server": sid, "action": "start"}, tok)
        check(m.wait_state(tok, sid, "running", 60), "running again")
        pid = call(f"server?id={sid}", tok=tok)["status"].get("pid")
        os.kill(int(pid), signal.SIGKILL)
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("0.0.0.0", port))
        said, end = None, time.time() + 150
        while time.time() < end and not said:
            said = next((e["message"] for e in call("events", tok=tok)
                         if e["server"] == sid and e["message"].startswith("Server Doctor:")), None)
            time.sleep(3)
        check(said is not None and "port" in said.lower(), f"crash alert explains it: \"{said}\"")
        holder.close()
        holder = None

        # 4. Database: a plugin's SQLite file inside the instance.
        dbf = root / "server" / "oxide" / "data" / "Economics.db"
        dbf.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(dbf)
        con.execute("CREATE TABLE balances (steamid TEXT PRIMARY KEY, money INTEGER)")
        con.executemany("INSERT INTO balances VALUES (?, ?)", [("p1", 100), ("p2", 250)])
        con.commit()
        con.close()
        srcs = call(f"db?server={sid}", tok=tok)["sources"]
        src = "sqlite:server/oxide/data/Economics.db"
        check([x["id"] for x in srcs] == [src], "Database tab finds the plugin's SQLite file")
        q = f"server={sid}&source={quote(src, safe='')}"
        check(call(f"db/tables?{q}", tok=tok)["tables"] == [{"name": "balances", "rows": 2}], "tables listed")
        rows = call(f"db/rows?{q}&table=balances&order=money&desc=1", tok=tok)
        check(rows["rows"][0] == ["p2", 250] and rows["key"] == ["steamid"], "rows sorted, key column known")
        try:
            call("db/query", {"server": sid, "source": src, "sql": "UPDATE balances SET money = 0"}, tok)
            check(False, "read-only query refuses a change")
        except m.Refused as e:
            check("Turn on Changes" in str(e), "read-only query refuses a change")
        r = call("db/change", {"server": sid, "source": src, "confirm": "CHANGE",
                               "edit": {"op": "update", "table": "balances", "key": {"steamid": "p1"}, "set": {"money": 999}}}, tok)
        bak = root / "backups" / "database" / r["backup"]
        old = sqlite3.connect(bak).execute("SELECT money FROM balances WHERE steamid = 'p1'").fetchone()[0]
        new = sqlite3.connect(dbf).execute("SELECT money FROM balances WHERE steamid = 'p1'").fetchone()[0]
        check(r["changed"] == 1 and new == 999 and old == 100, "edit saved, and the backup has the old value")
    finally:
        if holder:
            holder.close()
        call("delete", {"server": sid, "confirm": job["name"], "keep_backups": False}, tok)
    print(f"\n{m.passed} passed, {len(m.problems)} failed")
    for p in m.problems:
        print("  FAIL", p)
    sys.exit(1 if m.problems else 0)


if __name__ == "__main__":
    main()
