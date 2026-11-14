"""Players and moderation for adapters: who's online, kick, ban, unban, say. Two ways to talk to a game:
- RustRcon: Rust WebRCON (playerlist JSON, kick, banid, unban, say).
- BattlEyeMod: BattlEye RCon (players table, kick #, addBan GUID, removeBan, say -1), for Arma 3.
Every player comes back in one shape: {"key": "steam:<id>" | "be:<guid>", "name", "ping", "connected_s", "ref"}.
"key" is the same on every server (the player database uses it); "ref" is what this server needs to kick them.
IP addresses are dropped here: HostSpawn doesn't keep them.
Kick/ban/say only work when can_moderate() says so: servers HostSpawn made, or ones the owner opted in
(cfg moderation = true). Servers it only watches get a read-only player list."""
import json
import re

from ..battleye import BattlEyeError, BattlEyeRcon
from ..rcon import RconError, WebRcon
from .base import Unsupported

STEAM_ID = re.compile(r"^\d{17}$")
GUID = re.compile(r"^[0-9a-f]{32}$")


class ModerationError(Exception):
    pass


def clean_text(text: str, limit: int = 120) -> str:
    """Reasons and messages: no quotes or control characters (they could break out of a console command)."""
    return re.sub(r"[\x00-\x1f\x7f\"\\]", "", str(text)).strip()[:limit]


class Moderation:
    """Default: no players support. Mixed into adapters before their base class."""
    def can_moderate(self) -> bool:
        return bool(self.cfg.get("managed") or self.cfg.get("moderation"))

    def _need_moderation(self):
        if not self.can_moderate():
            raise ModerationError("HostSpawn only watches this server: kicking and banning are off for it")

    def players(self) -> list[dict]: raise Unsupported("players")
    def kick(self, ref: str, reason: str) -> str: raise Unsupported("kick")
    def ban(self, key: str, ref: str | None, name: str, minutes: int, reason: str) -> str: raise Unsupported("ban")
    def unban(self, key: str) -> str: raise Unsupported("unban")
    def say(self, text: str) -> str: raise Unsupported("say")


class RustRcon(Moderation):
    """Needs rcon_port and rcon_password() (both Rust adapters have them)."""
    def _rcon(self) -> WebRcon:
        return WebRcon("127.0.0.1", int(self.rcon_port), self.rcon_password(), timeout=5)

    def _cmd(self, command: str) -> str:
        try:
            return self._rcon().command(command)
        except RconError as e:
            raise ModerationError(f"RCON: {e}") from None

    def players(self) -> list[dict]:
        try:
            rows = json.loads(self._cmd("playerlist") or "[]")
        except ValueError:
            raise ModerationError("RCON: unexpected playerlist reply") from None
        out = []
        for r in rows if isinstance(rows, list) else []:
            sid = str(r.get("SteamID", ""))
            if STEAM_ID.match(sid):
                out.append({"key": f"steam:{sid}", "name": str(r.get("DisplayName", ""))[:64], "ping": r.get("Ping"),
                            "connected_s": r.get("ConnectedSeconds"), "ref": sid})
        return out

    def kick(self, ref, reason):
        self._need_moderation()
        if not STEAM_ID.match(str(ref)):
            raise ModerationError("not a Steam id")
        self._cmd(f'kick {ref} "{clean_text(reason) or "Kicked"}"')
        return "kicked"

    def ban(self, key, ref, name, minutes, reason):
        self._need_moderation()
        sid = key.removeprefix("steam:")
        if not STEAM_ID.match(sid):
            raise ModerationError("not a Steam id")
        if minutes:  # temporary: HostSpawn's own ban list keeps them out (kicked on sight) until it ends
            if ref:
                self._cmd(f'kick {sid} "{clean_text(reason) or "Banned"}"')
            return "banned (temporary, kept by HostSpawn)"
        self._cmd(f'banid {sid} "{clean_text(name, 64)}" "{clean_text(reason) or "Banned"}"')
        return "banned"

    def unban(self, key):
        self._need_moderation()
        sid = key.removeprefix("steam:")
        if not STEAM_ID.match(sid):
            raise ModerationError("not a Steam id")
        self._cmd(f"unban {sid}")
        return "unbanned"

    def say(self, text):
        self._need_moderation()
        self._cmd(f'say "{clean_text(text, 200)}"')
        return "said"


class BattlEyeMod(Moderation):
    """Needs cfg rcon_port and rcon_password()."""
    def _be(self) -> BattlEyeRcon:
        port = self.cfg.get("rcon_port")
        if not port or not self.rcon_password():
            raise ModerationError("RCON isn't set up for this server")
        return BattlEyeRcon("127.0.0.1", int(port), self.rcon_password())

    def _cmd(self, command: str) -> str:
        try:
            return self._be().command(command)
        except BattlEyeError as e:
            raise ModerationError(f"RCON: {e}") from None

    def players(self) -> list[dict]:
        from ..battleye import parse_players
        out = []
        for p in parse_players(self._cmd("players")):
            if p["guid"]:  # still joining (no GUID yet): shows up next minute
                out.append({"key": f"be:{p['guid']}", "name": p["name"][:64], "ping": p["ping"], "connected_s": None,
                            "ref": str(p["num"]), "lobby": p["lobby"]})
        return out

    def kick(self, ref, reason):
        self._need_moderation()
        if not str(ref).isdigit():
            raise ModerationError("not a player number")
        self._cmd(f"kick {ref} {clean_text(reason) or 'Kicked'}")
        return "kicked"

    def ban(self, key, ref, name, minutes, reason):
        self._need_moderation()
        guid = key.removeprefix("be:")
        if not GUID.match(guid):
            raise ModerationError("not a BattlEye GUID")
        self._cmd(f"addBan {guid} {max(0, int(minutes))} {clean_text(reason) or 'Banned'}")
        if ref is not None and str(ref).isdigit():
            try:
                self._cmd(f"kick {ref} {clean_text(reason) or 'Banned'}")
            except ModerationError:
                pass
        return "banned"

    def unban(self, key):
        self._need_moderation()
        from ..battleye import parse_bans
        guid = key.removeprefix("be:")
        for b in sorted(parse_bans(self._cmd("bans")), key=lambda b: -b["num"]):  # highest first: numbers shift
            if b["guid"] == guid:
                self._cmd(f"removeBan {b['num']}")
        return "unbanned"

    def say(self, text):
        self._need_moderation()
        self._cmd(f"say -1 {clean_text(text, 200)}")
        return "said"
