"""Backups are never half-written or run during another action; a failed wipe backup or Oxide install starts Rust
again. Contributed by IceEagle132 (pull request 1). Never starts a real game."""
import tarfile
import unittest
from unittest.mock import patch

from manager.adapters.managed import Busy
from manager.adapters.rust_server import RustServer
from tests.test_manager import Sandbox


class TestBackupSafety(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "rust1"
        self.save = self.root / "server/server/server"
        self.save.mkdir(parents=True)
        (self.save / "world.sav").write_text("first")
        self.a = RustServer("rust1", {"root": str(self.root)})

    def archives(self):
        return sorted((self.root / "backups").glob("rust_*.tar.gz"))

    def test_same_second_backups_do_not_overwrite(self):
        self.a.backup()
        (self.save / "world.sav").write_text("second")
        self.a.backup()
        files = self.archives()
        self.assertEqual(len(files), 2)
        contents = []
        for file in files:
            with tarfile.open(file) as archive:
                contents.append(archive.extractfile("server/world.sav").read().decode())
        self.assertCountEqual(contents, ["first", "second"])

    def test_five_backups_in_one_second_are_all_kept(self):
        from unittest import mock
        from datetime import datetime
        frozen = datetime(2026, 11, 18, 4, 0, 0)
        with mock.patch("manager.adapters.managed.datetime") as dt:
            dt.now.return_value = frozen
            for i in range(5):
                (self.save / "world.sav").write_text(f"save {i}")
                self.a.backup()
        files = self.archives()
        self.assertEqual(len(files), 5)
        self.assertEqual(len({f.name for f in files}), 5)
        contents = set()
        for file in files:
            with tarfile.open(file) as archive:
                contents.add(archive.extractfile("server/world.sav").read().decode())
        self.assertEqual(contents, {f"save {i}" for i in range(5)})

    def test_error_does_not_publish_partial_archive(self):
        with patch.object(self.a, "backup_paths", side_effect=OSError("injected read error")):
            with self.assertRaises(OSError):
                self.a.backup()
        self.assertEqual(self.archives(), [])
        self.assertFalse(list((self.root / "backups").glob("*.partial")))

    def test_backup_rejects_concurrent_operation(self):
        with self.a.lock("other action"):
            with self.assertRaises(Busy):
                self.a.backup()
        self.assertEqual(self.archives(), [])

    def test_wipe_backup_failure_preserves_saves_and_restarts(self):
        calls = []
        with patch.object(self.a, "pid", return_value=123), \
             patch.object(self.a, "_stop", side_effect=lambda **kw: calls.append("stop")), \
             patch.object(self.a, "_start", side_effect=lambda: calls.append("start")), \
             patch.object(self.a, "_backup", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.a.wipe("map", warn=False)
        self.assertEqual(calls, ["stop", "start"])
        self.assertTrue((self.save / "world.sav").exists())

    def test_oxide_install_failure_attempts_restart(self):
        calls = []
        with patch.object(self.a, "pid", return_value=123), \
             patch.object(self.a, "_stop", side_effect=lambda **kw: calls.append("stop")), \
             patch.object(self.a, "_start", side_effect=lambda: calls.append("start")), \
             patch("manager.adapters.rust_server.install_oxide", side_effect=OSError("download failed")):
            with self.assertRaisesRegex(OSError, "download failed"):
                self.a.oxide()
        self.assertEqual(calls, ["stop", "start"])
        self.assertNotEqual(self.a.cfg.get("framework"), "oxide")


if __name__ == "__main__":
    unittest.main()
