"""Arma Reforger server created and run by Manager (vanilla scenarios). Anonymous SteamCMD (app 1874900).
Config: server/manager/config.json, written at every start. RCON (native, read-only "monitor" permission) listens
on 127.0.0.1 only; its password is generated into secrets.env."""
import json
import os
import socket

from ..util import community, newest, secrets
from .managed import ManagedServer
from .reforger import console_stats


def public_ip() -> str:
    """This machine's outward-facing IPv4 address (no internet lookup: asks the routing table)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 9))  # UDP connect sends nothing
            return s.getsockname()[0]
    except OSError:
        return ""


class ReforgerServer(ManagedServer):
    game = "Arma Reforger"
    slug = "reforger"
    app_id = "1874900"
    exe_name = "ArmaReforgerServer"
    log_alert_s = 600          # -logStats 60000: one FPS line a minute
    actions = ("start", "stop", "restart", "update", "backup", "rotate")
    confirm = frozenset({"stop", "restart", "update", "rotate"})
    backup_exclude = ("logs", "addons")

    @property
    def port(self) -> int: return int(self.cfg.get("port", 2001))
    def query_port(self): return int(self.cfg.get("query_port", 17777))
    @property
    def profile(self): return self.root / "profile"
    @property
    def config_path(self): return self.server_dir / "manager/config.json"

    def launch_args(self):
        return ["-config", str(self.config_path), "-profile", str(self.profile), "-maxFPS",
                str(self.cfg.get("max_fps", 60)), "-logStats", "60000", "-addonTempDir", str(self.root / "tmp")]

    def write_configs(self):
        sec = secrets()
        name = str(self.cfg.get("hostname") or f"{community()} {self.cfg.get('mode_name', 'Arma Reforger')}")[:100]
        cfg = {
            "bindAddress": "0.0.0.0",
            "bindPort": self.port,
            "publicAddress": self.cfg.get("public_ip") or public_ip(),
            "publicPort": self.port,
            "a2s": {"address": "0.0.0.0", "port": self.query_port()},
            "game": {
                "name": name,
                "password": sec.get(self.cfg.get("password_key", ""), "") if self.cfg.get("password_key") else "",
                "passwordAdmin": sec.get(self.cfg.get("admin_password_key", ""), ""),
                "admins": [],
                "scenarioId": self.cfg["scenario"],
                "maxPlayers": int(self.cfg.get("max_players", 32)),
                "visible": True,
                "crossPlatform": False,
                "gameProperties": {"serverMaxViewDistance": 2500, "networkViewDistance": 1500,
                                   "disableThirdPerson": False, "fastValidation": True, "battlEye": True},
                "mods": [],
            },
            "operating": {"lobbyPlayerSynchronise": True},
        }
        rcon_pw = sec.get(self.cfg.get("rcon_password_key", ""), "")
        if self.cfg.get("rcon_port") and rcon_pw:
            cfg["rcon"] = {"address": "127.0.0.1", "port": int(self.cfg["rcon_port"]), "password": rcon_pw,
                           "permission": "monitor", "maxClients": 2}
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)
        self.config_path.touch(mode=0o600)
        os.chmod(self.config_path, 0o600)
        self.config_path.write_text(json.dumps(cfg, indent=2))

    # ---- map rotation: Reforger runs one scenario per start, so Manager switches it at scheduled restarts ----
    def rotation(self) -> dict:
        from .. import catalog
        names = {s["id"]: s["label"] for s in catalog.scenarios("reforger")}
        lst = [x for x in self.cfg.get("rotation", []) if x in names]
        on = bool(self.cfg.get("rotation_on")) and len(lst) >= 2
        cur = self.cfg.get("scenario")
        i = lst.index(cur) if cur in lst else -1
        nxt = lst[(i + 1) % len(lst)] if on else None
        return {"on": on, "list": [{"id": x, "label": names[x]} for x in lst], "current": cur,
                "current_label": names.get(cur, cur), "next": nxt, "next_label": names.get(nxt) if nxt else None}

    def advance_rotation(self) -> str | None:
        """Pick the next scenario (used before a scheduled restart). Returns its label, or None if rotation is off."""
        r = self.rotation()
        if not r["on"]:
            return None
        self.save_cfg({"scenario": r["next"]})
        return r["next_label"]

    def before_scheduled_restart(self) -> None:
        label = self.advance_rotation()
        if label:
            from ..util import add_event
            add_event(self.id, "info", f"map rotation: next map is {label}")

    def rotate(self) -> str:
        """Switch to the next map now (restarts the server)."""
        label = self.advance_rotation()
        if not label:
            return "map rotation is off (needs at least 2 maps)"
        with self.lock("rotate"):
            was = bool(self.pid())
            self._stop(maintenance=False)
            if was:
                self._start()
        return f"switched to {label}"

    def log_file(self):
        d = newest(self.profile / "logs", "logs_*") if (self.profile / "logs").is_dir() else None
        return d / "console.log" if d else None

    def status(self):
        s = super().status()
        if s.get("state") in ("running", "starting") and self.log_file():
            s.update(console_stats(self.log_file()))
        return s

    def backup_paths(self):
        return [self.profile, self.server_dir / "manager"]
