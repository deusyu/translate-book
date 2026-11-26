import process_guard  # noqa: F401  first, so it guards every process the tests below start
import contextlib
import io
import json
import os
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



class ChunkFileNameTests(unittest.TestCase):
    def write(self, tmp, *names):
        for name in names:
            Path(tmp, name).write_text(name, encoding="utf-8")

    def test_lists_chunks_in_numeric_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "chunk0010.md", "chunk10000.md", "chunk9999.md", "chunk0002.md",
                       "output_chunk0002.md", "output_chunk0002.meta.json", "input.md",
                       "glossary.json", "chunk0002.md.bak")
            self.assertEqual(manifest.list_chunk_files(tmp),
                             ["chunk0002.md", "chunk0010.md", "chunk9999.md", "chunk10000.md"])
            self.assertEqual(manifest.list_chunk_files(tmp, "output"), ["output_chunk0002.md"])
            self.assertEqual(manifest.list_chunk_files(tmp, "meta"), ["output_chunk0002.meta.json"])

    def test_rejects_other_chunk_like_names(self):
        cases = [("source", "chunk1.md"), ("source", "chunk0001 .md"), ("source", "chunk$(x).md"),
                 ("source", "chunk0001.md.md"), ("source", "chunk-0001.md"),
                 ("output", "output_chunk12.md"), ("output", "output_chunk0001;x.md"),
                 ("meta", "output_chunk1.meta.json")]
        for kind, name in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                self.write(tmp, name)
                with self.assertRaises(ValueError) as raised:
                    manifest.list_chunk_files(tmp, kind)
                self.assertIn(repr(name), str(raised.exception))

    def test_rejects_symlinks_directories_and_repeated_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "target.md")
            os.symlink(os.path.join(tmp, "target.md"), os.path.join(tmp, "chunk0001.md"))
            with self.assertRaisesRegex(ValueError, "symlink"):
                manifest.list_chunk_files(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            os.mkdir(os.path.join(tmp, "chunk0001.md"))
            with self.assertRaisesRegex(ValueError, "not a regular file"):
                manifest.list_chunk_files(tmp)
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp, "chunk0001.md", "chunk00001.md")
            with self.assertRaisesRegex(ValueError, "same chunk number"):
                manifest.list_chunk_files(tmp)

    def test_chunk_ids_and_file_names(self):
        self.assertEqual(manifest.chunk_id_from_file_name("chunk0001.md"), "chunk0001")
        self.assertEqual(manifest.chunk_id_from_file_name("output_chunk0001.meta.json", "meta"), "chunk0001")
        self.assertEqual(manifest.chunk_file_name("chunk0001", "output"), "output_chunk0001.md")
        for name, kind in (("../chunk0001.md", "source"), ("chunk0001.md", "output"), ("chunk1.md", "source")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                manifest.chunk_id_from_file_name(name, kind)
        for value in ("chunk1", "chunk0001\n", "../chunk0001", None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manifest.check_chunk_id(value)


class LoadManifestValidationTests(unittest.TestCase):
    ENTRY = {"id": "chunk0001", "source_file": "chunk0001.md", "output_file": "output_chunk0001.md",
             "order": 1, "source_hash": "ab" * 32}

    def load(self, chunks):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "manifest.json").write_text(json.dumps({"chunks": chunks}), encoding="utf-8")
            return manifest.load_manifest(tmp)

    def test_accepts_a_valid_manifest(self):
        self.assertEqual(self.load([self.ENTRY])["chunks"], [self.ENTRY])
        # create_manifest writes an empty hash for a chunk file it could not find.
        self.assertEqual(self.load([dict(self.ENTRY, source_hash="")])["chunks"][0]["source_hash"], "")

    def test_accepts_what_create_manifest_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "chunk0001.md").write_text("Body\n", encoding="utf-8")
            Path(tmp, "input.md").write_text("Body\n", encoding="utf-8")
            manifest.create_manifest(tmp, ["chunk0001.md", "chunk0002.md"], os.path.join(tmp, "input.md"))
            self.assertEqual([c["id"] for c in manifest.load_manifest(tmp)["chunks"]],
                             ["chunk0001", "chunk0002"])

    def test_rejects_entries_that_point_elsewhere(self):
        cases = [
            dict(self.ENTRY, id="../chunk0001"),
            dict(self.ENTRY, source_file="../../etc/passwd"),
            dict(self.ENTRY, output_file="output_chunk0002.md"),
            "chunk0001",
        ]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.load([entry])

    def test_rejects_incomplete_or_mistyped_entries(self):
        cases = [{k: v for k, v in self.ENTRY.items() if k != key} for key in self.ENTRY]
        cases += [dict(self.ENTRY, **{key: value}) for key, value in (
            ("order", True), ("order", "1"), ("order", 0), ("order", -1), ("order", 1.0),
            ("source_hash", None), ("source_hash", 5), ("source_hash", "abc"),
            ("source_hash", "AB" * 32), ("source_hash", "ab" * 32 + "\n"), ("notes", "x"))]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.load([entry])

    def test_rejects_entries_listed_twice(self):
        with self.assertRaisesRegex(ValueError, "listed twice"):
            self.load([self.ENTRY, dict(self.ENTRY, order=2)])
        with self.assertRaisesRegex(ValueError, "not a list"):
            self.load({"chunk0001": self.ENTRY})


if __name__ == "__main__":
    unittest.main()
