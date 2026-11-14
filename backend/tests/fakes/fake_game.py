"""Fake game server for tests: answers A2S_INFO on (-port + 1), exits cleanly on SIGINT.
Files next to the exe change its behaviour (so a test can make it misbehave):
  CRASH      exit at once (crash loop)
  HANG       stay alive but stop answering queries (frozen server)
  IGNORE_INT ignore SIGINT (only SIGTERM/SIGKILL stop it)
  GARBAGE    answer with a malformed packet
FAKE_RCON=rust:<port>:<password> or be:<port>:<password> also answers RCON (fake_webrcon / fake_battleye) with a few
made-up players who join and leave every few seconds; kicked players leave, banned ones can't come back."""
import os
import signal
import socket
import struct
import sys

exe_dir = os.path.dirname(os.environ.get("FAKE_EXE", sys.argv[0]))
flag = lambda name: os.path.exists(os.path.join(exe_dir, name))
if flag("CRASH"):
    sys.exit(1)
port = next(int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("-port="))
signal.signal(signal.SIGINT, signal.SIG_IGN if flag("IGNORE_INT") else (lambda *a: sys.exit(0)))
if os.environ.get("FAKE_RCON"):
    import random
    import threading
    import time
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # run with -I: our own folder isn't on the path
    kind, rport, rpw = os.environ["FAKE_RCON"].split(":", 2)
    # made-up players (test values): Steam ids in an unused range, BattlEye GUIDs from their number
    pool = [(name, f"7656119000000{1000 + n}", f"{n:032x}")
            for n, name in enumerate("Alice Bob Carol Dave Erin Frank".split(), 1)]
    if kind == "rust":
        from fake_webrcon import FakeWebRcon
        rc = FakeWebRcon(password=rpw, port=int(rport), on_quit=lambda: os._exit(0))
        online = lambda: [p["SteamID"] for p in rc.players]
        def join(name, sid, guid):
            if sid not in rc.banned and sid not in online():
                rc.players.append({"SteamID": sid, "DisplayName": name, "Ping": random.randint(20, 90),
                                   "ConnectedSeconds": 0, "Address": "10.9.9.9:5000"})
        def leave():
            if rc.players:
                rc.players.pop(random.randrange(len(rc.players)))
    else:
        from fake_battleye import FakeBattlEye
        rc = FakeBattlEye(password=rpw, port=int(rport))
        online = lambda: [p["guid"] for p in rc.players]
        def join(name, sid, guid):
            if guid not in online():
                rc.add(name, guid, "10.9.9.9")
        def leave():
            if rc.players:
                rc.players.pop(random.randrange(len(rc.players)))
    said_run = rc.run
    def run(cmd):  # everything said in game goes to said.txt next to the server, so sandbox checks can read it
        if cmd.startswith("say"):
            with open("said.txt", "a") as f:
                f.write(cmd + "\n")
        return said_run(cmd)
    rc.run = run
    for p in pool[:3]:
        join(*p)
    def churn():
        while True:
            time.sleep(float(os.environ.get("FAKE_CHURN_S", "20")))
            if random.random() < 0.5 and len(rc.players) > 1:
                leave()
            else:
                join(*random.choice(pool))
    threading.Thread(target=churn, daemon=True).start()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", port + 1))
challenge = b"\x01\x02\x03\x04"
while True:
    data, addr = s.recvfrom(2048)
    if flag("HANG") or not data.startswith(b"\xff\xff\xff\xffTSource Engine Query\x00"):
        continue
    if flag("GARBAGE"):
        s.sendto(b"\xff\xff\xff\xffI\x11no-terminators", addr)
        continue
    if data[-4:] != challenge:
        s.sendto(b"\xff\xff\xff\xffA" + challenge, addr)
        continue
    body = b"I\x11" + b"Fake Server\x00" + b"Altis\x00" + b"Arma3\x00" + b"Arma 3\x00" + struct.pack("<hBBB", 0, 3, 32, 0)
    s.sendto(b"\xff\xff\xff\xff" + body, addr)
