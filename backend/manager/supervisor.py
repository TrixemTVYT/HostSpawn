"""Health loop. Once a minute, for every server, 4 checks: process (status), query (A2S), RCON, console-log
activity. Raises alerts on changes. Runs daily backups (on by default). For servers the manager supervises itself
(Arma 3) it also runs the watchdog and scheduled restarts."""
import threading
import time
import traceback

from .adapters.base import Unsupported
from .adapters.moderation import ModerationError
from .util import add_event, settings, uk_now

HANG_ALERT_CHECKS = 5   # Rust/Reforger: minutes of no query answer before a "possible freeze" alert
RCON_ALERT_CHECKS = 3   # minutes of RCON failing (while the server runs) before an alert
GRACE_S = 600


def restart_warn_s(a) -> int:
    """How long before a scheduled restart players are warned: cfg restart_warn_s (default 5 min), or 0 when this
    server can't show messages (then the restart simply happens on time)."""
    if not a.can_say():
        return 0
    try:
        return max(0, min(1800, int(a.cfg.get("restart_warn_s", 300))))
    except (TypeError, ValueError):
        return 300


class Supervisor:
    def __init__(self, adapters: dict, interval: float = 60, control: bool = True, config: dict | None = None):
        self.adapters = adapters
        b = (settings() if config is None else config).get("backups", {})
        self.backups = {"enabled": b.get("enabled", True), "time": str(b.get("time", "04:30")),
                        "max_age_hours": float(b.get("max_age_hours", 36))}
        self.interval = interval
        # False in the development copy: watch only. Only the live copy may restart anything.
        self.control = control
        self.snapshot: dict[str, dict] = {}
        self._prev: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._watchers: dict[str, threading.Thread] = {}
        self._db = None
        self._install = None
        self._doctor_said: dict[str, str] = {}

    def diagnose(self, sid: str) -> dict:
        """Server Doctor for one instance (fresh check). Also keeps a short summary in its snapshot for badges."""
        from . import doctor
        a = self.adapters[sid]
        snap = self.snapshot.get(sid) or self.check(sid)
        r = doctor.diagnose(a, snap)
        probs = [f for f in r["findings"] if f["level"] == "problem"]
        summary = {"problems": len(probs), "warnings": sum(f["level"] == "warning" for f in r["findings"]),
                   "top": (probs or r["findings"] or [{}])[0].get("title")}
        with self._lock:
            if sid in self.snapshot:
                self.snapshot[sid]["doctor"] = summary
        return r

    def _doctor_after_trouble(self, sid: str) -> None:
        """After a crash, a hang or a give-up: say why in the alerts, once per cause."""
        try:
            top = next((f for f in self.diagnose(sid)["findings"] if f["level"] in ("problem", "warning")), None)
        except Exception:
            return
        if top and self._doctor_said.get(sid) != top["title"]:
            self._doctor_said[sid] = top["title"]
            add_event(sid, "alert", f"Server Doctor: {top['title']}. {top['fix']}")

    @property
    def install(self):
        """Installation jobs and the hourly Steam update check (created on first use)."""
        if self._install is None:
            from .install import InstallManager
            self._install = InstallManager(self)
        return self._install

    @property
    def db(self):
        """The player database (opened on first use, so tests that never touch players don't create one)."""
        if self._db is None:
            from .players import PlayerDB
            self._db = PlayerDB()
            for server in {o["server"] for o in self._db.online()} - set(self.adapters):
                self._db.forget_server(server)  # deleted while the agent was off
        return self._db

    def ctl(self, a) -> bool:
        return self.control or bool(a.cfg.get("sandbox"))  # dev copy: only its own sandbox servers

    def can_moderate(self, a) -> bool:
        return self.ctl(a) and a.can_moderate()

    # ---- players: seen once a minute; bans hold on every server ----
    def _players(self, sid: str, a, snap: dict) -> None:
        if not (a.can_moderate() or a.cfg.get("players_watch")):
            return  # servers HostSpawn only watches: not asked for players unless the owner turns it on
        if snap["status"].get("state") not in ("running", "starting"):
            self.db.forget_server(sid)
            return
        try:
            players = a.players()
        except (Unsupported, ModerationError):
            return  # no RCON yet (still starting) or none: the RCON health check reports that
        joined, _ = self.db.observe(sid, players)
        for p in joined:
            ban = self.db.active_ban(p["key"])
            if ban and self.can_moderate(a):
                try:
                    a.kick(p["ref"], f"Banned: {ban['reason']}"[:120])
                    self.db.gone(sid, p["key"])
                    add_event(sid, "info", f"{p['name']} is banned (by {ban['by']}): kicked on joining")
                    self.db.audit("HostSpawn", "kick", p["key"], sid, "banned player joined")
                except (Unsupported, ModerationError) as e:
                    add_event(sid, "warn", f"{p['name']} is banned but couldn't be kicked: {e}")
            w = self.db.watched(p["key"])
            if w:
                add_event(sid, "alert", f"watched player {p['name']} joined" + (f" ({w['reason']})" if w["reason"] else ""))

    def online_ref(self, sid: str, key: str) -> str | None:
        return next((o["ref"] for o in self.db.online(sid) if o["key"] == key), None)

    def kick_player(self, sid: str, key: str, reason: str, by: str) -> str:
        a = self.adapters[sid]
        if not self.can_moderate(a):
            raise ModerationError("HostSpawn only watches this server: kicking and banning are off for it")
        ref = self.online_ref(sid, key)
        if ref is None:
            raise ModerationError("that player isn't on this server now")
        msg = a.kick(ref, reason)
        self.db.gone(sid, key)
        self.db.audit(by, "kick", key, sid, reason)
        add_event(sid, "info", f"kicked {self._name(key)} (by {by}){': ' + reason if reason else ''}")
        return msg

    def ban_player(self, key: str, minutes: int, reason: str, by: str) -> tuple[dict, list[str]]:
        """Ban on HostSpawn's list (every server), and in each game that can hold the ban itself."""
        ban = self.db.ban(key, minutes, reason, by)
        applied = []
        for sid, a in list(self.adapters.items()):
            if not self.can_moderate(a):
                continue
            try:
                ref = self.online_ref(sid, key)
                a.ban(key, ref, ban["name"] or "", minutes, reason)
                applied.append(sid)
                if ref is not None:
                    self.db.gone(sid, key)
            except (Unsupported, ModerationError):
                pass  # another game's kind of id, or RCON down: HostSpawn's list still kicks them on sight
        length = "permanently" if not minutes else f"for {minutes} min"
        add_event("players", "info", f"banned {ban['name'] or 'a player'} {length} (by {by}): {reason}")
        return ban, applied

    def unban_player(self, key: str, by: str) -> list[str]:
        self.db.unban(key, by)
        lifted = []
        for sid, a in list(self.adapters.items()):
            if self.can_moderate(a):
                try:
                    a.unban(key)
                    lifted.append(sid)
                except (Unsupported, ModerationError):
                    pass
        add_event("players", "info", f"unbanned {self._name(key)} (by {by})")
        return lifted

    def say(self, sid: str | None, text: str, by: str) -> list[str]:
        sent = []
        for s, a in list(self.adapters.items()):
            if (sid is None or s == sid) and self.can_moderate(a):
                try:
                    a.say(text)
                    sent.append(s)
                except (Unsupported, ModerationError):
                    pass
        self.db.audit(by, "say", "", sid or "all", text[:200])
        return sent

    def _name(self, key: str) -> str:
        p = self.db.profile(key)
        return p["name"] if p and p["name"] else "a player"

    def check(self, sid: str) -> dict:
        a = self.adapters[sid]
        try:
            status = a.status()
        except Exception as e:  # one broken adapter must not stop the others
            status = {"state": "unknown", "flags": [f"status error: {e}"]}
        health = a.health() if status.get("state") in ("running", "starting") else None
        if health and status.get("players") is None:
            status["players"] = health.get("players")
        running = status.get("state") in ("running", "starting")
        rcon = None
        if running:
            try:
                rcon = a.rcon_check()
            except Exception as e:
                rcon = {"ok": False, "error": str(e)}
        if rcon and rcon.get("ok"):
            for k in ("players", "fps"):
                if status.get(k) is None and rcon.get(k) is not None:
                    status[k] = rcon[k]
        checks = {
            "process": running,
            "query": (bool(health) if a.query_port() else None) if running else None,
            "rcon": rcon,
            "log_age_s": a.log_age(),
            "last_backup": a.last_backup(),
        }
        snap = {"status": status, "health": health, "checks": checks, "checked": time.time()}
        try:
            snap["schedule"] = a.schedule()
        except Exception:
            snap["schedule"] = []
        with self._lock:
            self.snapshot[sid] = snap
        return snap

    def _alerts(self, sid: str, snap: dict) -> None:
        a = self.adapters[sid]
        st = snap["status"]
        prev = self._prev.get(sid, {})
        state, pstate = st.get("state"), prev.get("state")
        if pstate == "running" and state == "stopped" and a.supervised_by != "manager":
            who = {"rustctl": "rustctl watchdog", "systemd": "systemd", "manager": "the manager"}[a.supervised_by]
            add_event(sid, "alert", f"server went down ({who} should restart it)")
        elif pstate in ("stopped", "starting") and state == "running":
            add_event(sid, "info", "server is running")
        if pstate and state == "maintenance" and pstate != "maintenance":
            add_event(sid, "info", "stopped on purpose (maintenance)")
        if "restarts" in st and "restarts" in prev and st["restarts"] > prev["restarts"]:
            add_event(sid, "alert", "systemd restarted it after a crash")
        for f in set(st.get("flags", [])) - set(prev.get("flags", [])):
            add_event(sid, "warn", f)
        # Freeze hint for servers we don't supervise (Arma 3's watchdog handles its own).
        missed = prev.get("_missed", 0)
        if a.supervised_by != "manager" and state == "running" and a.query_port():
            missed = 0 if snap["health"] else missed + 1
            if missed == HANG_ALERT_CHECKS:
                add_event(sid, "alert", f"running but not answering queries for {missed} min (frozen?)")
        st["_missed"] = missed
        # RCON: alert after a few failed checks in a row while the server runs; say when it's back.
        ck = snap.get("checks", {})
        rcon = ck.get("rcon")
        rfail = prev.get("_rcon_fail", 0)
        if rcon is not None and state == "running":
            if rcon.get("ok"):
                if rfail >= RCON_ALERT_CHECKS:
                    add_event(sid, "info", "RCON answering again")
                rfail = 0
            else:
                rfail += 1
                if rfail == RCON_ALERT_CHECKS:
                    add_event(sid, "alert", f"RCON not answering for {rfail} min ({rcon.get('error')})")
        st["_rcon_fail"] = rfail
        # Console log: a running server that writes a log line every minute has gone quiet.
        age, quiet = ck.get("log_age_s"), prev.get("_quiet", False)
        if a.log_alert_s and state == "running" and age is not None:
            if age > a.log_alert_s and not quiet:
                add_event(sid, "warn", f"console log hasn't changed for {int(age // 60)} min (frozen?)")
                quiet = True
            elif age <= a.log_alert_s:
                quiet = False
        st["_quiet"] = quiet
        # Backups: warn once a day if the newest one is too old (or there is none).
        if self.backups["enabled"] and "backup" in a.actions and a.cfg.get("backup", True):
            last, today = ck.get("last_backup"), f"{uk_now():%Y-%m-%d}"
            too_old = last is None or time.time() - last > self.backups["max_age_hours"] * 3600
            marker = a.flag("backup_age_warned")
            if too_old and (not marker.exists() or marker.read_text() != today) and state != "not_installed":
                marker.write_text(today)
                add_event(sid, "warn", "no backup yet" if last is None else
                          f"newest backup is {int((time.time() - last) // 3600)} h old")
        self._prev[sid] = st

    def _scheduled_restart(self, sid: str) -> None:
        a = self.adapters[sid]
        from datetime import timedelta
        warn = restart_warn_s(a)
        now = uk_now()
        # The next restart within the warning time (checked once a minute, so up to 59 s more; its own minute counts).
        # The countdown starts early and ends exactly at HH:MM. A restart set, or an agent started, less than `warn`
        # before it still happens, with a shorter countdown.
        soon = []
        for t in a.cfg.get("restart_times", []):
            try:
                hh, mm = map(int, str(t).split(":"))
                at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                continue
            if at <= now - timedelta(minutes=1):
                at += timedelta(days=1)
            left = max(0, int((at - now).total_seconds()))
            if left <= warn + 59:
                soon.append((left, at))
        if not soon:
            return
        left, at = min(soon)
        key = f"{at:%Y-%m-%d %H:%M}"
        marker = a.flag("last_scheduled_restart")  # on disk: an agent restart won't run the same restart twice
        if a.status().get("state") == "running" and (not marker.exists() or marker.read_text() != key):
            marker.write_text(key)
            add_event(sid, "info", f"scheduled restart (with update) at {at:%H:%M %Z}"
                      + (": countdown started" if left >= 60 else ""))
            threading.Thread(target=self._restart_with_update, args=(a, left), daemon=True).start()

    def _scheduled_messages(self, sid: str, a) -> None:
        """cfg messages = [{"text", "every_min"} or {"text", "times": ["HH:MM"]}]: said in game when due."""
        msgs = a.cfg.get("messages") or []
        if not msgs or not a.can_say() or a.status().get("state") != "running":
            return
        now = uk_now()
        minute = int(now.timestamp() // 60)
        due = [i for i, m in enumerate(msgs) if isinstance(m, dict) and m.get("text") and (
            (m.get("every_min") and minute % max(1, int(m["every_min"])) == 0) or f"{now:%H:%M}" in (m.get("times") or []))]
        if not due:
            return
        marker = a.flag("last_messages")  # once per minute, even if the agent restarts
        key = f"{now:%Y-%m-%d %H:%M}"
        if marker.exists() and marker.read_text() == key:
            return
        marker.write_text(key)
        for i in due:
            try:
                a.say(self.fill(a, sid, str(msgs[i]["text"])))
            except Exception as e:
                add_event(sid, "warn", f"scheduled message not sent: {e}")

    def fill(self, a, sid: str, text: str) -> str:
        """Placeholders: {server} {players} {max} {next_restart} {time}."""
        from .adapters.base import next_daily
        nxt = next((e["when"][11:16] for e in next_daily(a.cfg.get("restart_times") or [])), None)
        snap = self.snapshot.get(sid, {}).get("status", {})
        values = {"server": a.name, "players": snap.get("players") if snap.get("players") is not None else len(self.db.online(sid)),
                  "max": a.cfg.get("max_players", "?"), "next_restart": nxt or "not planned", "time": f"{uk_now():%H:%M}"}
        out = text
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return out[:200]

    def _scheduled_wipe(self, sid: str) -> None:
        a = self.adapters[sid]
        if not hasattr(a, "wipe_due"):
            return
        from datetime import timedelta
        w = a.wipe_settings()
        at = uk_now() + timedelta(seconds=w["warn_s"])  # start the countdown warn_s before the wipe time
        kind = a.wipe_due(at)
        key = f"{at:%Y-%m-%d %H:%M}"
        marker = a.flag("last_scheduled_wipe")
        if kind and (not marker.exists() or marker.read_text() != key) and a.status().get("state") in ("running", "stopped"):
            marker.write_text(key)
            add_event(sid, "info", f"scheduled {kind} wipe at {at:%H:%M %Z}: countdown started")
            threading.Thread(target=self._run_wipe, args=(a, kind), daemon=True).start()

    @staticmethod
    def _run_wipe(a, kind) -> None:
        try:
            a.wipe(kind)
        except Exception as e:
            add_event(a.id, "alert", f"scheduled wipe failed: {e}")

    def _scheduled_backup(self, sid: str) -> None:
        """Daily backup of every server that can back up (on by default; settings.toml [backups])."""
        a = self.adapters[sid]
        if not (self.backups["enabled"] and "backup" in a.actions and a.cfg.get("backup", True)):
            return
        now = uk_now()
        key = f"{now:%Y-%m-%d}"
        marker = a.flag("last_scheduled_backup")
        if f"{now:%H:%M}" == self.backups["time"] and (not marker.exists() or marker.read_text() != key) \
                and a.status().get("state") != "not_installed":
            marker.write_text(key)
            threading.Thread(target=self._run_backup, args=(a,), daemon=True).start()

    @staticmethod
    def _run_backup(a) -> None:
        try:
            add_event(a.id, "info", f"daily backup: {a.backup()}")
        except Exception as e:
            add_event(a.id, "alert", f"daily backup failed: {e}")

    @staticmethod
    def _restart_with_update(a, warn: int = 0) -> None:
        try:
            if warn:
                a.countdown(warn, "restart")
            if hasattr(a, "before_scheduled_restart"):
                a.before_scheduled_restart()  # e.g. Reforger map rotation picks the next scenario
            a.update()  # update stops, updates and starts again
        except Exception as e:
            add_event(a.id, "alert", f"scheduled restart failed: {e}")

    def _watch(self, sid: str) -> None:
        try:
            did = self.adapters[sid].watchdog()
            if did:
                add_event(sid, "info", f"watchdog: {did}")
                self.check(sid)
                self._doctor_after_trouble(sid)
        except Exception:
            if sid not in self.adapters:
                return  # deleted while this check ran: nothing to report
            add_event(sid, "warn", "watchdog error: " + traceback.format_exc(limit=1).strip().splitlines()[-1])

    def tick(self) -> None:
        if any(self.ctl(a) and a.supervised_by == "manager" for a in self.adapters.values()):
            self.install.check_updates()  # at most once an hour, in its own thread
        for sid, a in list(self.adapters.items()):  # the setup helper may add servers while this runs
            if sid not in self.adapters:
                continue  # deleted since this round started
            ctl = self.ctl(a)
            try:
                if ctl and a.supervised_by == "manager":
                    # Own thread: a watchdog doing a slow update must not hold up the other servers' checks.
                    t = self._watchers.get(sid)
                    if not (t and t.is_alive()):
                        self._watchers[sid] = threading.Thread(target=self._watch, args=(sid,), daemon=True)
                        self._watchers[sid].start()
                    self._scheduled_restart(sid)
                    self._scheduled_wipe(sid)
                if ctl:
                    self._scheduled_backup(sid)
                snap = self.check(sid)
                self._alerts(sid, snap)
                try:
                    self.diagnose(sid)  # keeps the dashboard's Doctor badge current
                except Exception:
                    pass
                self._players(sid, a, snap)
                if ctl:
                    self._scheduled_messages(sid, a)  # after the player count is fresh
            except Exception:
                if sid not in self.adapters:
                    continue  # deleted halfway through its check
                add_event(sid, "warn", "supervisor error: " + traceback.format_exc(limit=1).strip().splitlines()[-1])

    def run_forever(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval - (time.time() % self.interval))

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, daemon=True, name="supervisor")
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
