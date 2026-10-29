import contextlib
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

from scripts.recover_nami_login import cookie_from_curl, install_verified_cookie, read_hidden_request


class LoginRecoveryTests(unittest.TestCase):
    def test_browser_multiline_curl(self):
        raw = "curl 'https://www.n.cn/download' \\\n  -b 'Q=fake-q; T=fake-t; __NS_T=fake-ns' \\\n  -H 'accept: text/html'"
        self.assertEqual(cookie_from_curl(raw), "Q=fake-q; T=fake-t; __NS_T=fake-ns")

    def test_cookie_header(self):
        self.assertEqual(cookie_from_curl("curl 'https://www.n.cn/api/info' -H 'Cookie: Q=fake; T=fake'"), "Q=fake; T=fake")

    def test_browser_terminal_bracketed_paste(self):
        self.assertEqual(cookie_from_curl("\x1b[200~curl 'https://www.n.cn/download' -b 'Q=fake; T=fake'\x1b[201~\n"), "Q=fake; T=fake")

    def test_rejects_wrong_domain_and_incomplete_cookie(self):
        for raw in ("curl 'https://evil.invalid/' -b 'Q=fake; T=fake'",
                    "curl 'https://www.n.cn.evil.invalid/' -b 'Q=fake; T=fake'",
                    "curl 'http://www.n.cn/' -b 'Q=fake; T=fake'",
                    "curl 'https://www.n.cn/' -b 'Q=fake'",
                    "curl 'https://www.n.cn/' -b 'Q=; T=fake'",
                    "curl 'https://www.n.cn/' -b 'Q=fake; T=fake\n'",
                    "curl 'https://www.n.cn/' -b 'Q=fake; T=fake' -b 'Q=other; T=other'",
                    "curl 'https://www.n.cn/' -b 'SECRET_UNTERMINATED"):
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught:
                cookie_from_curl(raw)
            self.assertNotIn("SECRET_UNTERMINATED", str(caught.exception))

    def test_shell_syntax_is_never_executed(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "executed"
            raw = f"curl 'https://www.n.cn/' -b 'Q=$(touch {marker}); T=fake'"
            self.assertIn("$(touch", cookie_from_curl(raw))
            self.assertFalse(marker.exists())

    def test_failed_verification_does_not_touch_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".cookie.json"
            path.write_text("OLD")
            self.assertFalse(install_verified_cookie("Q=fake; T=fake", path, verify=lambda _: False))
            self.assertEqual(path.read_text(), "OLD")
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_atomic_private_write_and_backup(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / ".cookie.json"
            path.write_text("OLD")
            self.assertTrue(install_verified_cookie("Q=fake; T=fake", path, verify=lambda _: True))
            self.assertEqual(json.loads(path.read_text()), {"cookie": "Q=fake; T=fake"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            backups = list(Path(folder).glob(".cookie.json.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), "OLD")
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(folder).glob(".cookie.json.new-*")), [])

    def test_failed_replace_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / ".cookie.json"
            path.write_text("OLD")
            with patch("scripts.recover_nami_login.os.replace", side_effect=OSError), self.assertRaises(OSError):
                install_verified_cookie("Q=fake; T=fake", path, verify=lambda _: True)
            self.assertEqual(path.read_text(), "OLD")
            self.assertEqual(list(Path(folder).glob(".cookie.json.new-*")), [])

    def test_hidden_multiline_paste_and_terminal_restore(self):
        request = "curl 'https://www.n.cn/download' \\\n -b 'Q=fake-private; T=fake; padding=" + "x" * 6000 + "'\n"
        pid, fd = pty.fork()
        if pid == 0:
            try:
                before = termios.tcgetattr(0)
                actual = read_hidden_request()
                after = termios.tcgetattr(0)
                print("HIDDEN_INPUT_OK" if actual == request and before == after else "HIDDEN_INPUT_FAILED", flush=True)
                os._exit(0)
            except BaseException as exc:
                print(f"TEST_CHILD_FAILED: {type(exc).__name__}: {exc}", flush=True)
                os._exit(1)
        output = b""
        sent = False
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        part = os.read(fd, 8192)
                    except OSError:
                        break
                    if not part:
                        break
                    output += part
                    if not sent and b"hidden" in output:
                        data = (request + "END_NAMI_REQUEST\n").encode()
                        while data:
                            data = data[os.write(fd, data):]
                        sent = True
            self.assertIn(b"HIDDEN_INPUT_OK", output)
            self.assertNotIn(b"fake-private", output)
            self.assertNotIn(b"padding", output)
        finally:
            os.close(fd)
            if os.waitpid(pid, os.WNOHANG)[0] == 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)


if __name__ == "__main__":
    unittest.main()
