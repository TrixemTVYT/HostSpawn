"""Supported games and their server types (game modes). The setup helper and the "Supported games" page read this.
status: "ready" = can be created now; "soon" = listed but not yet installable (shown greyed out with a reason);
"preview" = built and tested against fakes but not yet proven on real servers: shown as "Coming soon" to everyone,
unless previews are on (HOSTSPAWN_PREVIEW=1, or settings.toml [developer] previews = true), as in the tests."""
import json
import os
import time
import urllib.request

GAMES = {
    "reforger": {
        "name": "Arma Reforger", "adapter": "reforger-server", "app_id": "1874900", "login": "anonymous",
        "about": "Bohemia's Enfusion-engine Arma. Free server, no Steam account needed.",
        "size_gb": 8, "ram_gb": 4, "platforms": ["linux", "windows"],
        "ports": [{"role": "game", "default": 2001, "proto": "udp", "public": True},
                  {"role": "query", "default": 17777, "proto": "udp", "public": True},
                  {"role": "rcon", "default": 19999, "proto": "udp", "public": False}],
        "options": {"max_players": {"label": "Max players", "default": 32, "min": 1, "max": 128}},
        "modes": {
            # Scenario IDs below come from the server's own -listScenarios output (checked 2026-11-06).
            "conflict": {"name": "Conflict", "status": "preview",
                         "about": "Large-scale team battle: capture bases, build, supply.",
                         "scenarios": {"Everon": "{ECC61978EDCC2B5A}Missions/23_Campaign.conf",
                                       "Arland": "{C41618FD18E9D714}Missions/23_Campaign_Arland.conf",
                                       "Northern Everon": "{C700DB41F0C546E1}Missions/23_Campaign_NorthCentral.conf",
                                       "Western Everon": "{94992A3D7CE4FF8A}Missions/23_Campaign_Western.conf",
                                       "South-West Coast": "{28802845ADA64D52}Missions/23_Campaign_SWCoast.conf",
                                       "Montignac": "{FDE33AFE2ED7875B}Missions/23_Campaign_Montignac.conf"}},
            "gamemaster": {"name": "Game Master", "status": "preview",
                           "about": "Zeus-style sandbox: a game master builds the mission live.",
                           "scenarios": {"Everon": "{59AD59368755F41A}Missions/21_GM_Eden.conf",
                                         "Arland": "{2BBBE828037C6F4B}Missions/22_GM_Arland.conf",
                                         "Kolguyev": "{F45C6C15D31252E6}Missions/27_GM_Cain.conf"}},
            "combatops": {"name": "Combat Ops", "status": "preview",
                          "about": "Co-op missions against AI.",
                          "scenarios": {"Everon": "{DFAC5FABD11F2390}Missions/26_CombatOpsEveron.conf",
                                        "Kolguyev": "{CB347F2F10065C9C}Missions/CombatOpsCain.conf"}},
            "captureandhold": {"name": "Capture & Hold", "status": "preview",
                               "about": "Short, fast rounds fighting over a few objectives.",
                               "scenarios": {"Briars Coast": "{3F2E005F43DBD2F8}Missions/CAH_Briars_Coast.conf",
                                             "Castle": "{F1A1BEA67132113E}Missions/CAH_Castle.conf",
                                             "Concrete Plant": "{589945FB9FA7B97D}Missions/CAH_Concrete_Plant.conf",
                                             "Factory": "{9405201CBD22A30C}Missions/CAH_Factory.conf",
                                             "Forest": "{1CD06B409C6FAE56}Missions/CAH_Forest.conf",
                                             "Le Moule": "{7C491B1FCC0FF0E1}Missions/CAH_LeMoule.conf",
                                             "Military Base": "{6EA2E454519E5869}Missions/CAH_Military_Base.conf",
                                             "Morton": "{2B4183DF23E88249}Missions/CAH_Morton.conf"}},
        },
    },
    "rust": {
        "name": "Rust", "adapter": "rust-server", "app_id": "258550", "login": "anonymous",
        "about": "Survival PvP. Free server, no Steam account needed.",
        "size_gb": 12, "ram_gb": 8, "platforms": ["linux", "windows"],
        "ports": [{"role": "game", "default": 28015, "proto": "udp", "public": True},
                  {"role": "rcon", "default": 28016, "proto": "tcp", "public": False},
                  {"role": "query", "default": 28017, "proto": "udp", "public": True}],
        "options": {"max_players": {"label": "Max players", "default": 50, "min": 1, "max": 500},
                    "world_size": {"label": "Map size", "default": 3000, "min": 1000, "max": 6000, "step": 250},
                    "oxide": {"label": "Install Oxide (uMod) so you can add plugins?", "type": "bool", "default": False,
                              "help": "Oxide lets you add uMod plugins (2X gather, kits, teleports...). You can also add it later."}},
        "modes": {
            "vanilla": {"name": "Rust", "status": "ready",
                        "about": "Survival PvP. Monthly forced wipe on the first Thursday. Optional Oxide for plugins.",
                        "settings": {"framework": "vanilla"}},
            # Chosen by the "Install Oxide?" question (not shown as its own server type).
            "oxide": {"name": "Modded (Oxide / uMod)", "status": "hidden",
                      "about": "Rust with the Oxide plugin framework.", "settings": {"framework": "oxide"}},
            "carbon": {"name": "Modded (Carbon)", "status": "soon",
                       "about": "Carbon plugin framework.", "reason": "Coming soon"},
        },
    },
    "arma3": {
        "name": "Arma 3", "adapter": "arma3", "app_id": "233780", "login": "steam",
        "about": "Classic military sandbox. Needs a Steam account to download the server (it doesn't have to own Arma 3).",
        "size_gb": 10, "ram_gb": 6, "platforms": ["linux", "windows"],
        "ports": [{"role": "game", "default": 2302, "proto": "udp", "public": True, "range": 5}],
        "options": {"max_players": {"label": "Max players", "default": 32, "min": 1, "max": 256}},
        "modes": {
            "vanilla": {"name": "Vanilla", "status": "ready",
                        "about": "Official missions. The server starts in mission select; pick one in-game."},
            "kpliberation": {"name": "KP Liberation", "status": "preview",
                             "about": "Co-op campaign: liberate the whole map from AI. Persistent progress.",
                             "releases": {"repo": "KillahPotatoes/KP-Liberation",
                                          "asset": "kp_liberation.{map}.zip"},
                             "maps": {"Altis": "Altis", "Malden": "Malden", "Tanoa": "Tanoa (Apex DLC)",
                                      "Enoch": "Livonia (Contact DLC)"},
                             "licence": "MIT"},
            "altislife": {"name": "Altis Life (AsYetUntitled)", "status": "ready",
                          "about": "Roleplay: police, medics, civilians, jobs, houses, banking. Needs a small database "
                                   "(MariaDB): HostSpawn guides you through it. Its anti-cheat (Spyglass) may throw out honest "
                                   "players on newer Arma 3: the Server Doctor spots it and Update Spyglass fixes it.",
                          "licence": "CC BY-NC-ND 4.0 (no commercial use)",
                          "maps": {"Altis": "Altis", "Tanoa": "Tanoa (players need the Apex DLC)"},
                          "releases": {"repo": "AsYetUntitled/Framework", "source": True,
                                       "list": [{"tag": "v5.0.0", "date": "2019-12-25"},
                                                {"tag": "master", "date": "latest code (2021-11-17)"}]}},
            "wasteland": {"name": "A3Wasteland", "status": "soon", "about": "Open-world survival PvP.",
                          "reason": "Coming soon", "licence": "AGPL-3.0"},
            "antistasi": {"name": "Antistasi (Community)", "status": "soon",
                          "about": "Guerrilla campaign against an occupying army.",
                          "reason": "Coming soon: needs Workshop mods (CBA)"},
            "exile": {"name": "Exile", "status": "soon", "about": "Survival mod: bases, trading, territory.",
                      "reason": "Windows only: arrives with the Windows version"},
        },
    },
    "dayz": {"name": "DayZ", "status": "soon", "about": "Zombie survival.", "modes": {}, "reason": "Coming soon"},
    "valheim": {"name": "Valheim", "status": "soon", "about": "Viking survival co-op.", "modes": {}, "reason": "Coming soon"},
    "zomboid": {"name": "Project Zomboid", "status": "soon", "about": "Isometric zombie survival.", "modes": {},
                "reason": "Coming soon"},
}

TERMS_VERSION = "draft-2026-11-07"

# Recommended restart schedules (UK time): common community practice, not official rules. A server type can
# override its game's. Each scheduled restart also checks for game updates.
RESTARTS = {
    "rust": {"times": ["05:00"], "why": "Rust slowly uses more memory and gets laggy. One restart a day at a quiet "
             "hour (with an update check) keeps it smooth."},
    "reforger": {"times": ["00:00", "06:00", "12:00", "18:00"], "why": "Reforger servers slow down over long "
                 "sessions. Restarting every 6 hours keeps the server FPS up."},
    "arma3": {"times": ["00:00", "06:00", "12:00", "18:00"], "why": "Arma 3 builds up lag over long sessions. "
              "Every 6 hours keeps it smooth; persistent modes like KP Liberation save their progress."},
    "arma3:altislife": {"times": ["02:00", "06:00", "10:00", "14:00", "18:00", "22:00"], "why": "Busy roleplay "
                        "servers build up lag and memory quickly. Life servers usually restart every 4 hours."},
}


def scenarios(game: str) -> list[dict]:
    """Every scenario a game's server types offer: [{id, mode, mode_name, map, label}] (for map rotation)."""
    out = []
    for mid, m in GAMES.get(game, {}).get("modes", {}).items():
        for mp, sid in (m.get("scenarios") or {}).items():
            out.append({"id": sid, "mode": mid, "mode_name": m["name"], "map": mp, "label": f"{m['name']} · {mp}"})
    return out


def restarts_for(game: str, mode: str) -> dict:
    return RESTARTS.get(f"{game}:{mode}") or RESTARTS.get(game) or {"times": [], "why": ""}


def terms_for(game: str, mode: str) -> list[dict]:
    """What someone agrees to by deploying this server type (shown on the Review step)."""
    g = GAMES.get(game, {})
    m = g.get("modes", {}).get(mode, {})
    out = [{"name": "HostSpawn Terms of Use", "detail": "How HostSpawn may be used, your responsibilities, no warranty.",
            "url": None}]
    if g.get("app_id"):
        out.append({"name": "Steam Subscriber Agreement", "detail": f"{g['name']}'s server files are downloaded from Steam.",
                    "url": "https://store.steampowered.com/subscriber_agreement/"})
        out.append({"name": f"{g['name']} server licence", "detail": "The publisher's own terms for running its server.", "url": None})
    if m.get("licence"):
        url = "https://creativecommons.org/licenses/by-nc-nd/4.0/" if "BY-NC-ND" in m["licence"] else None
        out.append({"name": f"{m['name']} licence", "detail": m["licence"], "url": url})
    return out


_release_cache: dict[str, tuple[float, list]] = {}


def releases(game: str, mode: str) -> list[dict]:
    """Releases of a downloadable server type, newest first: [{tag, date, assets[]}]. Cached for an hour."""
    spec = GAMES.get(game, {}).get("modes", {}).get(mode, {}).get("releases")
    if not spec:
        return []
    if spec.get("list"):  # fixed choices (source releases): no GitHub lookup needed
        return [dict(r, assets={}) for r in spec["list"]]
    key = spec["repo"]
    hit = _release_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    req = urllib.request.Request(f"https://api.github.com/repos/{key}/releases?per_page=20",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "HostSpawn"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    out = [{"tag": x["tag_name"], "date": x["published_at"][:10],
            "assets": {a["name"]: a["browser_download_url"] for a in x.get("assets", [])}}
           for x in data if not x.get("draft")]
    _release_cache[key] = (time.time(), out)
    return out


PREVIEW_REASON = "Coming soon: being tested on real servers first"


def previews_on() -> bool:
    if os.environ.get("HOSTSPAWN_PREVIEW") == "1":
        return True
    from .util import settings
    return (settings().get("developer") or {}).get("previews") is True


def ready(m: dict) -> bool:
    """Can this server type be created now? (A preview only with previews on.)"""
    return m.get("status") == "ready" or (m.get("status") == "preview" and previews_on())


def shown(m: dict) -> dict:
    """status and reason as people see them: a preview is "Coming soon" unless previews are on."""
    if m.get("status") != "preview":
        return {"status": m.get("status"), "reason": m.get("reason")}
    return {"status": "ready", "reason": None} if previews_on() else {"status": "soon", "reason": PREVIEW_REASON}


def public_catalog() -> dict:
    """The catalogue as the dashboard sees it (no internal settings)."""
    out = {}
    for gid, g in GAMES.items():
        out[gid] = {k: g.get(k) for k in ("name", "about", "size_gb", "ram_gb", "platforms", "login", "options",
                                          "reason")}
        out[gid]["status"] = g.get("status", "ready" if any(ready(m) for m in g["modes"].values()) else "soon")
        if out[gid]["status"] == "soon" and not out[gid]["reason"]:
            out[gid]["reason"] = PREVIEW_REASON
        out[gid]["ports"] = g.get("ports", [])
        out[gid]["terms"] = {mid: terms_for(gid, mid) for mid in g.get("modes", {})}
        out[gid]["restarts"] = {mid: restarts_for(gid, mid) for mid in g.get("modes", {})}
        out[gid]["scenarios"] = scenarios(gid)
        out[gid]["modes"] = {mid: {k: m.get(k) for k in ("name", "about", "licence")} | shown(m)
                             | {"maps": m.get("maps") or ({k: k for k in m["scenarios"]} if m.get("scenarios") else None),
                                "has_releases": bool(m.get("releases"))} for mid, m in g["modes"].items()
                             if m.get("status") != "hidden"}
    return out
