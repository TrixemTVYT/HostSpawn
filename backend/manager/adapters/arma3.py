"""Arma 3. Runs on the shared Manager engine (managed.py): tmux, watchdog, crash-loop and update handling.
Needs a Steam login (not anonymous): the setup helper's guided step or backend/games/arma3/install.sh sets it up once.
Optional mission (e.g. KP Liberation): cfg "mission" = mission folder/pbo name in mpmissions, without .pbo."""
import os
from pathlib import Path
from string import Template

from .. import spyglass as spy
from ..util import ROOT, community, secrets
from .managed import Busy, ManagedServer  # noqa: F401  (Busy re-exported for callers/tests)
from .moderation import BattlEyeMod

APP_ID = "233780"


class Arma3Adapter(BattlEyeMod, ManagedServer):
    game = "Arma 3"
    is_arma3 = True  # the file editor offers packing mission folders into .pbo files
    slug = "arma3"
    app_id = APP_ID
    exe_name = "arma3server_x64"
    install_hint = "not installed: run backend/games/arma3/install.sh in a terminal first"
    confirm = ManagedServer.confirm | {"spyglass", "spyglass_off", "spyglass_on"}

    def can_moderate(self) -> bool:
        # Players, kicks, bans and in-game messages go through BattlEye RCon, not yet proven on a real Arma 3 server:
        # "Coming soon" unless previews are on (catalog.previews_on)
        from .. import catalog
        return catalog.previews_on() and super().can_moderate()

    @property
    def actions(self):
        # Altis Life missions carry the Spyglass anti-cheat: offer updating its allowed list and turning it off/on
        extra = ("spyglass", "spyglass_off", "spyglass_on") if spy.has_spyglass(self) else ()
        return ManagedServer.actions + extra

    def spyglass_log(self) -> str:
        """Spyglass's lines from the last 10 console logs (it writes SPYGLASS-FLAG when it throws someone out)."""
        logs = sorted(self.log_dir.glob(f"{self.slug}_*.log"), key=lambda p: p.stat().st_mtime)[-10:]
        return "\n".join(ln for p in logs for ln in p.read_text(errors="replace").splitlines() if "SPYGLASS-FLAG" in ln)

    def spyglass_status(self) -> dict:
        """spyglass.status plus running, and pending: the mission changed after the running server loaded it."""
        out = spy.status(self, self.spyglass_log())
        pid, p = self.pid(), spy.mission_pbo(self)
        out["running"] = bool(pid)
        try:
            out["pending"] = bool(pid and p and p.stat().st_mtime > Path(f"/proc/{pid}").stat().st_mtime)
        except OSError:
            out["pending"] = False
        return out

    def spyglass(self) -> str:
        """Allow the official Arma 3 parts Spyglass kicked players for (see manager/spyglass.py)."""
        return self._spyglass_change(lambda: spy.allow(self, self.spyglass_log()))

    def spyglass_off(self) -> str:
        return self._spyglass_change(lambda: spy.set_on(self, False))

    def spyglass_on(self) -> str:
        return self._spyglass_change(lambda: spy.set_on(self, True))

    def _spyglass_change(self, change) -> str:
        """Change the mission, then apply it: Arma loads the mission when it starts, so restart if running."""
        pbo_file = spy.mission_pbo(self)
        stamp = lambda: pbo_file.stat().st_mtime_ns if pbo_file and pbo_file.exists() else 0
        with self.lock("spyglass"):
            before = stamp()
            msg = change()
            if stamp() == before:  # nothing changed
                return msg
            msg = msg.replace(" Restart the instance to apply it.", "").replace(
                " Restart the instance so players get the new mission.", "")
            if not self.pid():
                return msg + " It applies the next time the instance starts."
            self._stop(maintenance=False)
            started = self._start()
            return msg + f" The instance was restarted to apply it ({started})."

    @property
    def steam_user_key(self) -> str:
        return self.cfg.get("steam_user_key", "ARMA3_STEAM_USER")

    @property
    def port(self) -> int: return int(self.cfg.get("port", 2302))
    def query_port(self): return self.port + 1

    def rcon_password(self) -> str:
        return secrets().get(self.cfg.get("rcon_password_key", ""), "")

    def login_hint(self):
        return "Steam login needed: press Log in to Steam on this server's panel"

    def launch_args(self):
        args = [f"-port={self.port}", "-config=manager/server.cfg", "-cfg=manager/basic.cfg",
                "-name=server", "-loadMissionToMemory"]
        if self.cfg.get("mission"):  # start the mission with the server, not when the first player joins (needs persistent)
            args.append("-autoInit")
        mods = self.cfg.get("server_mods") or []
        if mods:  # server-side only mods (e.g. @life_server;@extdb3); players don't need them
            args.append("-serverMod=" + ";".join(mods))
        return args

    def write_configs(self) -> None:
        sec = secrets()
        cfg_dir = self.server_dir / "manager"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        clean = lambda s: str(s).replace('"', "").replace("\n", " ").replace(";", "")
        mission = clean(self.cfg.get("mission", ""))
        if mission:
            missions = ("class Missions {\n    class Mission1 {\n"
                        f'        template = "{mission}";\n'
                        f'        difficulty = "{clean(self.cfg.get("difficulty", "Regular"))}";\n'
                        "    };\n};")
        else:
            missions = ("// No mission set: the server waits in mission select.\n"
                        "// Log in in-game with  #login <admin password>  then  #missions  to pick one.\nclass Missions {};")
        values = {
            "hostname": clean(self.cfg.get("hostname", f"{community()} Arma 3")),
            "community": clean(community()),
            "password": clean(sec.get(self.cfg.get("password_key", "ARMA3_SERVER_PASSWORD"), "")),
            "password_admin": clean(sec.get(self.cfg.get("admin_password_key", "ARMA3_ADMIN_PASSWORD"), "")),
            "max_players": int(self.cfg.get("max_players", 32)),
            "missions": missions,
            "persistent": 1 if mission else 0,
            "battleye": 0 if self.cfg.get("battleye") is False else 1,  # Settings tab switch; on unless turned off
        }
        rcon_pw = self.rcon_password()
        if self.cfg.get("rcon_port") and rcon_pw:  # BattlEye RCon, this machine only
            be = self.server_dir / "battleye"
            be.mkdir(parents=True, exist_ok=True)
            f = be / "beserver_x64.cfg"
            f.touch(mode=0o600)
            os.chmod(f, 0o600)
            f.write_text(f"RConPassword {clean(rcon_pw)}\nRConIP 127.0.0.1\nRConPort {int(self.cfg['rcon_port'])}\n")
        for name in ("server.cfg", "basic.cfg"):
            text = Template((ROOT / "backend/games/arma3" / f"{name}.template").read_text()).substitute(values)
            path = cfg_dir / name
            path.touch(mode=0o600)
            os.chmod(path, 0o600)
            path.write_text(text)

    def db_login(self) -> tuple[str, Path] | None:
        """(database name, MariaDB login file) for Altis Life instances; None when there's no database."""
        db, cnf = self.cfg.get("db_name"), self.root / "manager" / "db.cnf"
        if not cnf.exists():
            cnf = self.root / "manager-db.cnf"  # servers made before 2026-11-06
        return (db, cnf) if db and cnf.exists() else None

    def pre_backup(self):
        login = self.db_login()
        if not login:
            return
        db, cnf = login
        out = self.server_dir / "manager" / "database.sql"
        out.parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        with open(out, "w") as f:
            os.chmod(out, 0o600)
            p = subprocess.run(["mysqldump", f"--defaults-extra-file={cnf}", "--single-transaction", db],
                               stdout=f, stderr=subprocess.PIPE, text=True, timeout=600)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[:200])

    def backup_paths(self):
        profiles = Path.home() / ".local/share/Arma 3 - Other Profiles"
        return [self.server_dir / "manager", self.server_dir / "mpmissions", self.server_dir / "keys", profiles,
                self.server_dir / "@extdb3" / "logs"]
