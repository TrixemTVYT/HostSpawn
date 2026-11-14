"""The file editor: only the instance's own text files, passwords never shown, format checks, no overwriting a file
that changed, versions and restore, read-only generated files, Arma 3 mission packing, and admin-only API."""
import http.client
import json
import os
import threading
from pathlib import Path

from manager import accounts as A
from manager import pbo
from manager.adapters.managed import ManagedServer
from manager.files import FileError, Files, sqf_problem
from manager.supervisor import Supervisor
from manager.web import make_handler
from tests.test_manager import Sandbox


class Inst(ManagedServer):
    game, slug, app_id, exe_name = "Fake Game", "fake", "999", "game"


class Arma(Inst):
    is_arma3 = True


class FilesSandbox(Sandbox):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "inst"
        s = self.root / "server"
        for rel, text in {"server/oxide/config/Kits.json": '{"a": 1}\n', "server/oxide/plugins/Kits.cs": "class Kits {}\n",
                          "server/notes.txt": "hello\n", "server/manager/server.cfg": "generated\n",
                          "manager/private.txt": "private\n", "backups/old.txt": "x\n",
                          "server/steamapps/appmanifest.acf": "x\n", "server/RustDedicated_Data/big.txt": "x\n",
                          "server/mpmissions/Training.Stratis/mission.sqm": "version=54;\nclass Mission { };\n",
                          "server/mpmissions/Training.Stratis/init.sqf": 'hint "hi";\n'}.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        (s / "data.bin").write_bytes(b"\x00\x01")
        (s / "huge.txt").write_text("x" * (1 << 20 + 1))
        self.cfg = {"root": str(self.root), "managed": True}
        self.f = Files(Inst("i", self.cfg))

    def paths(self, f=None):
        return [x["path"] for x in (f or self.f).list()]


class TestListAndRead(FilesSandbox):
    def test_lists_only_editable_text_files(self):
        got = self.paths()
        for p in ("server/oxide/config/Kits.json", "server/oxide/plugins/Kits.cs", "server/notes.txt",
                  "server/mpmissions/Training.Stratis/init.sqf"):
            self.assertIn(p, got)
        for p in ("manager/private.txt", "backups/old.txt", "server/steamapps/appmanifest.acf",
                  "server/RustDedicated_Data/big.txt", "server/data.bin", "server/huge.txt"):
            self.assertNotIn(p, got)
        gen = next(x for x in self.f.list() if x["path"] == "server/manager/server.cfg")
        self.assertTrue(gen["read_only"])

    def test_nothing_outside_the_folder(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("not yours")
        (self.root / "server" / "link.txt").symlink_to(outside)
        for bad in ("../secret.txt", "/etc/passwd", "server/../../secret.txt", "server/link.txt",
                    "manager/private.txt", "backups/old.txt", "server/.hidden.txt", ""):
            with self.assertRaises(FileError, msg=bad):
                self.f.read(bad)
        self.assertNotIn("server/link.txt", self.paths())

    def test_never_opens_a_file_with_a_managed_password(self):
        (self.root / "server/oxide/config/rcon.json").write_text('{"pw": "adminsecret99"}')  # in the test secrets
        with self.assertRaises(FileError) as e:
            self.f.read("server/oxide/config/rcon.json")
        self.assertIn("password", str(e.exception))
        cur = self.f.read("server/notes.txt")
        with self.assertRaises(FileError):
            self.f.save("server/notes.txt", "pw adminsecret99", cur["sha"])

    def test_not_text_or_too_big(self):
        for bad in ("server/data.bin", "server/huge.txt"):
            with self.assertRaises(FileError):
                self.f.read(bad)

    def test_unmanaged_instances_are_refused(self):
        with self.assertRaises(FileError):
            Files(Inst("x", {"root": str(self.root)}))


class TestBrowse(FilesSandbox):
    def test_starts_in_the_server_folder_and_lists_everything(self):
        b = self.f.browse("")
        self.assertEqual(b["start"], "server")
        self.assertEqual([e["name"] for e in b["entries"]], ["server"])  # manager/ and backups/ stay private
        b = self.f.browse("server")
        self.assertEqual((b["dir"], b["up"]), ("server", ""))
        names = [e["name"] for e in b["entries"]]
        self.assertEqual(names[:3], ["manager", "mpmissions", "oxide"])  # folders first, then files
        e = {x["name"]: x for x in b["entries"]}
        self.assertTrue(e["mpmissions"]["dir"])
        self.assertFalse(e["data.bin"]["editable"])  # game data is listed but can't be opened
        self.assertFalse(e["huge.txt"]["editable"])
        self.assertTrue(e["notes.txt"]["editable"])
        self.assertEqual(self.f.browse("server/mpmissions/Training.Stratis")["up"], "server/mpmissions")

    def test_never_outside_or_into_private_folders(self):
        for bad in ("..", "../..", "manager", "backups", "server/../../"):
            with self.assertRaises(FileError, msg=bad):
                self.f.browse(bad)
        with self.assertRaises(FileError):
            self.f.browse("server/notes.txt")  # a file, not a folder


class TestSave(FilesSandbox):
    def test_save_keeps_a_version_and_restores(self):
        cur = self.f.read("server/oxide/config/Kits.json")
        new = self.f.save("server/oxide/config/Kits.json", '{"a": 2}\n', cur["sha"], by="owner")
        self.assertEqual((self.root / "server/oxide/config/Kits.json").read_text(), '{"a": 2}\n')
        self.assertEqual(len(new["versions"]), 1)
        v = new["versions"][0]
        self.assertEqual((v["by"], self.f.version_text("server/oxide/config/Kits.json", v["id"])), ("owner", '{"a": 1}\n'))
        back = self.f.restore("server/oxide/config/Kits.json", v["id"], by="owner")
        self.assertEqual(back["text"], '{"a": 1}\n')
        self.assertEqual(len(back["versions"]), 2)  # the restore is itself undoable

    def test_bad_formats_are_refused_with_the_line(self):
        cases = {"server/oxide/config/Kits.json": ('{\n"a": 1,\n}', "line 2"),
                 "server/mpmissions/Training.Stratis/init.sqf": ('if (a) then {\n hint "x";\n', "never closed"),
                 "server/mpmissions/Training.Stratis/mission.sqm": ("class Mission {\n", "never closed")}
        for path, (text, words) in cases.items():
            before = (self.root / path).read_text()
            with self.assertRaises(FileError) as e:
                self.f.save(path, text, self.f.read(path)["sha"])
            self.assertIn(words, str(e.exception))
            self.assertIn("Nothing was saved", str(e.exception))
            self.assertEqual((self.root / path).read_text(), before)

    def test_file_changed_since_opened_is_not_overwritten(self):
        cur = self.f.read("server/notes.txt")
        (self.root / "server/notes.txt").write_text("the game wrote this\n")
        with self.assertRaises(FileError) as e:
            self.f.save("server/notes.txt", "mine\n", cur["sha"])
        self.assertIn("changed since you opened it", str(e.exception))
        self.assertEqual((self.root / "server/notes.txt").read_text(), "the game wrote this\n")

    def test_generated_files_are_read_only(self):
        cur = self.f.read("server/manager/server.cfg")
        self.assertTrue(cur["read_only"])
        with self.assertRaises(FileError) as e:
            self.f.save("server/manager/server.cfg", "x", cur["sha"])
        self.assertIn("Settings", str(e.exception))

    def test_keeps_20_versions_and_permissions(self):
        p = self.root / "server/notes.txt"
        os.chmod(p, 0o640)
        for i in range(25):
            self.f.save("server/notes.txt", f"v{i}\n", self.f.read("server/notes.txt")["sha"])
        self.assertEqual(len(self.f.versions("server/notes.txt")), 20)
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o640")
        self.assertFalse(list(p.parent.glob("*.hostspawn-tmp")))

    def test_sqf_check(self):
        self.assertIsNone(sqf_problem('x = "say ""hi"" (";\n// ( comment\n/* [ */ y = [1, {2}];'))
        self.assertIn("doesn't match", sqf_problem("f = { (a] };"))
        self.assertIn("extra )", sqf_problem("a = 1);"))


class TestMissions(FilesSandbox):
    def test_pack_mission_into_a_pbo(self):
        f = Files(Arma("a", self.cfg))
        self.assertEqual(f.missions(), [{"folder": "Training.Stratis", "packed": False, "packed_at": None}])
        msg = f.pack_mission("Training.Stratis", by="owner")
        self.assertIn("Packed 2 files", msg)
        out = self.root / "server/mpmissions/Training.Stratis.pbo"
        _, files = pbo.read(out)
        self.assertEqual(files["init.sqf"], b'hint "hi";\n')
        f.pack_mission("Training.Stratis")
        self.assertTrue(out.with_name(out.name + ".bak").exists())  # the previous pbo is kept
        self.assertTrue(f.missions()[0]["packed"])

    def test_pack_refusals(self):
        f = Files(Arma("a", self.cfg))
        (self.root / "server/mpmissions/NotAMission").mkdir()
        for bad in ("NotAMission", "../..", "Missing", ".hidden"):
            with self.assertRaises(FileError, msg=bad):
                f.pack_mission(bad)
        with self.assertRaises(FileError):
            self.f.pack_mission("Training.Stratis")  # not an Arma 3 instance
        self.assertEqual(self.f.missions(), [])


class TestFilesApi(FilesSandbox):
    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.acc = A.Accounts(self.tmp / "accounts.json", self.tmp / "sessions.json")
        self.acc.setup_owner("owner", "correct horse battery staple")
        self.acc.create("mod", "quiet river lantern stone", "moderator", by="owner")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Supervisor({"i": Inst("i", self.cfg)}, config={}),
                                                                        "", "live", None, None, self.acc))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.tok = {"owner": self.acc.login("owner", "correct horse battery staple", device="t"),
                    "mod": self.acc.login("mod", "quiet river lantern stone", device="t")}

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, path, body=None, who="owner"):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        c.request("POST" if body is not None else "GET", "/api/v1/" + path, json.dumps(body) if body else None,
                  {"X-Manager": "1", "Content-Type": "application/json", "Authorization": "Bearer " + self.tok[who]})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return r.status, data

    def test_admins_only_and_a_save(self):
        self.assertEqual(self.call("files?server=i", who="mod")[0], 403)
        self.assertEqual(self.call("file?server=i&path=server/notes.txt", who="mod")[0], 403)
        self.assertEqual(self.call("file", {"server": "i", "path": "server/notes.txt", "text": "x", "sha": ""}, "mod")[0], 403)
        code, d = self.call("files?server=i")
        self.assertEqual(code, 200)
        self.assertIn("server/notes.txt", [x["path"] for x in d["files"]])
        code, cur = self.call("file?server=i&path=server/notes.txt")
        code, saved = self.call("file", {"server": "i", "path": "server/notes.txt", "text": "new\n", "sha": cur["sha"]})
        self.assertEqual((code, saved["text"]), (200, "new\n"))
        code, err = self.call("file", {"server": "i", "path": "../secret", "text": "x", "sha": ""})
        self.assertEqual(code, 400)
        self.assertEqual(self.call("file?server=i&path=/etc/passwd")[0], 400)
