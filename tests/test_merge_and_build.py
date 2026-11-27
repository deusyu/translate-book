import process_guard  # noqa: F401  first, so it guards every process the tests below start
import contextlib
import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import merge_and_build  # noqa: E402


class ConvertWithPandocTests(unittest.TestCase):
    def test_renders_tex_math_as_mathml(self):
        with mock.patch.object(merge_and_build.subprocess, "run") as run_mock:
            run_mock.return_value.stderr = ""
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = merge_and_build.convert_with_pandoc("in.md", "out.html", "Title", "zh-CN")

        self.assertTrue(ok)
        cmd = run_mock.call_args.args[0]
        self.assertIn("--mathml", cmd)
        self.assertEqual(cmd[cmd.index("--to") + 1], "html5")
        self.assertNotIn("Warning", buf.getvalue())

    def test_warns_about_formulas_left_as_raw_tex(self):
        stderr = (
            "[WARNING] Could not convert TeX math a & b, rendering as TeX:\n  a & b\n    ^\n  unexpected '&'\n"
            "[WARNING] Could not convert TeX math x \\quad \\\\, rendering as TeX:\n  unexpected control sequence \\\\\n"
        )
        with mock.patch.object(merge_and_build.subprocess, "run") as run_mock:
            run_mock.return_value.stderr = stderr
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = merge_and_build.convert_with_pandoc("in.md", "out.html", "Title", "zh-CN")

        self.assertTrue(ok)
        out = buf.getvalue()
        self.assertIn("Warning: pandoc left 2 formula(s) as raw TeX instead of MathML:", out)
        self.assertIn("  a & b\n", out)
        self.assertIn("  x \\quad \\\\\n", out)

    @process_guard.needs("pandoc")
    def test_real_pandoc_warning_is_recognized(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "in.md"
            md.write_text("$$a & b$$\n\n$$\\frac{1}{2}$$\n", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), process_guard.library_processes("pandoc"):
                ok = merge_and_build.convert_with_pandoc(str(md), str(Path(tmp) / "out.html"), "T", "en")
        self.assertTrue(ok)
        self.assertIn("pandoc left 1 formula(s) as raw TeX", buf.getvalue())

    def test_uses_ascii_heading_ids(self):
        # Non-ASCII ids lose their anchors in Calibre's split EPUB TOC.
        with mock.patch.object(merge_and_build.subprocess, "run") as run_mock:
            run_mock.return_value.stderr = ""
            with contextlib.redirect_stdout(io.StringIO()):
                merge_and_build.convert_with_pandoc("in.md", "out.html", "Title", "zh-CN")

        cmd = run_mock.call_args.args[0]
        self.assertIn("+ascii_identifiers", cmd[cmd.index("--from") + 1])


class GenerateFormatTests(unittest.TestCase):
    def _write_file(self, path, content="data"):
        Path(path).write_text(content, encoding="utf-8")

    def _set_mtime(self, path, timestamp):
        os.utime(path, (timestamp, timestamp))

    def test_skips_when_output_is_up_to_date(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            html_file = os.path.join(temp_dir, "book_doc.html")
            output_file = os.path.join(temp_dir, "book.epub")
            self._write_file(html_file, "<html></html>")
            self._write_file(output_file, "epub")
            self._set_mtime(html_file, 100)
            self._set_mtime(output_file, 200)

            with mock.patch.object(merge_and_build.subprocess, "run") as run_mock:
                result = merge_and_build.generate_format(
                    html_file, temp_dir, ".epub", "zh-CN"
                )

            self.assertEqual(result, output_file)
            run_mock.assert_not_called()

    def test_rebuilds_when_image_assets_are_newer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            html_file = os.path.join(temp_dir, "book_doc.html")
            output_file = os.path.join(temp_dir, "book.epub")
            images_dir = os.path.join(temp_dir, "images")
            image_file = os.path.join(images_dir, "cover.jpg")

            os.makedirs(images_dir, exist_ok=True)
            self._write_file(html_file, "<html></html>")
            self._write_file(output_file, "epub")
            self._write_file(image_file, "image")

            self._set_mtime(html_file, 100)
            self._set_mtime(output_file, 200)
            self._set_mtime(image_file, 300)

            with mock.patch.object(
                merge_and_build.subprocess,
                "run",
                return_value=SimpleNamespace(stdout="", stderr=""),
            ) as run_mock:
                result = merge_and_build.generate_format(
                    html_file, temp_dir, ".epub", "zh-CN"
                )

            self.assertEqual(result, output_file)
            run_mock.assert_called_once()
            cmd = run_mock.call_args.args[0]
            self.assertEqual(cmd[0], "python3")
            self.assertEqual(cmd[2], html_file)
            self.assertEqual(cmd[4], output_file)

    @unittest.skipUnless(
        "cover" in inspect.signature(merge_and_build.generate_format).parameters,
        "cover parameter unavailable",
    )
    def test_rebuilds_epub_when_cover_is_explicitly_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            html_file = os.path.join(temp_dir, "book_doc.html")
            output_file = os.path.join(temp_dir, "book.epub")
            cover_file = os.path.join(temp_dir, "cover.jpg")

            self._write_file(html_file, "<html></html>")
            self._write_file(output_file, "epub")
            self._write_file(cover_file, "image")

            self._set_mtime(html_file, 100)
            self._set_mtime(output_file, 200)
            self._set_mtime(cover_file, 300)

            with mock.patch.object(
                merge_and_build.subprocess,
                "run",
                return_value=SimpleNamespace(stdout="", stderr=""),
            ) as run_mock:
                result = merge_and_build.generate_format(
                    html_file, temp_dir, ".epub", "zh-CN", cover=cover_file
                )

            self.assertEqual(result, output_file)
            run_mock.assert_called_once()
            cmd = run_mock.call_args.args[0]
            self.assertIn("--cover", cmd)
            self.assertIn(cover_file, cmd)


class MissingCoverPathTests(unittest.TestCase):
    @unittest.skipUnless(
        "cover" in inspect.signature(merge_and_build.generate_format).parameters,
        "cover parameter unavailable",
    )
    def test_main_rejects_missing_cover_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_cover = os.path.join(temp_dir, "missing-cover.jpg")

            with mock.patch.object(
                merge_and_build, "load_config", return_value={}
            ), mock.patch.object(
                merge_and_build, "get_lang_config", return_value={"lang_attr": "zh-CN"}
            ), mock.patch.object(
                merge_and_build, "merge_markdown_files", return_value=True
            ), mock.patch.object(
                merge_and_build, "convert_md_to_html", return_value=True
            ), mock.patch.object(
                merge_and_build, "add_toc", return_value=True
            ), mock.patch.object(
                merge_and_build, "generate_formats"
            ) as generate_formats_mock, mock.patch.object(
                sys, "argv", ["merge_and_build.py", "--temp-dir", temp_dir, "--cover", missing_cover]
            ):
                with self.assertRaises(SystemExit) as exc:
                    merge_and_build.main()

            self.assertNotEqual(exc.exception.code, 0)
            generate_formats_mock.assert_not_called()


class ExportAliasTests(unittest.TestCase):
    def _write(self, path, content="x"):
        Path(path).write_text(content, encoding="utf-8")

    def test_export_named_aliases_copies_canonical_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                self._write(Path(temp_dir) / name, name)

            copied = merge_and_build.export_named_aliases(temp_dir, "Translated Book")

            self.assertEqual(
                set(copied),
                {
                    "Translated Book.html",
                    "Translated Book_doc.html",
                    "Translated Book.docx",
                    "Translated Book.epub",
                    "Translated Book.pdf",
                },
            )
            self.assertEqual(
                (Path(temp_dir) / "Translated Book.epub").read_text(encoding="utf-8"),
                "book.epub",
            )
            self.assertTrue((Path(temp_dir) / "book.epub").exists())

    def test_export_name_rejects_paths(self):
        with self.assertRaises(ValueError):
            merge_and_build.export_named_aliases("/tmp", "../bad")

    def test_export_name_rejects_canonical_collision_without_writing(self):
        for export_name in ["book_doc", "BOOK_DOC", "  book_doc  "]:
            with self.subTest(export_name=export_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                originals = {
                    name: name.encode("utf-8")
                    for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]
                }
                for name, content in originals.items():
                    (root / name).write_bytes(content)

                with self.assertRaisesRegex(ValueError, "book_doc.html"):
                    merge_and_build.export_named_aliases(temp_dir, export_name)

                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, originals)

    def test_canonical_book_export_name_is_a_noop(self):
        for export_name in ["book", "BOOK"]:
            with self.subTest(export_name=export_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                    self._write(root / name, name)
                originals = {p.name: p.read_bytes() for p in root.iterdir()}
                same_files = (root / f"{export_name}.html").exists()

                copied = merge_and_build.export_named_aliases(temp_dir, export_name)

                self.assertEqual(len(copied), 0 if same_files else 5)
                for name, content in originals.items():
                    self.assertEqual((root / name).read_bytes(), content)
                record = {merge_and_build.EXPORT_COPIES_FILE} if copied else set()
                self.assertEqual({p.name for p in root.iterdir()} - set(originals), set(copied) | record)

    def test_export_name_rejects_pipeline_html_without_writing(self):
        for export_name, kept in [("input", "input.html"), ("Input", "input.html"),
                                  (" INPUT ", "input.html"), ("output", "output.html"),
                                  ("OUTPUT", "output.html")]:
            with self.subTest(export_name=export_name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                originals = {
                    name: name.encode("utf-8")
                    for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf",
                                 "input.html", "output.html"]
                }
                for name, content in originals.items():
                    (root / name).write_bytes(content)

                with self.assertRaisesRegex(ValueError, kept):
                    merge_and_build.export_named_aliases(temp_dir, export_name)

                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, originals)

    def test_copies_stay_recorded_until_their_files_are_gone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                self._write(root / name, name)
            record_path = root / merge_and_build.EXPORT_COPIES_FILE

            first = merge_and_build.export_named_aliases(temp_dir, "Old Title")
            second = merge_and_build.export_named_aliases(temp_dir, "New Title")
            self.assertEqual(json.loads(record_path.read_text(encoding="utf-8")),
                             {"copies": first + second})

            (root / "Old Title.pdf").unlink()
            merge_and_build.export_named_aliases(temp_dir, "New Title")
            self.assertEqual(json.loads(record_path.read_text(encoding="utf-8")),
                             {"copies": [n for n in first if n != "Old Title.pdf"] + second})

    def test_main_rejects_export_collision_before_building(self):
        for export_name, kept in [("book_doc", "book_doc.html"), ("input", "input.html"),
                                  ("Output", "output.html")]:
            with self.subTest(export_name=export_name), tempfile.TemporaryDirectory() as temp_dir:
                output = io.StringIO()
                with mock.patch.object(
                    sys, "argv",
                    ["merge_and_build.py", "--temp-dir", temp_dir, "--export-name", export_name],
                ), mock.patch.object(
                    merge_and_build, "merge_markdown_files"
                ) as merge_mock, contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as exc:
                        merge_and_build.main()

                self.assertNotEqual(exc.exception.code, 0)
                self.assertIn(kept, output.getvalue())
                merge_mock.assert_not_called()

    def test_export_name_whose_longest_copy_takes_255_bytes_is_accepted(self):
        for kind, export_name in [("ascii", "a" * 246), ("multibyte", "书" * 82)]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.assertEqual(len((export_name + "_doc.html").encode("utf-8")), 255)
                for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                    self._write(root / name, name)

                copied = merge_and_build.export_named_aliases(temp_dir, export_name)

                self.assertEqual(len(copied), 5)
                self.assertTrue((root / (export_name + "_doc.html")).is_file())
                record = json.loads((root / merge_and_build.EXPORT_COPIES_FILE).read_text(encoding="utf-8"))
                self.assertEqual(record, {"copies": copied})

    def test_export_name_whose_longest_copy_takes_256_bytes_is_rejected_without_writing(self):
        for kind, export_name in [("ascii", "a" * 247), ("multibyte", "书" * 81 + "éé")]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self.assertEqual(len((export_name + "_doc.html").encode("utf-8")), 256)
                for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                    self._write(root / name, name)
                originals = {p.name: p.read_bytes() for p in root.iterdir()}

                with self.assertRaisesRegex(ValueError, "shorten it to 246 bytes or fewer"):
                    merge_and_build.export_named_aliases(temp_dir, export_name)
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, originals)

                output = io.StringIO()
                with mock.patch.object(
                    sys, "argv",
                    ["merge_and_build.py", "--temp-dir", temp_dir, "--export-name", export_name],
                ), mock.patch.object(
                    merge_and_build, "merge_markdown_files"
                ) as merge_mock, contextlib.redirect_stdout(output):
                    with self.assertRaises(SystemExit) as exc:
                        merge_and_build.main()

                self.assertEqual(exc.exception.code, 1)
                self.assertIn("--export-name is too long", output.getvalue())
                merge_mock.assert_not_called()
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, originals)

    def test_export_aliases_refuse_an_unusable_record(self):
        for text in ["{not json", '{"copies": ["../outside.pdf"]}']:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                    self._write(root / name, name)
                (root / merge_and_build.EXPORT_COPIES_FILE).write_text(text, encoding="utf-8")
                originals = {p.name: p.read_bytes() for p in root.iterdir()}

                with self.assertRaisesRegex(ValueError, "no export copy has|Expecting"):
                    merge_and_build.export_named_aliases(temp_dir, "New Title")

                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, originals)

    def test_main_rejects_an_unusable_record_before_building(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / merge_and_build.EXPORT_COPIES_FILE).write_text("{not json", encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(
                sys, "argv",
                ["merge_and_build.py", "--temp-dir", temp_dir, "--export-name", "New Title"],
            ), mock.patch.object(
                merge_and_build, "merge_markdown_files"
            ) as merge_mock, contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as exc:
                    merge_and_build.main()

            self.assertEqual(exc.exception.code, 1)
            self.assertIn(f"{merge_and_build.EXPORT_COPIES_FILE} cannot be used", output.getvalue())
            self.assertIn("Nothing was built.", output.getvalue())
            merge_mock.assert_not_called()


class DeleteOutputsTests(unittest.TestCase):
    OUTPUTS = ["book.html", "book_doc.html", "output.html", "book.docx", "book.epub", "book.pdf"]
    # Everything else the pipeline and the agent leave in a temp dir.
    KEPT = ["input.html", "input.md", "chunk0001.md", "output_chunk0001.md",
            "output_chunk0001.meta.json", "output.md", "manifest.json", "config.txt",
            "source_fingerprint.json", "glossary.json", "run_state.json", "merge_decisions.json",
            "title.txt", "author.txt", "export_name.txt", "images/fig1.png", "figure_text/0a1b.json"]
    SUFFIXES = [".html", "_doc.html", ".docx", ".epub", ".pdf"]

    def make_temp_dir(self, parent, export_name="Old Title"):
        temp_dir = Path(parent) / "book_temp"
        for rel in self.OUTPUTS + self.KEPT:
            path = temp_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel, encoding="utf-8")
        (temp_dir / "export_name.txt").write_text(export_name + "\n", encoding="utf-8")
        return temp_dir

    def files(self, temp_dir):
        return sorted(p.relative_to(temp_dir).as_posix() for p in temp_dir.rglob("*")
                      if p.is_file() or p.is_symlink())

    def contents(self, temp_dir):
        return {p.relative_to(temp_dir).as_posix(): os.readlink(p) if p.is_symlink() else p.read_bytes()
                for p in temp_dir.rglob("*") if p.is_file() or p.is_symlink()}

    def delete(self, temp_dir):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = merge_and_build.delete_outputs(str(temp_dir))
        return ok, out.getvalue()

    def run_main(self, temp_dir, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["merge_and_build.py", "--temp-dir", str(temp_dir), *args]), \
                contextlib.redirect_stdout(out):
            try:
                merge_and_build.main()
            except SystemExit as e:
                return e.code, out.getvalue()
        return 0, out.getvalue()

    def record(self, temp_dir):
        path = temp_dir / merge_and_build.EXPORT_COPIES_FILE
        return json.loads(path.read_text(encoding="utf-8"))

    def write_record(self, temp_dir, names):
        (temp_dir / merge_and_build.EXPORT_COPIES_FILE).write_text(
            json.dumps({"copies": names}), encoding="utf-8")

    def test_deletes_outputs_and_recorded_copies_only(self):
        name = "O'Brien's \"tale\" $(touch pwned) `id` $HOME *?[ab] -rf\nend"
        # An empty name would give .html etc.; a glob or a prefix match would reach the rest.
        decoys = [".html", "_doc.html", ".pdf", "other.html", name + ".md", "Old Title.pdf.bak"]
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            for decoy in decoys:
                (temp_dir / decoy).write_text("decoy", encoding="utf-8")
            self.assertEqual(len(merge_and_build.export_named_aliases(str(temp_dir), name)), 5)

            ok, _ = self.delete(temp_dir)

            self.assertTrue(ok)
            self.assertEqual(self.files(temp_dir),
                             sorted(self.KEPT + decoys + [merge_and_build.EXPORT_COPIES_FILE]))
            self.assertEqual(self.record(temp_dir), {"copies": []})
            self.assertEqual(list(Path(tmp).glob("pwned*")), [])

    def test_finds_copies_after_the_export_name_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            merge_and_build.export_named_aliases(str(temp_dir), "Old Title")
            # A rebuild under a new name that skipped the deletion.
            merge_and_build.export_named_aliases(str(temp_dir), "Mid Title")
            (temp_dir / "export_name.txt").write_text("New Title\n", encoding="utf-8")
            # With a record, export_name.txt names nothing to delete.
            (temp_dir / "New Title.pdf").write_text("not a copy", encoding="utf-8")

            ok, _ = self.delete(temp_dir)

            self.assertTrue(ok)
            self.assertEqual(self.files(temp_dir),
                             sorted(self.KEPT + ["New Title.pdf", merge_and_build.EXPORT_COPIES_FILE]))

    def test_an_unusable_record_deletes_nothing(self):
        cases = {"corrupt JSON": "{not json", "an empty file": "", "a list": "[]",
                 "copies not a list": '{"copies": "bad"}', "copies not names": '{"copies": [1]}',
                 "no copies": "{}", "a directory": None, "a dangling symlink": None}
        for case, text in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                merge_and_build.export_named_aliases(str(temp_dir), "Old Title")
                # The next export name is already written, and a file has its copy's name.
                (temp_dir / "export_name.txt").write_text("New Title\n", encoding="utf-8")
                (temp_dir / "New Title.pdf").write_text("not a copy", encoding="utf-8")
                record = temp_dir / merge_and_build.EXPORT_COPIES_FILE
                record.unlink()
                if case == "a directory":
                    record.mkdir()
                elif case == "a dangling symlink":
                    os.symlink(Path(tmp) / "missing.json", record)
                else:
                    record.write_text(text, encoding="utf-8")
                before = self.contents(temp_dir)

                code, out = self.run_main(temp_dir, "--delete-outputs")

                self.assertEqual(code, 1)
                self.assertEqual(self.contents(temp_dir), before)
                self.assertTrue(os.path.lexists(record))
                self.assertIn(f"{merge_and_build.EXPORT_COPIES_FILE} cannot be used", out)
                self.assertIn("Nothing was deleted.", out)
                self.assertNotIn("deleting the copies named after", out)

    def test_a_record_naming_files_no_copy_has_deletes_nothing(self):
        # Each next to a real copy, which stays too: every name is checked first.
        entries = ["../outside.pdf", "<tmp>/outside.pdf", "..", ".", "", "nul\x00.pdf",
                   "images/x.pdf", "back\\slash.pdf", " Padded.pdf", ".html", "chunk0001.md",
                   "glossary.json", "export_name.txt", "input.html", "INPUT.html", "Input.html",
                   "OUTPUT.html", "BOOK_DOC.html", "\ud800.pdf", "a" * 247 + ".pdf", "书" * 81 + "éé.pdf"]
        for entry in entries:
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                entry = entry.replace("<tmp>", tmp)
                temp_dir = self.make_temp_dir(tmp)
                (Path(tmp) / "outside.pdf").write_text("keep", encoding="utf-8")
                for rel in ["images/x.pdf", "back\\slash.pdf", " Padded.pdf", ".html", "Legit.pdf"]:
                    (temp_dir / rel).write_text("keep", encoding="utf-8")
                self.write_record(temp_dir, ["Legit.pdf", entry])
                before = self.contents(temp_dir)

                ok, out = self.delete(temp_dir)

                self.assertFalse(ok)
                self.assertEqual(self.contents(temp_dir), before)
                self.assertEqual((Path(tmp) / "outside.pdf").read_text(encoding="utf-8"), "keep")
                self.assertIn(f"no export copy has: {entry!r}", out)
                self.assertIn("Nothing was deleted.", out)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlinks_are_deleted_not_their_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            target_file = Path(tmp) / "target.pdf"
            target_file.write_text("keep", encoding="utf-8")
            target_dir = Path(tmp) / "target_dir"
            target_dir.mkdir()
            (target_dir / "inner.txt").write_text("keep", encoding="utf-8")
            (temp_dir / "book.pdf").unlink()
            links = {"book.pdf": target_file, "Linked.pdf": target_file,
                     "Linked.epub": target_dir, "Dangling.docx": Path(tmp) / "missing"}
            for name, target in links.items():
                os.symlink(target, temp_dir / name)
            self.write_record(temp_dir, ["Linked.pdf", "Linked.epub", "Dangling.docx"])

            ok, _ = self.delete(temp_dir)

            self.assertTrue(ok)
            for name in links:
                self.assertFalse(os.path.lexists(temp_dir / name), name)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "keep")
            self.assertEqual((target_dir / "inner.txt").read_text(encoding="utf-8"), "keep")

    def test_without_a_record_deletes_the_copies_named_in_export_name_txt(self):
        texts = ["Qui suis-je \n", " Qui suis-je\n", "Qui suis-je\r\n", "\u3000书名\u3000\n",
                 "-rf --no-preserve-root\n", "A\rB\n", "O'Brien's $(touch pwned) `id`\n", "..\n",
                 "书" * 82 + "\n"]
        for text in texts:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                (temp_dir / "export_name.txt").write_text(text, encoding="utf-8", newline="")
                # A build from before export_copies.json: --export-name "$(cat export_name.txt)"
                merge_and_build.export_named_aliases(str(temp_dir), text.rstrip("\n"))
                (temp_dir / merge_and_build.EXPORT_COPIES_FILE).unlink()

                ok, _ = self.delete(temp_dir)

                self.assertTrue(ok)
                self.assertEqual(self.files(temp_dir), sorted(self.KEPT))
                self.assertEqual(list(Path(tmp).glob("pwned*")), [])

    def test_without_a_record_or_export_name_txt_deletes_only_the_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            (temp_dir / "export_name.txt").unlink()
            # What an empty export name would give.
            for suffix in self.SUFFIXES:
                (temp_dir / suffix).write_text("keep", encoding="utf-8")

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok)
            self.assertEqual(self.files(temp_dir),
                             sorted([n for n in self.KEPT if n != "export_name.txt"] + self.SUFFIXES))
            self.assertNotIn("Error", out)

    def test_without_a_record_a_blank_export_name_txt_deletes_only_the_outputs(self):
        # No build makes copies for a blank name; the suffixes alone are what an
        # empty name would give.
        from manifest import create_manifest

        for text in ["", "\n", "  \r\n", "\u3000\n"]:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                with contextlib.redirect_stdout(io.StringIO()):
                    create_manifest(str(temp_dir), ["chunk0001.md"], str(temp_dir / "input.md"))
                (temp_dir / "export_name.txt").write_text(text, encoding="utf-8", newline="")
                for suffix in self.SUFFIXES:
                    (temp_dir / suffix).write_text("keep", encoding="utf-8")

                ok, out = self.delete(temp_dir)

                self.assertTrue(ok)
                self.assertEqual(self.files(temp_dir), sorted(self.KEPT + self.SUFFIXES))
                self.assertNotIn("Error", out)

    def test_a_blank_export_name_txt_without_a_conversion_deletes_nothing(self):
        # Only a directory that looks like a translate-book temp dir says what an empty
        # export_name.txt means: it has the manifest.json create_manifest() writes and its chunks.
        from manifest import create_manifest

        def entry(chunk_id, order):
            return {"id": chunk_id, "order": order, "source_file": f"{chunk_id}.md", "source_hash": "",
                    "output_file": f"output_{chunk_id}.md"}

        def made_up(*entries):
            return {"chunk_count": len(entries), "source_hash": "0" * 64, "chunks": list(entries)}

        # What convert.py leaves passes, past chunk9999 too and after a chunk changes; so does a
        # made-up manifest of that shape, as no hash is compared with a file.
        for count in [1, 3, 10000]:
            with self.subTest(chunks=count), tempfile.TemporaryDirectory() as tmp:
                names = [f"chunk{n:04d}.md" for n in range(1, count + 1)]
                for name in names:
                    (Path(tmp) / name).write_text("# One\n", encoding="utf-8")
                (Path(tmp) / "input.md").write_text("# Book\n", encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()):
                    create_manifest(tmp, names, str(Path(tmp) / "input.md"))
                with open(Path(tmp) / names[-1], "a", encoding="utf-8") as f:
                    f.write("\nText in a figure\n")
                self.assertIsNone(merge_and_build._conversion_problem(tmp))
        full = made_up(entry("chunk0001", 1))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "chunk0001.md").write_text("# One\n", encoding="utf-8")
            (Path(tmp) / "manifest.json").write_text(json.dumps(full), encoding="utf-8")
            self.assertIsNone(merge_and_build._conversion_problem(tmp))
        # Each case breaks one thing: (manifest.json, the chunk files, the reason given).
        one, two = ("chunk0001.md",), ("chunk0001.md", "chunk0002.md")
        cases = [(None, one, "there is none"),
                 ("manifest.json", one, "Expecting value"),
                 ('{"chunks":[]}', one, "it has the fields chunks,"),
                 (json.dumps({**full, "chunks": [], "chunk_count": 0}), one, "it lists no chunks"),
                 (json.dumps({"chunks": full["chunks"]}), one, "it has the fields chunks,"),
                 (json.dumps({**full, "chunk_count": 2}), one, "as its chunk_count says"),
                 (json.dumps({**full, "chunk_count": True}), one, "as its chunk_count says"),
                 (json.dumps({**full, "source_hash": ""}), one, "its source_hash is not 64 lowercase hex digits"),
                 (json.dumps(made_up(entry("chunk0001", 1), entry("chunk0002", 3))), two,
                  "its orders do not run from 1 without a gap"),
                 (json.dumps(made_up(entry("chunk0001", 1), entry("chunk0003", 2))), ("chunk0001.md", "chunk0003.md"),
                  "its entry of order 2 is chunk0003.md, not chunk0002.md"),
                 (json.dumps(made_up(entry("chunk00001", 1))), ("chunk00001.md",),
                  "its entry of order 1 is chunk00001.md, not chunk0001.md"),
                 (json.dumps(made_up(entry("chunk0002", 1), entry("chunk0001", 2))), two,
                  "its entry of order 1 is chunk0002.md, not chunk0001.md"),
                 (json.dumps(full), (), "chunk0001.md, which it lists, is not there as a file"),
                 (json.dumps(full), "symlink", "is not a regular file but a symlink"),
                 (json.dumps(full), two, "it does not list chunk0002.md, which is there")]
        for manifest, chunks, reason in cases:
            with self.subTest(manifest=manifest, chunks=chunks), tempfile.TemporaryDirectory() as tmp:
                temp_dir = Path(tmp) / "notes"
                temp_dir.mkdir()
                (temp_dir / "book.pdf").write_text("mine", encoding="utf-8")
                (temp_dir / "export_name.txt").write_text("", encoding="utf-8")
                if chunks == "symlink":
                    (Path(tmp) / "one.md").write_text("# One\n", encoding="utf-8")
                    (temp_dir / "chunk0001.md").symlink_to(Path(tmp) / "one.md")
                else:
                    for name in chunks:
                        (temp_dir / name).write_text("# One\n", encoding="utf-8")
                if manifest:
                    (temp_dir / "manifest.json").write_text(manifest, encoding="utf-8")
                before = self.contents(temp_dir)

                code, out = self.run_main(temp_dir, "--delete-outputs")

                self.assertEqual(code, 1)
                self.assertIn("export_name.txt is empty and", out)
                self.assertIn(reason, out)
                self.assertIn("Nothing was deleted.", out)
                self.assertEqual(self.contents(temp_dir), before)

    def test_without_a_record_an_unusable_export_name_txt_deletes_nothing(self):
        # The suffixes alone are what an empty name would give.
        decoys = self.SUFFIXES + ["sub\\bait.pdf"]
        reasons = {"<directory>": "cannot be read", "<dangling symlink>": "is a symlink to a missing file"}
        for text in ["../bait\n", "<parent>/bait\n", "sub\\bait\n",
                     "nul\x00bait\n", "input\n", "BOOK_DOC\n", "a" * 247 + "\n", "<directory>",
                     "<dangling symlink>"]:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                bait = [Path(tmp) / f"bait{suffix}" for suffix in self.SUFFIXES]
                for path in bait + [temp_dir / decoy for decoy in decoys]:
                    path.write_text("keep", encoding="utf-8")
                name_file = temp_dir / "export_name.txt"
                name_file.unlink()
                if text == "<directory>":
                    name_file.mkdir()
                elif text == "<dangling symlink>":
                    os.symlink(Path(tmp) / "missing.txt", name_file)
                else:
                    name_file.write_text(text.replace("<parent>", tmp), encoding="utf-8", newline="")
                before = self.contents(temp_dir)

                ok, out = self.delete(temp_dir)

                self.assertFalse(ok)
                self.assertEqual(self.contents(temp_dir), before)
                self.assertTrue(os.path.lexists(name_file))
                for path in bait:
                    self.assertEqual(path.read_text(encoding="utf-8"), "keep")
                reason = reasons.get(text, "holds no usable export name")
                self.assertIn(f"Error: export_name.txt {reason}", out)
                self.assertIn("Nothing was deleted.", out)

    def test_without_a_record_copies_outside_the_temp_dir_delete_nothing(self):
        # A valid stem with a drive prefix such as "D:" does this on Windows.
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            before = self.contents(temp_dir)
            with mock.patch.object(merge_and_build, "_export_copy_path", return_value=None):
                ok, out = self.delete(temp_dir)

            self.assertFalse(ok)
            self.assertEqual(self.contents(temp_dir), before)
            self.assertIn("export_name.txt names copies outside the temp dir", out)

    def test_main_exits_nonzero_when_something_could_not_be_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            (temp_dir / "book.pdf").unlink()
            for name in ["book.pdf", "Stuck.pdf"]:
                (temp_dir / name).mkdir()
                (temp_dir / name / "inner.txt").write_text("keep", encoding="utf-8")
            self.write_record(temp_dir, ["Stuck.pdf"])
            output = io.StringIO()
            with mock.patch.object(
                sys, "argv", ["merge_and_build.py", "--temp-dir", str(temp_dir), "--delete-outputs"],
            ), contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as exc:
                    merge_and_build.main()

            self.assertEqual(exc.exception.code, 1)
            self.assertIn("could not delete 'book.pdf'", output.getvalue())
            self.assertEqual(self.files(temp_dir),
                             sorted(self.KEPT + ["book.pdf/inner.txt", "Stuck.pdf/inner.txt",
                                                 merge_and_build.EXPORT_COPIES_FILE]))
            self.assertEqual(self.record(temp_dir), {"copies": ["Stuck.pdf"]})

    def test_main_deletes_and_builds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            merge_and_build.export_named_aliases(str(temp_dir), "Old Title")
            output = io.StringIO()
            with mock.patch.object(
                sys, "argv", ["merge_and_build.py", "--temp-dir", str(temp_dir), "--delete-outputs"],
            ), mock.patch.object(
                merge_and_build, "merge_markdown_files"
            ) as merge_mock, mock.patch.object(
                merge_and_build, "generate_formats"
            ) as formats_mock, contextlib.redirect_stdout(output):
                merge_and_build.main()

            merge_mock.assert_not_called()
            formats_mock.assert_not_called()
            self.assertEqual(self.files(temp_dir),
                             sorted(self.KEPT + [merge_and_build.EXPORT_COPIES_FILE]))
            self.assertIn("Nothing was built", output.getvalue())

    def test_delete_outputs_does_not_combine_with_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            before = self.files(temp_dir)
            with mock.patch.object(
                sys, "argv",
                ["merge_and_build.py", "--temp-dir", str(temp_dir), "--headings", "--delete-outputs"],
            ), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    merge_and_build.main()

            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(self.files(temp_dir), before)


class MergeBlankOutputTests(unittest.TestCase):
    """A whitespace-only output chunk must abort the merge instead of being
    silently dropped from the final book."""

    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def _workspace(self, tmp):
        from manifest import create_manifest

        temp_dir = Path(tmp)
        self._write(temp_dir / "input.md", "One.\n\nTwo.\n")
        self._write(temp_dir / "chunk0001.md", "One.\n")
        self._write(temp_dir / "chunk0002.md", "Two.\n")
        self._write(temp_dir / "output_chunk0001.md", "一。\n")
        self._write(temp_dir / "output_chunk0002.md", "二。\n")
        create_manifest(
            str(temp_dir),
            ["chunk0001.md", "chunk0002.md"],
            str(temp_dir / "input.md"),
        )
        return temp_dir

    def _merge(self, temp_dir):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = merge_and_build.merge_markdown_files(str(temp_dir))
        return ok, buf.getvalue()

    def test_manifest_merge_fails_on_blank_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            self._write(temp_dir / "output_chunk0002.md", "\n   \n")
            ok, out = self._merge(temp_dir)

            self.assertFalse(ok)
            self.assertFalse((temp_dir / "output.md").exists())
            self.assertIn("Blank output", out)

    def test_legacy_merge_fails_on_blank_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            (temp_dir / "manifest.json").unlink()
            self._write(temp_dir / "output_chunk0002.md", "\n   \n")
            ok, out = self._merge(temp_dir)

            self.assertFalse(ok)
            self.assertFalse((temp_dir / "output.md").exists())
            self.assertIn("Blank output", out)

    def test_merge_succeeds_with_substantive_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self._workspace(tmp)
            ok, _ = self._merge(temp_dir)

            self.assertTrue(ok)
            merged = (temp_dir / "output.md").read_text(encoding="utf-8")
            self.assertIn("一。", merged)
            self.assertIn("二。", merged)


class FootnoteValidationTests(unittest.TestCase):
    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def _validate(self, source, output):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(Path(temp_dir) / "chunk0001.md", source)
            self._write(Path(temp_dir) / "output_chunk0001.md", output)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = merge_and_build._validate_chunk_footnotes(temp_dir)
        return ok, buf.getvalue()

    def test_passes_when_markers_are_preserved(self):
        ok, out = self._validate(
            "Text.[^1] More.[^2]\n\n[^1]: Note one.\n\n[^2]: Note two.\n\n    Continued.\n",
            "正文。[^1]更多。[^2]\n\n[^1]: 注释一。\n\n[^2]: 注释二。\n\n    续段。\n",
        )
        self.assertTrue(ok, msg=out)

    def test_fails_when_reference_is_dropped(self):
        ok, out = self._validate("Text.[^1]\n\n[^1]: Note.\n", "正文。\n\n[^1]: 注释。\n")
        self.assertFalse(ok)
        self.assertIn("output_chunk0001.md", out)
        self.assertIn("missing references [^N] (count): [('1', 1)]", out)

    def test_fails_when_definition_is_renumbered(self):
        ok, out = self._validate("Text.[^7]\n\n[^7]: Note.\n", "正文。[^7]\n\n[^1]: 注释。\n")
        self.assertFalse(ok)
        self.assertIn("missing definitions [^N]: (count): [('7', 1)]", out)
        self.assertIn("extra   definitions [^N]: (count): [('1', 1)]", out)

    def test_reference_followed_by_a_colon_is_not_a_definition(self):
        ok, out = self._validate("As noted[^3]: the rest.\n", "如前所述[^3]：其余。\n")
        self.assertTrue(ok, msg=out)

    def test_ignores_pseudo_footnotes_from_older_conversions(self):
        ok, out = self._validate("Text.[^1^]\n\n[^1^] Note.\n", "正文。^1^\n\n^1^ 注释。\n")
        self.assertTrue(ok, msg=out)

    def test_merge_fails_and_removes_stale_output_on_footnote_divergence(self):
        from manifest import create_manifest

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            self._write(temp_dir / "input.md", "Text.[^1]\n\n[^1]: Note.\n")
            self._write(temp_dir / "chunk0001.md", "Text.[^1]\n\n[^1]: Note.\n")
            self._write(temp_dir / "output_chunk0001.md", "正文。\n\n注释。\n")
            create_manifest(str(temp_dir), ["chunk0001.md"], str(temp_dir / "input.md"))
            self._write(temp_dir / "output.md", "stale merged content")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = merge_and_build.merge_markdown_files(str(temp_dir))

            self.assertFalse(ok)
            self.assertFalse((temp_dir / "output.md").exists())
            self.assertIn("Footnote validation failed", buf.getvalue())

    @unittest.skipUnless(merge_and_build.MARKDOWN_AVAILABLE, "python-markdown is not installed")
    def test_python_markdown_fallback_renders_footnotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "output.md"
            html = Path(tmp) / "output.html"
            self._write(md, "Text.[^1]\n\n[^1]: Note.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(merge_and_build.convert_with_python_markdown(str(md), str(html), "T"))
            rendered = html.read_text(encoding="utf-8")
        self.assertIn('class="footnote"', rendered)
        self.assertNotIn("[^1]", rendered)


class FigureTextValidationTests(unittest.TestCase):
    SOURCE = (
        "![Flow](images/flow.png)\n\n"
        '::: {.figure-text image="images/flow.png"}\n- Client\n- Load balancer\n:::\n\n'
        "After.\n"
    )
    TRANSLATED = (
        "![流程](images/flow.png)\n\n"
        '::: {.figure-text image="images/flow.png"}\n- 客户端\n- 负载均衡器\n:::\n\n'
        "之后。\n"
    )

    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def _validate(self, source, output):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(Path(temp_dir) / "chunk0001.md", source)
            self._write(Path(temp_dir) / "output_chunk0001.md", output)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = merge_and_build._validate_chunk_figure_text(temp_dir)
        return ok, buf.getvalue()

    def test_passes_when_only_the_items_are_translated(self):
        ok, out = self._validate(self.SOURCE, self.TRANSLATED)
        self.assertTrue(ok, msg=out)

    def test_passes_without_figure_text(self):
        ok, out = self._validate("Text.\n", "正文。\n")
        self.assertTrue(ok, msg=out)

    def test_fails_when_the_closing_fence_is_lost(self):
        ok, out = self._validate(self.SOURCE, self.TRANSLATED.replace("\n:::\n", "\n：：：\n"))
        self.assertFalse(ok)
        self.assertIn("blocks without a closing ::: line: 1", out)

    def test_fails_when_the_opening_line_is_altered(self):
        altered = self.TRANSLATED.replace('image="images/flow.png"}', 'image="图片/flow.png"}')
        ok, out = self._validate(self.SOURCE, altered)
        self.assertFalse(ok)
        self.assertIn("""missing opening lines (count): [('::: {.figure-text image="images/flow.png"}', 1)]""", out)
        self.assertIn("""extra   opening lines (count): [('::: {.figure-text image="图片/flow.png"}', 1)]""", out)

    def test_fails_when_the_output_predates_the_injected_text(self):
        ok, out = self._validate(self.SOURCE, "![流程](images/flow.png)\n\n之后。\n")
        self.assertFalse(ok)
        self.assertIn("translate it again", out)

    def test_merge_fails_and_removes_stale_output_on_divergence(self):
        from manifest import create_manifest

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            self._write(temp_dir / "input.md", self.SOURCE)
            self._write(temp_dir / "chunk0001.md", self.SOURCE)
            self._write(temp_dir / "output_chunk0001.md", self.TRANSLATED.replace("\n:::\n", "\n"))
            with contextlib.redirect_stdout(io.StringIO()):
                create_manifest(str(temp_dir), ["chunk0001.md"], str(temp_dir / "input.md"))
            self._write(temp_dir / "output.md", "stale merged content")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ok = merge_and_build.merge_markdown_files(str(temp_dir))

            self.assertFalse(ok)
            self.assertFalse((temp_dir / "output.md").exists())
            self.assertIn("Figure text validation failed", buf.getvalue())


class ImageValidationTests(unittest.TestCase):
    """Validates _validate_chunk_images, _check_generated_html_sanity, and the
    basic-regex alt-escape fix. Together these guard against subagent-produced
    malformed <img> tags surviving into the final HTML."""

    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def _run_validator(self, temp_dir):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = merge_and_build._validate_chunk_images(temp_dir)
        return ok, buf.getvalue()

    def _run_html_sanity(self, html_path):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = merge_and_build._check_generated_html_sanity(html_path)
        return ok, buf.getvalue()

    def test_passes_for_clean_chunks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                'Hello <img src="images/a.png" alt="A"> and ![fig](images/b.png).',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                'Translated <img src="images/a.png" alt="译"> and ![图](images/b.png).',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_fails_on_unescaped_double_quote_in_alt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                'Bottle <img src="images/a.png" alt="Drink Me bottle">',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '瓶子 <img src="images/a.png" alt="标着"喝我"的瓶子">',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("output_chunk0001.md", out)
            self.assertIn("malformed <img>", out)

    def test_passes_for_curly_quote_alt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                '<img src="images/a.png" alt="Drink Me bottle">',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '<img src="images/a.png" alt="标着“喝我”的瓶子">',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_passes_for_html_entity_alt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                '<img src="images/a.png" alt="Drink Me">',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '<img src="images/a.png" alt="标着&quot;喝我&quot;的瓶子">',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_fails_on_missing_image_src(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                '<img src="images/a.png" alt="A"> some text <img src="images/b.png" alt="B">',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '<img src="images/a.png" alt="译"> 译文',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("images/b.png", out)
            self.assertIn("missing <img src>", out)

    def test_fails_on_changed_src_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                '<img src="images/000034.png" alt="orig">',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '<img src="images/000035.png" alt="译">',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("images/000034.png", out)
            self.assertIn("images/000035.png", out)

    def test_fails_on_repeated_image_dropped_to_one(self):
        # Counter-based regression: source has same src twice, translated has it once.
        # A set-based comparison would miss this.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                '<img src="images/a.png" alt="first"> middle <img src="images/a.png" alt="second">',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '<img src="images/a.png" alt="译一"> 中间译文',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("images/a.png", out)

    def test_fails_when_output_md_uptodate_but_chunks_bad(self):
        # Regression test for cache-bypass: even if output.md exists and is "up to date",
        # bad chunks must still cause merge_markdown_files() to fail, and the stale
        # output.md must be removed.
        with tempfile.TemporaryDirectory() as temp_dir:
            chunk = Path(temp_dir) / "chunk0001.md"
            out_chunk = Path(temp_dir) / "output_chunk0001.md"
            output_md = Path(temp_dir) / "output.md"

            self._write(chunk, '<img src="images/a.png" alt="A">')
            self._write(out_chunk, '<img src="images/a.png" alt="标"喝我"">')
            self._write(output_md, "stale merged content")

            os.utime(chunk, (100, 100))
            os.utime(out_chunk, (150, 150))
            os.utime(output_md, (200, 200))  # newer than chunks

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = merge_and_build.merge_markdown_files(temp_dir)
            out = buf.getvalue()

            self.assertFalse(result)
            self.assertFalse(output_md.exists(), msg=f"stale output.md should be deleted; stdout=\n{out}")
            self.assertIn("output_chunk0001.md", out)

    def test_passes_when_code_block_preserves_broken_img_example(self):
        # Regression: a tech book may legitimately ship a fenced code block that
        # demonstrates a deliberately-broken <img> tag. Both source and output
        # carry the same example, so the per-chunk delta is empty — must pass.
        broken_example = (
            "Here is a buggy tag to demonstrate the parser:\n\n"
            "```html\n"
            '<img src="x.png" alt="he said "hi" loudly">\n'
            "```\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(Path(temp_dir) / "chunk0001.md", broken_example)
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                broken_example.replace("Here is a buggy tag to demonstrate the parser:",
                                       "下面这个有 bug 的标签是用来演示解析器的："),
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_fails_when_output_introduces_new_broken_img(self):
        # If source had one broken example and output adds a *second* broken tag
        # (not present in source), the new bad attr shows up in the delta and
        # we must flag it — even when there's a baseline of broken attrs.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                "Demo block:\n\n"
                "```html\n"
                '<img src="x.png" alt="he said "hi" loudly">\n'
                "```\n\n"
                'And a real image: <img src="images/a.png" alt="A">\n',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                "演示代码块：\n\n"
                "```html\n"
                '<img src="x.png" alt="he said "hi" loudly">\n'
                "```\n\n"
                # New corruption: subagent broke the real image alt with unescaped quote
                '真实图片：<img src="images/a.png" alt="标着"喝我"">\n',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("introduced malformed <img>", out)
            self.assertIn("output_chunk0001.md", out)

    def test_fails_when_real_image_replaced_with_escaped_markdown(self):
        # Regression: a regex that didn't honor `\!` would count `\![](path)` as
        # an image, masking real loss when the subagent escaped it accidentally.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                "See ![Fig 1](images/a.png) for details.",
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                "见 \\![图 1](images/a.png) 了解详情。",
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("missing ![](path)", out)
            self.assertIn("images/a.png", out)

    def test_passes_when_both_chunks_have_escaped_markdown_image(self):
        # Symmetric case: both chunks intentionally use `\![...]` as literal text.
        # Neither counts as a real image; counts match.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                "Use \\![alt](path) syntax to write the example as text.",
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                "用 \\![alt](path) 语法把示例写成文本。",
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_fails_when_markdown_image_missing_closing_paren(self):
        # Regression: the regex must require the closing `)` — a fragment like
        # `![图](images/a.png` does NOT render as an image, so counting it as a
        # preserved reference would mask real image loss.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                "See ![Fig 1](images/a.png) for details.",
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                "见 ![图 1](images/a.png 了解详情。",  # missing closing )
            )
            ok, out = self._run_validator(temp_dir)
            self.assertFalse(ok)
            self.assertIn("missing ![](path)", out)
            self.assertIn("images/a.png", out)

    def test_passes_for_markdown_image_with_title(self):
        # Forward-compatibility: standard `![alt](url "title")` syntax must keep
        # parsing as a single image when both chunks preserve the title.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                'Look at ![Fig 1](images/a.png "Diagram of the system") below.',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '看下方 ![图 1](images/a.png "系统示意图")。',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_passes_with_gt_in_quoted_alt(self):
        # Regression: a regex like <img\b[^>]*> would truncate at the first `>`
        # inside a quoted attribute value, producing a false-positive malformed
        # report for legitimate math/comparison content. HTMLParser handles this.
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write(
                Path(temp_dir) / "chunk0001.md",
                'Compare <img src="images/a.png" alt="x > y and a < b"> here.',
            )
            self._write(
                Path(temp_dir) / "output_chunk0001.md",
                '比较 <img src="images/a.png" alt="x > y 且 a < b"> 这里。',
            )
            ok, out = self._run_validator(temp_dir)
            self.assertTrue(ok, msg=out)

    def test_html_canary_passes_when_prose_mentions_img_tag(self):
        # Regression: a book that legitimately discusses HTML will render `<img>`
        # mentions as `&lt;img&gt;` in prose or inside <pre><code>. That is not
        # corruption and must not block the build.
        with tempfile.TemporaryDirectory() as temp_dir:
            html_file = Path(temp_dir) / "book.html"
            self._write(
                html_file,
                "<html><body>"
                "<p>The &lt;img&gt; tag is used for inline images.</p>"
                "<pre><code>&lt;img src=&quot;example.png&quot;&gt;</code></pre>"
                "<p>Here is a real image: <img src=\"images/real.png\" alt=\"real\"></p>"
                "</body></html>",
            )
            ok, out = self._run_html_sanity(str(html_file))
            self.assertTrue(ok, msg=out)

    def test_basic_regex_escapes_alt_with_quote(self):
        # The basic-regex fallback used to inline alt verbatim, so a literal " in
        # markdown alt text would produce malformed raw <img>. Now it html.escapes.
        with tempfile.TemporaryDirectory() as temp_dir:
            md_file = Path(temp_dir) / "in.md"
            html_file = Path(temp_dir) / "out.html"
            self._write(md_file, '![Title with "quote" inside](images/x.png)\n')

            ok = merge_and_build.convert_with_basic_regex(str(md_file), str(html_file), "t")
            self.assertTrue(ok)

            html_text = html_file.read_text(encoding="utf-8")
            self.assertIn("&quot;", html_text)
            self.assertNotIn('alt="Title with "quote"', html_text)

            sanity_ok, sanity_out = self._run_html_sanity(str(html_file))
            self.assertTrue(sanity_ok, msg=sanity_out)


class MetadataTitleTests(unittest.TestCase):
    def test_strips_wrapping_book_title_marks(self):
        self.assertEqual(merge_and_build._metadata_title("《财阀、先知、主帅与君王》"), "财阀、先知、主帅与君王")
        self.assertEqual(merge_and_build._metadata_title("  Plain Title "), "Plain Title")

    def test_keeps_marks_that_do_not_wrap_the_whole_title(self):
        title = "《A》与《B》"
        self.assertEqual(merge_and_build._metadata_title(title), title)


class _HeadReader(HTMLParser):
    """Reads <html lang>, <title> and <meta name="author"> the way an HTML parser does."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = ""
        self.authors = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.lang = attrs.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and attrs.get("name") == "author":
            self.authors.append(attrs.get("content"))

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


class TemplateMetadataTests(unittest.TestCase):
    # Characters that are special in HTML, and a backslash, which re.sub
    # reads as a group reference in a replacement string.
    TITLE = "O'Brien's \"Third\" Policeman & List<T>"
    AUTHOR = "Flann O'Brien & \"Myles\" <na gCopaleen> \\1"

    def test_title_and_author_parse_back_unchanged(self):
        for template in ("template_ebook.html", "template.html"):
            with self.subTest(template=template), tempfile.TemporaryDirectory() as temp_dir:
                out = Path(temp_dir) / "book.html"
                ok = merge_and_build.apply_template_to_html(
                    "<p>x</p>", str(SCRIPT_DIR / template), str(out),
                    self.TITLE, merge_and_build.get_lang_config("en"), self.AUTHOR,
                )

                self.assertTrue(ok)
                reader = _HeadReader()
                reader.feed(out.read_text(encoding="utf-8"))
                self.assertEqual(reader.authors, [self.AUTHOR])
                self.assertEqual(reader.title, self.TITLE)


class TemplatePlaceholderTests(unittest.TestCase):
    # A book about templates can show the placeholders in its text and title.
    BODY = "<pre><code>$title$ $lang$ $body_font$ $toc_label$ $body$ {{content}}</code></pre>"
    TITLE = "Writing $title$, $lang$ and $body$"

    def test_placeholders_in_the_book_text_are_kept(self):
        for template in ("template_ebook.html", "template.html"):
            with self.subTest(template=template), tempfile.TemporaryDirectory() as temp_dir:
                out = Path(temp_dir) / "book.html"
                ok = merge_and_build.apply_template_to_html(
                    self.BODY, str(SCRIPT_DIR / template), str(out),
                    self.TITLE, merge_and_build.get_lang_config("de"),
                )

                self.assertTrue(ok)
                html = out.read_text(encoding="utf-8")
                self.assertEqual(html.count(self.BODY), 1)
                reader = _HeadReader()
                reader.feed(html)
                self.assertEqual(reader.title, self.TITLE)
                self.assertEqual(reader.lang, "de")
                rest = html.replace(self.BODY, "").replace(self.TITLE, "")
                self.assertNotRegex(rest, r"\$(title|lang|body_font|toc_label|body)\$")


class LangConfigTests(unittest.TestCase):
    def test_table_codes_keep_their_entries(self):
        for code, entry in merge_and_build.LANG_CONFIG.items():
            with self.subTest(code=code):
                self.assertIs(merge_and_build.get_lang_config(code), entry)
        self.assertEqual(merge_and_build.get_lang_config("zh")["lang_attr"], "zh-CN")

    def test_any_language_tag_is_used_as_given(self):
        table = merge_and_build.LANG_CONFIG
        default = merge_and_build._DEFAULT_LANG_CONFIG
        # Fonts and the TOC label come from the entry for the primary subtag.
        cases = {
            "it": ("it", default),
            "ru": ("ru", default),
            "pt-BR": ("pt-BR", default),
            "pt_BR": ("pt-BR", default),
            "zh-SG": ("zh-SG", table["zh"]),
            "en-GB": ("en-GB", table["en"]),
        }
        for code, (lang_attr, entry) in cases.items():
            with self.subTest(code=code):
                self.assertEqual(merge_and_build.get_lang_config(code), dict(entry, lang_attr=lang_attr))

    def test_traditional_chinese_gets_its_toc_label(self):
        zh = merge_and_build.LANG_CONFIG["zh"]
        # A script subtag decides; without one, TW, HK and MO are Traditional.
        cases = {
            "zh-TW": "目錄", "zh_TW": "目錄", "zh-hk": "目錄", "zh-MO": "目錄",
            "zh-Hant": "目錄", "zh-Hant-CN": "目錄", "zh-Hant-HK": "目錄",
            "zh-CN": "目录", "zh-SG": "目录", "zh-Hans": "目录", "zh-Hans-TW": "目录", "zh-Hans-HK": "目录",
        }
        for code, label in cases.items():
            with self.subTest(code=code):
                self.assertEqual(
                    merge_and_build.get_lang_config(code),
                    dict(zh, lang_attr=code.replace("_", "-"), toc_label=label),
                )
        self.assertIs(merge_and_build.get_lang_config("zh"), zh)

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "book.html"
            self.assertTrue(merge_and_build.apply_template_to_html(
                "<p>x</p>", str(SCRIPT_DIR / "template.html"), str(out),
                "Title", merge_and_build.get_lang_config("zh-TW"),
            ))
            self.assertIn("<h3>目錄</h3>", out.read_text(encoding="utf-8"))

    def test_a_value_that_is_not_a_language_code_falls_back_to_en(self):
        for value in ("Italian", "Traditional Chinese", "", 'it" onload="x'):
            with self.subTest(value=value):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    cfg = merge_and_build.get_lang_config(value)
                self.assertIs(cfg, merge_and_build._DEFAULT_LANG_CONFIG)
                self.assertIn(f"'{value}' is not a language code", out.getvalue())

    def test_html_lang_is_the_language_tag(self):
        for code, lang in (("zh", "zh-CN"), ("it", "it"), ("zh-TW", "zh-TW"), ("pt_BR", "pt-BR")):
            for template in ("template_ebook.html", "template.html"):
                with self.subTest(code=code, template=template), tempfile.TemporaryDirectory() as temp_dir:
                    out = Path(temp_dir) / "book.html"
                    ok = merge_and_build.apply_template_to_html(
                        "<p>x</p>", str(SCRIPT_DIR / template), str(out),
                        "Title", merge_and_build.get_lang_config(code),
                    )

                    self.assertTrue(ok)
                    reader = _HeadReader()
                    reader.feed(out.read_text(encoding="utf-8"))
                    self.assertEqual(reader.lang, lang)

    def test_main_passes_the_tag_to_the_html_and_calibre(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "config.txt").write_text("output_lang=pt-BR\n", encoding="utf-8")
            with mock.patch.object(
                merge_and_build, "merge_markdown_files", return_value=True
            ), mock.patch.object(
                merge_and_build, "convert_md_to_html", return_value=True
            ) as html_mock, mock.patch.object(
                merge_and_build, "add_toc", return_value=True
            ), mock.patch.object(
                merge_and_build, "generate_formats", return_value=True
            ) as formats_mock, mock.patch.object(
                sys, "argv", ["merge_and_build.py", "--temp-dir", temp_dir]
            ), contextlib.redirect_stdout(io.StringIO()):
                merge_and_build.main()

        self.assertEqual(html_mock.call_args.args[2]["lang_attr"], "pt-BR")
        self.assertEqual(formats_mock.call_args.args[1], "pt-BR")


class PdfRetryTests(unittest.TestCase):
    def test_generate_formats_retries_pdf_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "book_doc.html").write_text("<html></html>", encoding="utf-8")
            attempts = {".pdf": 0}

            def fake_generate_format(html_file, temp_dir_, ext, lang_attr, cover=None):
                path = os.path.join(temp_dir_, "book" + ext)
                if ext == ".pdf":
                    attempts[".pdf"] += 1
                    if attempts[".pdf"] == 1:
                        return None
                Path(path).write_bytes(b"x")
                return path

            with mock.patch.object(merge_and_build, "generate_format", side_effect=fake_generate_format), \
                    contextlib.redirect_stdout(io.StringIO()):
                ok = merge_and_build.generate_formats(temp_dir, "zh-CN")

            self.assertTrue(ok)
            self.assertEqual(attempts[".pdf"], 2)


class HeadingOutlineTests(unittest.TestCase):
    def _write_chunks(self, temp_dir, chunks):
        for name, text in chunks.items():
            Path(temp_dir, name).write_text(text, encoding="utf-8")

    def test_lists_headings_in_book_order_without_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_chunks(temp_dir, {
                "chunk0001.md": "# Source chunks are not listed\n",
                "output_chunk0001.md": (
                    "# 第一章 标题\n"
                    "\n"
                    "```bash\n"
                    "# install deps\n"
                    "```\n"
                    "\n"
                    "~~~python\n"
                    "# comment\n"
                    "~~~\n"
                    "\n"
                    "#hashtag 不是标题\n"
                    "\n"
                    "   ## 缩进的不是标题\n"
                    "\n"
                    "## 第一节\n"
                ),
                # An unclosed fence hides the rest of its own chunk only.
                "output_chunk0002.md": "```\n# still code\n",
                "output_chunk10000.md": "## 第三章\n",
                "output_chunk9999.md": "## 第二章\n",
            })

            self.assertEqual(merge_and_build.heading_outline(temp_dir), [
                "output_chunk0001.md:1: # 第一章 标题",
                "output_chunk0001.md:15: ## 第一节",
                "output_chunk9999.md:1: ## 第二章",
                "output_chunk10000.md:1: ## 第三章",
            ])

    def test_stray_output_chunk_name_stops_the_outline(self):
        for name in ("output_chunk2.md", "output_chunk$(touch x).md", "output_chunk0001 .md"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                self._write_chunks(temp_dir, {"output_chunk0001.md": "# 标题\n", name: "# x\n"})
                out = io.StringIO()
                with mock.patch.object(
                    sys, "argv", ["merge_and_build.py", "--temp-dir", temp_dir, "--headings"]
                ), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                    merge_and_build.main()

                self.assertEqual(raised.exception.code, 1)
                self.assertIn(f"Error: unexpected file {name!r}", out.getvalue())

    def test_main_prints_outline_without_building(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._write_chunks(temp_dir, {"output_chunk0001.md": "# 标题\n\n```\n# code\n```\n"})
            out = io.StringIO()
            with mock.patch.object(
                sys, "argv", ["merge_and_build.py", "--temp-dir", temp_dir, "--headings"]
            ), contextlib.redirect_stdout(out):
                merge_and_build.main()

            self.assertEqual(out.getvalue(), "output_chunk0001.md:1: # 标题\n")
            self.assertEqual(os.listdir(temp_dir), ["output_chunk0001.md"])



class NameFilesTests(unittest.TestCase):
    BOOK_TEXT = "O'Brien's \"tale\" $(touch pwned) `touch pwned-tick` $HOME"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.temp_dir = str(self.root / "book_temp")
        os.mkdir(self.temp_dir)
        self.cover = self.root / "Cover.PNG"
        self.cover.write_bytes(b"png")
        Path(self.temp_dir, "chunk0001.md").write_text("Body\n", encoding="utf-8")
        Path(self.temp_dir, "output_chunk0001.md").write_text("正文\n", encoding="utf-8")
        Path(self.temp_dir, "config.txt").write_text(
            "output_lang=zh\noriginal_title=Original\ncreator=Config Author\n", encoding="utf-8")

    def build(self, *args, out=None):
        calls = {}

        def html(temp_dir, title, lang_cfg, author):
            calls.update(title=title, author=author)
            return True

        def formats(temp_dir, lang_attr, cover=None):
            calls["cover"] = cover
            return True

        def aliases(temp_dir, export_name):
            calls["export_name"] = export_name
            return []

        out = out or io.StringIO()
        argv = ["merge_and_build.py", "--temp-dir", self.temp_dir, *args]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(merge_and_build, "merge_markdown_files", return_value=True), \
                mock.patch.object(merge_and_build, "convert_md_to_html", side_effect=html), \
                mock.patch.object(merge_and_build, "add_toc"), \
                mock.patch.object(merge_and_build, "generate_formats", side_effect=formats), \
                mock.patch.object(merge_and_build, "export_named_aliases", side_effect=aliases), \
                contextlib.redirect_stdout(out):
            merge_and_build.main()
        return calls, out.getvalue()

    def write_stored_request(self, cover):
        request = {"input": "/books/a.epub", "target_lang": "zh", "temp_root": None,
                   "cover": cover, "parser": None}
        Path(self.temp_dir, "request.json").write_text(json.dumps(request), encoding="utf-8")

    def test_reads_title_author_export_name_and_cover_from_the_temp_dir(self):
        Path(self.temp_dir, "title.txt").write_text(f"《{self.BOOK_TEXT}》\n", encoding="utf-8")
        Path(self.temp_dir, "author.txt").write_text(self.BOOK_TEXT + "\n", encoding="utf-8")
        Path(self.temp_dir, "export_name.txt").write_text(self.BOOK_TEXT + "\n", encoding="utf-8")
        self.write_stored_request(str(self.cover))

        calls, _ = self.build()

        self.assertEqual(calls, {"title": self.BOOK_TEXT, "author": self.BOOK_TEXT,
                                 "cover": str(self.cover), "export_name": self.BOOK_TEXT})

    def test_cover_comes_only_from_the_request_or_the_command_line(self):
        for name in ("cover.png", "cover.jpg"):
            Path(self.temp_dir, name).write_bytes(b"not used")
        calls, _ = self.build()
        self.assertIsNone(calls["cover"])
        self.write_stored_request(None)
        calls, _ = self.build()
        self.assertIsNone(calls["cover"])
        self.write_stored_request(str(self.cover))
        other = self.root / "other.jpg"
        other.write_bytes(b"jpg")
        calls, _ = self.build("--cover", str(other))
        self.assertEqual(calls["cover"], str(other))

    def test_without_files_uses_config_and_makes_no_copies(self):
        calls, _ = self.build()
        self.assertEqual(calls, {"title": "Original", "author": "Config Author", "cover": None})

    def test_command_line_values_win(self):
        Path(self.temp_dir, "title.txt").write_text("From file\n", encoding="utf-8")
        calls, _ = self.build("--title", "From flag")
        self.assertEqual(calls["title"], "From flag")

    def test_blank_name_files_count_as_missing(self):
        for name in ("title.txt", "author.txt", "export_name.txt"):
            Path(self.temp_dir, name).write_text(" \n", encoding="utf-8")
        calls, _ = self.build()
        self.assertEqual(calls, {"title": "Original", "author": "Config Author", "cover": None})

    def test_unusable_files_stop_the_build(self):
        request = {"input": "/books/a.epub", "target_lang": "zh", "temp_root": None,
                   "cover": str(self.cover), "parser": None}
        cases = [("title.txt", b"\xff\xfe", "not UTF-8"),
                 ("export_name.txt", "a/b".encode(), "export"),
                 ("request.json", b'{"my": "notes"}', "request.json"),
                 ("request.json", b"{", "request.json"),
                 ("request.json", json.dumps(dict(request, parser_args=[])).encode(), "request.json"),
                 ("request.json", json.dumps(dict(request, cover="cover.png")).encode(), "request.json"),
                 ("request.json", json.dumps(dict(request, cover=str(self.root / "gone.png"))).encode(),
                  "Cover image not found")]
        for name, data, needle in cases:
            with self.subTest(name=name, data=data[:40]):
                Path(self.temp_dir, name).write_bytes(data)
                out = io.StringIO()
                with self.assertRaises(SystemExit) as raised:
                    self.build(out=out)
                self.assertEqual(raised.exception.code, 1)
                self.assertIn(needle, out.getvalue())
                os.unlink(os.path.join(self.temp_dir, name))


if __name__ == "__main__":
    unittest.main()
