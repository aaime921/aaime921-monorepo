import re
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hello import greet

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "hello.py"

TIMESTAMP_RE = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"


class TestGreet(unittest.TestCase):
    def test_greet_with_name(self):
        now = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(greet("Alice", now=now), "Hello Alice! [2026-01-02 03:04:05]")

    def test_greet_without_name(self):
        now = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(greet(None, now=now), "Hello world! [2026-01-02 03:04:05]")

    def test_greet_with_name_shout(self):
        now = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(
            greet("Alice", shout=True, now=now),
            "HELLO ALICE! [2026-01-02 03:04:05]",
        )

    def test_greet_without_name_shout(self):
        now = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(
            greet(None, shout=True, now=now),
            "HELLO WORLD! [2026-01-02 03:04:05]",
        )

    def test_greet_default_now_matches_pattern(self):
        result = greet("Alice")
        self.assertRegex(result, rf"^Hello Alice! \[{TIMESTAMP_RE}\]$")

    def test_greet_no_timestamp_with_name(self):
        self.assertEqual(greet("Alice", timestamp=False), "Hello Alice!")

    def test_greet_no_timestamp_without_name(self):
        self.assertEqual(greet(None, timestamp=False), "Hello world!")

    def test_greet_no_timestamp_shout(self):
        self.assertEqual(
            greet(None, shout=True, timestamp=False), "HELLO WORLD!"
        )


class TestCli(unittest.TestCase):
    def test_cli_with_name(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "Alice"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, rf"^Hello Alice! \[{TIMESTAMP_RE}\]\n$")

    def test_cli_without_name(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, rf"^Hello world! \[{TIMESTAMP_RE}\]\n$")

    def test_cli_with_name_shout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shout", "Alice"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, rf"^HELLO ALICE! \[{TIMESTAMP_RE}\]\n$")

    def test_cli_without_name_shout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shout"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, rf"^HELLO WORLD! \[{TIMESTAMP_RE}\]\n$")

    def test_cli_no_timestamp_with_name(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-timestamp", "Alice"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Hello Alice!\n")

    def test_cli_no_timestamp_without_name(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-timestamp"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Hello world!\n")

    def test_cli_no_timestamp_then_shout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--no-timestamp", "--shout"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "HELLO WORLD!\n")

    def test_cli_shout_then_no_timestamp(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shout", "--no-timestamp"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "HELLO WORLD!\n")

    def test_cli_help_lists_no_timestamp(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--no-timestamp", result.stdout)


if __name__ == "__main__":
    unittest.main()
