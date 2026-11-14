"""Dependencies: everything Manager and the server types need besides Manager itself, with a check and a way to
install each. On Linux, system packages install with `sudo -n apt-get` when the person presses Install (and
confirms the exact command); if sudo needs a password, the command is shown for them to run. Windows items carry
their official download link until the Windows installer exists."""
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

from .util import add_event, run

LINUX = platform.system() == "Linux"

DEPS = [
    {"id": "app-window", "name": "App window (WebKitGTK)", "platform": "linux", "pkg": "gir1.2-webkit2-4.1",
     "why": "Opens HostSpawn as its own app window, without a web browser.", "needed_by": ["HostSpawn app"]},
    {"id": "tmux", "name": "tmux", "platform": "linux", "pkg": "tmux",
     "why": "Keeps the servers HostSpawn runs going in the background.", "needed_by": ["Arma 3", "Rust", "Arma Reforger"]},
    {"id": "steamcmd-libs", "name": "SteamCMD 32-bit libraries", "platform": "linux", "pkg": "lib32gcc-s1",
     "why": "SteamCMD (the game downloader) is a 32-bit program.", "needed_by": ["Arma 3", "Rust", "Arma Reforger"]},
    {"id": "mariadb", "name": "MariaDB (database)", "platform": "linux", "pkg": "mariadb-server",
     "why": "Altis Life saves players, money, houses and vehicles in a database.", "needed_by": ["Arma 3: Altis Life"]},
    {"id": "tbbmalloc", "name": "extDB3 library (libtbbmalloc2)", "platform": "linux", "pkg": "libtbbmalloc2",
     "why": "extDB3 (Altis Life's database link) can't load without it.", "needed_by": ["Arma 3: Altis Life"]},
    {"id": "directx", "name": "DirectX End-User Runtime (June 2010)", "platform": "windows",
     "url": "https://www.microsoft.com/en-gb/download/details.aspx?id=8109",
     "why": "Older DirectX files the Arma servers use on Windows.", "needed_by": ["Arma 3", "Arma Reforger"]},
    {"id": "vcredist", "name": "Visual C++ 2015–2022 Redistributable (x64)", "platform": "windows",
     "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
     "why": "Runtime files most game servers need on Windows.", "needed_by": ["Rust", "Arma 3", "Arma Reforger"]},
]

_installing: set[str] = set()


def pkg_installed(pkg: str) -> bool:
    code, out = run(["dpkg-query", "-W", "-f=${Status}", pkg], timeout=10)
    return code == 0 and "install ok installed" in out


def sudo_ok() -> bool:
    """True if sudo works without a password (then Install can run the command itself)."""
    if not shutil.which("sudo"):
        return False
    code, _ = run(["sudo", "-n", "true"], timeout=10)
    return code == 0


def install_command(dep: dict) -> str:
    return f"sudo apt-get install -y {dep['pkg']}"


def status(oxide_servers: list | None = None) -> dict:
    here = "linux" if LINUX else "windows"
    can_sudo = LINUX and sudo_ok()
    items = []
    for d in DEPS:
        item = {k: d[k] for k in ("id", "name", "why", "needed_by", "platform")} | {"url": d.get("url")}
        if d["platform"] != here:
            item["state"] = "other_os"
        elif d["id"] in _installing:
            item["state"] = "installing"
        else:
            item["state"] = "installed" if pkg_installed(d["pkg"]) else "missing"
        if d.get("pkg"):
            item["command"] = install_command(d)
            item["auto"] = can_sudo
        items.append(item)
    # Oxide is per Rust server: one row per server Manager created
    for s in oxide_servers or []:
        items.append({"id": f"oxide:{s['id']}", "name": f"Oxide (uMod) on {s['name']}", "platform": "any",
                      "why": "Lets this Rust server run uMod plugins (2X gather, kits...).", "needed_by": [s["name"]],
                      "state": "installed" if s.get("framework") == "oxide" else "missing", "server": s["id"]})
    return {"os": here, "sudo_without_password": can_sudo, "items": items}


def install(dep_id: str) -> dict:
    """Start installing a system package in the background. Returns {"started": True} or {"command": ...}."""
    d = next((x for x in DEPS if x["id"] == dep_id), None)
    if not d or not d.get("pkg"):
        raise ValueError("unknown dependency")
    if not LINUX:
        raise ValueError("install it from its download link")
    if not sudo_ok():
        return {"command": install_command(d), "message": "Run this command yourself (sudo needs your password), then press Check again."}
    if dep_id in _installing:
        return {"started": True}
    _installing.add(dep_id)

    def work():
        try:
            # Same PATH (so the same sudo) that sudo_ok() checked; never a different, hard-coded one.
            sudo = shutil.which("sudo")
            p = subprocess.run([sudo, "-n", "apt-get", "install", "-y", d["pkg"]], capture_output=True, text=True,
                               timeout=1800, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
            ok = p.returncode == 0 and pkg_installed(d["pkg"])
            add_event("dependencies", "info" if ok else "alert",
                      f"{d['name']}: {'installed' if ok else 'install failed: ' + (p.stderr.strip().splitlines() or ['?'])[-1][:200]}")
        finally:
            _installing.discard(dep_id)
    threading.Thread(target=work, daemon=True).start()
    return {"started": True}
