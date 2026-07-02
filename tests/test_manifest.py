import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import manifest  # noqa: E402


class ValidateForMergeTests(unittest.TestCase):
    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def _workspace(self, tmp):
        temp_dir = Path(tmp)
        self._write(temp_dir / "input.md", "Source text one.\n\nSource text two.\n")
        self._write(temp_dir / "chunk0001.md", "Source text one.\n")
        self._write(temp_dir / "chunk0002.md", "Source text two.\n")
        self._write(temp_dir / "output_chunk0001.md", "译文一。\n")
        self._write(temp_dir / "output_chunk0002.md", "译文二。\n")
        manifest.create_manifest(
            str(temp_dir),
            ["chunk0001.md", "chunk0002.md"],
            str(temp_dir / "input.md"),
        )
        return temp_dir

    def _validate(self, temp_dir):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok, ordered, warnings = manifest.validate_for_merge(str(temp_dir))
        return ok, ordered, warnings, buf.getvalue()

    def test_passes_with_complete_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            ok, ordered, _, _ = self._validate(temp_dir)

        self.assertTrue(ok)
        self.assertEqual(len(ordered), 2)

    def test_rejects_empty_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            self._write(temp_dir / "output_chunk0002.md", "")
            ok, ordered, _, out = self._validate(temp_dir)

        self.assertFalse(ok)
        self.assertIsNone(ordered)
        self.assertIn("Empty output", out)

    def test_rejects_blank_whitespace_only_output(self):
        # Whitespace-only files have bytes on disk but merge to nothing after
        # strip() — a chunk's content would vanish silently without this check.
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            self._write(temp_dir / "output_chunk0002.md", "\n   \n\t\n")
            ok, ordered, _, out = self._validate(temp_dir)

        self.assertFalse(ok)
        self.assertIsNone(ordered)
        self.assertIn("Blank output", out)
        self.assertIn("output_chunk0002.md", out)

    def test_rejects_undecodable_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            (temp_dir / "output_chunk0002.md").write_bytes(b"\xff\xfe\x00 broken")
            ok, ordered, _, out = self._validate(temp_dir)

        self.assertFalse(ok)
        self.assertIsNone(ordered)
        self.assertIn("Unreadable output", out)

    def test_rejects_missing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            (temp_dir / "output_chunk0001.md").unlink()
            ok, ordered, _, out = self._validate(temp_dir)

        self.assertFalse(ok)
        self.assertIn("Missing output", out)


class ReadOutputTextTests(unittest.TestCase):
    def test_returns_text_for_utf8_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output_chunk0001.md"
            path.write_text("译文。\n", encoding="utf-8")
            self.assertEqual(manifest.read_output_text(str(path)), "译文。\n")

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(manifest.read_output_text("/nonexistent/path.md"))

    def test_returns_none_for_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output_chunk0001.md"
            path.write_bytes(b"\xff\xfe\x00")
            self.assertIsNone(manifest.read_output_text(str(path)))


if __name__ == "__main__":
    unittest.main()
