"""Steam login inside Manager: SteamCMD in a pseudo-terminal, password typed into its prompt and never stored.
Uses a fake SteamCMD that prompts like the real one (no real Steam account is touched)."""
import os
import time
from pathlib import Path

from manager import steamlogin, util
from tests.test_manager import Sandbox, write_exec

PW = "Corr3ct-H0rse-Battery"


class TestSteamLogin(Sandbox):
    def setUp(self):
        super().setUp()
        self.mode = self.tmp / "mode"
        self.mode.write_text("guard")
        self.argv_log = self.tmp / "argv"
        self.fake = self.tmp / "steamcmd.sh"
        write_exec(self.fake, f'''#!/usr/bin/env bash
echo "$@" > "{self.argv_log}"
m=$(cat "{self.mode}")
echo "Logging in user '$2' to Steam Public..."
if [ "$m" = cached ]; then echo "Logging in using cached credentials."; echo "Waiting for user info...OK"; read -r q; exit 0; fi
if [ "$m" = silent ]; then sleep 30; exit 0; fi
if [ "$m" = phone ]; then echo "Please confirm the login in the Steam Mobile app on your phone."; sleep 1; echo "Waiting for user info...OK"; read -r q; exit 0; fi
printf "password: "; read -r -s pw; echo
if [ "$pw" != "{PW}" ]; then echo "FAILED (Invalid Password)"; exit 5; fi
if [ "$m" = guard ]; then
  printf "Steam Guard code:"; read -r code; echo
  if [ "$code" != "AB12C" ]; then echo "FAILED (Invalid Login Auth Code)"; exit 5; fi
fi
echo "Waiting for user info...OK"
read -r q
''')

    def wait(self, s, states, secs=10):
        end = time.time() + secs
        while time.time() < end and s.state not in states:
            time.sleep(0.05)
        return s.state

    def test_password_and_guard_code(self):
        s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
        self.assertEqual(self.wait(s, ("need_password",)), "need_password")
        s.submit("need_password", PW)
        self.assertEqual(self.wait(s, ("need_code",)), "need_code")
        with self.assertRaises(ValueError):
            s.submit("need_password", PW)  # it isn't asking for the password any more
        s.submit("need_code", "AB12C")
        self.assertEqual(self.wait(s, ("ok", "failed")), "ok")
        self.assertIn("remember", s.message)
        # the password was never on SteamCMD's command line, in the session's reply, or in any Manager file
        self.assertEqual(self.argv_log.read_text().strip(), "+login serverbot")
        self.assertNotIn(PW, repr(s.public()))
        self.assertNotIn(PW, s._out)
        for f in Path(self.tmp).rglob("*"):
            if f.is_file() and f != self.fake:
                self.assertNotIn(PW, f.read_text(errors="ignore"), f)

    def test_wrong_password_is_reported_not_retried(self):
        self.mode.write_text("noguard")
        s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
        self.wait(s, ("need_password",))
        s.submit("need_password", "wrong")
        self.assertEqual(self.wait(s, ("ok", "failed")), "failed")
        self.assertIn("password is wrong", s.message)

    def test_wrong_guard_code(self):
        s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
        self.wait(s, ("need_password",))
        s.submit("need_password", PW)
        self.wait(s, ("need_code",))
        s.submit("need_code", "ZZZZZ")
        self.assertEqual(self.wait(s, ("ok", "failed")), "failed")
        self.assertIn("Steam Guard code was wrong", s.message)

    def test_cached_login_needs_nothing(self):
        self.mode.write_text("cached")
        s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
        self.assertEqual(self.wait(s, ("ok", "failed")), "ok")

    def test_phone_approval(self):
        self.mode.write_text("phone")
        s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
        self.assertEqual(self.wait(s, ("confirm_on_phone", "ok")), "confirm_on_phone")
        self.assertEqual(self.wait(s, ("ok", "failed")), "ok")

    def test_timeout(self):
        self.mode.write_text("silent")
        old = steamlogin.IDLE_TIMEOUT
        steamlogin.IDLE_TIMEOUT = 1
        try:
            s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
            self.assertEqual(self.wait(s, ("failed",), 8), "failed")
            self.assertIn("timed out", s.message)
        finally:
            steamlogin.IDLE_TIMEOUT = old

    def test_bad_values_refused(self):
        s = steamlogin.LoginSession(str(self.fake), "serverbot").start()
        self.wait(s, ("need_password",))
        for bad in ("", "x\nquit", "y" * 201):
            with self.assertRaises(ValueError):
                s.submit("need_password", bad)
        s.close()

    def test_one_session_per_target(self):
        self.mode.write_text("silent")
        L = steamlogin.Logins()
        a = L.start("job:1", str(self.fake), "u")
        self.assertIs(L.start("job:1", str(self.fake), "u"), a)
        a.close()
