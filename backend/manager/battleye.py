"""BattlEye RCon client (UDP, stdlib), for Arma 3 (and other BattlEye games).
Protocol v2 (battleye.com/downloads/BERConProtocol.txt): every packet is
  'B' 'E' | CRC32 (little-endian) of the bytes from the 0xFF on | 0xFF | type | ...
  login   0x00 password            -> 0x00 0x01 (ok) / 0x00 0x00 (wrong password)
  command 0x01 seq command         -> 0x01 seq reply   (or 0x01 seq 0x00 count index part, for long replies)
  message 0x02 seq text (server)   -> the client must answer 0x02 seq
One short connection per call (log in, run, done), like rcon.WebRcon. The password is never put in errors."""
import re
import socket
import struct
import time
import zlib


class BattlEyeError(Exception):
    pass


def packet(payload: bytes) -> bytes:
    body = b"\xff" + payload
    return b"BE" + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF) + body


def unpack(data: bytes) -> bytes | None:
    """The payload after 0xFF, or None if this isn't a valid BattlEye packet."""
    if len(data) < 8 or data[:2] != b"BE" or data[6] != 0xFF:
        return None
    if struct.unpack("<I", data[2:6])[0] != zlib.crc32(data[6:]) & 0xFFFFFFFF:
        return None
    return data[7:]


class BattlEyeRcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0, tries: int = 3):
        self.host, self.port, self.password, self.timeout, self.tries = host, port, password, timeout, tries

    def _recv(self, sock: socket.socket, deadline: float) -> bytes | None:
        """The next valid payload before the deadline; server messages are acknowledged and skipped."""
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            sock.settimeout(left)
            try:
                data = sock.recv(65535)
            except socket.timeout:
                return None
            p = unpack(data)
            if p is None:
                continue
            if p[:1] == b"\x02" and len(p) >= 2:
                sock.send(packet(b"\x02" + p[1:2]))  # unanswered messages get us dropped
                continue
            return p

    def command(self, command: str) -> str:
        """Log in, run one command, return its reply. Raises BattlEyeError on any failure."""
        if not self.password:
            raise BattlEyeError("no RCON password configured")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((self.host, self.port))
                for _ in range(self.tries):  # UDP: a lost login is simply sent again
                    sock.send(packet(b"\x00" + self.password.encode()))
                    p = self._recv(sock, time.monotonic() + self.timeout)
                    if p is not None and p[:1] == b"\x00":
                        if p[1:2] != b"\x01":
                            raise BattlEyeError("wrong RCON password")
                        break
                else:
                    raise BattlEyeError("no answer (server down or RCON off)")
                seq = 0
                for _ in range(self.tries):
                    sock.send(packet(b"\x01" + bytes([seq]) + command.encode()))
                    parts: dict[int, bytes] = {}
                    count, single = None, None
                    deadline = time.monotonic() + self.timeout
                    while True:
                        p = self._recv(sock, deadline)
                        if p is None:
                            break
                        if p[:1] != b"\x01" or p[1:2] != bytes([seq]):
                            continue
                        body = p[2:]
                        if len(body) >= 3 and body[0] == 0x00:  # one part of a long reply
                            count = body[1]
                            parts[body[2]] = body[3:]
                            if len(parts) == count:
                                break
                        else:
                            single = body
                            break
                    if single is not None:
                        return single.decode(errors="replace")
                    if count and len(parts) == count:
                        return b"".join(parts[i] for i in range(count)).decode(errors="replace")
                raise BattlEyeError("no reply to the command")
        except BattlEyeError:
            raise
        except ConnectionRefusedError:  # nothing listens on that port (the system said so)
            raise BattlEyeError("no answer (server down or RCON off)") from None
        except OSError as e:
            raise BattlEyeError(type(e).__name__) from None

    # ---- helpers on top of the BattlEye commands ----
    def players(self) -> list[dict]:
        return parse_players(self.command("players"))

    def bans(self) -> list[dict]:
        return parse_bans(self.command("bans"))


_PLAYER = re.compile(r"^(\d+)\s+(\S+?):(\d+)\s+(-?\d+)\s+([0-9a-fA-F]{32}|-)(?:\(([^)]*)\))?\s+(.*?)(\s+\(Lobby\))?\s*$")
_BAN = re.compile(r"^(\d+)\s+([0-9a-fA-F]{32})\s+(perm|-?\d+)\s*(.*)$")


def parse_players(text: str) -> list[dict]:
    """BattlEye's 'players' table -> [{num, ip, ping, guid, verified, name, lobby}]."""
    out = []
    for line in text.splitlines():
        m = _PLAYER.match(line.strip())
        if m:
            out.append({"num": int(m[1]), "ip": m[2], "ping": int(m[4]), "guid": None if m[5] == "-" else m[5].lower(),
                        "verified": m[6] == "OK", "name": m[7], "lobby": bool(m[8])})
    return out


def parse_bans(text: str) -> list[dict]:
    """GUID bans from BattlEye's 'bans' table -> [{num, guid, minutes (None = permanent), reason}]."""
    out = []
    for line in text.splitlines():
        m = _BAN.match(line.strip())
        if m:
            out.append({"num": int(m[1]), "guid": m[2].lower(), "minutes": None if m[3] == "perm" else int(m[3]),
                        "reason": m[4].strip()})
    return out
