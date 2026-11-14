"""The file editor: an instance's config and script files, read and edited from the app (admins only).

Safety: only text files inside the instance's own folder (symlinks resolved, nothing outside), at most 1 MB, never a
file holding a password HostSpawn manages (it refuses to open it), and never the files HostSpawn writes itself at
every start (those are shown read-only: change them in Settings). Every save checks the format first (JSON, TOML,
XML; balanced braces for Arma-style .cfg), refuses if the file changed since it was opened, keeps the previous text
as a version (20 per file, in state/<id>/file_versions) and writes atomically.
"""
import hashlib
import json
import os
import re
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from . import util
from .adapters.managed import ManagedServer
from .util import add_event

TEXT_EXT = {".cfg", ".txt", ".json", ".ini", ".xml", ".yaml", ".yml", ".toml", ".lua", ".cs", ".js", ".sqf",
            ".sqm", ".hpp", ".ext", ".properties", ".conf", ".md", ".sh", ".csv", ".html", ".css", ".py"}
SKIP_DIRS = {"steamapps", "Bundles", "backups", "logs", "__pycache__", "node_modules", ".reinstall-keep", "tmp",
             "addons", "battleye", "dta", "keys", "steamcmd"}
MAX_BYTES = 1 << 20
MAX_LIST = 2000
KEEP_VERSIONS = 20


class FileError(Exception):
    """A plain-English reason a file can't be listed, opened or saved."""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def sqf_problem(text: str) -> str | None:
    """Definite SQF mistakes only: an unclosed or mismatched ( [ {, a string or /* comment */ left open.
    Strings are "..." or '...' (a doubled quote is a quote); // and /* */ are comments."""
    pairs, stack, i, line, n = {")": "(", "]": "[", "}": "{"}, [], 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
        elif c in "\"'":
            start, i = line, i + 1
            while True:
                if i >= n:
                    return f"Text in quotes started on line {start} is never closed"
                if text[i] == c:
                    if i + 1 < n and text[i + 1] == c:
                        i += 2
                        continue
                    break
                line += text[i] == "\n"
                i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                return f"A comment started on line {line} with /* is never closed with */"
            line += text.count("\n", i, end)
            i = end + 2
            continue
        elif c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if not stack:
                return f"An extra {c} on line {line}"
            if stack[-1][0] != pairs[c]:
                return f"{c} on line {line} doesn't match the {stack[-1][0]} opened on line {stack[-1][1]}"
            stack.pop()
        i += 1
    if stack:
        return f"The {stack[-1][0]} opened on line {stack[-1][1]} is never closed"
    return None


class Files:
    def __init__(self, a):
        if not isinstance(a, ManagedServer) or not a.cfg.get("managed"):
            raise FileError("HostSpawn didn't create this instance, so its files are edited where they live.")
        self.a = a
        self.root = a.root.resolve()

    # ---- paths ----
    def resolve(self, rel: str) -> Path:
        rel = str(rel or "").strip().lstrip("/")
        if not rel or "\x00" in rel:
            raise FileError("No file given")
        p = (self.root / rel).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise FileError("That file is outside this instance's folder")
        parts = p.relative_to(self.root).parts
        if parts[0] in ("manager", "backups") or any(x.startswith(".") for x in parts):
            raise FileError("That's one of HostSpawn's private files")
        return p

    def generated(self, p: Path) -> bool:
        """Written by HostSpawn at every start: edits would be replaced, so they're read-only here."""
        for g in self.a.generated_files():
            g = g.resolve()
            if p == g or g in p.parents:
                return True
        return False

    def rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    # ---- browse: one folder at a time, like a file manager (starts in the game server's folder) ----
    def start_dir(self) -> str:
        d = getattr(self.a, "server_dir", self.root)
        try:
            return self.rel(Path(d).resolve()) if Path(d).resolve() != self.root else ""
        except ValueError:
            return ""

    def browse(self, rel: str = "") -> dict:
        """{dir, up, start, entries: [{name, path, dir, size, editable, read_only}]}: folders first, then files.
        Every file is listed (game data too); text files under 1 MB open in the editor."""
        rel = str(rel or "").strip().strip("/")
        d = self.resolve(rel) if rel else self.root
        if not d.is_dir():
            raise FileError("That folder doesn't exist (any more)")
        entries = []
        for p in sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name.startswith(".") or (d == self.root and p.name in ("manager", "backups")):
                continue  # hidden files and HostSpawn's private folders
            try:
                is_dir = p.is_dir()
                size = 0 if is_dir else p.stat().st_size
            except OSError:
                continue
            editable = (not is_dir and p.suffix.lower() in TEXT_EXT and size <= MAX_BYTES and not p.is_symlink())
            entries.append({"name": p.name, "path": self.rel(p), "dir": is_dir, "size": size, "editable": editable,
                            "read_only": editable and self.generated(p.resolve())})
            if len(entries) >= MAX_LIST:
                break
        here = self.rel(d) if d != self.root else ""
        return {"dir": here, "up": (str(Path(here).parent) if "/" in here else "") if here else None,
                "start": self.start_dir(), "entries": entries}

    # ---- list ----
    def list(self) -> list[dict]:
        out = []
        for dirpath, dirs, files in os.walk(self.root):
            d = Path(dirpath)
            depth = len(d.relative_to(self.root).parts)
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith(".") and not x.endswith("_Data")
                             and not (depth == 0 and x == "manager") and depth < 7)
            for f in sorted(files):
                p = d / f
                if p.suffix.lower() not in TEXT_EXT or p.is_symlink():
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > MAX_BYTES:
                    continue
                out.append({"path": self.rel(p), "size": st.st_size, "modified": st.st_mtime,
                            "read_only": self.generated(p)})
                if len(out) >= MAX_LIST:
                    return out
        return out

    # ---- read ----
    def read(self, rel: str) -> dict:
        p = self.resolve(rel)
        if not p.is_file():
            raise FileError("That file doesn't exist (any more)")
        if p.suffix.lower() not in TEXT_EXT:
            raise FileError("Only text files (config and script files) can be opened here")
        if p.stat().st_size > MAX_BYTES:
            raise FileError("That file is bigger than 1 MB, too big to edit here")
        raw = p.read_bytes()
        if b"\x00" in raw:
            raise FileError("That isn't a text file")
        text = raw.decode("utf-8", errors="replace")
        if any(len(v) >= 6 and v in text for v in util.secrets().values()):
            raise FileError("This file holds a password HostSpawn manages, so it can't be opened here")
        return {"path": self.rel(p), "text": text, "sha": sha(text), "read_only": self.generated(p),
                "versions": self.versions(rel)}

    # ---- save ----
    @staticmethod
    def check(path: Path, text: str) -> str | None:
        """A format problem, or None. Line numbers are 1-based."""
        ext = path.suffix.lower()
        try:
            if ext == ".json":
                json.loads(text)
            elif ext == ".toml":
                tomllib.loads(text)
            elif ext == ".xml":
                ET.fromstring(text)
        except json.JSONDecodeError as e:
            return f"Not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})"
        except tomllib.TOMLDecodeError as e:
            return f"Not valid TOML: {e}"
        except ET.ParseError as e:
            return f"Not valid XML: line {e.position[0]}"
        if ext == ".sqf":
            return sqf_problem(text)
        if ext in (".cfg", ".hpp", ".ext", ".sqm"):
            depth, line = 0, 1
            for chunk in re.split(r'("(?:[^"\n]|"")*")', text):  # skip quoted text ("" is a quote in Arma)
                if chunk.startswith('"') and len(chunk) > 1:
                    line += chunk.count("\n")
                    continue
                for c in chunk:
                    if c == "\n":
                        line += 1
                    elif c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth < 0:
                            return f"An extra closing brace }} on line {line}"
            if depth:
                return f"{depth} brace{'s' if depth > 1 else ''} {{ never closed"
        return None

    def save(self, rel: str, text: str, base_sha: str, by: str = "") -> dict:
        p = self.resolve(rel)
        if p.suffix.lower() not in TEXT_EXT:
            raise FileError("Only text files (config and script files) can be saved here")
        if self.generated(p):
            raise FileError("HostSpawn writes this file at every start, so edits here would be lost. Change these "
                            "settings in the Settings tab.")
        if len(text.encode()) > MAX_BYTES:
            raise FileError("That's over 1 MB, too big to save here")
        if any(len(v) >= 6 and v in text for v in util.secrets().values()):
            raise FileError("That text contains a password HostSpawn manages; it can't be saved here")
        problem = self.check(p, text)
        if problem:
            raise FileError(problem + ". Nothing was saved.")
        old = p.read_text(errors="replace") if p.exists() else ""
        if p.exists() and sha(old) != base_sha:
            raise FileError("This file changed since you opened it (the game or someone else saved it). Reload it, "
                            "then make your change again.")
        if p.exists():
            self._keep_version(rel, old, by)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".hostspawn-tmp")
        tmp.write_text(text)
        if p.exists():
            os.chmod(tmp, p.stat().st_mode & 0o777)
        os.replace(tmp, p)
        add_event(self.a.id, "info", f"file saved: {self.rel(p)}" + (f" (by {by})" if by else ""))
        return self.read(rel)

    # ---- versions ----
    def _vdir(self, rel: str) -> Path:
        return self.a.flag("file_versions") / hashlib.sha256(str(self.resolve(rel)).encode()).hexdigest()[:20]

    def _keep_version(self, rel: str, text: str, by: str):
        d = self._vdir(rel)
        d.mkdir(parents=True, exist_ok=True)
        vid = f"{time.time():.3f}"
        (d / f"{vid}.txt").write_text(text)
        index = self._index(d)
        index.append({"id": vid, "at": float(vid), "by": by, "size": len(text.encode())})
        for gone in index[:-KEEP_VERSIONS]:
            (d / f"{gone['id']}.txt").unlink(missing_ok=True)
        (d / "index.json").write_text(json.dumps(index[-KEEP_VERSIONS:]))

    @staticmethod
    def _index(d: Path) -> list:
        try:
            return json.loads((d / "index.json").read_text())
        except (OSError, ValueError):
            return []

    def versions(self, rel: str) -> list:
        return list(reversed(self._index(self._vdir(rel))))

    def version_text(self, rel: str, vid: str) -> str:
        if not re.fullmatch(r"\d+\.\d{3}", str(vid)):
            raise FileError("Unknown version")
        f = self._vdir(rel) / f"{vid}.txt"
        if not f.exists():
            raise FileError("That version is no longer kept")
        return f.read_text()

    def restore(self, rel: str, vid: str, by: str = "") -> dict:
        text = self.version_text(rel, vid)
        cur = self.read(rel)
        return self.save(rel, text, cur["sha"], by)

    # ---- Arma 3: mission folders packed into .pbo files ----
    def missions(self) -> list[dict]:
        """Unpacked mission folders in mpmissions/ (Arma 3), with whether a packed .pbo exists."""
        if not hasattr(self.a, "is_arma3"):
            return []
        d = self.a.server_dir / "mpmissions"
        if not d.is_dir():
            return []
        return [{"folder": x.name, "packed": (d / f"{x.name}.pbo").exists(),
                 "packed_at": (d / f"{x.name}.pbo").stat().st_mtime if (d / f"{x.name}.pbo").exists() else None}
                for x in sorted(d.iterdir()) if x.is_dir() and not x.name.startswith(".")]

    def pack_mission(self, folder: str, by: str = "") -> str:
        """mpmissions/<folder> → mpmissions/<folder>.pbo. The previous .pbo is kept as <folder>.pbo.bak."""
        from . import pbo
        if not hasattr(self.a, "is_arma3"):
            raise FileError("Packing missions is for Arma 3")
        if not re.fullmatch(r"[\w .\-]{1,100}", str(folder or "")) or folder.startswith("."):
            raise FileError("Unknown mission folder")
        src = self.resolve(f"server/mpmissions/{folder}")
        if not src.is_dir():
            raise FileError("That mission folder doesn't exist")
        if not any(src.glob("mission.sqm")):
            raise FileError("That folder has no mission.sqm, so it isn't a mission")
        out = src.parent / f"{folder}.pbo"
        tmp = src.parent / f"{folder}.pbo.hostspawn-tmp"
        n = pbo.pack(src, tmp)
        if out.exists():
            os.replace(out, out.with_name(out.name + ".bak"))
        os.replace(tmp, out)
        add_event(self.a.id, "info", f"mission packed: {out.name} ({n} files)" + (f" (by {by})" if by else ""))
        return f"Packed {n} files into {out.name}. It's used at the next restart (if it's the mission in Settings)."
