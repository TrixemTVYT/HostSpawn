"""Altis Life: PBO packer, framework build, extDB3 install, MariaDB/database guided steps, server mods, DB dump.
Fakes only (fake downloads, fake mariadb/mysqldump/ldd on PATH, fake MariaDB greeting): no real database is touched."""
import io
import os
import socket
import stat
import struct
import threading
import zipfile
from pathlib import Path

from manager import deploy, pbo, util
from manager.adapters.arma3 import Arma3Adapter
from tests.test_deploy import DeploySandbox
from tests.test_manager import write_exec

SQL = """CREATE DATABASE IF NOT EXISTS `altislife` DEFAULT CHARACTER SET utf8mb4;
USE `altislife`;
CREATE TABLE IF NOT EXISTS `players` (`uid` int);
CREATE USER IF NOT EXISTS `arma3`@`localhost` IDENTIFIED BY 'changeme';
GRANT SELECT, UPDATE, INSERT, EXECUTE ON `altislife`.* TO 'arma3'@'localhost';
"""


def zip_of(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestPbo(DeploySandbox):
    def test_round_trip_with_folders_and_prefix(self):
        src = self.tmp / "addon"
        (src / "sub/deeper").mkdir(parents=True)
        (src / "config.cpp").write_text("class CfgPatches {};")
        (src / "sub/deeper/fn.sqf").write_bytes(b"\x00\x01binary\xff")
        (src / "PboPrefix.txt").write_text("life_server")
        n = pbo.pack(src, self.tmp / "out.pbo", prefix="life_server")
        props, files = pbo.read(self.tmp / "out.pbo")
        self.assertEqual(n, 2)
        self.assertEqual(props, {"prefix": "life_server"})
        self.assertEqual(files, {"config.cpp": b"class CfgPatches {};", "sub\\deeper\\fn.sqf": b"\x00\x01binary\xff"})

    def test_unpack_and_cli_round_trip(self):
        src = self.tmp / "mission"
        (src / "core").mkdir(parents=True)
        (src / "core/a.sqf").write_text("hint 1;")
        (src / "$PBOPREFIX$").write_text("my\\prefix")
        self.assertEqual(pbo.main(["pack", str(src), str(self.tmp / "m.pbo")]), 0)
        self.assertEqual(pbo.read(self.tmp / "m.pbo")[0], {"prefix": "my\\prefix"})
        self.assertEqual(pbo.main(["unpack", str(self.tmp / "m.pbo"), str(self.tmp / "out")]), 0)
        self.assertEqual((self.tmp / "out/core/a.sqf").read_text(), "hint 1;")
        self.assertEqual((self.tmp / "out/$PBOPREFIX$").read_text(), "my\\prefix")

    def test_unpack_refuses_escaping_names(self):
        import hashlib
        body = b"\x00" + struct.pack("<5I", pbo.VERS, 0, 0, 0, 0) + b"\x00" + b"..\\..\\evil.txt\x00" + struct.pack("<5I", 0, 0, 0, 0, 1) \
            + b"\x00" + struct.pack("<5I", 0, 0, 0, 0, 0) + b"x"
        (self.tmp / "bad.pbo").write_bytes(body + b"\x00" + hashlib.sha1(body).digest())
        with self.assertRaises(ValueError):
            pbo.unpack(self.tmp / "bad.pbo", self.tmp / "dest")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_corrupt_pbo_is_rejected(self):
        src = self.tmp / "m"
        src.mkdir()
        (src / "a.txt").write_text("x")
        pbo.pack(src, self.tmp / "m.pbo")
        raw = bytearray((self.tmp / "m.pbo").read_bytes())
        raw[30] ^= 0xFF
        (self.tmp / "m.pbo").write_bytes(raw)
        with self.assertRaises(ValueError):
            pbo.read(self.tmp / "m.pbo")


class TestAltisLifeSteps(DeploySandbox):
    def setUp(self):
        super().setUp()
        self.job = deploy.Job({"id": "jal", "server_id": "arma3-altislife-beef", "game": "arma3", "mode": "altislife",
                               "name": "AL", "options": {"max_players": 40, "release": "v5.0.0"}, "ports": {"port": 2502},
                               "root": str(self.base / "arma3-altislife-beef"), "steps": [{"key": "x"}], "current": 0,
                               "log": []}, self.d)
        (self.base / "arma3-altislife-beef/server").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self._path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self._path}"
        self.orig_dl = deploy._download

    def tearDown(self):
        os.environ["PATH"] = self._path
        deploy._download = self.orig_dl
        super().tearDown()

    def fake_downloads(self, framework: dict, extdb: dict):
        deploy._download = lambda url, job, label: zip_of(extdb if "extDB3" in url else framework)

    def test_framework_extdb_database_end_to_end(self):
        root = Path(self.job.d["root"])
        self.fake_downloads(
            {"Framework-5.0.0/Altis_Life.Altis/description.ext": 'class CfgServer { DatabaseName = "altislife"; };',
             "Framework-5.0.0/Altis_Life.Altis/core/init.sqf": "hint 1;",
             "Framework-5.0.0/SQMs/missionAltis.sqm": "version=53;\nclass Mission {};\n",
             "Framework-5.0.0/SQMs/missionTanoa.sqm": "version=53;\nclass Tanoa {};\n",
             "Framework-5.0.0/life_server/config.cpp": "class CfgPatches {};",
             "Framework-5.0.0/life_server/PboPrefix.txt": "life_server\n",
             "Framework-5.0.0/BEFilters/remoteexec.txt": "//filters",
             "Framework-5.0.0/altislife.sql": SQL, "Framework-5.0.0/AL.ini": "[Default]"},
            {"@extDB3/extDB3_x64.so": b"\x7fELF", "@extDB3/extDB3.so": b"\x7fELF", "@extDB3/addons/extdb3.pbo": b"PBO",
             "@extDB3/extdb3-conf.ini": "[Main]\nVersion = 1\n\n[Database]\nIP = 127.0.0.1\nUsername = changeme\n"})
        self.d.step_framework(self.job)
        server = root / "server"
        props, files = pbo.read(server / "@life_server/addons/life_server.pbo")
        self.assertEqual(props["prefix"], "life_server")
        self.assertIn("config.cpp", files)
        _, mission = pbo.read(server / "mpmissions/Altis_Life.Altis.pbo")
        self.assertEqual(sorted(mission), ["core\\init.sqf", "description.ext", "mission.sqm"])
        self.assertEqual(mission["mission.sqm"], b"version=53;\nclass Mission {};\n")  # the Altis one, from SQMs/
        self.assertTrue((server / "battleye/remoteexec.txt").exists())
        self.d.step_extdb(self.job)
        ext = server / "@extdb3"
        self.assertFalse((server / "@extDB3").exists())  # lowercase only (Linux)
        self.assertTrue((ext / "extdb3_x64.so").is_symlink())
        self.assertEqual((ext / "sql_custom/AL.ini").read_text(), "[Default]")
        # MariaDB step: not running -> clear message; running -> passes (fake ldd reports nothing missing)
        write_exec(self.bin / "ldd", '#!/usr/bin/env bash\necho "linux-vdso.so.1 (0x0)"\n')
        orig = deploy.mariadb_listening
        try:
            deploy.mariadb_listening = lambda: False
            ok, msg = self.d.step_mariadb(self.job, check=True)
            self.assertFalse(ok)
            self.assertIn("isn't answering", msg)
            deploy.mariadb_listening = lambda: True
            write_exec(self.bin / "ldd", '#!/usr/bin/env bash\necho "libtbbmalloc.so.2 => not found"\n')
            ok, msg = self.d.step_mariadb(self.job, check=True)
            self.assertIn("libtbbmalloc.so.2", msg)
            write_exec(self.bin / "ldd", '#!/usr/bin/env bash\necho "linux-vdso.so.1 (0x0)"\n')
            self.assertEqual(self.d.step_mariadb(self.job, check=True), (True, ""))
        finally:
            deploy.mariadb_listening = orig
        guide = self.d.step_mariadb(self.job, check=False)
        self.assertIn("libtbbmalloc2", guide["commands"][0])
        # Database step: the guide never shows the password; files are private
        guide = self.d.step_database(self.job, check=False)
        pw = util.secrets()["SRV_ARMA3_ALTISLIFE_BEEF_DB"]
        self.assertGreater(len(pw), 16)
        self.assertNotIn(pw, repr(guide))
        setup = root / "manager/db-setup.sql"
        self.assertIn(str(setup), guide["commands"][0])
        sql = setup.read_text()
        self.assertIn("`al_beef`", sql)
        self.assertIn(f"IDENTIFIED BY '{pw}'", sql)
        self.assertNotIn("changeme", sql)
        self.assertNotIn("`arma3`", sql)
        for f in (setup, root / "manager/db.cnf", ext / "extdb3-conf.ini"):
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600, f)
        self.assertEqual(stat.S_IMODE((root / "manager").stat().st_mode), 0o700)  # private folder
        conf = (ext / "extdb3-conf.ini").read_text()
        self.assertIn("[altislife]", conf)
        self.assertIn("Database = al_beef", conf)
        self.assertNotIn("[Database]", conf)
        write_exec(self.bin / "mariadb", "#!/usr/bin/env bash\nexit 1\n")
        ok, msg = self.d.step_database(self.job, check=True)
        self.assertFalse(ok)
        self.assertTrue(setup.exists())
        calls = self.tmp / "mariadb_calls"
        write_exec(self.bin / "mariadb", f'#!/usr/bin/env bash\necho "$@" >> "{calls}"\necho 0\n')
        self.assertEqual(self.d.step_database(self.job, check=True), (True, ""))
        self.assertFalse(setup.exists())  # deleted once the database works
        self.assertNotIn(pw, calls.read_text())  # password only in the private defaults file
        self.assertIn("--defaults-extra-file=", calls.read_text())
        # Server settings: mission + server-side mods
        cfg = self.d.cfg(self.job)
        self.assertEqual((cfg["mission"], cfg["server_mods"], cfg["db_name"]), ("Altis_Life.Altis", ["@life_server", "@extdb3"], "al_beef"))
        a = Arma3Adapter("arma3-altislife-beef", cfg)
        self.assertIn("-serverMod=@life_server;@extdb3", a.launch_args())
        self.assertIn("-autoInit", a.launch_args())  # the mission starts with the server, no player needed
        a.write_configs()
        self.assertIn("persistent = 1;", (root / "server/manager/server.cfg").read_text())
        # Backups dump the database first
        write_exec(self.bin / "mysqldump", '#!/usr/bin/env bash\necho "-- dump of $@"\n')
        a.backup()
        dump = root / "server/manager/database.sql"
        self.assertIn("al_beef", dump.read_text())
        self.assertNotIn(pw, dump.read_text())

    def test_unexpected_sql_is_refused(self):
        root = Path(self.job.d["root"])
        (root / "manager").mkdir(parents=True)
        (root / "manager/altislife.sql").write_text("CREATE DATABASE something_else;")
        with self.assertRaises(deploy.StepFailed):
            self.d.step_database(self.job, check=False)

    def test_tanoa_uses_its_own_sqm_and_mission_name(self):
        self.job.d["options"]["map"] = "Tanoa"
        self.fake_downloads(
            {"Framework-5.0.0/Altis_Life.Altis/description.ext": "x", "Framework-5.0.0/life_server/config.cpp": "x",
             "Framework-5.0.0/SQMs/missionAltis.sqm": "altis", "Framework-5.0.0/SQMs/missionTanoa.sqm": "tanoa",
             "Framework-5.0.0/altislife.sql": SQL}, {})
        self.d.step_framework(self.job)
        mp = Path(self.job.d["root"]) / "server/mpmissions"
        self.assertEqual(sorted(p.name for p in mp.iterdir()), ["Altis_Life.Tanoa.pbo"])
        self.assertEqual(pbo.read(mp / "Altis_Life.Tanoa.pbo")[1]["mission.sqm"], b"tanoa")

    def test_release_without_mission_sqm_fails_clearly(self):
        self.fake_downloads({"Framework-5.0.0/Altis_Life.Altis/description.ext": "x",
                             "Framework-5.0.0/life_server/config.cpp": "x", "Framework-5.0.0/altislife.sql": SQL}, {})
        with self.assertRaises(deploy.StepFailed) as e:
            self.d.step_framework(self.job)
        self.assertIn("mission.sqm", str(e.exception))

    def test_release_without_framework_folders_fails_clearly(self):
        self.fake_downloads({"Framework-4.4r3/README.md": "old layout"}, {})
        with self.assertRaises(deploy.StepFailed) as e:
            self.d.step_framework(self.job)
        self.assertIn("pick v5.0.0 or master", str(e.exception))

    def test_mariadb_greeting_detection(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def greet():
            c, _ = srv.accept()
            c.sendall(struct.pack("<I", 20)[:3] + b"\x00" + b"\x0a" + b"10.11.8-MariaDB\x00")
            c.close()
        threading.Thread(target=greet, daemon=True).start()
        self.assertTrue(deploy.mariadb_listening(port=port))
        srv.close()
        self.assertFalse(deploy.mariadb_listening(port=port))

    def test_catalogue_offers_releases(self):
        from manager import catalog
        self.assertEqual([r["tag"] for r in catalog.releases("arma3", "altislife")], ["v5.0.0", "master"])
        steps = [k for k, _, _ in deploy.Deployer._steps("arma3", "altislife")]
        self.assertLess(steps.index("licence"), steps.index("download"))
        self.assertLess(steps.index("database"), steps.index("start"))
