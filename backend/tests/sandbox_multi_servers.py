"""Several servers at once, against a RUNNING app sandbox (backend/tests/app_sandbox.py on port 8199, fake SteamCMD, fake
games): create three instances (Rust x2 with the same name, Arma 3), check a fourth is refused (limit 3) and that they don't clash; poke at the edges (double click,
two setups at once, wrong confirm, deleting a server HostSpawn didn't make); restart one; back one up; delete them
one by one and check each delete leaves the others running and nothing behind.
Usage: python3 -I backend/tests/sandbox_multi_servers.py      (sign-in: the sandbox test account from the integration tests)
Not run by unittest discovery: it needs the sandbox running and takes about two minutes."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manager import a2s  # noqa: E402

B = f"http://127.0.0.1:{os.environ.get('SANDBOX_PORT', '8199')}/api/v1/"
USER, PASSWORD = "sandboxowner", "sandbox horse battery"  # the integration tests' sandbox account (test values)
problems, passed = [], 0


class Refused(Exception):
    pass


def call(path, body=None, tok=None):
    h = {"X-Manager": "1", "Content-Type": "application/json", **({"Authorization": "Bearer " + tok} if tok else {})}
    r = urllib.request.Request(B + path, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        return json.load(urllib.request.urlopen(r, timeout=30))
    except urllib.error.HTTPError as e:
        raise Refused(json.loads(e.read() or b"{}").get("error", e.code)) from None


def check(ok, what):
    global passed
    print(("  ok    " if ok else "  FAIL  ") + what, flush=True)
    if ok:
        passed += 1
    else:
        problems.append(what)


def sign_in():
    info = call("info")
    if info["setup_needed"]:
        return call("setup", {"username": USER, "password": PASSWORD, "device": "multi-server check"})["token"]
    return call("login", {"username": USER, "password": PASSWORD, "device": "multi-server check"})["token"]


def create(tok, game, mode, options, rid):
    terms = call("terms", tok=tok)["version"]
    for _ in range(40):
        try:
            return call("deploy", {"game": game, "mode": mode, "options": options, "request_id": rid,
                                   "accepted_terms": terms}, tok)
        except Refused as e:
            if "Too many new servers" not in str(e):
                raise
            time.sleep(3)  # the agent allows 3 new servers a minute
    raise Refused("still rate limited after 2 minutes")


def finish(tok, job):
    """Drive a setup to the end: guided steps (ports, Steam login) are skipped or continued."""
    end = time.time() + 180
    while time.time() < end:
        j = call(f"job?id={job['id']}", tok=tok)
        if j["status"] == "done":
            return j
        if j["status"] in ("failed", "cancelled"):
            raise Refused(f"{j['name']} {j['status']}: {j['log'][-3:]}")
        if j["status"] == "waiting":
            for act in ("skip", "continue"):
                try:
                    call("job", {"id": j["id"], "action": act}, tok)
                    break
                except Refused:
                    pass
        time.sleep(0.5)
    raise Refused(f"{job['name']} not ready after 3 minutes")


def servers(tok):
    return {s["id"]: s for s in call("overview", tok=tok)["servers"]}


def wait_state(tok, sid, state, secs=60):
    end = time.time() + secs
    while time.time() < end:
        s = call(f"server?id={sid}", tok=tok)
        if s.get("status", {}).get("state") == state:
            return True
        time.sleep(1)
    return False


def main():
    tok = sign_in()
    before = set(servers(tok))
    specs = [("rust", "vanilla", {"name": "Multi Rust"}),
             ("rust", "vanilla", {"name": "Multi Rust"}),  # same name on purpose
             ("arma3", "vanilla", {"name": "Multi Arma", "steam_user": "fakeuser"})]

    print("1. create three instances (the limit), one after another; a fourth is refused")
    made = []
    for i, (game, mode, opts) in enumerate(specs):
        job = create(tok, game, mode, opts, f"multi-{os.getpid()}-{i}")
        if i == 0:
            again = create(tok, game, mode, opts, f"multi-{os.getpid()}-0")
            check(again["id"] == job["id"], "same request sent twice (double click) gives the same setup, not two")
            try:
                create(tok, "reforger", "conflict", {"name": "Too Soon"}, f"multi-{os.getpid()}-x")
                check(False, "a second setup while one is running is refused")
            except Refused as e:
                check("still being set up" in str(e), f"a second setup while one is running is refused ({e})")
        made.append(finish(tok, job))
        print(f"     {made[-1]['name']}: ready ({made[-1]['server_id']})", flush=True)

    try:
        create(tok, "reforger", "conflict", {"name": "One Too Many"}, f"multi-{os.getpid()}-4")
        check(False, "a fourth instance is refused (limit 3)")
    except Refused as e:
        check("limit of 3 instances" in str(e), f"a fourth instance is refused ({e})")
    sids = [j["server_id"] for j in made]
    now = servers(tok)
    check(all(s in now for s in sids) and set(now) == before | set(sids), "dashboard lists the 3 built-in + 3 new")
    check(len(set(sids)) == 3, "three different server ids")
    roots = [j["root"] for j in made]
    check(len(set(roots)) == 3 and all(Path(r).is_dir() for r in roots), "three separate folders, even with the same name")
    ports = [p for s in sids for p in now[s]["ports"].values()]
    ports += [p for s in before for p in now[s]["ports"].values()]
    check(len(ports) == len(set(ports)), f"no port used twice across all 6 servers ({sorted(ports)})")

    print("2. all three running and answering")
    for s in sids:
        check(wait_state(tok, s, "running"), f"{now[s]['name']} ({s}) is running")
        q = now[s]["ports"].get("query_port") or (now[s]["ports"]["port"] + 1)
        check(a2s.info("127.0.0.1", q, timeout=2) is not None, f"{s} answers game queries on {q}")

    print("3. edges")
    try:
        call("delete", {"server": sids[0], "confirm": "wrong name"}, tok)
        check(False, "delete with the wrong name is refused")
    except Refused as e:
        check("to confirm" in str(e), f"delete with the wrong name is refused ({e})")
    check(Path(roots[0]).is_dir(), "…and nothing was deleted")
    try:
        call("delete", {"server": "rust", "confirm": now["rust"]["name"]}, tok)
        check(False, "a server HostSpawn didn't make can't be deleted")
    except Refused as e:
        check(True, f"a server HostSpawn didn't make can't be deleted ({e})")
    check("rust" in servers(tok), "…and it is still listed")
    try:
        call("delete", {"server": "../../etc", "confirm": "x"}, tok)
        check(False, "a made-up server id is refused")
    except Refused:
        check(True, "a made-up server id is refused")

    print("4. restart one: the others keep running")
    call("action", {"server": sids[1], "action": "restart", "confirm": "restart"}, tok)
    t0, end = time.time() - 1, time.time() + 120  # the restart runs in the background: wait for it to finish
    while time.time() < end and not any(e["server"] == sids[1] and e["message"].startswith("restart:")
                                        and e["t"] >= t0 for e in call("overview", tok=tok)["events"]):
        time.sleep(1)
    check(time.time() < end, f"{sids[1]} restart finished")
    q1, end = now[sids[1]]["ports"]["query_port"], time.time() + 90  # the restarted game boots for a moment
    while time.time() < end and a2s.info("127.0.0.1", q1, timeout=1) is None:
        time.sleep(0.5)
    time.sleep(3)  # let the health loop see it answering again
    check(wait_state(tok, sids[1], "running", 90), f"{sids[1]} back up after restart")
    for s in (sids[0], sids[2]):
        check(call(f"server?id={s}", tok=tok)["status"]["state"] == "running", f"{s} unaffected by the restart")

    print("5. back up one, then delete it keeping backups")
    r = call("action", {"server": sids[2], "action": "backup"}, tok)
    end = time.time() + 60
    while time.time() < end and not any((Path(roots[2]) / "backups").glob("*")):
        time.sleep(1)
    check(any((Path(roots[2]) / "backups").glob("*")), f"backup made ({r.get('message')})")
    msg = call("delete", {"server": sids[2], "confirm": now[sids[2]]["name"], "keep_backups": True}, tok)["message"]
    check("backups kept" in msg, f"deleted, backups kept ({msg})")
    kept = Path(roots[2]).parents[1] / "_kept_backups"
    check(any(kept.glob(f"{sids[2]}_*/*")), "kept backups are in servers/_kept_backups")

    print("6. delete the rest one by one")
    for i in (0, 1):
        call("delete", {"server": sids[i], "confirm": now[sids[i]]["name"], "keep_backups": False}, tok)
        left = servers(tok)
        check(sids[i] not in left and not Path(roots[i]).exists(), f"{sids[i]} gone from the list and the disk")
        for j in range(3):
            if j != i and sids[j] in left:
                st = left[sids[j]]["status"]
                check(st["state"] == "running", f"  {sids[j]} still running ({st})")
    check(set(servers(tok)) == before, "back to the 3 built-in servers")
    check(call("overview", tok=tok)["jobs"] == [], "no setups left on the dashboard")
    time.sleep(2)
    left_procs = []
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                c = open(f"/proc/{p}/cmdline", "rb").read().decode(errors="replace")
            except OSError:
                continue
            if any(r in c for r in roots):
                left_procs.append(p)
    check(not left_procs, f"no game processes left running ({left_procs})")
    games = Path(roots[0]).parents[1]
    check(sorted(x.name for x in games.iterdir()) == ["_kept_backups", "_shared"],
          f"servers/ holds only _shared and _kept_backups ({sorted(x.name for x in games.iterdir())})")
    secrets = (games.parent / "config" / "secrets.env").read_text()
    check(not any(s.upper().replace("-", "_") in secrets for s in sids), "their secrets were removed")

    print(f"\n{passed} passed, {len(problems)} failed")
    for p in problems:
        print("  FAIL", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
