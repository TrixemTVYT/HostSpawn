"""CLI. ./manager.sh <command>
  serve                      run the dashboard (127.0.0.1:8100) + supervisor
  status                     one line per server
  <server> <action> [arg]    start | stop | restart | update | backup | wipe map|full | send "<cmd>" | logs
  init-secrets               create config/secrets.env with generated passwords (never printed)
  pair <username>            a one-time 6-digit code (5 min) to sign the HostSpawn app in on another device
"""
import json
import sys

from .adapters import Unsupported, build
from .supervisor import Supervisor
from .util import init_secrets, instance, load_servers, secrets


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[0] == "pbo":
        from .pbo import main as pbo_main
        return pbo_main(argv[1:])
    if argv[0] == "init-secrets":
        filled = init_secrets()
        print("config/secrets.env ready (chmod 600)." + (f" Generated: {', '.join(filled)}" if filled else ""))
        return 0
    if argv[0] == "pair":
        from .accounts import PAIR_S, Accounts
        try:
            code = Accounts().new_pair_code(argv[1] if len(argv) > 1 else "")
        except ValueError as e:
            print(f"{e} Usage: ./manager.sh pair <username>")
            return 1
        print(f"Pairing code for {argv[1]}: {code}  (one use, expires in {PAIR_S // 60} minutes)")
        return 0
    secrets()  # load secret values so output is redacted
    adapters = build(load_servers())
    inst = instance()
    if argv[0] == "serve":
        from .web import serve
        port = int(argv[1]) if len(argv) > 1 else inst["port"]
        from .deploy import Deployer
        sup = Supervisor(adapters, interval=inst["interval"], control=inst["mode"] == "live")
        httpd = serve(sup, port=port, mode=inst["mode"], allowed_hosts=inst["allowed_hosts"], deployer=Deployer(sup))
        sup.start()
        print(f"HostSpawn ({inst['mode']}) on http://127.0.0.1:{port}", flush=True)
        httpd.serve_forever()
        return 0
    if argv[0] == "status":
        sup = Supervisor(adapters)
        for sid in adapters:
            snap = sup.check(sid)
            st = snap["status"]
            players = st.get("players")
            line = f"{sid:9} {st.get('state'):13}"
            if players is not None:
                line += f" players {players}/{st.get('max_players') or '?'}"
            if st.get("fps") is not None:
                line += f"  fps {st['fps']}"
            line += "  query " + ("ok" if snap["health"] else "-")
            for f in st.get("flags", []):
                line += f"\n{'':10}! {f}"
            print(line)
        return 0
    sid = argv[0]
    if sid not in adapters or len(argv) < 2:
        print(f"unknown server or missing action. Servers: {', '.join(adapters)}")
        return 2
    a, action, arg = adapters[sid], argv[1], (argv[2] if len(argv) > 2 else "")
    if action == "logs":
        for name, text in a.logs().items():
            print(f"===== {name}\n{text}")
        return 0
    if action == "status":
        print(json.dumps(a.status(), indent=2))
        return 0
    if inst["mode"] != "live" and not a.cfg.get("sandbox"):
        print("Development copy: controls are off for real servers. Use the live copy's manager.sh")
        return 2
    try:
        print(a.run_action(action, arg))
        return 0
    except Unsupported as e:
        print(f"{a.name} doesn't support: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
