import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import chunk_context  # noqa: E402


class ChunkContextTests(unittest.TestCase):
    def _write(self, path, text):
        Path(path).write_text(text, encoding="utf-8")

    def test_middle_chunk_gets_prev_tail_and_next_head(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(Path(temp_dir) / "chunk0001.md", "A" * 10 + "prev-tail")
            self._write(Path(temp_dir) / "chunk0002.md", "middle")
            self._write(Path(temp_dir) / "chunk0003.md", "next-head" + "B" * 10)

            ctx = chunk_context.get_neighbor_context(temp_dir, "chunk0002.md", chars=9)

            self.assertEqual(ctx["chunk_id"], "chunk0002")
            self.assertEqual(ctx["prev_chunk"], "chunk0001.md")
            self.assertEqual(ctx["next_chunk"], "chunk0003.md")
            self.assertEqual(ctx["prev_excerpt"], "prev-tail")
            self.assertEqual(ctx["next_excerpt"], "next-head")

    def test_edge_chunks_omit_missing_neighbors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(Path(temp_dir) / "chunk0001.md", "first")
            self._write(Path(temp_dir) / "chunk0002.md", "second")

            first = chunk_context.get_neighbor_context(temp_dir, "chunk0001.md")
            last = chunk_context.get_neighbor_context(temp_dir, "chunk0002.md")

            self.assertEqual(first["prev_excerpt"], "")
            self.assertEqual(first["next_excerpt"], "second")
            self.assertEqual(last["prev_excerpt"], "first")
            self.assertEqual(last["next_excerpt"], "")

    def test_prompt_format_is_empty_without_neighbors(self):
        ctx = {
            "chunk_id": "chunk0001",
            "prev_chunk": None,
            "next_chunk": None,
            "prev_excerpt": "",
            "next_excerpt": "",
        }

        self.assertEqual(chunk_context.format_for_prompt(ctx), "")

    def test_prompt_format_labels_neighbors_read_only(self):
        ctx = {
            "chunk_id": "chunk0002",
            "prev_chunk": "chunk0001.md",
            "next_chunk": "chunk0003.md",
            "prev_excerpt": "previous",
            "next_excerpt": "next",
        }

        rendered = chunk_context.format_for_prompt(ctx)

        self.assertIn("Previous chunk excerpt", rendered)
        self.assertIn("Next chunk excerpt", rendered)
        self.assertIn("read-only", rendered)

    def test_rejects_non_chunk_filename(self):
        with self.assertRaises(ValueError):
            chunk_context.parse_chunk_name("output_chunk0001.md")

    def test_json_shape_is_serializable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(Path(temp_dir) / "chunk0001.md", "one")
            ctx = chunk_context.get_neighbor_context(temp_dir, "chunk0001.md")

        self.assertIn('"chunk_id"', json.dumps(ctx))


if __name__ == "__main__":
    unittest.main()
