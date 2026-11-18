"""Time zone setting: the chosen zone (settings.toml [general] timezone) drives restarts, backups and wipes; this
computer's zone is the default; unknown or harmful names are refused (and ignored if written by hand)."""
from datetime import datetime
from unittest import mock

from manager import util
from tests.test_manager import Sandbox


class TestTimeZone(Sandbox):
    def setUp(self):
        super().setUp()
        self.cfg = self.tmp / "config"
        self.cfg.mkdir()
        self._p = mock.patch.object(util, "CONFIG", self.cfg)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def test_default_is_this_computers_zone(self):
        with mock.patch.object(util, "system_timezone", return_value="America/New_York"):
            self.assertEqual(util.timezone_name(), "America/New_York")

    def test_chosen_zone_wins_and_bad_names_are_ignored(self):
        (self.cfg / "settings.toml").write_text('[general]\ntimezone = "Asia/Tokyo"\n')
        self.assertEqual(util.timezone_name(), "Asia/Tokyo")
        self.assertEqual(util.local_now().utcoffset().total_seconds(), 9 * 3600)
        for bad in ("Nowhere/Fake", "../../etc/passwd", "", "London"):
            (self.cfg / "settings.toml").write_text(f'[general]\ntimezone = "{bad}"\n')
            with mock.patch.object(util, "system_timezone", return_value="UTC"):
                self.assertEqual(util.timezone_name(), "UTC", bad)

    def test_restart_times_are_in_the_chosen_zone(self):
        (self.cfg / "settings.toml").write_text('[general]\ntimezone = "Asia/Tokyo"\n')
        from manager.adapters.base import next_daily
        nxt = datetime.fromisoformat(next_daily(["06:00"])[0]["when"])
        self.assertEqual((nxt.hour, nxt.minute, nxt.utcoffset().total_seconds()), (6, 0, 9 * 3600))
