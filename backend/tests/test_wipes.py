"""Rust wipes for servers Manager created: what's deleted and kept, backup first, new seed, schedules.
A fake identity folder with the same file names as a real Rust server; no real server is touched."""
import tarfile
import time
from datetime import date, datetime

from manager import util
from manager.adapters.rust_server import RustServer, wipe_kind_on
from manager.supervisor import Supervisor
from tests.test_manager import FakeAdapter, Sandbox

MAP = ["proceduralmap.3000.123.290.map", "proceduralmap.3000.123.290.sav", "proceduralmap.3000.123.290.sav.1",
       "proceduralmap.3000.123.290.navmesh", "proceduralmap.3000.123.290_occlusion_3.dat",
       "proceduralmap.3000.123.290_oceanpatrol_1.dat"]
BP = ["player.blueprints.19.db", "player.blueprints.19.db-wal"]
KEEP = ["player.identities.19.db", "player.deaths.19.db", "player.states.290.db", "player.tokens.db",
        "clans.290.db", "relationship.290.db", "cfg/users.cfg", "cfg/bans.cfg"]


class TestRustWipe(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "rust1"
        self.idd = self.root / "server/server/server"
        (self.idd / "cfg").mkdir(parents=True)
        for f in MAP + BP + KEEP:
            (self.idd / f).write_text("x")
        (self.root / "config").mkdir()
        self.f = self.tmp / "servers.d.toml"
        self.f.write_text('[servers.rust1]\nname = "R"\nadapter = "rust-server"\nseed = 123\n')
        self.a = RustServer("rust1", {"root": str(self.root), "seed": 123, "_file": str(self.f), "steamcmd": str(self.tmp / "none")})

    def present(self):
        return sorted(str(p.relative_to(self.idd)) for p in self.idd.rglob("*") if p.is_file())

    def test_map_wipe_keeps_blueprints_and_players(self):
        out = self.a.wipe("map", warn=False)
        self.assertIn("6 files removed", out)
        self.assertEqual(self.present(), sorted(BP + KEEP))
        backup = next((self.root / "backups").glob("rust_*.tar.gz"))  # made before deleting
        with tarfile.open(backup) as t:
            names = t.getnames()
        self.assertTrue(any(n.endswith(MAP[0]) for n in names), "backup must still contain the old map")
        self.assertNotEqual(self.a.cfg["seed"], 123)
        self.assertIn("seed = ", self.f.read_text())
        self.assertNotIn("seed = 123", self.f.read_text())
        self.assertTrue(any("map wipe done" in e["message"] for e in util.read_events()))

    def test_full_wipe_also_removes_blueprints(self):
        self.a.wipe("full", warn=False)
        self.assertEqual(self.present(), sorted(KEEP))

    def test_bad_kind_and_seed_option(self):
        with self.assertRaises(ValueError):
            self.a.wipe("everything")
        self.a.cfg["wipe_new_seed"] = False
        self.a.wipe("map", warn=False)
        self.assertEqual(self.a.cfg["seed"], 123)

    def test_map_size_changes_only_at_a_wipe(self):
        self.a.cfg["wipe_world_size"] = 3500
        self.a.wipe("map", warn=False)
        self.assertEqual(self.a.cfg["world_size"], 3500)


class TestWipeSchedules(Sandbox):
    def test_calendar_rules(self):
        thursdays = [date(2026, 12, d) for d in (3, 10, 17, 24, 31)]
        pick = lambda mode, bp: [wipe_kind_on(d, mode, bp) for d in thursdays]
        self.assertEqual(pick("monthly", "forced"), ["full", None, None, None, None])
        self.assertEqual(pick("monthly", "never"), ["map", None, None, None, None])
        self.assertEqual(pick("biweekly", "forced"), ["full", None, "map", None, None])
        self.assertEqual(pick("weekly", "every"), ["full", "full", "full", "full", "full"])
        self.assertEqual(pick("off", "forced"), [None] * 5)
        self.assertIsNone(wipe_kind_on(date(2026, 12, 2), "weekly", "forced"))  # not a Thursday

    def test_next_wipes_listed(self):
        a = RustServer("r", {"root": str(self.tmp / "r")})
        nxt = a.next_wipes(3)
        self.assertEqual(len(nxt), 3)
        self.assertTrue(all(datetime.fromisoformat(e["when"]).weekday() == 3 for e in nxt))
        self.assertTrue(nxt[0]["forced"])  # monthly: always the first Thursday

    def test_supervisor_starts_countdown_once_so_wipe_lands_on_time(self):
        class W(FakeAdapter):
            supervised_by = "manager"
            def __init__(s, *a, **k):
                super().__init__(*a, **k)
                s.wipes, s.asked = [], []
            def wipe_settings(s): return {"warn_s": 300}
            def wipe_due(s, when):
                s.asked.append(when)
                return "map"
            def wipe(s, kind): s.wipes.append(kind)
        a = W("w", {})
        sup = Supervisor({"w": a}, config={})
        before = util.uk_now()
        sup.tick(); sup.tick()
        end = time.time() + 10  # the wipe runs in its own thread after the countdown starts
        while not a.wipes and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(a.wipes, ["map"])
        self.assertGreaterEqual((a.asked[0] - before).total_seconds(), 299)  # checks 5 minutes ahead
        Supervisor({"w": a}, config={}).tick()  # Manager restarted in the same minute: still once
        time.sleep(1)
        self.assertEqual(a.wipes, ["map"])
