"""Fake BattlEye RCon server for tests (UDP, stdlib). Keeps a small world: players, GUID bans, messages said.
Behaviour switches:
  password   the accepted password
  drop       drop this many incoming packets first (lost UDP packets)
  split      reply in parts of this many bytes (multi-packet replies), 0 = never split
  chatter    send a server message before each reply (the client must acknowledge it)
Commands: players, kick <#> [reason], ban <#> [min] [reason], addBan <guid> [min] [reason], bans,
removeBan <#>, say -1 <text>."""
import socket
import struct
import threading
import zlib


def packet(payload: bytes) -> bytes:
    body = b"\xff" + payload
    return b"BE" + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF) + body


class FakeBattlEye:
    def __init__(self, password="pw", drop=0, split=0, chatter=False, players=None, port=0):
        self.password, self.drop, self.split, self.chatter = password, drop, split, chatter
        # [{"name", "guid", "ip"}]; the player number is the list position
        self.players = list(players or [])
        self.bans: list[dict] = []
        self.said, self.commands, self.acks = [], [], []
        self.logged_in = set()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        self._msg_seq = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self):
        self.sock.close()

    def add(self, name, guid, ip="10.0.0.5"):
        if any(b["guid"] == guid for b in self.bans):
            return False  # banned: BattlEye refuses them
        self.players.append({"name": name, "guid": guid, "ip": ip})
        return True

    def _reply(self, addr, payload: bytes):
        self.sock.sendto(packet(payload), addr)

    def _serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
            except OSError:
                return
            if self.drop > 0:
                self.drop -= 1
                continue
            if len(data) < 8 or data[:2] != b"BE" or struct.unpack("<I", data[2:6])[0] != zlib.crc32(data[6:]):
                continue
            p = data[7:]
            kind = p[:1]
            if kind == b"\x00":
                ok = p[1:].decode() == self.password
                if ok:
                    self.logged_in.add(addr)
                self._reply(addr, b"\x00" + (b"\x01" if ok else b"\x00"))
            elif kind == b"\x02":
                self.acks.append(p[1])
            elif kind == b"\x01" and addr in self.logged_in:
                seq, cmd = p[1:2], p[2:].decode(errors="replace")
                if not cmd:
                    self._reply(addr, b"\x01" + seq)  # keep-alive
                    continue
                self.commands.append(cmd)
                if self.chatter:
                    self._reply(addr, b"\x02" + bytes([self._msg_seq]) + b"RCon admin #0 logged in")
                    self._msg_seq = (self._msg_seq + 1) % 256
                out = self.run(cmd).encode()
                if self.split and len(out) > self.split:
                    chunks = [out[i:i + self.split] for i in range(0, len(out), self.split)]
                    for i in reversed(range(len(chunks))):  # out of order on purpose
                        self._reply(addr, b"\x01" + seq + bytes([0, len(chunks), i]) + chunks[i])
                else:
                    self._reply(addr, b"\x01" + seq + out)

    def run(self, cmd: str) -> str:
        word, _, rest = cmd.partition(" ")
        if word == "players":
            lines = ["Players on server:", "[#] [IP Address]:[Port] [Ping] [GUID] [Name]",
                     "--------------------------------------------------"]
            for i, pl in enumerate(self.players):
                lines.append(f"{i}   {pl['ip']}:2304   42   {pl['guid']}(OK) {pl['name']}")
            lines.append(f"({len(self.players)} players in total)")
            return "\n".join(lines)
        if word == "kick":
            num = rest.split(" ", 1)[0]
            if num.isdigit() and int(num) < len(self.players):
                self.players.pop(int(num))
            return ""
        if word in ("ban", "addBan"):
            parts = rest.split(" ", 2)
            minutes = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 0
            reason = parts[2] if len(parts) > 2 else ""
            if word == "ban":
                if not (parts[0].isdigit() and int(parts[0]) < len(self.players)):
                    return ""
                guid = self.players.pop(int(parts[0]))["guid"]
            else:
                guid = parts[0].lower()
                self.players = [pl for pl in self.players if pl["guid"] != guid]
            self.bans.append({"guid": guid, "minutes": minutes, "reason": reason})
            return ""
        if word == "bans":
            lines = ["GUID Bans:", "[#] [GUID] [Minutes left] [Reason]", "----------------------------------------"]
            for i, b in enumerate(self.bans):
                lines.append(f"{i}  {b['guid']} {'perm' if b['minutes'] <= 0 else b['minutes']} {b['reason']}")
            lines += ["", "IP Bans:", "[#] [IP Address] [Minutes left] [Reason]", "----------------------------------------"]
            return "\n".join(lines)
        if word == "removeBan":
            if rest.isdigit() and int(rest) < len(self.bans):
                self.bans.pop(int(rest))
            return ""
        if word == "say":
            self.said.append(rest.partition(" ")[2])
            return ""
        return "Unknown command"
