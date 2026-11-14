"""Fake Rust WebRCON server for tests (WebSocket, stdlib). Behaviour switches:
  password      the only accepted path (/<password>); anything else: connection closed with no reply (like Rust)
  noise         send unrelated console lines before the real reply
  fragment      send the reply split over continuation frames, with a ping in between
  silent        accept but never reply
  serverinfo    dict returned for the serverinfo command
  players       [{"SteamID", "DisplayName", ...}] returned by playerlist; kick/banid remove a player, banid/unban
                keep the ban list (self.banned)"""
import base64
import hashlib
import json
import socket
import struct
import threading
from urllib.parse import unquote

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def frame(payload: bytes, op=0x1, fin=True) -> bytes:
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | op])
    head += bytes([n]) if n < 126 else (bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n))
    return head + payload


def read_frame(c: socket.socket) -> tuple[int, bytes]:
    b1, b2 = c.recv(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", c.recv(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", c.recv(8))[0]
    mask = c.recv(4)
    data = b""
    while len(data) < n:
        data += c.recv(n - len(data))
    return b1 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


class FakeWebRcon:
    def __init__(self, password="pw", noise=False, fragment=False, silent=False, serverinfo=None, players=None, port=0, on_quit=None):
        self.password, self.noise, self.fragment, self.silent = password, noise, fragment, silent
        self.players, self.banned, self.on_quit = list(players or []), set(), on_quit
        self.serverinfo = serverinfo or {"Hostname": "Fake", "MaxPlayers": 50, "Players": 7, "Queued": 0, "Framerate": 99}
        self.paths, self.commands = [], []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self.sock.close()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def run(self, command: str) -> str:
        word, _, rest = command.partition(" ")
        if word == "serverinfo":
            return json.dumps(self.serverinfo)
        if word == "playerlist":
            return json.dumps(self.players)
        if word in ("kick", "banid"):
            sid = rest.split(" ", 1)[0]
            self.players = [p for p in self.players if p["SteamID"] != sid]
            if word == "banid":
                self.banned.add(sid)
        if word == "quit" and self.on_quit:
            threading.Timer(0.2, self.on_quit).start()  # reply first, then stop like the game would
        if word == "unban":
            self.banned.discard(rest.strip())
        return f"ran {command}"

    def _client(self, c):
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(1024)
            path = req.split(b" ")[1].decode()
            self.paths.append(path)
            if unquote(path.lstrip("/")) != self.password:
                c.close()  # Rust hangs up on a wrong password
                return
            key = next(l.split(b":", 1)[1].strip() for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key"))
            accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
            c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            op, data = read_frame(c)
            msg = json.loads(data)
            self.commands.append(msg["Message"])
            if self.silent:
                c.recv(1024)
                return
            if self.noise:
                for i in range(3):
                    c.sendall(frame(json.dumps({"Identifier": 0, "Message": f"console line {i}", "Type": "Generic"}).encode()))
                c.sendall(frame(b"not json at all"))
            out = self.run(msg["Message"])
            reply = json.dumps({"Identifier": msg["Identifier"], "Message": out, "Type": "Generic"}).encode()
            if self.fragment:
                half = len(reply) // 2
                c.sendall(frame(reply[:half], 0x1, fin=False) + frame(b"hi", 0x9) + frame(reply[half:], 0x0, fin=True))
            else:
                c.sendall(frame(reply))
            try:
                c.recv(1024)
            except OSError:
                pass
        except (OSError, ValueError, StopIteration):
            pass
        finally:
            c.close()
