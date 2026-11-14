"""PC check: can this machine run each game's server comfortably? Compares the machine (CPU cores, memory, free disk,
64-bit Linux) with each game's needs and gives a plain-English verdict: ok, tight or no, with the reasons.
A guide only: it reads specs, it can't promise how a server will run (CPU speed, other programs, mods all matter)."""
import os
import platform
import shutil
from pathlib import Path

from . import catalog
from .util import servers_dir

# Per game: CPU cores and memory (minimum, recommended); disk is the download plus room for saves, logs and updates.
# Memory minimums are the catalog's ram_gb (what one server uses); recommended leaves room for the system and players.
NEEDS = {
    "rust": {"cores": (2, 4), "ram_gb": (8, 12), "extra_disk_gb": 8},
    "reforger": {"cores": (2, 4), "ram_gb": (4, 8), "extra_disk_gb": 5},
    "arma3": {"cores": (2, 4), "ram_gb": (6, 8), "extra_disk_gb": 5},
}
DEFAULT_NEEDS = {"cores": (2, 4), "ram_gb": (4, 8), "extra_disk_gb": 5}

ADVICE = ("Please double-check before you start: look up the game's official server requirements, and make sure this "
          "machine can handle everything a server needs (CPU, memory, disk space and your internet upload) on top of "
          "whatever else it runs, so the server can't overload it.")
DISCLAIMER = ("This check is a guide only. It compares this machine's specs with each game's published needs and "
              "can't promise how a server will run: CPU speed, other programs, mods and player numbers all make a "
              "difference. HostSpawn is free software provided \"as is\", without any warranty. You choose what to run "
              "on your machine and you're responsible for it, including its hardware, data and internet connection.")


def servers_disk():
    """Disk usage where instances live (the folder may not exist yet: the disk it will be on). In a sandbox the
    pretend instances sit in a temporary folder, so it measures the home folder's disk, where a real install goes."""
    where = Path.home() if os.environ.get("MANAGER_TEST_STEAMCMD") else servers_dir()
    while not where.exists() and where != where.parent:
        where = where.parent
    return shutil.disk_usage(where)


def machine() -> dict:
    mem = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) / 2**20  # GB
    except (OSError, ValueError):
        pass
    du = servers_disk()
    return {"os": platform.system(), "arch": platform.machine(), "cores": os.cpu_count() or 1,
            "ram_total_gb": round(mem.get("MemTotal", 0), 1), "ram_free_gb": round(mem.get("MemAvailable", 0), 1),
            "swap_gb": round(mem.get("SwapTotal", 0), 1), "disk_free_gb": round(du.free / 2**30, 1),
            "disk_total_gb": round(du.total / 2**30, 1)}


def check_game(gid: str, m: dict) -> dict:
    g = catalog.GAMES[gid]
    n = NEEDS.get(gid, DEFAULT_NEEDS)
    disk = g.get("size_gb", 10) + n["extra_disk_gb"]
    (cmin, crec), (rmin, rrec) = n["cores"], n["ram_gb"]
    no, tight, good = [], [], []
    if m["os"] != "Linux" or m["arch"] not in ("x86_64", "AMD64"):
        no.append(f"{g['name']} servers need 64-bit Linux on an Intel or AMD processor (this is {m['os']} {m['arch']}).")
    if m["cores"] < cmin:
        no.append(f"Needs at least {cmin} CPU cores; this machine has {m['cores']}.")
    elif m["cores"] < crec:
        tight.append(f"{m['cores']} CPU cores: it will run, but {crec} or more is recommended.")
    else:
        good.append(f"{m['cores']} CPU cores (recommended: {crec}).")
    if m["ram_total_gb"] < rmin:
        no.append(f"Needs at least {rmin} GB of memory; this machine has {m['ram_total_gb']:g} GB.")
    elif m["ram_total_gb"] < rrec:
        tight.append(f"{m['ram_total_gb']:g} GB of memory: enough for a small server, {rrec} GB is recommended.")
    else:
        good.append(f"{m['ram_total_gb']:g} GB of memory (recommended: {rrec} GB).")
    if m["ram_total_gb"] >= rmin and m["ram_free_gb"] < rmin:
        tight.append(f"Only {m['ram_free_gb']:g} GB of memory is free right now (it needs about {rmin} GB). "
                     "Stop another instance or program first.")
    if m["disk_free_gb"] < disk:
        no.append(f"Needs about {disk} GB of free disk space where instances are kept; {m['disk_free_gb']:g} GB is free there.")
    else:
        good.append(f"{m['disk_free_gb']:g} GB of free disk space (needs about {disk} GB).")
    verdict = "no" if no else "tight" if tight else "ok"
    return {"id": gid, "name": g["name"], "verdict": verdict,
            "summary": {"ok": "This machine can run it comfortably.",
                        "tight": "This machine can run it, with the notes below.",
                        "no": "Not recommended on this machine."}[verdict],
            "reasons": no + tight + good,
            "needs": {"cores": {"min": cmin, "recommended": crec}, "ram_gb": {"min": rmin, "recommended": rrec},
                      "disk_gb": disk}}


def report() -> dict:
    m = machine()
    notes = []
    if os.environ.get("MANAGER_TEST_STEAMCMD"):
        notes.append("This is a sandbox: its instances are small pretend games, so nothing real is downloaded. The "
                     "check above is for this machine as if you installed HostSpawn for real.")
    if m["swap_gb"] == 0:
        notes.append("No swap space: if memory runs out, the system stops a program instead of slowing down. "
                     "Adding a few GB of swap is a cheap safety net.")
    if m["disk_free_gb"] < 10:
        notes.append(f"Only {m['disk_free_gb']:g} GB of disk is free. Updates and backups need room; keep 10 GB or more free.")
    ready = [gid for gid, g in catalog.GAMES.items() if any(catalog.ready(x) for x in g.get("modes", {}).values())]
    return {"machine": m, "games": [check_game(gid, m) for gid in ready], "notes": notes, "advice": ADVICE,
            "disclaimer": DISCLAIMER}
