"""Spyglass, Altis Life's built-in anti-cheat (AsYetUntitled framework).

Its list of allowed game parts (`_patchList` in SpyGlass/fn_initSpy.sqf) was last updated for Arma 3 in 2019. Every
newer official part counts as an "unknown addon", so honest players get "The SpyGlass sees you!" and are thrown out.
The server log then has:  "SPYGLASS-FLAG: <name> : <uid> : [""A3_Part_1"",""A3_Part_2""]"

`allow(a)` adds the reported names to the list inside the mission .pbo (after a backup). Only official Bohemia parts
(names starting with A3_) are added automatically; anything else may be a cheat or a mod, so it is only listed.
`set_on(a, on)` turns Spyglass off or on with the framework's own switch (spyGlass_toggle in config/Config_Master.hpp):
the official, reversible way, better than deleting files. Every change backs up the mission and needs a restart."""
import re
import shutil
import struct
import tempfile
import time
from pathlib import Path

from . import pbo

FLAG = re.compile(r"SPYGLASS-FLAG: (.{0,80}?) : (\d*) : (.{0,4000})")
NAME = re.compile(r"^[A-Za-z0-9_]{1,100}$")
SCRIPT = "SpyGlass/fn_initSpy.sqf"
MASTER = "config/Config_Master.hpp"
TOGGLE = re.compile(r"(spyGlass_toggle\s*=\s*)(true|false|1|0)(\s*;)", re.I)
LIST_START = re.compile(r"(_patchList\s*=\s*\[)")


def official(name: str) -> bool:
    return name.startswith("A3_")


def reported(text: str) -> list[str]:
    """Game-part names Spyglass flagged in a log, oldest first, without repeats."""
    out = []
    for m in FLAG.finditer(text.replace('""', '"')):  # diag_log doubles the quotes inside the logged text
        for name in re.findall(r'"([^"]+)"', m.group(3)):
            if NAME.match(name) and name not in out:
                out.append(name)
    return out


def mission_pbo(a) -> Path | None:
    mission = (a.cfg.get("mission") or "").strip()
    p = a.server_dir / "mpmissions" / f"{mission}.pbo"
    return p if mission and p.is_file() else None


def has_spyglass(a) -> bool:
    p = mission_pbo(a)
    if not p:
        return False
    try:
        return any(n.replace("\\", "/").lower() == SCRIPT.lower() for n in pbo.read(p)[1])
    except Exception:
        return False


def allowed(script: str) -> set[str]:
    m = LIST_START.search(script)
    if not m:
        return set()
    end = script.find("];", m.end())
    return set(re.findall(r'"([^"]+)"', script[m.end():end]))


def add_names(script: str, names: list[str]) -> tuple[str, list[str]]:
    """The script with names added to the start of _patchList, and which names were new."""
    m = LIST_START.search(script)
    if not m:
        raise ValueError("this mission's Spyglass has no _patchList")
    have = allowed(script)
    new = [n for n in names if NAME.match(n) and n not in have]
    if not new:
        return script, []
    insert = "\n    " + ",".join(f'"{n}"' for n in new) + ","
    return script[:m.end()] + insert + script[m.end():], new


def allow(a, log_text: str) -> str:
    """Add the official parts Spyglass flagged in log_text to the mission's allowed list."""
    if not has_spyglass(a):
        return "this instance's mission has no Spyglass: nothing to change"
    names = reported(log_text)
    # Flagged official parts, plus every official part in the server's own game files (one kick only names ~30)
    ok = [n for n in names if official(n)] + sorted(official_parts(a.server_dir))
    other = [n for n in names if not official(n)]
    note = f" Not added (not official Arma 3 parts, check them yourself): {', '.join(other[:10])}." if other else ""
    if not ok:
        return ("Spyglass hasn't flagged any official Arma 3 parts." + note) if names else \
            "Spyglass already knows every official Arma 3 part on this server: nothing to change."
    added = []

    def change(files):
        text, new = add_names(files[SCRIPT], ok)
        added.extend(new)
        return {SCRIPT: text} if new else None

    if not _edit(a, change):
        return "Spyglass already allows these parts. Restart the instance if it hasn't been restarted since." + note
    new = added
    return (f"Spyglass now allows {len(new)} more official Arma 3 part(s): {', '.join(new[:5])}{'…' if len(new) > 5 else ''}. "
            f"Restart the instance so players get the new mission. Old mission backed up to backups/missions." + note)


def _files(p: Path) -> dict[str, str]:
    """The mission's text files by forward-slash path."""
    return {k.replace("\\", "/"): v.decode("utf-8", "replace") for k, v in pbo.read(p)[1].items()
            if k.lower().endswith((".sqf", ".hpp", ".ext", ".cpp", ".h"))}


def _find(files: dict, want: str) -> str | None:
    return next((k for k in files if k.lower() == want.lower()), None)


def _edit(a, change) -> bool:
    """Unpack the mission, let change(files) return {path: new text} (or None for no change), back up, repack."""
    p = mission_pbo(a)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "mission"
        pbo.unpack(p, work)
        names = {f.relative_to(work).as_posix(): f for f in work.rglob("*") if f.is_file()}
        files = {SCRIPT: "", MASTER: ""}
        for want in (SCRIPT, MASTER):
            k = _find(names, want)
            if k:
                files[want] = names[k].read_text(errors="replace")
        out = change(files)
        if not out:
            return False
        for want, text in out.items():
            names[_find(names, want)].write_text(text)
        stamp, n = time.strftime('%Y%m%d-%H%M%S'), 1
        backup = a.root / "backups" / "missions" / f"{p.stem}_{stamp}.pbo"
        while backup.exists():  # two changes in one second: keep both backups
            n += 1
            backup = backup.with_name(f"{p.stem}_{stamp}-{n}.pbo")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, backup)
        prefix = (work / "$PBOPREFIX$").read_text().strip() if (work / "$PBOPREFIX$").exists() else None
        tmp_out = p.with_suffix(".pbo.new")
        pbo.pack(work, tmp_out, prefix)
        tmp_out.replace(p)
    return True


def is_on(master: str) -> bool | None:
    m = TOGGLE.search(master or "")
    return None if not m else m.group(2).lower() in ("true", "1")


def set_on(a, on: bool) -> str:
    """Turn Spyglass on or off with the framework's own switch."""
    if not mission_pbo(a) or not has_spyglass(a):
        return "this instance's mission has no Spyglass: nothing to change"

    def change(files):
        state = is_on(files[MASTER])
        if state is None:
            raise ValueError("spyGlass_toggle wasn't found in config/Config_Master.hpp")
        if state == on:
            return None
        return {MASTER: TOGGLE.sub(lambda m: m.group(1) + ("true" if on else "false") + m.group(3), files[MASTER], 1)}

    if not _edit(a, change):
        return f"Spyglass is already {'on' if on else 'off'}."
    return (f"Spyglass is now {'ON' if on else 'OFF'} in the mission. Restart the instance to apply it. "
            "Old mission backed up to backups/missions.")


def status(a, log_text: str = "") -> dict:
    """What the app's Spyglass card shows: present, on/off, how many parts it allows, and who it flagged lately."""
    p = mission_pbo(a)
    out = {"present": False, "on": None, "allowed": 0, "flags": [], "official": [], "other": []}
    if not p:
        return out
    try:
        files = _files(p)
    except Exception:
        return out
    script, master = _find(files, SCRIPT), _find(files, MASTER)
    if not script:
        return out
    out.update(present=True, on=is_on(files.get(master, "")), allowed=len(allowed(files[script])))
    have = allowed(files[script])
    text = log_text.replace('""', '"')
    for m in FLAG.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        when = text[line_start:line_start + 8].strip()
        out["flags"].append({"time": when if re.match(r"^\d{1,2}:\d\d:\d\d$", when) else "",
                             "player": m.group(1).strip()[:40], "reason": m.group(3).strip()[:300]})
    out["flags"] = out["flags"][-10:]
    names = [n for n in reported(log_text) if n not in have]
    out["official"] = [n for n in names if official(n)]
    out["other"] = [n for n in names if not official(n)]
    out["missing"] = len(official_parts(a.server_dir) - have)  # official parts this Arma 3 has that Spyglass lacks
    return out


# ---- every official Arma 3 part, read from the server's own game files ----
# Arma cuts log lines at about 1024 characters, so one kick only names ~30 missing parts. The server has the same
# official game data as players (base game and DLC data), so its CfgPatches names are the complete list.

def _pbo_configs(path: Path) -> list[bytes]:
    """Every config.bin in a .pbo (official addons keep one per sub-folder), reading only the header and those files
    (game .pbo files can be gigabytes)."""
    with open(path, "rb") as f:
        size_limit = 1 << 20
        while True:  # big addons have big headers: read more until the whole header fits (up to 64 MB)
            f.seek(0)
            head = f.read(size_limit)
            try:
                entries, offset = _pbo_header(head)
                break
            except (ValueError, struct.error):
                if len(head) < size_limit or size_limit >= 64 << 20:
                    return []
                size_limit *= 4
        out = []
        for name, method, size in entries:
            low = name.lower().replace("\\", "/")
            if (low == "config.bin" or low.endswith("/config.bin")) and method in (0, pbo.VERS) and size < 50 << 20:
                f.seek(offset)
                out.append(f.read(size))
            offset += size
    return out


def _pbo_header(head: bytes) -> tuple[list, int]:
    """([(name, method, size)], where the file data starts). Raises ValueError if head is cut short."""
    i, entries = 0, []

    def cstr():
        nonlocal i
        j = head.index(b"\x00", i)
        s = head[i:j].decode("latin-1")
        i = j + 1
        return s
    while True:
        name = cstr()
        method, _, _, _, size = struct.unpack_from("<5I", head, i)
        i += 20
        if method == pbo.VERS and not name:
            while cstr():
                cstr()
            continue
        if not name:
            return entries, i
        entries.append((name, method, size))


def _cfgpatches(data: bytes) -> list[str]:
    """Class names under CfgPatches in a rapified config.bin."""
    if not data or data[:4] != b"\x00raP":
        return []
    pos = 0

    def cstr():
        nonlocal pos
        j = data.index(b"\x00", pos)
        s = data[pos:j].decode("latin-1")
        pos = j + 1
        return s

    def cint():
        nonlocal pos
        n, shift = 0, 0
        while True:
            b = data[pos]
            pos += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                return n

    def skip_array():
        nonlocal pos
        for _ in range(cint()):
            t = data[pos]
            pos += 1
            if t in (0, 4):
                cstr()
            elif t in (1, 2):
                pos += 4
            elif t == 3:
                skip_array()
            elif t == 6:
                pos += 8
            else:
                raise ValueError(f"array type {t}")

    def classes(at: int) -> dict[str, int]:
        """{class name: body offset} for the class body at `at`."""
        nonlocal pos
        pos = at
        cstr()  # parent class
        out = {}
        for _ in range(cint()):
            t = data[pos]
            pos += 1
            if t == 0:
                name = cstr()
                out[name] = struct.unpack_from("<I", data, pos)[0]
                pos += 4
            elif t == 1:
                sub = data[pos]
                pos += 1
                cstr()
                if sub == 0 or sub == 4:
                    cstr()
                elif sub == 6:
                    pos += 8
                else:
                    pos += 4
            elif t == 2:
                cstr()
                skip_array()
            elif t in (3, 4):
                cstr()
            elif t == 5:
                pos += 4
                cstr()
                skip_array()
            else:
                raise ValueError(f"entry type {t}")
        return out
    try:
        root = classes(16)
        at = next((v for k, v in root.items() if k.lower() == "cfgpatches"), None)
        return list(classes(at)) if at else []
    except (ValueError, IndexError, struct.error):
        return []


_OFFICIAL: dict = {}


def official_parts(server_dir: Path) -> set[str]:
    """Every official (A3_) CfgPatches name in the server's game files. Cached per folder and file count."""
    files = [p for p in Path(server_dir).rglob("*.pbo")
             if not any(part.startswith("@") or part in ("mpmissions", "keys") for part in p.relative_to(server_dir).parts)]
    key = (str(server_dir), len(files))
    if key not in _OFFICIAL:
        names = set()
        for p in files:
            try:
                for data in _pbo_configs(p):
                    names.update(n for n in _cfgpatches(data) if official(n))
            except (OSError, ValueError, struct.error):
                continue
        _OFFICIAL.clear()
        _OFFICIAL[key] = names
    return _OFFICIAL[key]
