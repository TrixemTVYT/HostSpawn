"""Server Doctor: reads an instance's logs and this machine's state and says, in plain English, what's wrong and how
to fix it. Each known problem is one rule in RULES (a log pattern, or a check of the machine). Findings can carry a
safe fix the app offers as a button: an install op (repair, cleanup, update), an action (start, restart) or a page to
open (files, pc-check). Nothing here changes anything; it only looks."""
import re
import time
from pathlib import Path

from . import hostcheck
from .util import read_events

PROBLEM, WARNING, TIP = "problem", "warning", "tip"
ANY = ("rust", "reforger", "arma3")
GAME_KEYS = {"Rust": "rust", "Arma Reforger": "reforger", "Arma 3": "arma3"}  # adapter.game → the rules' game key


def rule(rid, games, pattern, level, title, why, fix, action=None, logs=("console",)):
    return {"id": rid, "games": games, "re": re.compile(pattern, re.I), "level": level, "title": title, "why": why,
            "fix": fix, "action": action, "logs": logs}


# Titles and fixes may use {m}: the first group the pattern caught (a plugin, a library, a file name).
RULES = [
    # ---- any game ----
    rule("port_in_use", ANY, r"address already in use|EADDRINUSE|failed to bind|couldn'?t bind|bind failed", PROBLEM,
         "Another program is using this server's port",
         "The game couldn't open its network port because something else already has it, often a second copy of "
         "the same server, or another instance set to the same port.",
         "Stop the other program, or give this instance different ports in Settings, then press Start."),
    rule("disk_full_log", ANY, r"no space left on device|disk full|not enough (disk )?space", PROBLEM,
         "The disk is full",
         "The server (or its update) couldn't write a file because there's no free disk space.",
         "Free some space: Installation → Clean up removes leftovers, and old backups can be deleted.",
         {"kind": "install", "op": "cleanup", "label": "Clean up"}, logs=("console", "steamcmd")),
    rule("out_of_memory", ANY, r"out of memory|std::bad_alloc|cannot allocate memory|^killed\s*$", PROBLEM,
         "The server ran out of memory",
         "The machine didn't have enough free memory, so the server (or Linux) stopped it.",
         "Close other programs, lower max players or the map size, or add memory or swap. The PC check shows "
         "what this machine has.",
         {"kind": "open", "page": "pccheck", "label": "Open PC check"}),
    rule("missing_library", ANY, r"error while loading shared libraries: (\S+?):", PROBLEM,
         "A system library is missing: {m}",
         "The game needs a Linux library that isn't installed on this machine.",
         "Install it with the system package manager (for 32-bit libraries the package name ends in :i386), "
         "then press Start. The Dependencies check in the browser dashboard lists what's missing."),
    rule("permission_denied", ANY, r"permission denied", WARNING,
         "The server wasn't allowed to open a file",
         "A file or folder it needs belongs to another user or isn't readable.",
         "Make sure the instance's folder belongs to the user HostSpawn runs as. Repair puts the game files back.",
         {"kind": "install", "op": "repair", "label": "Repair"}),
    rule("segfault", ANY, r"segmentation fault|signal 11|SIGSEGV", WARNING,
         "The game crashed inside its own code",
         "Usually a broken game file, a mod or plugin that doesn't match the game version, or a bad config value.",
         "Try Repair first. If it keeps happening, turn off recently added mods or plugins one at a time.",
         {"kind": "install", "op": "repair", "label": "Repair"}),
    rule("steam_login", ANY, r"steam guard|two-factor|invalid password|login failure|cached credentials not found",
         PROBLEM, "Steam needs you to log in again",
         "The game download needs a Steam login and Steam refused the saved one (or wants a Steam Guard code).",
         "Press Steam login on this instance and type your details there. HostSpawn never stores the password.",
         logs=("steamcmd",)),
    rule("steam_no_sub", ANY, r"no subscription", PROBLEM,
         "This Steam account can't download this server",
         "Steam says the account doesn't have access to this server's files.",
         "Arma 3's server needs a normal Steam account (it doesn't have to own the game). Log in with another "
         "account if this one is limited.", logs=("steamcmd",)),
    rule("steam_disk", ANY, r"0x202|0x212|not enough disk space", PROBLEM,
         "The update stopped: not enough disk space",
         "Steam needs room for the download and the unpacked files (roughly twice the game size during updates).",
         "Free some space, then update again.",
         {"kind": "install", "op": "cleanup", "label": "Clean up"}, logs=("steamcmd",)),
    rule("steam_failed", ANY, r"ERROR! Failed to install app '\d+' \(([^)]+)\)", WARNING,
         "The last update failed ({m})",
         "Steam couldn't finish the download. This is often a short problem on Steam's side.",
         "Try the update again in a few minutes. If it keeps failing, Repair checks every file.",
         {"kind": "install", "op": "update", "label": "Update now"}, logs=("steamcmd",)),

    # ---- Rust ----
    rule("rust_plugin_compile", ("rust",), r"(?:error while compiling|failed to compile)[: ]+(\w+)", WARNING,
         "The plugin {m} has an error",
         "Oxide couldn't build this plugin, usually because it's out of date with the latest Rust or Oxide update. "
         "The server keeps running without it.",
         "Update the plugin from its uMod page, or remove {m}.cs from oxide/plugins in the Files tab.",
         {"kind": "open", "page": "files", "label": "Open Files"}),
    rule("rust_plugin_load", ("rust",), r"failed to (?:load|initialize) plugin[ ']+(\w+)", WARNING,
         "The plugin {m} didn't start",
         "The plugin was built, but it hit an error while starting, often a broken config file in oxide/config.",
         "Check {m}'s config in oxide/config (the Files tab checks JSON for you), or update the plugin.",
         {"kind": "open", "page": "files", "label": "Open Files"}),
    rule("rust_rcon", ("rust",), r"(?:rcon|websocket).{0,40}(?:failed|error|couldn'?t start)", WARNING,
         "RCON didn't start",
         "Rust's remote console couldn't open its port. HostSpawn uses it for players, messages and health checks.",
         "Make sure no other program uses this instance's RCON port, then restart.",
         {"kind": "action", "action": "restart", "label": "Restart"}),
    rule("rust_map", ("rust",), r"(?:map|world) (?:generation|load(?:ing)?) failed|couldn'?t load (?:the )?map", PROBLEM,
         "The map didn't load",
         "Rust couldn't build or load the map. A custom map URL may be wrong, or the map file is damaged.",
         "Check the map settings, or wipe the map so Rust builds it fresh (a backup is made first)."),

    # ---- Arma Reforger ----
    rule("reforger_mod_download", ("reforger",),
         r"(?:addon|mod).{0,40}(?:download failed|failed to download|not found|missing)|required addons? (?:are )?missing",
         PROBLEM, "A mod couldn't be downloaded",
         "Reforger downloads mods from the Workshop at start-up. One of them is missing, private, or the version "
         "in the config doesn't exist any more.",
         "Check the mod list in config.json: remove the version number so the latest is used, or remove the mod.",
         {"kind": "open", "page": "files", "label": "Open Files"}),
    rule("reforger_config", ("reforger",), r"(?:json|config).{0,40}(?:invalid|parse error|failed to (?:load|parse|read))",
         PROBLEM, "config.json can't be read",
         "There is a mistake in the server's config file (a missing comma or quote is the usual cause).",
         "Open config.json in the Files tab; it shows the line with the mistake.",
         {"kind": "open", "page": "files", "label": "Open Files"}),
    rule("reforger_scenario", ("reforger",), r"(?:scenario|mission).{0,40}(?:not found|failed to load|unable to load)",
         PROBLEM, "The scenario (map) wasn't found",
         "The scenario ID in the config doesn't exist, or it belongs to a mod that isn't loaded.",
         "Pick the scenario again in Settings, or add the mod it comes from."),
    rule("reforger_backend", ("reforger",), r"backend.{0,40}(?:not available|failed|unreachable)", WARNING,
         "Reforger's online service didn't answer",
         "The server couldn't reach Bohemia's online service, so it may not appear in the server list.",
         "Usually Bohemia's side: wait a few minutes and restart. If it lasts, check the machine's internet.",
         {"kind": "action", "action": "restart", "label": "Restart"}),

    # ---- Arma 3 ----
    rule("arma3_be_kick", ("arma3",), r"kicked off by BattlEye: (.{0,80})", WARNING,
         "BattlEye kicked a player: {m}",
         "BattlEye on the player's own PC isn't running properly (\"Game restart required\" means their game was "
         "started without BattlEye, or BattlEye updated and needs the game restarted).",
         "The player closes Arma 3 fully and starts it from the launcher with BattlEye on. If it keeps happening, "
         "Settings › Game settings › BattlEye anti-cheat can turn it off for this instance (applies at the next restart)."),
    rule("arma3_spyglass", ("arma3",), r"SPYGLASS-FLAG: .{0,80}? : \d* : (.{0,160})", PROBLEM,
         "Spyglass (Altis Life's anti-cheat) threw a player out",
         "Spyglass only knows the Arma 3 parts that existed in 2019. Newer official parts, or any mod a player "
         "runs, look like cheats to it, so honest players get \"The SpyGlass sees you!\" and can't play.",
         "Press Update Spyglass: it adds the official Arma 3 parts (names starting A3_) it flagged to its allowed "
         "list, after a backup. Then restart. If players still can't join, use Turn off on the Spyglass card. "
         "Other names may be mods or cheats: check them first. Flagged: {m}",
         {"kind": "action", "action": "spyglass", "label": "Update Spyglass"}),
    rule("arma3_extdb", ("arma3",), r"extDB3.{0,60}(?:failed to connect|database error|can'?t connect|access denied)",
         PROBLEM, "Altis Life can't reach its database",
         "extDB3 couldn't log in to MariaDB: the database isn't running, or the login in extdb3-conf.ini is wrong.",
         "Make sure MariaDB is running (sudo systemctl start mariadb), then open the Database tab: if it opens, "
         "the login works.", {"kind": "open", "page": "database", "label": "Open Database"}),
    rule("arma3_mission", ("arma3",), r"mission .{0,80}(?:not found|cannot be found)|cannot open (?:mission|file) .{0,80}\.pbo",
         PROBLEM, "The mission file wasn't found",
         "The mission in server.cfg isn't in mpmissions, or its name doesn't match (Linux cares about capitals).",
         "Check mpmissions in the Files tab. If it's a folder, use Pack to PBO.",
         {"kind": "open", "page": "files", "label": "Open Files"}),
    rule("arma3_addon", ("arma3",), r"addon '([^']+)' requires addon", WARNING,
         "A mod needs another mod: {m}",
         "One of the loaded mods depends on another mod that isn't loaded.",
         "Add the missing mod, or check the mod order in the instance's settings."),
    rule("arma3_mod_case", ("arma3",), r"cannot (?:open|load) .{0,20}(@[A-Za-z0-9_]*[A-Z][A-Za-z0-9_]*)", WARNING,
         "Mod folder name has capitals: {m}",
         "On Linux, Arma 3 looks for mod folders in lower case.",
         "Rename the folder to lower case (for example @cba_a3)."),
    rule("arma3_battleye", ("arma3",), r"failed to initialize battleye", WARNING,
         "BattlEye didn't start",
         "Anti-cheat couldn't start, so players with BattlEye on can't join.",
         "Repair puts BattlEye's files back.", {"kind": "install", "op": "repair", "label": "Repair"}),
]

MIN_FREE_DISK_GB = 3
MIN_FREE_RAM_GB = 1


def port_busy(port: int, udp: bool) -> bool:
    """True if something on this machine already holds this port. Reads Linux's port tables (/proc/net) instead of
    trying the port, so it can never get in the way of a server that's starting at that moment."""
    names = ("udp", "udp6") if udp else ("tcp", "tcp6")
    want = f"{port:04X}"
    for name in names:
        try:
            lines = Path("/proc/net", name).read_text().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            if len(parts) > 3 and parts[1].rsplit(":", 1)[-1] == want and (udp or parts[3] == "0A"):  # 0A = LISTEN
                return True
    return False


def _finding(r, m=None, where=""):
    caught = (m.group(1) if m and m.groups() else "") or ""
    f = {"id": r["id"], "level": r["level"], "title": r["title"].replace("{m}", caught), "why": r["why"],
         "fix": r["fix"].replace("{m}", caught), "where": where}
    if r.get("action"):
        f["action"] = dict(r["action"])
    return f


def _log_findings(game: str, logs: dict, steam_relevant: bool) -> tuple[list, int]:
    out, seen, checked = [], set(), 0
    for r in RULES:
        if game not in r["games"]:
            continue
        checked += 1
        for name in r["logs"]:
            if name == "steamcmd" and not steam_relevant:
                continue  # an old update log only matters while an update is failing or the game isn't installed
            text = logs.get(name) or ""
            # Newest problem first: look from the end of the log.
            for line in reversed(text.splitlines()[-400:]):
                m = r["re"].search(line)
                if m and r["id"] not in seen:
                    seen.add(r["id"])
                    out.append(_finding(r, m, f"{name} log: {line.strip()[:160]}"))
                    break
    return out, checked


def diagnose(a, snap: dict | None = None, *, machine: dict | None = None, events: list | None = None,
             busy=port_busy) -> dict:
    """{"findings": [...], "checked": n, "ok": bool, "time": t}. Findings are sorted problems first."""
    snap = snap or {}
    st = snap.get("status") or {}
    state = st.get("state")
    flags = st.get("flags") or []
    findings, checked = [], 0
    game = GAME_KEYS.get(getattr(a, "game", ""), getattr(a, "game", ""))

    # The game isn't there: Repair (or the first install) fixes that before anything else matters.
    checked += 1
    if state == "not_installed":
        findings.append({"id": "not_installed", "level": PROBLEM, "title": "The game files are missing",
                         "why": "The server program isn't in the instance's folder, so it can't start.",
                         "fix": "Press Repair: HostSpawn downloads the game again and keeps your settings and saves.",
                         "action": {"kind": "install", "op": "repair", "label": "Repair"}, "where": ""})

    # Crash loop: the watchdog gave up.
    checked += 1
    if any("Crash loop" in f for f in flags) or (getattr(a, "has", None) and _safe(lambda: a.has("gave_up"))):
        findings.append({"id": "crash_loop", "level": PROBLEM, "title": "It kept crashing, so HostSpawn stopped trying",
                         "why": "The server failed to start several times in a row. The findings below (or the "
                                "console log) say why.",
                         "fix": "Fix the cause, then press Start.",
                         "action": {"kind": "action", "action": "start", "label": "Start"}, "where": ""})
    else:
        checked += 1
        evs = events if events is not None else read_events(400)
        hour = time.time() - 3600
        crashes = sum(1 for e in evs if e.get("server") == a.id and e.get("t", 0) > hour
                      and re.search(r"restarting|crash|restarted it", e.get("message", ""), re.I))
        if crashes >= 3:
            findings.append({"id": "many_restarts", "level": WARNING,
                             "title": f"Restarted {crashes} times in the last hour",
                             "why": "Something keeps stopping it. The findings below or the console log usually "
                                    "show what.", "fix": "Look at the console log around each crash.", "where": ""})

    # Ports: only meaningful while our server isn't the one holding them.
    cfg = getattr(a, "cfg", {}) or {}
    if state in ("stopped", "maintenance", None):
        for key, udp in (("port", True), ("query_port", True), ("rcon_port", False)):
            p = cfg.get(key)
            if isinstance(p, int) and 0 < p < 65536:
                checked += 1
                if busy(p, udp):
                    findings.append({"id": "port_taken", "level": PROBLEM,
                                     "title": f"Port {p} is already in use by another program",
                                     "why": f"This instance needs port {p} ({key.replace('_', ' ')}), but something "
                                            "else on this machine has it, so the server can't start.",
                                     "fix": "Stop the other program, or change this instance's ports in Settings.",
                                     "where": ""})

    # The machine.
    try:
        m = machine or hostcheck.machine()
    except Exception:
        m = {}
    checked += 2
    if m.get("disk_total_gb") and m.get("disk_free_gb", 99) < MIN_FREE_DISK_GB:
        findings.append({"id": "disk_low", "level": PROBLEM if m["disk_free_gb"] < 1 else WARNING,
                         "title": f"Only {m['disk_free_gb']:g} GB of disk space left",
                         "why": "Updates, backups and logs need room. A full disk stops servers and can damage saves.",
                         "fix": "Installation → Clean up removes leftovers; delete old backups you don't need.",
                         "action": {"kind": "install", "op": "cleanup", "label": "Clean up"}, "where": ""})
    if m.get("ram_total_gb") and m.get("ram_free_gb", 99) < MIN_FREE_RAM_GB:
        findings.append({"id": "ram_low", "level": WARNING, "title": f"Only {m['ram_free_gb']:g} GB of memory free",
                         "why": "Game servers use more memory as players join. With this little left, one may be "
                                "stopped by Linux.",
                         "fix": "Close other programs or lower max players. The PC check shows what this machine has.",
                         "action": {"kind": "open", "page": "pccheck", "label": "Open PC check"}, "where": ""})

    # The logs.
    try:
        logs = a.logs() or {}
    except Exception:
        logs = {}
    steam_relevant = state == "not_installed" or any(("update failed" in f.lower() or "steam login" in f.lower())
                                                     for f in flags)
    found, n = _log_findings(game, logs, steam_relevant)
    checked += n
    have = {f["id"] for f in findings}
    if any(f["id"] == "arma3_spyglass" for f in found) and hasattr(a, "spyglass_status"):
        try:
            spy = a.spyglass_status()
        except Exception:
            spy = {}
        if spy and (spy.get("on") is False or not (spy.get("official") or spy.get("missing"))):  # paused, or every flagged part is allowed now
            found = [f for f in found if f["id"] != "arma3_spyglass"]
    for f in found:
        if not (f["id"] == "port_in_use" and "port_taken" in have):  # the port check already says it, with the port
            findings.append(f)

    order = {PROBLEM: 0, WARNING: 1, TIP: 2}
    findings.sort(key=lambda f: order[f["level"]])
    return {"findings": findings, "checked": checked, "ok": not any(f["level"] == PROBLEM for f in findings),
            "time": time.time()}


def _safe(fn):
    try:
        return bool(fn())
    except Exception:
        return False
