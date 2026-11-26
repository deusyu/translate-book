import process_guard  # noqa: F401  first, so it guards every process the tests below start
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

import run_state  # noqa: E402
from manifest import create_manifest  # noqa: E402


def glossary_doc(target="太一", aliases=None):
    return {
        "version": 2,
        "terms": [
            {
                "id": "Tai",
                "source": "Tai",
                "target": target,
                "category": "person",
                "aliases": aliases or [],
                "gender": "unknown",
                "confidence": "medium",
                "frequency": 1,
                "evidence_refs": [],
                "notes": "",
            }
        ],
        "high_frequency_top_n": 0,
        "applied_meta_hashes": {},
    }


class RunStateTests(unittest.TestCase):
    def _write(self, path, content):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")

    def _workspace(self):
        tmp = tempfile.TemporaryDirectory()
        temp_dir = Path(tmp.name)
        self._write(temp_dir / "input.md", "Tai went home.\n")
        self._write(temp_dir / "chunk0001.md", "Tai went home.\n")
        self._write(temp_dir / "chunk0002.md", "No glossary hit.\n")
        self._write(temp_dir / "output_chunk0001.md", "太一回家了。\n")
        self._write(temp_dir / "output_chunk0002.md", "没有术语。\n")
        create_manifest(
            str(temp_dir),
            ["chunk0001.md", "chunk0002.md"],
            str(temp_dir / "input.md"),
        )
        self._write(temp_dir / "glossary.json", json.dumps(glossary_doc(), ensure_ascii=False))
        return tmp, temp_dir

    def test_untracked_outputs_are_record_only_by_default(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            plan = run_state.plan(str(temp_dir))

        self.assertEqual(plan["translation_chunk_ids"], [])
        self.assertEqual(plan["record_only_chunk_ids"], ["chunk0001", "chunk0002"])

    def test_retranslate_untracked_flag_marks_existing_outputs_for_translation(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            plan = run_state.plan(str(temp_dir), retranslate_untracked=True)

        self.assertEqual(plan["translation_chunk_ids"], ["chunk0001", "chunk0002"])

    def test_record_then_plan_is_unchanged(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001", "chunk0002"])
            plan = run_state.plan(str(temp_dir))

        self.assertEqual(plan["translation_chunk_ids"], [])
        self.assertEqual(plan["record_only_chunk_ids"], [])
        self.assertEqual(plan["unchanged_chunk_ids"], ["chunk0001", "chunk0002"])

    def test_missing_output_needs_translation(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            os.remove(temp_dir / "output_chunk0002.md")
            plan = run_state.plan(str(temp_dir))

        self.assertIn("chunk0002", plan["translation_chunk_ids"])

    def test_blank_output_needs_translation(self):
        # Whitespace-only output has bytes on disk but no usable translation;
        # planning must treat it like a missing output, not record it.
        tmp, temp_dir = self._workspace()
        with tmp:
            self._write(temp_dir / "output_chunk0002.md", "\n   \n\t\n")
            plan = run_state.plan(str(temp_dir))
            reasons = {
                item["chunk_id"]: item["reasons"] for item in plan["chunks"]
            }

        self.assertIn("chunk0002", plan["translation_chunk_ids"])
        self.assertIn("blank_output", reasons["chunk0002"])

    def test_recorded_output_written_empty_is_translated_again(self):
        # SKILL.md re-translates a chunk by having the agent write its output
        # empty rather than delete it.
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001", "chunk0002"])
            (temp_dir / "output_chunk0001.md").write_bytes(b"")
            plan = run_state.plan(str(temp_dir))
            reasons = {item["chunk_id"]: item["reasons"] for item in plan["chunks"]}

        self.assertEqual(plan["translation_chunk_ids"], ["chunk0001"])
        self.assertIn("empty_output", reasons["chunk0001"])
        self.assertEqual(plan["unchanged_chunk_ids"], ["chunk0002"])

    def test_record_rejects_blank_output(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            self._write(temp_dir / "output_chunk0001.md", "   \n")
            with self.assertRaises(ValueError):
                run_state.record_chunks(str(temp_dir), ["chunk0001"])

    def test_source_hash_change_needs_translation_after_record(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001", "chunk0002"])
            self._write(temp_dir / "chunk0001.md", "Tai changed.\n")
            plan = run_state.plan(str(temp_dir))

        self.assertIn("chunk0001", plan["translation_chunk_ids"])

    def test_glossary_target_change_needs_translation_for_affected_chunk_only(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001", "chunk0002"])
            self._write(
                temp_dir / "glossary.json",
                json.dumps(glossary_doc(target="泰"), ensure_ascii=False),
            )
            plan = run_state.plan(str(temp_dir))

        self.assertIn("chunk0001", plan["translation_chunk_ids"])
        self.assertNotIn("chunk0002", plan["translation_chunk_ids"])

    def test_alias_change_on_selected_term_needs_translation(self):
        # chunk0001 contains the source "Tai", so the term is already selected
        # before AND after the alias edit — selection alone cannot detect the
        # change. The injected term table's 别名 column changed, so term_hash
        # must flag the chunk for re-translation.
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001", "chunk0002"])
            self._write(
                temp_dir / "glossary.json",
                json.dumps(glossary_doc(aliases=["Taig"]), ensure_ascii=False),
            )
            plan = run_state.plan(str(temp_dir))
            reasons = {
                item["chunk_id"]: item["reasons"] for item in plan["chunks"]
            }

        self.assertIn("chunk0001", plan["translation_chunk_ids"])
        self.assertNotIn("chunk0002", plan["translation_chunk_ids"])
        self.assertTrue(
            any(
                isinstance(r, dict) and r.get("code") == "glossary_term_hash_changed"
                for r in reasons["chunk0001"]
            ),
            msg=f"expected glossary_term_hash_changed, got {reasons['chunk0001']}",
        )

    def test_new_alias_that_hits_chunk_changes_term_selection(self):
        tmp = tempfile.TemporaryDirectory()
        with tmp:
            temp_dir = Path(tmp.name)
            self._write(temp_dir / "input.md", "Taig appears.\n")
            self._write(temp_dir / "chunk0001.md", "Taig appears.\n")
            self._write(temp_dir / "output_chunk0001.md", "泰格出现。\n")
            create_manifest(str(temp_dir), ["chunk0001.md"], str(temp_dir / "input.md"))
            self._write(temp_dir / "glossary.json", json.dumps(glossary_doc(), ensure_ascii=False))
            run_state.record_chunks(str(temp_dir), ["chunk0001"])

            self._write(
                temp_dir / "glossary.json",
                json.dumps(glossary_doc(aliases=["Taig"]), ensure_ascii=False),
            )
            plan = run_state.plan(str(temp_dir))

        self.assertEqual(plan["translation_chunk_ids"], ["chunk0001"])

    def test_output_hash_change_is_record_only(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001"])
            self._write(temp_dir / "output_chunk0001.md", "手工编辑。\n")
            plan = run_state.plan(str(temp_dir))

        self.assertIn("chunk0001", plan["record_only_chunk_ids"])
        self.assertNotIn("chunk0001", plan["translation_chunk_ids"])

    def test_record_writes_required_fields(self):
        tmp, temp_dir = self._workspace()
        with tmp:
            run_state.record_chunks(str(temp_dir), ["chunk0001"])
            data = run_state.load_run_state(str(temp_dir))
            record = data["chunks"]["chunk0001"]

        self.assertIn("glossary_version_used", record)
        self.assertEqual(record["entity_ids_used"], ["Tai"])
        self.assertIn("output_hash", record)



class ChunkIdInputTests(unittest.TestCase):
    def make_temp_dir(self, tmp, with_manifest=True):
        for n in (1, 2):
            Path(tmp, f"chunk000{n}.md").write_text(f"source {n}\n", encoding="utf-8")
            Path(tmp, f"output_chunk000{n}.md").write_text(f"译文 {n}\n", encoding="utf-8")
        if with_manifest:
            Path(tmp, "input.md").write_text("source 1\n\nsource 2\n", encoding="utf-8")
            create_manifest(tmp, ["chunk0001.md", "chunk0002.md"], str(Path(tmp, "input.md")))

    def record(self, tmp, *args, stdin=""):
        return process_guard.run_script("run_state.py", "record", tmp, *args, input=stdin)

    def recorded(self, tmp):
        return sorted(json.loads(Path(tmp, "run_state.json").read_text(encoding="utf-8"))["chunks"])

    def test_reads_chunk_ids_from_standard_input(self):
        for stdin in ("chunk0001\n\nchunk0002 chunk0001\n", '["chunk0001", "chunk0002"]\n',
                      "\ufeffchunk0001\r\nchunk0002\r\n"):
            with self.subTest(stdin=stdin), tempfile.TemporaryDirectory() as tmp:
                self.make_temp_dir(tmp)
                result = self.record(tmp, stdin=stdin)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["recorded_chunk_ids"], ["chunk0001", "chunk0002"])
                self.assertEqual(self.recorded(tmp), ["chunk0001", "chunk0002"])

    def test_rejects_anything_but_chunk_ids(self):
        cases = [((), "chunk0001\n../../etc/passwd\n", "is not a chunk id"),
                 ((), "chunk1\n", "is not a chunk id"),
                 ((), '["chunk0001", 2]', "is not a chunk id"),
                 ((), '["chunk0001"', "not valid JSON"),
                 ((), "\n \n", "no chunk ids given"),
                 (("chunk1",), "", "is not a chunk id"),
                 (("$(touch x)",), "", "is not a chunk id")]
        for args, stdin, needle in cases:
            with self.subTest(args=args, stdin=stdin), tempfile.TemporaryDirectory() as tmp:
                self.make_temp_dir(tmp)
                result = self.record(tmp, *args, stdin=stdin)
                self.assertEqual(result.returncode, 1)
                self.assertIn(needle, result.stderr)
                self.assertFalse(Path(tmp, "run_state.json").exists())

    def test_read_chunk_ids_checks_every_id(self):
        for text in ("chunk0001\nchunk1\n", '["chunk0001", "chunk$(x)"]', "chunk0001 ../chunk0002"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "is not a chunk id"):
                run_state.read_chunk_ids(io.StringIO(text))
        self.assertEqual(run_state.read_chunk_ids(io.StringIO("chunk0002 chunk0001 chunk0002")),
                         ["chunk0002", "chunk0001"])

    def test_arguments_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_temp_dir(tmp)
            result = self.record(tmp, "chunk0002")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.recorded(tmp), ["chunk0002"])

    def test_plan_without_manifest_checks_chunk_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_temp_dir(tmp, with_manifest=False)
            ids = [item["chunk_id"] for item in run_state.plan(tmp)["chunks"]]
            self.assertEqual(ids, ["chunk0001", "chunk0002"])
            Path(tmp, "chunk$(touch x).md").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unexpected file"):
                run_state.plan(tmp)


if __name__ == "__main__":
    unittest.main()
