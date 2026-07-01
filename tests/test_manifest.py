import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from manifest import create_manifest, validate_for_merge  # noqa: E402


class ManifestIntegrityTests(unittest.TestCase):
    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def test_create_manifest_removes_output_when_chunk_hash_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._write(temp_path / "input.md", "Old source.\n")
            self._write(temp_path / "chunk0001.md", "Old source.\n")
            create_manifest(temp_dir, ["chunk0001.md"], str(temp_path / "input.md"))

            self._write(temp_path / "output_chunk0001.md", "旧译文。\n")
            self._write(temp_path / "output_chunk0001.meta.json", "{}\n")
            self._write(temp_path / "output.md", "stale merged output\n")
            self._write(temp_path / "book.html", "stale html\n")

            self._write(temp_path / "input.md", "New source with different meaning.\n")
            self._write(temp_path / "chunk0001.md", "New source with different meaning.\n")
            create_manifest(temp_dir, ["chunk0001.md"], str(temp_path / "input.md"))

            self.assertFalse((temp_path / "output_chunk0001.md").exists())
            self.assertFalse((temp_path / "output_chunk0001.meta.json").exists())
            self.assertFalse((temp_path / "output.md").exists())
            self.assertFalse((temp_path / "book.html").exists())

    def test_create_manifest_removes_output_for_removed_chunk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._write(temp_path / "input.md", "One.\nTwo.\n")
            self._write(temp_path / "chunk0001.md", "One.\n")
            self._write(temp_path / "chunk0002.md", "Two.\n")
            create_manifest(
                temp_dir,
                ["chunk0001.md", "chunk0002.md"],
                str(temp_path / "input.md"),
            )
            self._write(temp_path / "output_chunk0002.md", "二。\n")
            self._write(temp_path / "output_chunk0002.meta.json", "{}\n")

            os.remove(temp_path / "chunk0002.md")
            self._write(temp_path / "input.md", "One.\n")
            create_manifest(temp_dir, ["chunk0001.md"], str(temp_path / "input.md"))

            self.assertFalse((temp_path / "output_chunk0002.md").exists())
            self.assertFalse((temp_path / "output_chunk0002.meta.json").exists())

    def test_validate_for_merge_rejects_blank_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            self._write(temp_path / "input.md", "Source text.\n")
            self._write(temp_path / "chunk0001.md", "Source text.\n")
            create_manifest(temp_dir, ["chunk0001.md"], str(temp_path / "input.md"))
            self._write(temp_path / "output_chunk0001.md", "  \n\t\n")

            ok, _, warnings = validate_for_merge(temp_dir)

            self.assertFalse(ok)
            self.assertEqual(warnings, [])

    def test_validate_for_merge_rejects_severely_truncated_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = "A" * 500
            self._write(temp_path / "input.md", source)
            self._write(temp_path / "chunk0001.md", source)
            create_manifest(temp_dir, ["chunk0001.md"], str(temp_path / "input.md"))
            self._write(temp_path / "output_chunk0001.md", "短")

            ok, _, warnings = validate_for_merge(temp_dir)

            self.assertFalse(ok)
            self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
