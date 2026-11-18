"""Backups never overwrite one another: two in the same second both stay, and only the oldest are cleared."""
import time

from manager import util
from manager.adapters.managed import ManagedServer
from tests.test_manager import Sandbox


class Inst(ManagedServer):
    game, slug, app_id, exe_name = "Fake Game", "fake", "999", "game"


class TestBackupNames(Sandbox):
    def test_unique_path(self):
        d = self.tmp / "b"
        d.mkdir()
        p = d / "fake_2026-11-14_120000.tar.gz"
        self.assertEqual(util.unique_path(p), p)
        p.write_text("1")
        self.assertEqual(util.unique_path(p).name, "fake_2026-11-14_120000-2.tar.gz")
        (d / "fake_2026-11-14_120000-2.tar.gz").write_text("2")
        self.assertEqual(util.unique_path(p).name, "fake_2026-11-14_120000-3.tar.gz")
        q = d / "db_1.sql"
        q.write_text("x")
        self.assertEqual(util.unique_path(q).name, "db_1-2.sql")

    def test_backups_in_the_same_second_are_all_kept(self):
        root = self.tmp / "inst"
        (root / "server/manager").mkdir(parents=True)
        (root / "server/manager/server.cfg").write_text("x")
        a = Inst("i", {"root": str(root), "managed": True, "keep_backups": 3})
        for _ in range(3):
            a.backup()
        files = sorted((root / "backups").glob("fake_*.tar.gz"))
        self.assertEqual(len(files), 3)  # made within a second or two: none replaced another
        time.sleep(1.1)
        a.backup()  # a fourth: only the oldest goes (keep 3)
        left = sorted((root / "backups").glob("fake_*.tar.gz"), key=lambda p: p.stat().st_mtime)
        self.assertEqual(len(left), 3)
        self.assertNotIn(min(files, key=lambda p: p.stat().st_mtime if p.exists() else 0), left)
