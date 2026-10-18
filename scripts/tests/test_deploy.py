"""Tests for scripts/deploy.py (stdlib unittest): Docker checks, the guided key setup and key verification.
Nothing here runs Docker or calls a provider."""
import io
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy  # noqa: E402
from test_set_keys import FAKE_ANTHROPIC, FAKE_OPENAI  # noqa: E402


def done(code: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code)


class DockerCheckTest(unittest.TestCase):
    def test_each_missing_piece_has_its_own_fix(self):
        with unittest.mock.patch("shutil.which", return_value=None):
            self.assertIn("not installed", deploy.check_docker())
        colima = subprocess.CompletedProcess([], 0, stdout="colima-policy-analytics\n")
        with unittest.mock.patch("shutil.which", return_value="/usr/bin/docker"), \
                unittest.mock.patch.object(deploy, "run", side_effect=[done(0), done(1), colima]):
            self.assertIn("not running. Start it (run: colima start policy-analytics)", deploy.check_docker())
        with unittest.mock.patch("shutil.which", return_value="/usr/bin/docker"), \
                unittest.mock.patch.object(deploy, "run", side_effect=[done(0), done(0)]):
            self.assertIsNone(deploy.check_docker())


class KeySetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / ".env.example").write_text("OPENAI_API_KEY=\nANTHROPIC_API_KEY=\n")
        self.patches = [unittest.mock.patch.object(deploy, "ROOT", root),
                        unittest.mock.patch.object(deploy, "ENV", root / ".env")]
        for p in self.patches:
            p.start()
        self.env = root / ".env"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def setup_keys(self, answers, confirm="n", review=False):
        answers = iter(answers)
        with redirect_stdout(io.StringIO()) as out:
            ok = deploy.set_up_keys(review, ask=lambda _p: next(answers), confirm=lambda _p: confirm)
        return ok, out.getvalue()

    def test_first_time_asks_only_for_the_required_keys_unless_asked_for_more(self):
        ok, out = self.setup_keys([FAKE_ANTHROPIC, FAKE_OPENAI])
        self.assertTrue(ok)
        self.assertIn("Two keys are required", out)
        self.assertNotIn("TAVILY_API_KEY", out)
        lines = deploy.set_keys.read_lines(self.env)
        self.assertEqual(deploy.set_keys.get_value(lines, "ANTHROPIC_API_KEY"), FAKE_ANTHROPIC)

    def test_a_second_run_asks_nothing_when_the_required_keys_are_set(self):
        self.setup_keys([FAKE_ANTHROPIC, FAKE_OPENAI])
        ok, out = self.setup_keys([])
        self.assertTrue(ok)
        self.assertNotIn("Paste the key", out)

    def test_a_missing_required_key_stops_the_deploy(self):
        ok, out = self.setup_keys([FAKE_ANTHROPIC, ""])
        self.assertFalse(ok)
        self.assertIn("Still missing: OPENAI_API_KEY", out)

    def test_a_rejected_required_key_stops_the_deploy(self):
        self.setup_keys([FAKE_ANTHROPIC, FAKE_OPENAI])
        results = {"ANTHROPIC_API_KEY": (True, "OK"), "OPENAI_API_KEY": (False, "HTTP 401 (key rejected)")}
        with unittest.mock.patch.object(deploy.set_keys, "verify", side_effect=lambda name, _v: results[name]), \
                redirect_stdout(io.StringIO()) as out:
            self.assertFalse(deploy.verify_keys())
        self.assertIn("OPENAI_API_KEY: HTTP 401 (key rejected)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
