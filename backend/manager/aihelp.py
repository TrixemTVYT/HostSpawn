"""AI help: a plain-English guide to one instance for the owner's own AI assistant (Claude, ChatGPT, Copilot...).

Two forms of the same guide:
- guide(a): what the app's "Copy for AI" button copies, to paste into any AI chat. It adds the newest console lines
  and the Server Doctor's findings, so the AI sees what's going on right now.
- write(a): the same guide (without logs and findings, which go out of date) saved as AGENTS.md in the instance's
  folder at every start, for AI tools that work inside a folder (Claude Code, Codex, Copilot...). A CLAUDE.md that
  only points at it is added too, because Claude Code reads that name. An AGENTS.md or CLAUDE.md the owner wrote
  themself (no HostSpawn marker in it) is never replaced.

Secrets never go in: everything passes through util.redact, files holding a password HostSpawn manages are named as
"don't open" but never read out, and the guide holds no IPs, SteamIDs or absolute paths (only paths inside the folder).
"""
import os
import re
from pathlib import Path

from . import util

MARKER = "<!-- Written by HostSpawn. It's rewritten at every start: put your own notes in AI_NOTES.md. -->"
NOTES = "AI_NOTES.md"        # the owner's own notes for their AI, added to the guide (never written by HostSpawn)
LOG_LINES = 40
TREE_LINES = 40
GUIDE_FILES = ("AGENTS.md", "CLAUDE.md")
SCAN_LIMIT = 2000            # text files checked for passwords (the Files editor's own list limit)
# Player details in log lines: IP addresses, SteamIDs (17 digits from 7656119) and BattlEye GUIDs (32 hex).
PLAYER_DETAILS = [(re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
                  (re.compile(r"\b7656119\d{10}\b"), "<steamid>"),
                  (re.compile(r"\b[0-9a-f]{32}\b", re.I), "<guid>")]


def _clean(a, text: str) -> str:
    """No secrets, and no paths that show this computer's folders (the instance folder, then the home folder)."""
    text = util.redact(text)
    for path, name in ((Path(a.root), "<instance folder>"), (Path.home(), "~")):
        if str(path) not in ("", "."):
            text = text.replace(str(path.resolve()), name).replace(str(path), name)
    return text


def hide_players(text: str) -> str:
    """Log lines without players' IP addresses, SteamIDs or BattlEye GUIDs (they're personal details)."""
    for pat, sub in PLAYER_DETAILS:
        text = pat.sub(sub, text)
    return text


def _rel(a, p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(Path(a.root).resolve()))
    except (ValueError, OSError):
        return ""


def _files(a):
    """The Files editor for a HostSpawn-made instance, else None (servers set up by hand live elsewhere)."""
    from .files import FileError, Files
    try:
        return Files(a)
    except FileError:
        return None


def private_files(a, f=None) -> list[str]:
    """Paths (inside the folder) an AI must not open: HostSpawn's private folders and any text file that holds a
    password HostSpawn manages (the same check the Files editor uses before opening a file)."""
    f = f or _files(a)
    if not f:
        return []
    out = [x for x in ("manager/", "backups/", "server/battleye/") if (Path(a.root) / x).exists()]
    values = [v for v in util.secrets().values() if len(v) >= 6]
    if values:
        for item in f.list()[:SCAN_LIMIT]:
            try:
                text = (Path(a.root) / item["path"]).read_text(errors="replace")
            except OSError:
                continue
            if any(v in text for v in values):
                out.append(item["path"])
    return out


def _tree(f) -> list[str]:
    """Folders (up to 4 deep) with how many editable text files each has: enough for an AI to find its way."""
    counts: dict[str, int] = {}
    for item in f.list():
        if item["path"] in GUIDE_FILES:
            continue
        parts = item["path"].split("/")[:-1][:4]
        key = "/".join(parts) + "/" if parts else "(top of the folder)"
        counts[key] = counts.get(key, 0) + 1
    rows = [f"- {k} ({n} file{'s' if n != 1 else ''})" for k, n in sorted(counts.items())]
    if len(rows) > TREE_LINES:
        rows = rows[:TREE_LINES] + [f"- ... and {len(rows) - TREE_LINES} more folders"]
    return rows


def _game_section(a) -> list[str]:
    """Where the things people usually want to change live, for this game and framework."""
    cfg, game = a.cfg or {}, getattr(a, "game", "")
    if game == "Arma 3":
        mission = cfg.get("mission") or ""
        out = ["## Arma 3: where things are",
               "- `server/mpmissions/`: missions. The server runs the packed `.pbo` file; edit an unpacked folder "
               "with the same name (for example `Altis_Life.Altis/` next to `Altis_Life.Altis.pbo`).",
               "- To unpack a mission once: `manager.sh pbo unpack server/mpmissions/<name>.pbo "
               "server/mpmissions/<name>` (run it in the HostSpawn folder, giving the full path to this instance's "
               "folder in front of both paths).",
               "- After editing, pack it again: the HostSpawn app's Files tab has a **Pack** button (it keeps the old "
               "file as `.pbo.bak`). The new mission is used at the next restart.",
               "- `.sqf` scripts are checked for unclosed brackets and quotes when saved from the Files tab."]
        if mission:
            out.insert(1, f"- The mission this server runs: `{mission}` (set in HostSpawn's Settings).")
        if "@life_server" in (cfg.get("server_mods") or []):
            out += ["", "## Altis Life (AsYetUntitled framework)",
                    f"- Framework release: {cfg.get('framework_release') or 'unknown'}.",
                    "- Player-side code is in the mission (`server/mpmissions/Altis_Life.<map>`): `core/` (scripts), "
                    "`config/` (shops, vehicles, licences, prices: `Config_*.hpp`), `dialog/` (menus), "
                    "`description.ext`.",
                    "- Server-side code is `server/@life_server/addons/life_server.pbo` (unpack and pack it the same "
                    "way; keep its `PboPrefix.txt`).",
                    "- The database is MariaDB through extDB3 (`server/@extdb3/`). Its settings file holds the "
                    "database password: never open it. To look at or change data, use the app's Database tab.",
                    "- The Spyglass anti-cheat kicks players for script names it doesn't know. HostSpawn's "
                    "**Spyglass** button allows the official ones it saw in the log."]
        return out
    if game == "Rust":
        ident = cfg.get("identity", "server")
        out = ["## Rust: where things are",
               f"- `server/server/{ident}/cfg/server.cfg` is written by HostSpawn at every start: change those "
               "settings in the app's Settings, not in the file."]
        if cfg.get("framework") == "oxide":
            out += ["- Oxide (uMod) plugins: `server/oxide/plugins/*.cs` (C#). A saved plugin is reloaded by Oxide "
                    "on its own; compile errors show in the console log.",
                    "- Plugin settings: `server/oxide/config/*.json`. Plugin data: `server/oxide/data/`. "
                    "Language files: `server/oxide/lang/`.",
                    "- Reload one plugin from the app's console: `oxide.reload <PluginName>`."]
        else:
            out.append("- This server is vanilla (no plugin framework). The app can add Oxide.")
        return out
    if game == "Arma Reforger":
        return ["## Arma Reforger: where things are",
                "- `server/manager/config.json` is written by HostSpawn at every start: change the scenario, mods "
                "and settings in the app, not in the file.",
                "- `profile/`: the server's profile (saves and logs). Logs are in `profile/logs/`."]
    return []


def _header(a, f) -> list[str]:
    cfg = a.cfg or {}
    lines = [f"# AI help: {getattr(a, 'name', a.id)}", "",
             "This is a game server run by **HostSpawn**, a game-server manager. The owner may not be an expert: "
             "explain changes simply, change one thing at a time, and say how to undo it.", "",
             "## This server",
             f"- Game: {getattr(a, 'game', 'unknown')}" + (f" ({cfg['mode_name']})" if cfg.get("mode_name") else ""),
             f"- HostSpawn id: `{a.id}`",
             f"- Kept running by: {'HostSpawn' if a.supervised_by == 'manager' else a.supervised_by}"]
    for k, label in (("scenario", "Scenario"), ("max_players", "Max players")):
        if cfg.get(k):
            lines.append(f"- {label}: {cfg[k]}")
    if cfg.get("server_mods"):
        lines.append("- Server-side mods: " + ", ".join(f"`{m}`" for m in cfg["server_mods"]))
    lines += [""] + _game_section(a)
    if f:
        gen = sorted({r for r in (_rel(a, p) for p in a.generated_files()) if r and r not in GUIDE_FILES})
        lines += ["", "## Rules (please follow these)",
                  "- **Never open, print or copy a password, token or Steam login.** Don't open these:"]
        lines += [f"  - `{p}`" for p in private_files(a, f)] or ["  - (none found)"]
        lines += ["- HostSpawn rewrites these at every start, so edits there are lost (change them in the app's "
                  "Settings instead):"] + [f"  - `{p}`" for p in gen]
        lines += ["- Before changing a file, keep a copy (`name.ext.bak`). The app's Files tab also keeps the last "
                  "20 versions of every file it saves.",
                  "- Don't delete saves, player data or the database, and don't stop the server, without asking "
                  "the owner first.",
                  "- After a change: restart the server, then read the console log to check it started cleanly.",
                  "", "## Restart and logs",
                  f"- Logs: `{_rel(a, a.log_dir) or 'logs'}/` (the newest file is the current console).",
                  "- In the HostSpawn app: the instance's page has Restart, the console and the Server Doctor "
                  "(which explains common errors in plain English).",
                  f"- From a terminal in the HostSpawn folder: `./manager.sh {a.id} logs`, "
                  f"`./manager.sh {a.id} status`, `./manager.sh {a.id} restart` (ask the owner before restarting "
                  "while players are online).",
                  "", "## Folders (editable text files only)"] + _tree(f)
    notes = Path(a.root) / NOTES if f else None
    if notes and notes.is_file():
        try:
            text = notes.read_text(errors="replace")[:4000].strip()
        except OSError:
            text = ""
        if text:
            lines += ["", "## The owner's notes", text]
    return lines


def guide(a, *, doctor: dict | None = None) -> str:
    """The "Copy for AI" text: the guide plus the newest console lines and the Server Doctor's findings."""
    f = _files(a)
    lines = _header(a, f)
    if doctor and doctor.get("findings"):
        lines += ["", "## Server Doctor findings (now)"]
        for d in doctor["findings"][:10]:
            lines.append(f"- [{d.get('level')}] {d.get('title')}: {d.get('why')} Fix: {d.get('fix')}")
    elif doctor:
        lines += ["", "## Server Doctor findings (now)", "- Nothing wrong found."]
    try:
        logs = a.logs() or {}
    except Exception:
        logs = {}
    for name in ("console", "systemd", "steamcmd"):
        text = (logs.get(name) or "").strip()
        if text:
            tail = hide_players("\n".join(text.splitlines()[-LOG_LINES:])).replace("```", "'''")
            lines += ["", f"## Newest {name} log lines", "```", tail, "```"]
    lines += ["", "My question:", ""]
    return _clean(a, "\n".join(lines))


def ours(p: Path) -> bool:
    """A file HostSpawn wrote (has the marker), so it may be replaced."""
    try:
        return MARKER in p.read_text(errors="replace")[:600]
    except OSError:
        return False


def write(a) -> str | None:
    """Save AGENTS.md (and a CLAUDE.md pointing at it) in a HostSpawn-made instance's folder. Returns what was done,
    or None when there's nothing to do (a server set up by hand, or the owner's own files are there)."""
    f = _files(a)
    if not f or not Path(a.root).is_dir():
        return None
    text = MARKER + "\n" + _clean(a, "\n".join(_header(a, f))) + "\n"
    done = []
    for name, body in ((GUIDE_FILES[0], text),
                       (GUIDE_FILES[1], MARKER + "\nThe guide for this server is in AGENTS.md.\n\n@AGENTS.md\n")):
        p = Path(a.root) / name
        if p.exists() and (p.is_symlink() or not ours(p)):
            continue  # the owner's own file
        if p.exists() and p.read_text(errors="replace") == body:
            continue
        tmp = p.with_name(p.name + ".hostspawn-tmp")
        tmp.write_text(body)
        os.replace(tmp, p)
        done.append(name)
    return ("wrote " + ", ".join(done)) if done else None
