"""PC check: verdicts and reasons for made-up machines (no real hardware involved), and the API endpoint."""
import unittest
from unittest import mock

from manager import hostcheck

BIG = {"os": "Linux", "arch": "x86_64", "cores": 8, "ram_total_gb": 32.0, "ram_free_gb": 28.0, "swap_gb": 4.0,
       "disk_free_gb": 200.0, "disk_total_gb": 500.0}


def m(**k):
    return {**BIG, **k}


class TestVerdicts(unittest.TestCase):
    def test_a_big_machine_is_ok_for_everything(self):
        for gid in ("rust", "reforger", "arma3"):
            r = hostcheck.check_game(gid, BIG)
            self.assertEqual(r["verdict"], "ok", r)
            self.assertIn("comfortably", r["summary"])

    def test_below_recommended_is_tight_with_a_reason(self):
        r = hostcheck.check_game("rust", m(cores=2, ram_total_gb=10.0, ram_free_gb=9.0))
        self.assertEqual(r["verdict"], "tight")
        self.assertTrue(any("4 or more is recommended" in x for x in r["reasons"]))
        self.assertTrue(any("12 GB is recommended" in x for x in r["reasons"]))

    def test_memory_in_use_right_now_is_tight(self):
        r = hostcheck.check_game("rust", m(ram_free_gb=3.0))
        self.assertEqual(r["verdict"], "tight")
        self.assertTrue(any("free right now" in x for x in r["reasons"]))

    def test_too_little_memory_disk_or_cores_is_no(self):
        for bad, words in ((dict(ram_total_gb=4.0, ram_free_gb=3.0), "at least 8 GB of memory"),
                           (dict(disk_free_gb=5.0), "free disk space"),
                           (dict(cores=1), "at least 2 CPU cores"),
                           (dict(arch="aarch64"), "64-bit Linux")):
            r = hostcheck.check_game("rust", m(**bad))
            self.assertEqual(r["verdict"], "no", bad)
            self.assertIn(words, r["reasons"][0])  # the blocking reason comes first
            self.assertEqual(r["summary"], "Not recommended on this machine.")

    def test_report_lists_ready_games_with_the_disclaimer(self):
        with mock.patch.object(hostcheck, "machine", return_value=m(swap_gb=0.0)):
            r = hostcheck.report()
        self.assertEqual({g["id"] for g in r["games"]}, {"rust", "reforger", "arma3"})  # not the "coming soon" ones
        self.assertIn("without any warranty", r["disclaimer"])
        self.assertIn("official server requirements", r["advice"])  # double-check it themselves
        self.assertTrue(any("swap" in n for n in r["notes"]))

    def test_sandbox_says_its_games_are_pretend(self):
        with mock.patch.dict("os.environ", {"MANAGER_TEST_STEAMCMD": "/fake"}), \
                mock.patch.object(hostcheck, "machine", return_value=BIG):
            r = hostcheck.report()
        self.assertTrue(any("pretend games" in n for n in r["notes"]))

    def test_real_machine_reading_has_every_field(self):
        got = hostcheck.machine()
        self.assertEqual(set(got), set(BIG))
        self.assertGreaterEqual(got["cores"], 1)


if __name__ == "__main__":
    unittest.main()
