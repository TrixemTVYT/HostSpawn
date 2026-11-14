"""Players and moderation end to end, against a RUNNING app sandbox (backend/tests/app_sandbox.py; SANDBOX_PORT picks it):
two Rust servers and an Arma 3 server, each with made-up players over RCON (WebRCON and BattlEye fakes). Checks the
same player shows on both Rust servers, ban once = gone everywhere, kick on one server only, a temporary Arma ban,
say to all servers, unban, the audit log; then deletes the servers again.
Usage: SANDBOX_PORT=8198 python3 -I backend/tests/sandbox_players.py"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import sandbox_multi_servers as m  # noqa: E402

check, call = m.check, m.call


def wait(f, secs=60):
    end = time.time() + secs
    while time.time() < end:
        try:
            v = f()
            if v:
                return v
        except m.Refused:
            pass
        time.sleep(1)
    return None


def main():
    tok = m.sign_in()
    print("1. create Rust x2 and Arma 3")
    made = []
    for i, (game, mode, opts) in enumerate([("rust", "vanilla", {"name": "Players Rust A"}),
                                            ("rust", "vanilla", {"name": "Players Rust B"}),
                                            ("arma3", "vanilla", {"name": "Players Arma", "steam_user": "fakeuser"})]):
        made.append(m.finish(tok, m.create(tok, game, mode, opts, f"players-{os.getpid()}-{i}")))
    ra, rb, a3 = (j["server_id"] for j in made)
    try:
        print("2. players show up (once a minute live; every 2 s in the sandbox); they come and go")
        online = lambda sid: call(f"players?server={sid}", tok=tok)
        keys = lambda sid: [p["key"] for p in online(sid)]
        for sid in (ra, rb, a3):
            check(wait(lambda: len(online(sid)) >= 2), f"{sid}: fake players online")
        srv = call(f"server?id={ra}", tok=tok)
        check(srv["players_supported"] and srv["players_tracked"] and srv["can_moderate"], "Rust panel: players on")
        both = lambda: [p for p in online(ra) if p["key"] in keys(rb)]
        first = wait(both)[0]  # the same made-up player is on both Rust servers
        prof = call(f"player?key={first['key']}", tok=tok)
        check(sorted(o["server"] for o in prof["online"]) == sorted([ra, rb]),
              f"{first['name']}: one profile, online on both Rust servers")
        check(not any("10.9.9.9" in str(x) for x in (online(ra), prof)), "no IP addresses anywhere in the API")

        print(f"3. ban {first['name']} once: gone everywhere")
        r = call("players", {"action": "ban", "key": first["key"], "minutes": 0, "reason": "sandbox test ban"}, tok)
        check(sorted(r["applied"]) == sorted([ra, rb]), f"ban applied on both Rust servers; Arma uses other ids ({r['applied']})")
        check(wait(lambda: all(first["key"] not in keys(s) for s in (ra, rb)), 20), "gone from both Rust servers")
        check(first["key"] in [b["key"] for b in call("bans", tok=tok)], "on the ban list")
        time.sleep(3)
        check(all(first["key"] not in keys(s) for s in (ra, rb)), "and doesn't come back")

        print("4. kick on one server only")
        second = wait(lambda: [p for p in both() if p["key"] != first["key"]], 90)[0]
        call("players", {"action": "kick", "server": ra, "key": second["key"], "reason": "sandbox kick"}, tok)
        check(second["key"] not in keys(ra), f"{second['name']} kicked from Rust A")
        check(second["key"] in keys(rb), f"{second['name']} still on Rust B")

        print("5. temporary ban on Arma 3 (BattlEye)")
        third = wait(lambda: online(a3))[0]
        check(third["key"].startswith("be:"), "Arma players are keyed by BattlEye GUID")
        call("players", {"action": "ban", "key": third["key"], "minutes": 30, "reason": "sandbox temp ban"}, tok)
        check(wait(lambda: third["key"] not in keys(a3), 20), f"{third['name']} gone from Arma 3")
        p = call(f"player?key={third['key']}", tok=tok)
        check(p["ban"] and p["ban"]["until"] and p["ban"]["until"] - time.time() > 25 * 60, "the ban ends in 30 minutes")

        print("6. message every server; unban; audit; search")
        r = call("players", {"action": "say", "text": "Sandbox: hello everyone"}, tok)
        check(sorted(r["servers"]) == sorted([ra, rb, a3]), f"message sent to all three ({r['message']})")
        call("players", {"action": "unban", "key": first["key"]}, tok)
        check(first["key"] not in [b["key"] for b in call("bans", tok=tok)], f"{first['name']} unbanned")
        actions = [a["action"] for a in call("audit", tok=tok)][:5]
        check(actions == ["unban", "say", "ban", "kick", "ban"], f"audit log in order ({actions})")
        found = call(f"players/search?q={third['name'][:4]}", tok=tok)
        check(any(f["key"] == third["key"] and f["banned"] for f in found), "search finds the Arma player as banned")
    finally:
        print("7. clean up")
        names = {j["server_id"]: j["name"] for j in made}
        for sid in (ra, rb, a3):
            try:
                call("delete", {"server": sid, "confirm": names[sid], "keep_backups": False}, tok)
            except m.Refused as e:
                print("  could not delete", sid, e)
    print(f"\n{m.passed} passed, {len(m.problems)} failed")
    for p in m.problems:
        print("  FAIL", p)
    sys.exit(1 if m.problems else 0)


if __name__ == "__main__":
    main()
