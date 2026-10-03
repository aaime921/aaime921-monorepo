import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hello import greet

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "hello.py"


class TestGreet(unittest.TestCase):
    def test_greet_with_name(self):
        self.assertEqual(greet("Alice"), "Hello, Alice!")

    def test_greet_without_name(self):
        self.assertEqual(greet(None), "Hello, world!")

    def test_greet_with_name_shout(self):
        self.assertEqual(greet("Alice", shout=True), "HELLO, ALICE!")

    def test_greet_without_name_shout(self):
        self.assertEqual(greet(None, shout=True), "HELLO, WORLD!")


class TestCli(unittest.TestCase):
    def test_cli_with_name(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "Alice"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Hello, Alice!\n")

    def test_cli_without_name(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "Hello, world!\n")

    def test_cli_with_name_shout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shout", "Alice"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "HELLO, ALICE!\n")

    def test_cli_without_name_shout(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shout"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "HELLO, WORLD!\n")


if __name__ == "__main__":
    unittest.main()
