import contextlib
import inspect
import io
import os
import sys
import tempfile
import unittest
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
            with contextlib.redirect_stdout(io.StringIO()):
                ok = merge_and_build.convert_with_pandoc("in.md", "out.html", "Title", "zh-CN")

        self.assertTrue(ok)
        cmd = run_mock.call_args.args[0]
        self.assertIn("--mathml", cmd)
        self.assertEqual(cmd[cmd.index("--to") + 1], "html5")


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
                self.assertEqual(len(list(root.iterdir())), len(originals) + len(copied))

    def test_main_rejects_export_collision_before_building(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = io.StringIO()
            with mock.patch.object(
                sys, "argv",
                ["merge_and_build.py", "--temp-dir", temp_dir, "--export-name", "book_doc"],
            ), mock.patch.object(
                merge_and_build, "merge_markdown_files"
            ) as merge_mock, contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as exc:
                    merge_and_build.main()

            self.assertNotEqual(exc.exception.code, 0)
            self.assertIn("book_doc.html", output.getvalue())
            merge_mock.assert_not_called()


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


if __name__ == "__main__":
    unittest.main()


class MetadataTitleTests(unittest.TestCase):
    def test_strips_wrapping_book_title_marks(self):
        self.assertEqual(merge_and_build._metadata_title("《财阀、先知、主帅与君王》"), "财阀、先知、主帅与君王")
        self.assertEqual(merge_and_build._metadata_title("  Plain Title "), "Plain Title")

    def test_keeps_marks_that_do_not_wrap_the_whole_title(self):
        title = "《A》与《B》"
        self.assertEqual(merge_and_build._metadata_title(title), title)


class PandocEpubTests(unittest.TestCase):
    LANG_CFG = merge_and_build.get_lang_config("zh")

    def _book(self, temp_dir, md):
        os.makedirs(os.path.join(temp_dir, "images"))
        Path(temp_dir, "images", "cover.jpg").write_bytes(b"jpg")
        Path(temp_dir, "output.md").write_text(md, encoding="utf-8")
        Path(temp_dir, "book_doc.html").write_text("<html></html>", encoding="utf-8")

    def _run(self, temp_dir, cover=None, returncode=0):
        seen = {}

        def fake_run(cmd, **kwargs):
            src = cmd[1]
            seen["cmd"] = cmd
            seen["source"] = Path(src).read_text(encoding="utf-8")
            seen["css"] = Path(cmd[cmd.index("--css") + 1]).read_text(encoding="utf-8")
            if returncode == 0:
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"epub")
            return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

        with mock.patch.object(merge_and_build, "check_pandoc_available", return_value=True), \
                mock.patch.object(merge_and_build.subprocess, "run", side_effect=fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            result = merge_and_build.generate_epub_with_pandoc(
                temp_dir, "书名", "作者", self.LANG_CFG, cover=cover)
        return result, seen

    def test_builds_toc_from_headings_and_uses_leading_image_as_cover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._book(temp_dir, "![](images/cover.jpg)\n\n# 第一部分\n\n## 第1章\n\n正文。\n")
            result, seen = self._run(temp_dir)

            self.assertEqual(result, os.path.join(temp_dir, "book.epub"))
            cmd = seen["cmd"]
            self.assertIn("--toc", cmd)
            self.assertIn("title=书名", cmd)
            self.assertIn("lang=zh-CN", cmd)
            self.assertIn("toc-title=目录", cmd)
            self.assertIn(f"--epub-cover-image={os.path.join(temp_dir, 'images', 'cover.jpg')}", cmd)
            # The cover image moves to the EPUB cover instead of opening the body.
            self.assertTrue(seen["source"].startswith("# 第一部分"))
            self.assertIn("text-indent: 2em", seen["css"])
            # Temporary source and stylesheet are removed.
            self.assertEqual(sorted(os.listdir(temp_dir)),
                             ["book.epub", "book_doc.html", "images", "output.md"])

    def test_explicit_cover_keeps_a_different_leading_image_in_the_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._book(temp_dir, "![](images/cover.jpg)\n\n# 标题\n")
            other = os.path.join(temp_dir, "other.jpg")
            Path(other).write_bytes(b"jpg")
            _, seen = self._run(temp_dir, cover=other)

            self.assertIn(f"--epub-cover-image={other}", seen["cmd"])
            self.assertTrue(seen["source"].startswith("![](images/cover.jpg)"))

    def test_returns_none_when_pandoc_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self._book(temp_dir, "# 标题\n")
            result, _ = self._run(temp_dir, returncode=1)
            self.assertIsNone(result)

    def test_generate_formats_falls_back_to_calibre_for_epub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "book_doc.html").write_text("<html></html>", encoding="utf-8")
            calls = []

            def fake_generate_format(html_file, temp_dir_, ext, lang_attr, cover=None):
                calls.append(ext)
                return os.path.join(temp_dir_, "book" + ext)

            for ext in (".docx", ".epub", ".pdf"):
                Path(temp_dir, "book" + ext).write_bytes(b"x")
            with mock.patch.object(merge_and_build, "generate_epub_with_pandoc", return_value=None), \
                    mock.patch.object(merge_and_build, "generate_format", side_effect=fake_generate_format), \
                    contextlib.redirect_stdout(io.StringIO()):
                ok = merge_and_build.generate_formats(
                    temp_dir, "zh-CN", title="书名", author="作者", lang_cfg=self.LANG_CFG)

            self.assertTrue(ok)
            self.assertEqual(calls, [".docx", ".epub", ".pdf"])

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
