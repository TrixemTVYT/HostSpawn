"""Reforger map rotation: Manager switches the scenario at scheduled restarts (Reforger has no built-in rotation)."""
from manager import catalog, util
from manager.adapters.reforger_server import ReforgerServer
from manager.supervisor import Supervisor
from tests.test_deploy import DeploySandbox
from tests.test_manager import Sandbox

C = catalog.GAMES["reforger"]["modes"]["conflict"]["scenarios"]
EVERON, ARLAND, WESTERN = C["Everon"], C["Arland"], C["Western Everon"]


class TestRotation(Sandbox):
    def make(self, **cfg):
        f = self.tmp / "s.toml"
        f.write_text('[servers.r1]\nname = "R"\nadapter = "reforger-server"\n')
        return ReforgerServer("r1", {"root": str(self.tmp / "r1"), "_file": str(f), **cfg}), f

    def test_order_wraps_and_needs_two_maps(self):
        a, f = self.make(scenario=EVERON, rotation=[EVERON, ARLAND, WESTERN], rotation_on=True)
        r = a.rotation()
        self.assertEqual((r["on"], r["next"], r["current_label"]), (True, ARLAND, "Conflict · Everon"))
        self.assertEqual(a.advance_rotation(), "Conflict · Arland")
        self.assertEqual(a.advance_rotation(), "Conflict · Western Everon")
        self.assertEqual(a.advance_rotation(), "Conflict · Everon")  # back to the start
        self.assertIn(EVERON, f.read_text())  # saved, so a Manager restart keeps the place
        one, _ = self.make(scenario=EVERON, rotation=[EVERON], rotation_on=True)
        self.assertFalse(one.rotation()["on"])
        self.assertIsNone(one.advance_rotation())

    def test_unknown_scenarios_ignored_and_off_by_default(self):
        a, _ = self.make(scenario=EVERON, rotation=["{BAD}Missions/x.conf", EVERON, ARLAND])
        self.assertEqual([x["id"] for x in a.rotation()["list"]], [EVERON, ARLAND])
        self.assertFalse(a.rotation()["on"])
        self.assertIn("off", a.rotate())

    def test_supervisor_picks_next_map_before_scheduled_restart(self):
        a, _ = self.make(scenario=EVERON, rotation=[EVERON, ARLAND], rotation_on=True)
        seen = []
        a.update = lambda: seen.append(a.cfg["scenario"])
        Supervisor._restart_with_update(a)
        self.assertEqual(seen, [ARLAND])
        self.assertTrue(any("next map is Conflict · Arland" in e["message"] for e in util.read_events()))


class TestRotationSetup(DeploySandbox):
    def test_wizard_rotation_starts_on_chosen_map(self):
        self.steam_sleep.write_text("30")
        j = self.d.create("reforger", "conflict", {"map": "Arland", "rotation": True})
        cfg = self.d.cfg(j)
        self.assertEqual(cfg["scenario"], ARLAND)
        self.assertEqual(cfg["rotation"][0], ARLAND)
        self.assertEqual(sorted(cfg["rotation"]), sorted(C.values()))
        self.assertTrue(cfg["rotation_on"])
        self.d.action(j.id, "cancel")
