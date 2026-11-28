import process_guard  # noqa: F401  first, so it guards every process the tests below start
import contextlib
import errno
import hashlib
import inspect
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
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


def _content(path):
    """The size and SHA-256 of the file at path, which build_outputs.json records."""
    data = Path(path).read_bytes()
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _recorded(temp_dir):
    """The files build_outputs.json lists, or None if there is no such file."""
    path = Path(temp_dir) / merge_and_build.OUTPUTS_RECORD
    return json.loads(path.read_text(encoding="utf-8"))["files"] if path.exists() else None


def _build(temp_dir, files):
    """Write files, a {name: bytes} dict, in temp_dir as a build step does, and record them as it does."""
    for name, data in files.items():
        (Path(temp_dir) / name).write_bytes(data)
    merge_and_build._record_outputs(str(temp_dir), *files)


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
                record = {merge_and_build.OUTPUTS_RECORD} if copied else set()
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

    def test_the_copies_of_every_export_name_are_recorded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                self._write(root / name, name)

            first = merge_and_build.export_named_aliases(temp_dir, "Old Title")
            second = merge_and_build.export_named_aliases(temp_dir, "New Title")

            self.assertEqual(set(_recorded(root)), set(first + second))
            for name, entry in _recorded(root).items():
                self.assertEqual(entry, _content(root / name))

    def test_copies_made_before_one_fails_are_recorded_and_that_one_is_not(self):
        canonical = ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]
        for earlier in [None, b"the copy an earlier build made"]:
            with self.subTest(earlier=earlier), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                for name in canonical:
                    self._write(root / name, name)
                if earlier is not None:
                    (root / "My Book.docx").write_bytes(earlier)
                real_copy2 = shutil.copy2

                def disk_full_at_docx(src, dst, *args, **kwargs):
                    if os.path.basename(src) == "book.docx":
                        Path(dst).write_bytes(b"half a copy")
                        raise OSError(errno.ENOSPC, "No space left on device")
                    return real_copy2(src, dst, *args, **kwargs)

                with mock.patch.object(shutil, "copy2", disk_full_at_docx), self.assertRaises(OSError):
                    merge_and_build.export_named_aliases(temp_dir, "My Book")

                self.assertEqual(set(_recorded(root)), {"My Book.html", "My Book_doc.html"})
                if earlier is None:
                    self.assertFalse(os.path.lexists(root / "My Book.docx"))
                else:
                    self.assertEqual((root / "My Book.docx").read_bytes(), earlier)
                self.assertEqual({p.name for p in root.iterdir()},
                                 set(canonical) | {"My Book.html", "My Book_doc.html", merge_and_build.OUTPUTS_RECORD}
                                 | ({"My Book.docx"} if earlier is not None else set()))

    def test_a_symlink_under_a_copy_name_is_replaced_and_its_target_left(self):
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(temp_dir)
            for name in ["book.html", "book_doc.html", "book.docx", "book.epub", "book.pdf"]:
                self._write(root / name, name)
            target = Path(elsewhere) / "mine.pdf"
            target.write_bytes(b"the user's file")
            (root / "My Book.pdf").symlink_to(target)

            merge_and_build.export_named_aliases(temp_dir, "My Book")

            self.assertEqual(target.read_bytes(), b"the user's file")
            self.assertFalse((root / "My Book.pdf").is_symlink())
            self.assertEqual(_recorded(root)["My Book.pdf"], _content(root / "My Book.pdf"))
            self.assertEqual((root / "My Book.pdf").read_text(encoding="utf-8"), "book.pdf")

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
                self.assertEqual(set(_recorded(root)), set(copied))

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

class OutputsRecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.temp_dir = self.root / "book_temp"
        self.temp_dir.mkdir()

    def assert_recorded_as_they_are(self, names):
        files = _recorded(self.temp_dir)
        self.assertEqual(set(files), set(names))
        for name, entry in files.items():
            with self.subTest(name=name):
                self.assertEqual(entry, _content(self.temp_dir / name))

    def test_records_what_a_step_writes(self):
        _build(self.temp_dir, {"book.pdf": b"pdf", "book.epub": b"epub"})
        self.assert_recorded_as_they_are(["book.pdf", "book.epub"])

    def test_outputs_a_step_skips_as_up_to_date_are_not_recorded(self):
        # Files of the user's under output names, which the build takes for its own up-to-date outputs.
        (self.temp_dir / "output.md").write_text("# One\n", encoding="utf-8")
        (self.temp_dir / "book_doc.html").write_text("<html><body><p>mine</p></body></html>", encoding="utf-8")
        (self.temp_dir / "book.pdf").write_bytes(b"mine")
        os.utime(self.temp_dir / "output.md", (100, 100))
        for name in ["book_doc.html", "book.pdf"]:
            os.utime(self.temp_dir / name, (200, 200))

        with mock.patch.object(merge_and_build.subprocess, "run") as run_mock, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(merge_and_build.convert_md_to_html(
                str(self.temp_dir), "Title", merge_and_build.get_lang_config("zh")))
            self.assertTrue(merge_and_build.generate_format(
                str(self.temp_dir / "book_doc.html"), str(self.temp_dir), ".pdf", "zh-CN"))

        run_mock.assert_not_called()
        self.assertIsNone(_recorded(self.temp_dir))

    def test_html_is_recorded_once_it_is_made_and_not_when_the_step_fails(self):
        (self.temp_dir / "output.md").write_text("# One\n\nText.\n", encoding="utf-8")
        for sane in [False, True]:
            with self.subTest(sane=sane), \
                    mock.patch.object(merge_and_build, "check_pandoc_available", return_value=False), \
                    mock.patch.object(merge_and_build, "_check_generated_html_sanity", return_value=sane), \
                    contextlib.redirect_stdout(io.StringIO()):
                made = merge_and_build.convert_md_to_html(
                    str(self.temp_dir), "Title", merge_and_build.get_lang_config("zh"))

            self.assertEqual(made, sane)
            for name in ["output.html", "book_doc.html", "book.html"]:
                self.assertTrue((self.temp_dir / name).is_file())
            if sane:
                self.assert_recorded_as_they_are(["output.html", "book_doc.html", "book.html"])
            else:
                self.assertIsNone(_recorded(self.temp_dir))
                for name in ["output.html", "book_doc.html", "book.html"]:
                    (self.temp_dir / name).unlink()

    @unittest.skipIf(os.geteuid() == 0, "root writes files whatever their permissions")
    def test_html_is_not_recorded_when_a_template_cannot_be_written(self):
        mine = "<html><body><p>mine</p></body></html>"
        for name in ["book_doc.html", "book.html"]:
            with self.subTest(name=name):
                temp_dir = self.root / name.replace(".", "_")
                temp_dir.mkdir()
                (temp_dir / "output.md").write_text("# One\n\nText.\n", encoding="utf-8")
                (temp_dir / name).write_text(mine, encoding="utf-8")
                os.utime(temp_dir / name, (100, 100))
                (temp_dir / name).chmod(0o444)

                with mock.patch.object(merge_and_build, "check_pandoc_available", return_value=False), \
                        contextlib.redirect_stdout(io.StringIO()):
                    made = merge_and_build.convert_md_to_html(
                        str(temp_dir), "Title", merge_and_build.get_lang_config("zh"))

                self.assertFalse(made)
                self.assertEqual((temp_dir / name).read_text(encoding="utf-8"), mine)
                self.assertIsNone(_recorded(temp_dir))

    def test_html_is_not_recorded_when_the_separator_pass_fails(self):
        (self.temp_dir / "output.md").write_text("# One\n\nText.\n", encoding="utf-8")

        def disk_full(html_file):
            Path(html_file).write_text("<html><body><h1>On", encoding="utf-8")
            return False

        with mock.patch.object(merge_and_build, "check_pandoc_available", return_value=False), \
                mock.patch.object(merge_and_build, "process_html_separators", side_effect=disk_full), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(merge_and_build.convert_md_to_html(
                str(self.temp_dir), "Title", merge_and_build.get_lang_config("zh")))

        self.assertFalse((self.temp_dir / "book.html").exists())
        self.assertIsNone(_recorded(self.temp_dir))

    def test_the_toc_step_records_book_html_only_when_it_rewrites_it(self):
        page = '<html><body><div class="toc-content"></div><h1>One</h1><p>1</p></body></html>'
        _build(self.temp_dir, {"book.html": page.encode()})
        built = _recorded(self.temp_dir)
        (self.temp_dir / "book.html").write_text(page.replace("toc-content", "no-toc"), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(merge_and_build.add_toc(str(self.temp_dir)))
        self.assertEqual(_recorded(self.temp_dir), built)

        (self.temp_dir / "book.html").write_text(page, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(merge_and_build.add_toc(str(self.temp_dir)))
        self.assertIn('href="#', (self.temp_dir / "book.html").read_text(encoding="utf-8"))
        self.assert_recorded_as_they_are(["book.html"])
        self.assertNotEqual(_recorded(self.temp_dir), built)

    def test_generate_formats_records_only_the_formats_it_made(self):
        (self.temp_dir / "book_doc.html").write_text("<html></html>", encoding="utf-8")
        made = []

        def calibre(cmd, check, capture_output, text):
            output = Path(cmd[4])
            made.append(output.name)
            if output.name == "book.pdf" and made.count("book.pdf") == 1:
                raise subprocess.CalledProcessError(1, cmd, "", "GPU error")
            output.write_bytes(b"made from " + cmd[2].encode())
            if output.name == "book.epub":
                raise subprocess.CalledProcessError(1, cmd, "", "half written")
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(merge_and_build.subprocess, "run", side_effect=calibre), \
                contextlib.redirect_stdout(io.StringIO()):
            ok = merge_and_build.generate_formats(str(self.temp_dir), "zh-CN")

        self.assertFalse(ok)
        self.assertEqual(made, ["book.docx", "book.epub", "book.pdf", "book.pdf"])
        # The EPUB step failed, so what it left is not recorded; the PDF retry succeeded.
        self.assertTrue((self.temp_dir / "book.epub").is_file())
        self.assert_recorded_as_they_are(["book.docx", "book.pdf"])

    def test_main_records_every_output_and_export_copy(self):
        from manifest import create_manifest

        (self.temp_dir / "chunk0001.md").write_text("# One\n\nText.\n", encoding="utf-8")
        (self.temp_dir / "input.md").write_text("# One\n\nText.\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            create_manifest(str(self.temp_dir), ["chunk0001.md"], str(self.temp_dir / "input.md"))
        (self.temp_dir / "output_chunk0001.md").write_text("# 一\n\n正文。\n", encoding="utf-8")
        (self.temp_dir / "config.txt").write_text("output_lang=zh\noriginal_title=Book\ncreator=A\n",
                                                  encoding="utf-8")
        (self.temp_dir / "export_name.txt").write_text("My Book\n", encoding="utf-8")

        def calibre(cmd, check, capture_output, text):
            Path(cmd[4]).write_bytes(b"made from " + cmd[2].encode())
            return SimpleNamespace(stdout="", stderr="")

        with mock.patch.object(merge_and_build, "check_pandoc_available", return_value=False), \
                mock.patch.object(merge_and_build.subprocess, "run", side_effect=calibre), \
                mock.patch.object(sys, "argv", ["merge_and_build.py", "--temp-dir", str(self.temp_dir)]), \
                contextlib.redirect_stdout(io.StringIO()):
            merge_and_build.main()

        copies = ["My Book.html", "My Book_doc.html", "My Book.docx", "My Book.epub", "My Book.pdf"]
        self.assert_recorded_as_they_are(list(merge_and_build.BUILD_OUTPUTS) + copies)

    def test_symlinks_fifos_and_directories_under_written_names_are_not_recorded(self):
        target = self.root / "target.pdf"
        target.write_bytes(b"theirs")
        os.symlink(target, self.temp_dir / "book.pdf")
        os.mkfifo(self.temp_dir / "book.epub")
        (self.temp_dir / "book.docx").mkdir()
        out = io.StringIO()
        done = []

        def record():
            with contextlib.redirect_stdout(out):
                merge_and_build._record_outputs(str(self.temp_dir), "book.pdf", "book.epub", "book.docx")
            done.append(True)

        worker = threading.Thread(target=record, daemon=True)
        worker.start()
        worker.join(10)
        if worker.is_alive():
            os.close(os.open(self.temp_dir / "book.epub", os.O_WRONLY | os.O_NONBLOCK))
            self.fail("recording waited on a FIFO")
        self.assertEqual(done, [True])
        self.assertIsNone(_recorded(self.temp_dir))
        self.assertIn("Warning: could not record 'book.pdf', 'book.epub', 'book.docx'", out.getvalue())

    def test_hashing_reads_no_more_than_the_recorded_size(self):
        path = self.temp_dir / "book.pdf"
        path.write_bytes(b"book.pdf")
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        real_pread = os.pread
        reads = []

        def counted(fd_, n, offset):
            reads.append((n, offset))
            return real_pread(fd_, n, offset)

        with mock.patch.object(os, "pread", counted):
            self.assertEqual(merge_and_build._sha256_through(fd, 8), hashlib.sha256(b"book.pdf").hexdigest())
            self.assertIsNone(merge_and_build._sha256_through(fd, 4))
        self.assertEqual(reads, [(8, 0), (1, 8), (4, 0), (1, 4)])
        self.assertIsNone(merge_and_build._sha256_through(fd, 9))

    def test_a_step_keeps_the_entries_of_files_it_did_not_write(self):
        _build(self.temp_dir, {"book.pdf": b"pdf", "book.epub": b"epub"})
        before = _recorded(self.temp_dir)
        (self.temp_dir / "book.pdf").write_bytes(b"changed by someone else")

        _build(self.temp_dir, {"book.docx": b"docx"})

        self.assertEqual(_recorded(self.temp_dir), {**before, "book.docx": _content(self.temp_dir / "book.docx")})

    def test_an_unusable_record_is_left_as_it_is(self):
        record = self.temp_dir / merge_and_build.OUTPUTS_RECORD
        record.write_text("{not json", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _build(self.temp_dir, {"book.pdf": b"pdf"})
        self.assertEqual(record.read_text(encoding="utf-8"), "{not json")
        self.assertIn(f"Warning: could not record 'book.pdf' in {merge_and_build.OUTPUTS_RECORD}", out.getvalue())
        self.assertIn("--delete-outputs will leave it for you to delete", out.getvalue())
        self.assertEqual([p.name for p in self.temp_dir.iterdir() if p.name.startswith(".")], [])

    def test_a_record_that_cannot_be_written_leaves_no_file_behind(self):
        _build(self.temp_dir, {"book.pdf": b"pdf"})
        record = self.temp_dir / merge_and_build.OUTPUTS_RECORD
        before = record.read_bytes()
        real_replace = os.replace

        def disk_full(src, dst, *args, **kwargs):
            if os.path.basename(dst) == merge_and_build.OUTPUTS_RECORD:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_replace(src, dst, *args, **kwargs)

        out = io.StringIO()
        with mock.patch.object(os, "replace", disk_full), contextlib.redirect_stdout(out):
            _build(self.temp_dir, {"book.epub": b"epub"})

        self.assertEqual(record.read_bytes(), before)
        self.assertIn("Warning: could not record 'book.epub'", out.getvalue())
        self.assertEqual(sorted(p.name for p in self.temp_dir.iterdir()),
                         sorted(["book.epub", "book.pdf", merge_and_build.OUTPUTS_RECORD]))

    def test_a_file_put_in_place_of_the_record_being_written_is_left(self):
        theirs = self.root / "theirs"
        real_replace = os.replace

        def swap_then_fail(src, dst, *args, **kwargs):
            if os.path.basename(dst) == merge_and_build.OUTPUTS_RECORD:
                theirs.write_text("theirs", encoding="utf-8")
                real_replace(theirs, src)
                raise OSError(errno.EIO, "Input/output error")
            return real_replace(src, dst, *args, **kwargs)

        out = io.StringIO()
        with mock.patch.object(os, "replace", swap_then_fail), contextlib.redirect_stdout(out):
            _build(self.temp_dir, {"book.pdf": b"pdf"})

        [left] = [p for p in self.temp_dir.iterdir() if p.name.startswith(".tmp.")]
        self.assertEqual(left.read_text(encoding="utf-8"), "theirs")
        self.assertIn("Warning: could not record 'book.pdf'", out.getvalue())
        self.assertIsNone(_recorded(self.temp_dir))

    def test_the_record_is_read_strictly(self):
        _build(self.temp_dir, {"book.pdf": b"pdf"})
        record = self.temp_dir / merge_and_build.OUTPUTS_RECORD
        good = json.loads(record.read_text(encoding="utf-8"))
        entry = good["files"]["book.pdf"]
        self.assertEqual(merge_and_build._read_outputs_record(str(self.temp_dir)), good["files"])

        def with_entry(**changes):
            return {"version": 1, "files": {"book.pdf": {**entry, **changes}}}

        bad = ["{not json", "", "[]", "{}", json.dumps({"files": {}}), json.dumps({"version": 1}),
               json.dumps({"version": 2, "files": {}}), json.dumps({"version": True, "files": {}}),
               json.dumps({"version": 1.0, "files": {}}), json.dumps({"version": 1, "files": []}),
               json.dumps({"version": 1, "files": {}, "copies": []}), json.dumps({"copies": ["My Book.pdf"]}),
               json.dumps({"version": 1, "files": {"book.pdf": [1, 2]}}),
               json.dumps(with_entry(size=-1)), json.dumps(with_entry(size=True)),
               json.dumps(with_entry(size="3")), json.dumps(with_entry(sha256=entry["sha256"].upper())),
               json.dumps(with_entry(sha256=entry["sha256"][:-1])), json.dumps(with_entry(extra=1)),
               json.dumps({"version": 1, "files": {"book.pdf": {"sha256": entry["sha256"]}}})]
        bad += [json.dumps({"version": 1, "files": {"book.pdf": {k: v for k, v in entry.items() if k != field}}})
                for field in entry]
        names = ["../outside.pdf", str(self.root / "outside.pdf"), "..", ".", "", "nul\x00.pdf", "images/x.pdf",
                 "back\\slash.pdf", " Padded.pdf", ".html", "chunk0001.md", "glossary.json", "export_name.txt",
                 "input.html", "INPUT.html", "OUTPUT.html", "BOOK_DOC.html", "build_outputs.json",
                 "export_copies.json", "\ud800.pdf", "a" * 247 + ".pdf", "书" * 81 + "éé.pdf"]
        bad += [json.dumps({"version": 1, "files": {name: entry}}) for name in names]
        for text in bad:
            with self.subTest(text=text[:80]):
                record.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    merge_and_build._read_outputs_record(str(self.temp_dir))

        # A FIFO is in test_an_unusable_record_deletes_nothing, which would not hang on it.
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text(json.dumps(good), encoding="utf-8")
        for kind, why in [("directory", "it is a directory"), ("symlink", "it is a symlink"),
                          ("dangling symlink", "it is a symlink")]:
            with self.subTest(kind=kind):
                if record.is_dir() and not record.is_symlink():
                    record.rmdir()
                elif os.path.lexists(record):
                    record.unlink()
                if kind == "directory":
                    record.mkdir()
                else:
                    record.symlink_to(elsewhere if kind == "symlink" else self.root / "missing.json")
                with self.assertRaisesRegex(ValueError, why):
                    merge_and_build._read_outputs_record(str(self.temp_dir))

    def test_main_refuses_to_build_with_an_unusable_record(self):
        (self.temp_dir / merge_and_build.OUTPUTS_RECORD).write_text("{not json", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["merge_and_build.py", "--temp-dir", str(self.temp_dir)]), \
                mock.patch.object(merge_and_build, "merge_markdown_files") as merge_mock, \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as exc:
            merge_and_build.main()
        self.assertEqual(exc.exception.code, 1)
        self.assertIn(f"{merge_and_build.OUTPUTS_RECORD} cannot be used", out.getvalue())
        self.assertIn("Nothing was built.", out.getvalue())
        merge_mock.assert_not_called()


class DeleteOutputsTests(unittest.TestCase):
    OUTPUTS = list(merge_and_build.BUILD_OUTPUTS)
    # Everything else the pipeline and the agent leave in a temp dir.
    KEPT = ["input.html", "input.md", "chunk0001.md", "output_chunk0001.md",
            "output_chunk0001.meta.json", "output.md", "manifest.json", "config.txt",
            "source_fingerprint.json", "glossary.json", "run_state.json", "merge_decisions.json",
            "title.txt", "author.txt", "export_name.txt", "images/fig1.png", "figure_text/0a1b.json"]
    COPIES = ["My Book.html", "My Book_doc.html", "My Book.docx", "My Book.epub", "My Book.pdf"]
    RECORD = merge_and_build.OUTPUTS_RECORD

    def make_temp_dir(self, parent, export_name="My Book"):
        """A temp dir after a build: its outputs and export copies recorded, the intermediates around."""
        temp_dir = Path(parent) / "book_temp"
        for rel in self.KEPT:
            path = temp_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rel, encoding="utf-8")
        _build(temp_dir, {name: name.encode("utf-8") for name in self.OUTPUTS})
        if export_name:
            merge_and_build.export_named_aliases(str(temp_dir), export_name)
        return temp_dir

    def files(self, temp_dir):
        return sorted(p.relative_to(temp_dir).as_posix() for p in temp_dir.rglob("*")
                      if p.is_file() or p.is_symlink())

    def contents(self, temp_dir):
        return {p.relative_to(temp_dir).as_posix(): os.readlink(p) if p.is_symlink() else p.read_bytes()
                for p in temp_dir.rglob("*") if p.is_file() or p.is_symlink()}

    def leftovers(self, temp_dir):
        """Names this run moved files to or wrote the record under, if any is still there."""
        return [p.name for p in temp_dir.iterdir() if p.name.startswith((".translate-book-", ".tmp."))]

    def delete(self, temp_dir):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = merge_and_build.delete_outputs(str(temp_dir))
        return ok, out.getvalue()

    def delete_without_waiting_on(self, fifo, temp_dir):
        """delete(temp_dir), failing the test if it waits on the FIFO fifo."""
        result = []
        worker = threading.Thread(target=lambda: result.append(self.delete(temp_dir)), daemon=True)
        worker.start()
        worker.join(10)
        if worker.is_alive():
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            self.fail("--delete-outputs waited on a FIFO")
        return result[0]

    def run_main(self, temp_dir, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["merge_and_build.py", "--temp-dir", str(temp_dir), *args]), \
                contextlib.redirect_stdout(out):
            try:
                merge_and_build.main()
            except SystemExit as e:
                return e.code, out.getvalue()
        return 0, out.getvalue()

    def left(self, out):
        """{repr of name: why} for each file --delete-outputs said it left."""
        lines = out.splitlines()
        starts = [i for i, line in enumerate(lines) if line.startswith("Left ")]
        left = {}
        for line in lines[starts[0] + 1:] if starts else []:
            if not line.startswith("  "):
                break
            name, _, why = line[2:].partition(": ")
            left[name] = why
        return left

    def test_deletes_recorded_outputs_and_copies_only(self):
        name = "O'Brien's \"tale\" $(touch pwned) `id` $HOME *?[ab] -rf\nend"
        # An empty name would give .html etc.; a glob or a prefix match would reach the rest.
        decoys = [".html", "_doc.html", ".pdf", "other.html", name + ".md", name + ".pdf.bak"]
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp, export_name=None)
            for decoy in decoys:
                (temp_dir / decoy).write_text("decoy", encoding="utf-8")
            self.assertEqual(len(merge_and_build.export_named_aliases(str(temp_dir), name)), 5)

            code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 0, out)
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + decoys + [self.RECORD]))
            self.assertEqual(_recorded(temp_dir), {})
            self.assertEqual(list(Path(tmp).glob("pwned*")), [])
            self.assertIn("Deleted 11 file(s):", out)
            self.assertIn("Nothing was built; run the build command to rebuild.", out)
            self.assertEqual(self.leftovers(temp_dir), [])

    def test_deletes_the_copies_of_every_earlier_export_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp, "Old Title")
            # A rebuild under a new name that skipped the deletion.
            merge_and_build.export_named_aliases(str(temp_dir), "Mid Title")
            (temp_dir / "export_name.txt").write_text("New Title\n", encoding="utf-8")
            # Named like a copy of the next export name, but no build wrote it.
            (temp_dir / "New Title.pdf").write_text("not a copy", encoding="utf-8")

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok, out)
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + ["New Title.pdf", self.RECORD]))

    def test_without_a_record_nothing_is_deleted(self):
        # A temp dir an earlier version built, and a folder of the user's that only has a book.pdf.
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old_temp"
            mine = Path(tmp) / "mine"
            for rel in self.KEPT + self.OUTPUTS + self.COPIES:
                (old / rel).parent.mkdir(parents=True, exist_ok=True)
                (old / rel).write_text(rel, encoding="utf-8")
            (old / merge_and_build.EXPORT_COPIES_FILE).write_text(json.dumps({"copies": self.COPIES}),
                                                                  encoding="utf-8")
            mine.mkdir()
            (mine / "book.pdf").write_text("mine", encoding="utf-8")
            for temp_dir, named in [(old, self.OUTPUTS + self.COPIES), (mine, ["book.pdf"])]:
                with self.subTest(temp_dir=temp_dir.name):
                    before = self.contents(temp_dir)

                    code, out = self.run_main(temp_dir, "--delete-outputs")

                    self.assertEqual(code, 1)
                    self.assertEqual(self.contents(temp_dir), before)
                    self.assertEqual(set(self.left(out)), set(map(repr, named)))
                    for name in self.OUTPUTS:
                        if name in named:
                            self.assertIn(f"{name!r}: no build recorded it", out)
                    self.assertIn("No recorded build outputs to delete.", out)
                    self.assertIn("delete or move them yourself", out)
                    self.assertFalse((temp_dir / self.RECORD).exists())

    def test_a_made_up_manifest_and_a_blank_export_name_delete_nothing(self):
        # Both once let --delete-outputs delete by name; neither is read now.
        from manifest import create_manifest

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp) / "notes"
            temp_dir.mkdir()
            (temp_dir / "chunk0001.md").write_text("# One\n", encoding="utf-8")
            (temp_dir / "input.md").write_text("# Book\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                create_manifest(str(temp_dir), ["chunk0001.md"], str(temp_dir / "input.md"))
            (temp_dir / "export_name.txt").write_text("", encoding="utf-8")
            (temp_dir / "book.pdf").write_text("mine", encoding="utf-8")
            before = self.contents(temp_dir)

            code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            self.assertEqual(self.contents(temp_dir), before)
            self.assertEqual(set(self.left(out)), {"'book.pdf'"})

    def test_a_file_put_in_place_of_a_recorded_one_is_left_and_stays_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            recorded = _recorded(temp_dir)
            theirs = ["My Book.pdf", "book.html"]
            for name in theirs:
                Path(tmp, "theirs").write_text(f"theirs, not {name}", encoding="utf-8")
                Path(tmp, "theirs").replace(temp_dir / name)

            code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            self.assertEqual(set(self.left(out)), set(map(repr, theirs)))
            for name in theirs:
                self.assertEqual((temp_dir / name).read_text(encoding="utf-8"), f"theirs, not {name}")
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + theirs + [self.RECORD]))
            self.assertEqual(_recorded(temp_dir), {name: recorded[name] for name in theirs})

            # Run again, it still fails, so the build that follows does not overwrite them.
            code, out = self.run_main(temp_dir, "--delete-outputs")
            self.assertEqual(code, 1)
            self.assertEqual(set(self.left(out)), set(map(repr, theirs)))

            for name in theirs:
                (temp_dir / name).rename(Path(tmp) / name)
            code, out = self.run_main(temp_dir, "--delete-outputs")
            self.assertEqual(code, 0, out)
            self.assertEqual(_recorded(temp_dir), {})

    def test_a_copy_with_the_same_bytes_in_place_of_an_output_is_deleted(self):
        # Outputs are told by their size and SHA-256 alone, not by their inode.
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            copy = Path(tmp) / "copy.pdf"
            shutil.copy2(temp_dir / "book.pdf", copy)
            copy.replace(temp_dir / "book.pdf")

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok, out)
            self.assertFalse(os.path.lexists(temp_dir / "book.pdf"))

    def test_an_output_changed_since_the_build_is_left(self):
        changed = "it no longer holds what the build wrote"
        # Rewritten in place with as many bytes and with more, and replaced by another file.
        for how, data in [("in place", b"BOOK.PDF"), ("in place", b"book.pdf, annotated"),
                          ("replaced", b"BOOK.PDF")]:
            with self.subTest(how=how, data=data), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                entry = _recorded(temp_dir)["book.pdf"]
                if how == "in place":
                    with open(temp_dir / "book.pdf", "r+b") as f:
                        f.write(data)
                else:
                    Path(tmp, "theirs.pdf").write_bytes(data)
                    Path(tmp, "theirs.pdf").replace(temp_dir / "book.pdf")

                ok, out = self.delete(temp_dir)

                self.assertFalse(ok)
                self.assertEqual((temp_dir / "book.pdf").read_bytes(), data)
                self.assertEqual(self.left(out), {"'book.pdf'": changed})
                self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})
                self.assertIn("delete or move them yourself", out)
                self.assertNotIn("could not be read or deleted", out)

    def test_a_file_that_grows_while_it_is_checked_is_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            entry = _recorded(temp_dir)["book.pdf"]
            inode = os.stat(temp_dir / "book.pdf").st_ino
            real_pread = os.pread
            grown = []

            def grow_then_read(fd, n, offset):
                if os.fstat(fd).st_ino == inode and not grown:
                    grown.append(True)
                    with open(temp_dir / "book.pdf", "ab") as f:
                        f.write(b", still being written")
                return real_pread(fd, n, offset)

            with mock.patch.object(os, "pread", grow_then_read):
                ok, out = self.delete(temp_dir)

            self.assertFalse(ok)
            self.assertEqual((temp_dir / "book.pdf").read_bytes(), b"book.pdf, still being written")
            self.assertEqual(self.left(out), {"'book.pdf'": "it no longer holds what the build wrote"})
            self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})

    def test_symlinks_fifos_and_directories_under_recorded_names_are_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            target = Path(tmp) / "target.pdf"
            target.write_text("keep", encoding="utf-8")
            for name in ["book.pdf", "My Book.pdf", "book.epub", "book.docx"]:
                (temp_dir / name).unlink()
            os.symlink(target, temp_dir / "book.pdf")
            # A symlink to an output that is recorded too: only that output goes.
            os.symlink("book_doc.html", temp_dir / "My Book.pdf")
            os.mkfifo(temp_dir / "book.epub")
            (temp_dir / "book.docx").mkdir()
            (temp_dir / "book.docx" / "inner.txt").write_text("keep", encoding="utf-8")

            ok, out = self.delete_without_waiting_on(temp_dir / "book.epub", temp_dir)

            self.assertFalse(ok)
            why = "and builds write only regular files"
            self.assertEqual(self.left(out), {"'book.pdf'": f"it is a symlink, {why}",
                                              "'My Book.pdf'": f"it is a symlink, {why}",
                                              "'book.epub'": f"it is a FIFO, {why}",
                                              "'book.docx'": f"it is a directory, {why}"})
            self.assertEqual(target.read_text(encoding="utf-8"), "keep")
            self.assertEqual(os.readlink(temp_dir / "book.pdf"), str(target))
            self.assertTrue(stat.S_ISFIFO(os.lstat(temp_dir / "book.epub").st_mode))
            self.assertEqual((temp_dir / "book.docx" / "inner.txt").read_text(encoding="utf-8"), "keep")
            self.assertFalse(os.path.lexists(temp_dir / "book_doc.html"))
            self.assertEqual(self.leftovers(temp_dir), [])

    def test_a_file_swapped_in_before_it_is_opened_is_left(self):
        # Between the lstat and the open: another file, one with the same
        # bytes, which is not the file checked either, and a FIFO, which must
        # not be read.
        for kind, data in [("another file", b"theirs"), ("same bytes", b"book.pdf"), ("FIFO", None)]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                theirs = Path(tmp) / "theirs.pdf"
                if data is None:
                    os.mkfifo(theirs)
                else:
                    theirs.write_bytes(data)
                real_open = os.open

                def swap_then_open(path, flags, *args, **kwargs):
                    if os.path.basename(path) == "book.pdf" and os.path.lexists(theirs):
                        theirs.replace(path)
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(os, "open", swap_then_open):
                    ok, out = self.delete_without_waiting_on(temp_dir / "book.pdf", temp_dir)

                self.assertFalse(ok)
                if data is None:
                    self.assertTrue(stat.S_ISFIFO(os.lstat(temp_dir / "book.pdf").st_mode))
                else:
                    self.assertEqual((temp_dir / "book.pdf").read_bytes(), data)
                self.assertEqual(self.left(out),
                                 {"'book.pdf'": "another file took its place while it was being checked"})
                self.assertEqual(self.leftovers(temp_dir), [])

    def test_a_file_swapped_or_rewritten_before_it_is_moved_aside_is_put_back(self):
        def swap(path, tmp):
            Path(tmp, "theirs.pdf").write_bytes(b"theirs")
            Path(tmp, "theirs.pdf").replace(path)
            return b"theirs"

        def swap_same_bytes(path, tmp):
            # Not the file checked, though it holds the same bytes.
            Path(tmp, "theirs.pdf").write_bytes(b"book.pdf")
            Path(tmp, "theirs.pdf").replace(path)
            return b"book.pdf"

        def rewrite(path, tmp):
            with open(path, "r+b") as f:
                f.write(b"BOOK.PDF")
            return b"BOOK.PDF"

        for change in [swap, swap_same_bytes, rewrite]:
            with self.subTest(change=change.__name__), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                entry = _recorded(temp_dir)["book.pdf"]
                real_rename = os.rename
                changed = []

                def change_then_rename(src, dst, *args, **kwargs):
                    if os.path.basename(src) == "book.pdf" and not changed:
                        changed.append(change(src, tmp))
                    return real_rename(src, dst, *args, **kwargs)

                with mock.patch.object(os, "rename", change_then_rename):
                    ok, out = self.delete(temp_dir)

                self.assertFalse(ok)
                self.assertEqual((temp_dir / "book.pdf").read_bytes(), changed[0])
                self.assertEqual(self.left(out), {"'book.pdf'": "it changed while it was being deleted"})
                self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})
                self.assertEqual(self.leftovers(temp_dir), [])

    def test_a_file_that_cannot_be_deleted_once_moved_aside_is_put_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            entry = _recorded(temp_dir)["book.pdf"]
            inode = os.stat(temp_dir / "book.pdf").st_ino
            real_unlink = os.unlink
            refused = []

            def refuse_once(path, *args, **kwargs):
                if not refused and os.path.lexists(path) and os.lstat(path).st_ino == inode:
                    refused.append(path)
                    raise PermissionError(errno.EPERM, "Operation not permitted")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(os, "unlink", refuse_once):
                code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            self.assertTrue(os.path.basename(refused[0]).startswith(".translate-book-"))
            self.assertEqual(self.left(out), {"'book.pdf'": "it could not be moved aside or deleted; "
                                                            "check the temp directory's permissions"})
            self.assertEqual((temp_dir / "book.pdf").read_bytes(), b"book.pdf")
            self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + ["book.pdf", self.RECORD]))
            self.assertIn("could not be read or deleted", out)
            self.assertNotIn("delete or move them yourself", out)

    def test_a_file_that_can_be_neither_deleted_nor_put_back_is_named_where_it_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            entry = _recorded(temp_dir)["book.pdf"]
            inode = os.stat(temp_dir / "book.pdf").st_ino
            real_unlink, real_link = os.unlink, os.link

            def refuse(real):
                def call(path, *args, **kwargs):
                    if os.path.lexists(path) and os.lstat(path).st_ino == inode:
                        raise PermissionError(errno.EPERM, "Operation not permitted")
                    return real(path, *args, **kwargs)
                return call

            with mock.patch.object(os, "unlink", refuse(real_unlink)), mock.patch.object(os, "link", refuse(real_link)):
                code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            [aside] = [p for p in temp_dir.iterdir() if p.name.startswith(".translate-book-")]
            self.assertEqual(aside.read_bytes(), b"book.pdf")
            self.assertFalse(os.path.lexists(temp_dir / "book.pdf"))
            self.assertIn(f"Warning: {temp_dir / 'book.pdf'} could not be removed (Operation not permitted). "
                          f"What was there is now {aside}", out)
            self.assertEqual(self.left(out), {"'book.pdf'": "it could not be deleted (Operation not permitted) or "
                                                            "put back; the warning above says where it is"})
            self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})

    def test_a_file_that_cannot_be_read_while_it_is_checked_stays_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            entry = _recorded(temp_dir)["book.pdf"]
            inode = os.stat(temp_dir / "book.pdf").st_ino
            real_pread = os.pread

            def failing_disk(fd, n, offset):
                if os.fstat(fd).st_ino == inode:
                    raise OSError(errno.EIO, "Input/output error")
                return real_pread(fd, n, offset)

            with mock.patch.object(os, "pread", failing_disk):
                code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            self.assertEqual(self.left(out), {"'book.pdf'": "it cannot be read to check it (Input/output error)"})
            self.assertEqual((temp_dir / "book.pdf").read_bytes(), b"book.pdf")
            self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})
            self.assertIn("could not be read or deleted", out)

    def test_a_file_that_cannot_be_moved_keeps_its_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            entry = _recorded(temp_dir)["book.pdf"]
            real_rename = os.rename

            def refuse(src, dst, *args, **kwargs):
                if os.path.basename(src) == "book.pdf":
                    raise PermissionError(errno.EACCES, "Permission denied")
                return real_rename(src, dst, *args, **kwargs)

            with mock.patch.object(os, "rename", refuse):
                code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            self.assertEqual(self.left(out), {"'book.pdf'": "it could not be moved aside or deleted; "
                                                            "check the temp directory's permissions"})
            self.assertEqual(_recorded(temp_dir), {"book.pdf": entry})
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + ["book.pdf", self.RECORD]))

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok, out)
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + [self.RECORD]))

    @unittest.skipIf(os.geteuid() == 0, "root reads and moves files whatever their permissions")
    def test_files_it_cannot_read_or_move_stay_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            recorded = _recorded(temp_dir)
            os.chmod(temp_dir / "book.pdf", 0)
            try:
                ok, out = self.delete(temp_dir)
            finally:
                os.chmod(temp_dir / "book.pdf", 0o644)
            self.assertFalse(ok)
            self.assertEqual(self.left(out), {"'book.pdf'": "it cannot be read to check it (Permission denied)"})
            self.assertEqual(_recorded(temp_dir), {"book.pdf": recorded["book.pdf"]})

            temp_dir = self.make_temp_dir(Path(tmp) / "again")
            os.chmod(temp_dir, 0o500)
            try:
                ok, out = self.delete(temp_dir)
            finally:
                os.chmod(temp_dir, 0o700)
            self.assertFalse(ok)
            self.assertEqual(set(self.left(out)), set(map(repr, self.OUTPUTS + self.COPIES)))
            self.assertEqual(set(_recorded(temp_dir)), set(self.OUTPUTS + self.COPIES))

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok, out)
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + [self.RECORD]))

    def test_an_unusable_record_deletes_nothing(self):
        cases = {"corrupt JSON": "{not json", "an empty file": "", "a list": "[]",
                 "another version": json.dumps({"version": 2, "files": {}}),
                 "the earlier record": json.dumps({"copies": ["My Book.pdf"]}),
                 "a name no build writes": json.dumps({"version": 1, "files": {"../outside.pdf": {
                     "size": 4, "sha256": hashlib.sha256(b"keep").hexdigest()}}}),
                 "an entry with an inode": json.dumps({"version": 1, "files": {"book.pdf": {
                     "inode": 1, "size": 8, "sha256": hashlib.sha256(b"book.pdf").hexdigest()}}}),
                 "a directory": None, "a dangling symlink": None, "a symlink to a record": None, "a FIFO": None}
        for case, text in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                (Path(tmp) / "outside.pdf").write_text("keep", encoding="utf-8")
                record = temp_dir / self.RECORD
                elsewhere = Path(tmp) / "elsewhere.json"
                elsewhere.write_bytes(record.read_bytes())
                record.unlink()
                if case == "a directory":
                    record.mkdir()
                elif case == "a dangling symlink":
                    os.symlink(Path(tmp) / "missing.json", record)
                elif case == "a symlink to a record":
                    os.symlink(elsewhere, record)
                elif case == "a FIFO":
                    os.mkfifo(record)
                else:
                    record.write_text(text, encoding="utf-8")
                before = self.contents(temp_dir)

                ok, out = self.delete_without_waiting_on(record, temp_dir)

                self.assertFalse(ok)
                self.assertEqual(self.contents(temp_dir), before)
                self.assertTrue(os.path.lexists(record))
                self.assertEqual((Path(tmp) / "outside.pdf").read_text(encoding="utf-8"), "keep")
                self.assertIn(f"{self.RECORD} cannot be used", out)
                self.assertIn("Nothing was deleted.", out)

    def test_copies_an_earlier_version_listed_are_named_and_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp, export_name=None)
            old = ["Old Title.html", "Old Title.pdf", "Gone Title.pdf"]
            for name in old[:2]:
                (temp_dir / name).write_text("an old copy", encoding="utf-8")
            legacy = temp_dir / merge_and_build.EXPORT_COPIES_FILE
            legacy.write_text(json.dumps({"copies": old}), encoding="utf-8")

            ok, out = self.delete(temp_dir)

            self.assertFalse(ok)
            self.assertEqual(self.files(temp_dir),
                             sorted(self.KEPT + old[:2] + [merge_and_build.EXPORT_COPIES_FILE, self.RECORD]))
            self.assertEqual(set(self.left(out)), {"'Old Title.html'", "'Old Title.pdf'"})
            self.assertIn("an earlier version recorded only the name of its copy", out)
            self.assertEqual(json.loads(legacy.read_text(encoding="utf-8")), {"copies": old})

    def test_an_unusable_list_of_earlier_copies_deletes_nothing(self):
        for case in ["a FIFO", "a symlink", "corrupt JSON"]:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                temp_dir = self.make_temp_dir(tmp)
                (temp_dir / "Old Title.pdf").write_text("an old copy, or a file of the user's", encoding="utf-8")
                legacy = temp_dir / merge_and_build.EXPORT_COPIES_FILE
                elsewhere = Path(tmp) / "elsewhere.json"
                elsewhere.write_text(json.dumps({"copies": ["Old Title.pdf"]}), encoding="utf-8")
                if case == "a FIFO":
                    os.mkfifo(legacy)
                elif case == "a symlink":
                    os.symlink(elsewhere, legacy)
                else:
                    legacy.write_text('{"copies": ["Old Title.pdf"', encoding="utf-8")
                before = self.contents(temp_dir)
                recorded = _recorded(temp_dir)

                ok, out = self.delete_without_waiting_on(legacy, temp_dir)

                self.assertFalse(ok)
                self.assertEqual(self.contents(temp_dir), before)
                self.assertEqual(_recorded(temp_dir), recorded)
                self.assertIn(f"Error: {merge_and_build.EXPORT_COPIES_FILE} cannot be used", out)
                self.assertIn("Nothing was deleted.", out)
                if case == "corrupt JSON":
                    code, _ = self.run_main(temp_dir, "--delete-outputs")
                    self.assertEqual(code, 1)
                    self.assertEqual(self.contents(temp_dir), before)

    def test_a_record_that_cannot_be_updated_fails_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            real_replace = os.replace

            def disk_full(src, dst, *args, **kwargs):
                if os.path.basename(dst) == self.RECORD:
                    raise OSError(errno.ENOSPC, "No space left on device")
                return real_replace(src, dst, *args, **kwargs)

            with mock.patch.object(os, "replace", disk_full):
                code, out = self.run_main(temp_dir, "--delete-outputs")

            self.assertEqual(code, 1)
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + [self.RECORD]))
            self.assertIn(f"Error: could not update {self.RECORD}", out)
            self.assertIn("the record could not be updated", out)
            self.assertNotIn("delete or move them yourself", out)
            self.assertEqual(self.leftovers(temp_dir), [])

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok, out)
            self.assertEqual(_recorded(temp_dir), {})

    def test_recorded_files_that_are_gone_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
            for name in self.OUTPUTS + self.COPIES:
                (temp_dir / name).unlink()

            ok, out = self.delete(temp_dir)

            self.assertTrue(ok, out)
            self.assertIn("No recorded build outputs to delete.", out)
            self.assertEqual(_recorded(temp_dir), {})

    def test_main_deletes_and_builds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = self.make_temp_dir(tmp)
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
            self.assertEqual(self.files(temp_dir), sorted(self.KEPT + [self.RECORD]))
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

    def test_python_markdown_fallback_renders_footnotes(self):
        if not merge_and_build.MARKDOWN_AVAILABLE:
            # Like installed() for programs: the CI job that installs python-markdown names it, so that
            # a failed install fails here instead of quietly leaving the fallback untested.
            if "python-markdown" in process_guard.REQUIRED_TOOLS:
                self.fail("python-markdown is not installed, and TRANSLATE_BOOK_REQUIRED_TOOLS requires it")
            self.skipTest("python-markdown is not installed")
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
