import process_guard  # noqa: F401  first, so it guards every process the tests below start
import ast
import os
import runpy
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def runnable_scripts():
    """The scripts of scripts/ that have a command line: an `if __name__ == "__main__":` block."""
    found = []
    for path in sorted(SCRIPT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
               for node in tree.body):
            found.append(path.name)
    return found


class NativeWindowsTest(unittest.TestCase):
    """translate-book runs on macOS and Linux only; on native Windows each script says so at once."""

    def test_every_runnable_script_stops_first_and_points_to_wsl(self):
        scripts = runnable_scripts()
        self.assertGreaterEqual(len(scripts), 8)
        messages = set()
        for script in scripts:
            with self.subTest(script=script):
                # A first run imports the modules the script imports, so only its own check can stop the second.
                runpy.run_path(str(SCRIPT_DIR / script), run_name="native_windows")
                with mock.patch.object(os, "name", "nt"), self.assertRaises(SystemExit) as stop:
                    runpy.run_path(str(SCRIPT_DIR / script), run_name="native_windows")
                self.assertRegex(stop.exception.code, r"^translate-book runs on macOS and Linux only\. .*\bWSL\b")
                messages.add(stop.exception.code)
        self.assertEqual(len(messages), 1, messages)


if __name__ == "__main__":
    unittest.main()
