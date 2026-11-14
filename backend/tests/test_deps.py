"""Dependencies page back end, with fake dpkg-query / sudo / apt-get on PATH (nothing is really installed)."""
import os
import time

from manager import deps, util
from tests.test_manager import Sandbox, write_exec


class TestDeps(Sandbox):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.installed = self.tmp / "installed"
        self.installed.write_text("tmux\n")
        write_exec(self.bin / "dpkg-query", f'#!/usr/bin/env bash\ngrep -qx "${{@: -1}}" "{self.installed}" && echo "install ok installed" || exit 1\n')
        self._path, self._linux = os.environ["PATH"], deps.LINUX
        os.environ["PATH"] = f"{self.bin}:{self._path}"
        deps.LINUX = True

    def tearDown(self):
        os.environ["PATH"], deps.LINUX = self._path, self._linux
        super().tearDown()

    def sudo(self, works: bool):
        write_exec(self.bin / "sudo", '#!/usr/bin/env bash\n[ "$1" = -n ] && shift\n' + ('exec "$@"\n' if works else "exit 1\n"))
        import shutil
        assert shutil.which("sudo") == str(self.bin / "sudo"), "fake sudo must be the one used"

    def test_status_and_oxide_rows(self):
        self.sudo(False)
        s = deps.status([{"id": "r1", "name": "My Rust", "framework": "vanilla"}, {"id": "r2", "name": "Modded", "framework": "oxide"}])
        by = {i["id"]: i for i in s["items"]}
        self.assertEqual(by["tmux"]["state"], "installed")
        self.assertEqual(by["mariadb"]["state"], "missing")
        self.assertEqual(by["directx"]["state"], "other_os")
        self.assertIn("id=8109", by["directx"]["url"])
        self.assertEqual((by["oxide:r1"]["state"], by["oxide:r2"]["state"]), ("missing", "installed"))
        self.assertFalse(s["sudo_without_password"])

    def test_install_without_passwordless_sudo_returns_the_command(self):
        self.sudo(False)
        r = deps.install("mariadb")
        self.assertEqual(r["command"], "sudo apt-get install -y mariadb-server")

    def test_install_with_sudo_runs_apt_in_background(self):
        self.sudo(True)
        write_exec(self.bin / "apt-get", f'#!/usr/bin/env bash\necho "${{@: -1}}" >> "{self.installed}"\n')
        self.assertEqual(deps.install("tbbmalloc"), {"started": True})
        for _ in range(50):
            if deps.pkg_installed("libtbbmalloc2") and "tbbmalloc" not in deps._installing:
                break
            time.sleep(0.1)
        self.assertTrue(deps.pkg_installed("libtbbmalloc2"))
        self.assertTrue(any("installed" in e["message"] for e in util.read_events()))

    def test_unknown_and_windows_items_refused(self):
        for bad in ("nope", "directx", "oxide:r1"):
            with self.assertRaises(ValueError):
                deps.install(bad)
