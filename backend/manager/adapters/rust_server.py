"""Rust server created and run by Manager: vanilla or Oxide (uMod). Anonymous SteamCMD (app 258550).
Settings go in server/server/<identity>/cfg/server.cfg (written at every start). Stops gracefully over WebRCON
("quit" saves first). After every game update Oxide is reinstalled, because Rust updates overwrite it."""
import io
import os
import urllib.request
import zipfile

from ..rcon import RconError, WebRcon
from ..util import community, secrets
from .managed import ManagedServer
from .moderation import RustRcon

OXIDE_URL = "https://github.com/OxideMod/Oxide.Rust/releases/latest/download/Oxide.Rust-linux.zip"
# What a wipe deletes from server/server/<identity>/. Kept: player identities, deaths, states, Rust+ tokens,
# clans, team relationships, cfg/ (owners, mods, bans) and oxide/ (plugins, their data and permissions).
WIPE_MAP = ("proceduralmap.*", "*.map", "*.sav", "*.sav.*", "*.navmesh", "*_occlusion_*.dat", "*_oceanpatrol_*.dat")
WIPE_BLUEPRINTS = ("player.blueprints.*",)
WIPE_MODES = ("monthly", "biweekly", "weekly", "off")
RUST_REGIONS = ("EU", "NA", "SA", "WA", "EA", "OC", "AF")  # server-browser region tags
BP_POLICIES = ("forced", "every", "never")


def first_thursday(year: int, month: int):
    from datetime import date, timedelta
    d = date(year, month, 1)
    return d + timedelta(days=(3 - d.weekday()) % 7)


def wipe_kind_on(day, mode: str, bp: str) -> str | None:
    """'full', 'map' or None for a date under a wipe schedule. Facepunch forces a map wipe on the first Thursday."""
    if mode == "off" or day.weekday() != 3:
        return None
    forced = day.day <= 7
    if forced:
        return "full" if bp in ("forced", "every") else "map"
    if mode == "weekly" or (mode == "biweekly" and 15 <= day.day <= 21):
        return "full" if bp == "every" else "map"
    return None


def install_oxide(server_dir, url=OXIDE_URL) -> str:
    with urllib.request.urlopen(url, timeout=120) as r:
        data = r.read()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for m in z.infolist():
            target = (server_dir / m.filename).resolve()
            if not str(target).startswith(str(server_dir.resolve()) + os.sep):
                raise ValueError(f"unsafe path in Oxide zip: {m.filename}")
        z.extractall(server_dir)
    return f"Oxide installed ({len(data) // 1024} KB)"


class RustServer(RustRcon, ManagedServer):
    game = "Rust"
    slug = "rust"
    app_id = "258550"
    exe_name = "RustDedicated"
    stdout_to_log = False   # Rust writes its own -logfile
    actions = ("start", "stop", "restart", "update", "backup", "send", "oxide", "wipe")
    confirm = frozenset({"stop", "restart", "update", "oxide", "wipe"})

    @property
    def identity(self): return self.cfg.get("identity", "server")
    @property
    def port(self): return int(self.cfg.get("port", 28015))
    def query_port(self): return int(self.cfg.get("query_port", self.port + 2))
    @property
    def rcon_port(self): return int(self.cfg.get("rcon_port", self.port + 1))
    def rcon_password(self): return secrets().get(self.cfg.get("rcon_password_key", ""), "")

    def launch_env(self):
        d = str(self.server_dir)
        return {"LD_LIBRARY_PATH": f"{d}:{d}/RustDedicated_Data/Plugins/x86_64"}

    def launch_args(self):
        return ["-batchmode", "-nographics", "+server.identity", self.identity,
                "+server.port", str(self.port), "+server.queryport", str(self.query_port()),
                "+rcon.port", str(self.rcon_port), "+rcon.web", "1", "+rcon.password", self.rcon_password(),
                "+app.port", str(self.cfg.get("app_port", -1)), "-logfile", str(self.current_log)]

    def generated_files(self):
        return super().generated_files() + [self.server_dir / "server" / self.identity / "cfg" / "server.cfg"]

    def tags(self) -> list[str]:
        """Server-browser tags (Rust shows up to 4): wipe schedule, vanilla or modded, PvE, region."""
        out = []
        mode = self.cfg.get("wipe_mode", "monthly")
        if mode in ("monthly", "biweekly", "weekly"):
            out.append(mode)
        if self.cfg.get("framework") != "oxide":
            out.append("vanilla")
        if self.cfg.get("pve"):
            out.append("pve")
        if self.cfg.get("region") in RUST_REGIONS:
            out.append(self.cfg["region"])
        return out[:4]

    def write_configs(self):
        """server/<identity>/cfg/server.cfg, rewritten at every start from the instance's settings. Ports and RCON
        are given at launch, so its password is never in this file."""
        cfg_dir = self.server_dir / "server" / self.identity / "cfg"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        clean = lambda s: str(s).replace('"', "'").replace("\\", "/").replace("\r", " ").replace("\n", " ").strip()
        on = lambda key, default: "true" if self.cfg.get(key, default) else "false"
        name = clean(self.cfg.get("hostname") or f"{community()} Rust")
        desc = clean(self.cfg.get("description") or f"Welcome to {name}! Be respectful, have fun.")
        wipe = {"monthly": "Wipes monthly", "biweekly": "Wipes every two weeks", "weekly": "Wipes weekly"}.get(
            self.cfg.get("wipe_mode", "monthly"))
        if wipe and "wipe" not in desc.lower():
            desc += f"\\n{wipe} (Thursdays, {self.cfg.get('wipe_time', '20:00')} UK)."  # \n = new line in Rust
        lines = [
            "// " + "=" * 74,
            f"//  {name}",
            "//  Rust server settings, written by HostSpawn at every start.",
            "//  Change them in the HostSpawn app (the instance's Settings tab): edits made here are replaced.",
            "//  Ports and RCON are set when the server starts; the RCON password is never stored in this file.",
            "// " + "=" * 74,
            "",
            "// --- Identity: what players see in the server browser ---",
            f'server.hostname "{name}"',
            f'server.description "{desc}"',
        ]
        for key, conv in (("url", "server.url"), ("header_image", "server.headerimage"), ("logo_image", "server.logoimage")):
            if self.cfg.get(key):
                lines.append(f'{conv} "{clean(self.cfg[key])}"')
        lines.append(f'server.tags "{",".join(self.tags())}"')
        lines += ["",
                  "// --- Map (a new seed is picked at each map wipe if that's switched on) ---",
                  f"server.worldsize {int(self.cfg.get('world_size', 3000))}",
                  f"server.seed {int(self.cfg.get('seed', 12345))}",
                  "",
                  "// --- Players and performance (the world is saved every saveinterval seconds) ---",
                  f"server.maxplayers {int(self.cfg.get('max_players', 50))}",
                  f"server.saveinterval {int(self.cfg.get('save_interval', 300))}",
                  "",
                  "// --- Gameplay ---",
                  f"server.pve {on('pve', False)}",
                  f"server.globalchat {on('global_chat', True)}",
                  ""]
        f = cfg_dir / "server.cfg"
        f.write_text("\n".join(lines))
        os.chmod(f, 0o644)

    def graceful_stop(self):
        WebRcon("127.0.0.1", self.rcon_port, self.rcon_password(), timeout=5).command("quit")

    def post_update(self):
        if self.cfg.get("framework") == "oxide":
            install_oxide(self.server_dir)

    # ---- wipes ----
    @property
    def identity_dir(self):
        return self.server_dir / "server" / self.identity

    def wipe_settings(self) -> dict:
        return {"mode": self.cfg.get("wipe_mode", "monthly"), "time": self.cfg.get("wipe_time", "20:00"),
                "bp": self.cfg.get("wipe_bp", "forced"), "new_seed": self.cfg.get("wipe_new_seed", True),
                "update": self.cfg.get("wipe_update", True), "warn_s": int(self.cfg.get("wipe_warn_s", 300))}

    def wipe_due(self, when) -> str | None:
        """Kind of wipe due exactly at this UK minute, or None."""
        w = self.wipe_settings()
        return wipe_kind_on(when.date(), w["mode"], w["bp"]) if f"{when:%H:%M}" == w["time"] else None

    def next_wipes(self, n: int = 3) -> list[dict]:
        from datetime import datetime, timedelta
        from ..util import UK
        w, now, out = self.wipe_settings(), datetime.now(UK), []
        h, m = map(int, w["time"].split(":"))
        for i in range(0, 120):
            d = (now + timedelta(days=i)).date()
            kind = wipe_kind_on(d, w["mode"], w["bp"])
            when = datetime(d.year, d.month, d.day, h, m, tzinfo=UK)
            if kind and when > now:
                out.append({"when": when.isoformat(), "what": "full wipe (map + blueprints)" if kind == "full" else "map wipe",
                            "kind": kind, "forced": d.day <= 7})
                if len(out) >= n:
                    break
        return out

    def schedule(self):
        return sorted(super().schedule() + self.next_wipes(2), key=lambda e: e["when"])

    def wipe(self, kind: str, warn: bool = True) -> str:
        """Countdown → stop → backup → delete map (and blueprints if full) → new seed → update → start."""
        if kind not in ("map", "full"):
            raise ValueError("wipe kind must be map or full")
        import random
        w = self.wipe_settings()
        with self.lock("wipe"):
            was_running = bool(self.pid())
            if was_running and warn:
                self.countdown(w["warn_s"], what="wipe")
            self._stop(maintenance=False)
            try:
                backup = self._backup()  # wipe already holds the instance lock
            except Exception:
                if was_running:
                    try:
                        self._start()
                    except Exception as restart_error:
                        from ..util import add_event
                        add_event(self.id, "alert", f"wipe backup failed; server restart failed: {restart_error}")
                raise
            removed = 0
            for pat in WIPE_MAP + (WIPE_BLUEPRINTS if kind == "full" else ()):
                for f in self.identity_dir.glob(pat):
                    if f.is_file():
                        f.unlink()
                        removed += 1
            changes = {}
            if w["new_seed"]:
                changes["seed"] = random.randint(1, 2_147_483_646)
            if self.cfg.get("wipe_world_size"):
                changes["world_size"] = int(self.cfg["wipe_world_size"])
            if changes:
                self.save_cfg(changes)
            updated = ""
            if w["update"] and self.steamcmd.exists():
                updated = "; update: " + self._update(restart=False)
            self._start()
        from ..util import add_event
        add_event(self.id, "info", f"{'full' if kind == 'full' else 'map'} wipe done: {removed} files removed"
                  f"{', new seed ' + str(changes['seed']) if 'seed' in changes else ''}; {backup}{updated}")
        return f"{kind} wipe done ({removed} files removed{updated})"

    def oxide(self) -> str:
        """Install (or update) Oxide: stop, install from the official release, switch to modded, start again."""
        with self.lock("oxide"):
            was_running = bool(self.pid())
            if was_running:
                self._stop(maintenance=False)
            try:
                msg = install_oxide(self.server_dir)
            except Exception:
                if was_running:
                    try:
                        self._start()
                    except Exception as restart_error:
                        from ..util import add_event
                        add_event(self.id, "alert", f"Oxide installation failed; restart failed: {restart_error}")
                raise
            first = self.cfg.get("framework") != "oxide"
            if first:
                self.cfg["framework"] = "oxide"  # later game updates reinstall it
                self.cfg["mode_name"] = "Modded (Oxide / uMod)"
                self.save_cfg({"framework": "oxide", "mode": "oxide", "mode_name": "Modded (Oxide / uMod)"})
            if was_running:
                self._start()
            return f"{msg}{'; server is now modded' if first else ''}{'; restarted' if was_running else ''}"

    def send(self, command):
        from ..util import clean_command
        command = clean_command(command)
        if not command:
            raise ValueError("empty command")
        try:
            out = WebRcon("127.0.0.1", self.rcon_port, self.rcon_password()).command(command)
            return out[:2000] or "sent"
        except RconError as e:
            return f"not sent ({e})"

    def rcon_check(self):
        try:
            info = WebRcon("127.0.0.1", self.rcon_port, self.rcon_password()).serverinfo()
            return {"ok": True, "players": info.get("Players"), "fps": info.get("Framerate")}
        except RconError as e:
            return {"ok": False, "error": str(e)}

    def backup_paths(self):
        return [self.server_dir / "server" / self.identity, self.server_dir / "oxide"]
