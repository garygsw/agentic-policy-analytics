"""Tests for scripts/set_keys.py (stdlib unittest). Fake keys are built at runtime
so no key-shaped string appears in the source."""
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import set_keys  # noqa: E402

FAKE_OPENAI = "sk-" + "proj-" + "A1b2" * 6
FAKE_ANTHROPIC = "sk-" + "ant-" + "C3d4" * 6


class EnvLinesTest(unittest.TestCase):
    def test_set_value_replaces_in_place_and_keeps_other_lines(self):
        lines = ["# comment", "OPENAI_API_KEY=old", "OTHER=1"]
        self.assertEqual(set_keys.set_value(lines, "OPENAI_API_KEY", "new"),
                         ["# comment", "OPENAI_API_KEY=new", "OTHER=1"])

    def test_set_value_appends_when_missing(self):
        self.assertEqual(set_keys.set_value(["OTHER=1"], "ANTHROPIC_API_KEY", "x"), ["OTHER=1", "ANTHROPIC_API_KEY=x"])

    def test_get_value_strips_quotes(self):
        self.assertEqual(set_keys.get_value(['OPENAI_API_KEY="abc"'], "OPENAI_API_KEY"), "abc")
        self.assertEqual(set_keys.get_value(["# OPENAI_API_KEY=abc"], "OPENAI_API_KEY"), "")

    def test_looks_valid(self):
        self.assertTrue(set_keys.looks_valid("OPENAI_API_KEY", FAKE_OPENAI))
        self.assertFalse(set_keys.looks_valid("OPENAI_API_KEY", FAKE_ANTHROPIC))
        self.assertTrue(set_keys.looks_valid("ANTHROPIC_API_KEY", FAKE_ANTHROPIC))
        self.assertFalse(set_keys.looks_valid("ANTHROPIC_API_KEY", FAKE_OPENAI))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = self.dir / ".env"
        (self.dir / ".env.example").write_text("# example\nOPENAI_API_KEY=\nANTHROPIC_API_KEY=\nDEBUG=false\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, answers, args=(), confirm="n"):
        answers = iter(answers)
        out = io.StringIO()
        with redirect_stdout(out):
            code = set_keys.main([*args, "--env-file", str(self.env)],
                                 ask=lambda _prompt: next(answers), confirm=lambda _prompt: confirm)
        return code, out.getvalue()

    def test_new_file_is_seeded_from_example_and_owner_only(self):
        code, out = self.run_main([FAKE_OPENAI, FAKE_ANTHROPIC, "", ""])
        self.assertEqual(code, 0)
        self.assertEqual(self.env.read_text().splitlines(),
                         ["# example", f"OPENAI_API_KEY={FAKE_OPENAI}", f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}", "DEBUG=false"])
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        self.assertNotIn(FAKE_OPENAI, out)
        self.assertNotIn(FAKE_ANTHROPIC, out)

    def test_blank_input_keeps_the_current_value(self):
        self.run_main([FAKE_OPENAI, FAKE_ANTHROPIC, "", ""])
        code, out = self.run_main(["", "", "", ""])
        self.assertIn("No changes.", out)
        self.assertIn("ends ..." + FAKE_OPENAI[-4:], out)
        self.assertEqual(set_keys.get_value(set_keys.read_lines(self.env), "OPENAI_API_KEY"), FAKE_OPENAI)

    def test_wrong_prefix_needs_confirmation(self):
        self.run_main([FAKE_ANTHROPIC], args=["OPENAI_API_KEY"], confirm="n")
        self.assertFalse(self.env.exists())
        self.run_main([FAKE_ANTHROPIC], args=["OPENAI_API_KEY"], confirm="y")
        self.assertEqual(set_keys.get_value(set_keys.read_lines(self.env), "OPENAI_API_KEY"), FAKE_ANTHROPIC)

    def test_unknown_key_name_is_rejected(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                unittest.mock.patch("sys.stderr", io.StringIO()):
            set_keys.main(["NOT_A_KEY", "--env-file", str(self.env)])

    def test_refuses_to_write_when_git_does_not_ignore_env(self):
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        code, out = self.run_main([FAKE_OPENAI])
        self.assertEqual(code, 1)
        self.assertIn("Refusing", out)
        (self.dir / ".gitignore").write_text(".env\n")
        code, _ = self.run_main([FAKE_OPENAI, "", "", ""])
        self.assertEqual(code, 0)


class VerifyTest(unittest.TestCase):
    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def test_ok_and_headers(self):
        seen = {}

        def opener(request, timeout, context):
            seen.update(request.headers)
            return self.FakeResponse(json.dumps({"data": []}).encode())

        self.assertEqual(set_keys.verify("ANTHROPIC_API_KEY", FAKE_ANTHROPIC, opener), (True, "OK"))
        self.assertEqual(seen["X-api-key"], FAKE_ANTHROPIC)

    def test_rejected_key_and_missing_key(self):
        def opener(request, timeout, context):
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO())

        self.assertEqual(set_keys.verify("OPENAI_API_KEY", FAKE_OPENAI, opener), (False, "HTTP 401 (key rejected)"))
        self.assertEqual(set_keys.verify("OPENAI_API_KEY", "", opener), (False, "not set"))
        self.assertEqual(set_keys.verify("TAVILY_API_KEY", "", opener), (True, "not set (optional)"))


if __name__ == "__main__":
    unittest.main()
