"""Preview server types (not yet proven on real servers) are "Coming soon" for everyone unless previews are on: shown
greyed out, refused by the engine (also the Rust "Install Oxide?" route), and Arma 3 player tools are off."""
import os
import unittest
from unittest import mock

from manager import catalog
from manager.adapters.arma3 import Arma3Adapter
from tests.test_manager import Sandbox

OFF = {k: v for k, v in os.environ.items() if k != "HOSTSPAWN_PREVIEW"}


class TestPreviews(Sandbox):
    def setUp(self):
        super().setUp()
        from manager import util  # an empty config: this machine's own settings.toml must not count
        self._cfg = mock.patch.object(util, "CONFIG", self.tmp / "no-config")
        self._cfg.start()

    def tearDown(self):
        self._cfg.stop()
        super().tearDown()

    def test_previews_are_coming_soon_by_default(self):
        with mock.patch.dict(os.environ, OFF, clear=True):
            self.assertFalse(catalog.previews_on())
            c = catalog.public_catalog()
            self.assertEqual(c["rust"]["status"], "ready")  # proven on a real server (3 days, restarts, updates)
            self.assertEqual(c["rust"]["modes"]["vanilla"]["status"], "ready")
            self.assertEqual((c["reforger"]["status"], c["reforger"]["reason"]), ("soon", catalog.PREVIEW_REASON))
            self.assertEqual(c["reforger"]["status"], "soon")
            self.assertEqual({m["status"] for m in c["reforger"]["modes"].values()}, {"soon"})
            self.assertEqual(c["arma3"]["status"], "ready")
            self.assertEqual(c["arma3"]["modes"]["altislife"]["status"], "ready")
            self.assertEqual(c["arma3"]["modes"]["vanilla"]["status"], "ready")
            self.assertEqual(c["arma3"]["modes"]["kpliberation"]["status"], "soon")

    def test_the_engine_refuses_them_too(self):
        from manager import deploy
        import threading
        d = deploy.Deployer.__new__(deploy.Deployer)
        d._create_lock, d._by_request, d.jobs, d._recent = threading.Lock(), {}, {}, []
        with mock.patch.dict(os.environ, OFF, clear=True), mock.patch.object(deploy, "load_servers", return_value={}), \
                mock.patch.object(deploy, "mem_available_gb", return_value=64.0):
            for game, mode, opts in (("reforger", "conflict", {}), ("arma3", "kpliberation", {})):
                with self.subTest(f"{game} {mode} {opts}"), self.assertRaisesRegex(ValueError, "isn't available yet"):
                    d.create(game, mode, opts, accepted_terms=catalog.TERMS_VERSION)

    def test_arma3_player_tools_wait_for_previews(self):
        a = Arma3Adapter("a1", {"root": str(self.tmp / "a1"), "managed": True})
        with mock.patch.dict(os.environ, OFF, clear=True):
            self.assertFalse(a.can_moderate())
            self.assertFalse(a.can_say())
        with mock.patch.dict(os.environ, {**OFF, "HOSTSPAWN_PREVIEW": "1"}, clear=True):
            self.assertTrue(a.can_moderate())

    def test_the_developer_setting_turns_previews_on(self):
        from manager import util
        cfg = self.tmp / "config"
        cfg.mkdir()
        (cfg / "settings.toml").write_text("[developer]\npreviews = true\n")
        with mock.patch.dict(os.environ, OFF, clear=True), mock.patch.object(util, "CONFIG", cfg):
            self.assertTrue(catalog.previews_on())
            self.assertEqual(catalog.public_catalog()["reforger"]["status"], "ready")


if __name__ == "__main__":
    unittest.main()
