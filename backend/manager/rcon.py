"""Rust WebRCON client (WebSocket, RFC 6455), stdlib only.
Rust serves it at ws://<host>:<rcon.port>/<rcon.password>. Requests and replies are JSON:
  send {"Identifier": n, "Message": "<command>", "Name": "HostSpawn"}
  get  {"Identifier": n, "Message": "<output>", "Type": "Generic"}  (other console lines arrive too and are skipped)
The password travels in the URL path, so URLs are never logged and errors never include them."""
import base64
import hashlib
import json
import os
import random
import socket
import struct
import time
from urllib.parse import quote

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class RconError(Exception):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RconError("connection closed")
        buf += chunk
    return buf


def _send_frame(sock: socket.socket, payload: bytes, opcode: int = 0x1) -> None:
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)  # clients must mask every frame
    sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    """Returns (opcode, payload) of one complete message (joins continuation frames)."""
    data, first_op = b"", None
    while True:
        b1, b2 = _recv_exact(sock, 2)
        op, n = b1 & 0x0F, b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        if n > 16 * 1024 * 1024:
            raise RconError("frame too large")
        mask = _recv_exact(sock, 4) if b2 & 0x80 else None
        payload = _recv_exact(sock, n)
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if op >= 0x8:  # control frames can arrive between fragments
            if op == 0x9:
                _send_frame(sock, payload, 0xA)  # ping -> pong
                continue
            return op, payload
        first_op = op if first_op is None else first_op
        data += payload
        if b1 & 0x80:
            return first_op, data


class WebRcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout

    def command(self, command: str) -> str:
        """Run one console command and return its output. Raises RconError on any failure."""
        if not self.password:
            raise RconError("no RCON password configured")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.settimeout(self.timeout)
                key = base64.b64encode(os.urandom(16)).decode()
                sock.sendall((f"GET /{quote(self.password, safe='')} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
                              f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                              "Sec-WebSocket-Version: 13\r\n\r\n").encode())
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = sock.recv(1024)
                    if not chunk:  # Rust just hangs up on a wrong password
                        raise RconError("refused (wrong password or RCON off)")
                    if len(head) > 8192:
                        raise RconError("bad handshake")
                    head += chunk
                status = head.split(b"\r\n", 1)[0]
                if b" 101 " not in status + b" ":
                    raise RconError("refused (wrong password or RCON off)")
                want = base64.b64encode(hashlib.sha1(key.encode() + GUID).digest())
                if want not in head:
                    raise RconError("bad handshake")
                ident = random.randint(1000, 2_000_000_000)
                _send_frame(sock, json.dumps({"Identifier": ident, "Message": command, "Name": "HostSpawn"}).encode())
                deadline = time.monotonic() + self.timeout
                while time.monotonic() < deadline:
                    op, payload = _recv_frame(sock)
                    if op == 0x8:
                        raise RconError("closed by server")
                    try:
                        msg = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("Identifier") == ident:
                        try:
                            _send_frame(sock, b"\x03\xe8", 0x8)  # polite close
                        except OSError:
                            pass
                        return str(msg.get("Message", ""))
                raise RconError("no reply")
        except RconError:
            raise
        except (OSError, ValueError, struct.error) as e:
            raise RconError(type(e).__name__) from None  # never include the URL/password

    def serverinfo(self) -> dict:
        try:
            return json.loads(self.command("serverinfo"))
        except ValueError:
            raise RconError("unexpected serverinfo reply") from None
