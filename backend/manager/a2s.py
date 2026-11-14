"""Minimal Valve A2S_INFO query (UDP). Works for Rust, Arma 3 and Arma Reforger. Stdlib only."""
import socket
import struct
import time

A2S_INFO = b"\xff\xff\xff\xffTSource Engine Query\x00"


def _cstr(data: bytes, i: int) -> tuple[str, int]:
    j = data.index(b"\x00", i)
    return data[i:j].decode("utf-8", "replace"), j + 1


def parse_info(data: bytes) -> dict:
    if data[:4] != b"\xff\xff\xff\xff" or data[4:5] != b"I":
        raise ValueError("not an A2S_INFO reply")
    i = 6  # header + protocol byte
    name, i = _cstr(data, i)
    map_, i = _cstr(data, i)
    folder, i = _cstr(data, i)
    game, i = _cstr(data, i)
    i += 2  # app id
    players, max_players, bots = struct.unpack_from("<BBB", data, i)
    return {"name": name, "map": map_, "folder": folder, "game": game,
            "players": players, "max_players": max_players, "bots": bots}


def info(host: str, port: int, timeout: float = 3.0) -> dict | None:
    """Returns server info plus 'ping_ms', or None if it doesn't answer."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            start = time.monotonic()
            s.sendto(A2S_INFO, (host, port))
            data, _ = s.recvfrom(4096)
            if data[4:5] == b"A":  # challenge: resend with it appended
                s.sendto(A2S_INFO + data[5:9], (host, port))
                data, _ = s.recvfrom(4096)
            out = parse_info(data)
            out["ping_ms"] = round((time.monotonic() - start) * 1000)
            return out
    except (OSError, ValueError, struct.error):
        return None
