import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import convert  # noqa: E402


class CleanCalibreMarkersTests(unittest.TestCase):
    def test_removes_known_calibre_artifacts(self):
        content = "\n".join(
            [
                "## Heading {#calibre_link-12 .calibre3}",
                "[**Chapter One**]",
                "Paragraph text{.calibre5} (#calibre_link-2)",
                "::: {.calibre1}",
                "42",
                "broken.ct}",
                "Regular paragraph.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertIn("## Heading", cleaned)
        self.assertIn("**Chapter One**", cleaned)
        self.assertIn("Paragraph text", cleaned)
        self.assertIn("Regular paragraph.", cleaned)
        self.assertNotIn(".calibre", cleaned)
        self.assertNotIn("(#calibre_link-", cleaned)
        self.assertNotIn(":::", cleaned)
        # 42 sits between ::: noise and broken.ct} noise, both calibre artifacts.
        # Context-aware cleaner still drops it — but only because of the neighbors.
        self.assertNotIn("\n42\n", f"\n{cleaned}\n")
        self.assertNotIn("broken.ct}", cleaned)

    def test_preserves_year_in_paragraph(self):
        content = "\n".join(
            [
                "He was born in",
                "1984",
                "and died later.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertIn("1984", cleaned)
        self.assertIn("He was born in", cleaned)
        self.assertIn("and died later.", cleaned)

    def test_preserves_chapter_number_after_heading(self):
        content = "\n".join(
            [
                "## Chapter",
                "",
                "3",
                "",
                "Introduction text follows.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertIn("\n3\n", f"\n{cleaned}\n")
        self.assertIn("## Chapter", cleaned)
        self.assertIn("Introduction text follows.", cleaned)

    def test_drops_digit_line_inside_calibre_fence(self):
        content = "\n".join(
            [
                "Some real paragraph.",
                "::: {.calibre1}",
                "42",
                ":::",
                "More real paragraph.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertNotIn("42", cleaned)
        self.assertNotIn(":::", cleaned)
        self.assertIn("Some real paragraph.", cleaned)
        self.assertIn("More real paragraph.", cleaned)

    def test_drops_digit_line_adjacent_to_ct_marker(self):
        content = "\n".join(
            [
                "Real paragraph above.",
                "7",
                "broken.ct}",
                "Real paragraph below.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertNotIn("\n7\n", f"\n{cleaned}\n")
        self.assertNotIn("broken.ct}", cleaned)

    def test_drops_sequential_page_numbers(self):
        # Six paragraphs separated by sequential page-number footers — clear
        # monotonic spine should be detected and dropped.
        content = "\n".join(
            [
                "Para one.",
                "",
                "1",
                "",
                "Para two.",
                "",
                "2",
                "",
                "Para three.",
                "",
                "3",
                "",
                "Para four.",
                "",
                "4",
                "",
                "Para five.",
                "",
                "5",
                "",
                "Para six.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        for n in ("1", "2", "3", "4", "5"):
            self.assertNotIn(f"\n{n}\n", f"\n{cleaned}\n")
        for p in ("Para one.", "Para two.", "Para three.", "Para four.", "Para five.", "Para six."):
            self.assertIn(p, cleaned)

    def test_preserves_year_among_page_numbers(self):
        # Same page-number spine plus a year (1984) sitting between two page
        # numbers — LNDS picks 1..5 and skips 1984, which stays as content.
        content = "\n".join(
            [
                "Para one.",
                "",
                "1",
                "",
                "Para two.",
                "",
                "2",
                "",
                "He was born in 1984.",
                "Standalone year:",
                "1984",
                "and continued.",
                "",
                "3",
                "",
                "Para three.",
                "",
                "4",
                "",
                "Para four.",
                "",
                "5",
                "",
                "Para five.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertIn("\n1984\n", f"\n{cleaned}\n")
        for n in ("1", "2", "3", "4", "5"):
            self.assertNotIn(f"\n{n}\n", f"\n{cleaned}\n")

    def test_few_digits_not_treated_as_page_numbers(self):
        # Only three standalone digits — below the LNDS minimum length, so all
        # are preserved (assuming no calibre-noise neighbors).
        content = "\n".join(
            [
                "Intro paragraph.",
                "",
                "1",
                "",
                "Body paragraph.",
                "",
                "2",
                "",
                "More body.",
                "",
                "3",
                "",
                "Closing paragraph.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        for n in ("1", "2", "3"):
            self.assertIn(f"\n{n}\n", f"\n{cleaned}\n")

    def test_non_monotonic_digits_preserved(self):
        # Five standalone digits but no monotonic spine — LNDS coverage too low
        # to trigger; everything preserved.
        content = "\n".join(
            [
                "Intro.",
                "",
                "1984",
                "",
                "Para A.",
                "",
                "42",
                "",
                "Para B.",
                "",
                "7",
                "",
                "Para C.",
                "",
                "1066",
                "",
                "Para D.",
                "",
                "3",
                "",
                "Closing.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        for n in ("1984", "42", "7", "1066", "3"):
            self.assertIn(f"\n{n}\n", f"\n{cleaned}\n")

    def test_strip_page_numbers_flag_restores_legacy(self):
        content = "\n".join(
            [
                "He was born in",
                "1984",
                "and died later.",
                "",
                "## Chapter",
                "",
                "3",
                "",
                "Introduction text follows.",
            ]
        )

        cleaned = convert.clean_calibre_markers(content, strip_page_numbers=True)

        self.assertNotIn("1984", cleaned)
        self.assertNotIn("\n3\n", f"\n{cleaned}\n")
        self.assertIn("He was born in", cleaned)
        self.assertIn("Introduction text follows.", cleaned)


class TempRootTests(unittest.TestCase):
    def test_build_temp_dir_preserves_cwd_local_default(self):
        self.assertEqual(convert.build_temp_dir("/books/Alice.epub"), "Alice_temp")

    def test_build_temp_dir_uses_explicit_root(self):
        self.assertEqual(
            convert.build_temp_dir("/books/Alice.epub", "/tmp/work"),
            os.path.join("/tmp/work", "Alice_temp"),
        )

    def test_setup_temp_directory_uses_explicit_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "work"
            html_file = Path(temp_dir) / "input.html"
            images_dir = Path(temp_dir) / "images"
            image_file = images_dir / "cover.jpg"
            html_file.write_text("<html></html>", encoding="utf-8")
            images_dir.mkdir()
            image_file.write_text("image", encoding="utf-8")

            created = convert.setup_temp_directory(
                "/books/Alice.epub",
                str(html_file),
                str(images_dir),
                temp_root=str(root),
            )

            self.assertEqual(created, str(root / "Alice_temp"))
            self.assertTrue((root / "Alice_temp" / "input.html").exists())
            self.assertTrue((root / "Alice_temp" / "images" / "cover.jpg").exists())


class StripPageNumbersCacheConflictTests(unittest.TestCase):
    def test_no_blockers_when_flag_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_md = os.path.join(tmp, "input.md")
            with open(input_md, "w", encoding="utf-8") as f:
                f.write("placeholder")
            with open(os.path.join(tmp, "chunk0001.md"), "w", encoding="utf-8") as f:
                f.write("placeholder")

            blockers = convert._check_strip_page_numbers_cache_conflict(
                strip_flag=False, temp_dir=tmp, input_md=input_md
            )

            self.assertEqual(blockers, [])

    def test_no_blockers_when_temp_dir_missing(self):
        missing_dir = os.path.join(tempfile.gettempdir(), "definitely-not-here-xyz-123")
        # Make extra sure it really doesn't exist.
        self.assertFalse(os.path.isdir(missing_dir))
        input_md = os.path.join(missing_dir, "input.md")

        blockers = convert._check_strip_page_numbers_cache_conflict(
            strip_flag=True, temp_dir=missing_dir, input_md=input_md
        )

        self.assertEqual(blockers, [])

    def test_aborts_when_input_md_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_md = os.path.join(tmp, "input.md")
            with open(input_md, "w", encoding="utf-8") as f:
                f.write("cached markdown")

            blockers = convert._check_strip_page_numbers_cache_conflict(
                strip_flag=True, temp_dir=tmp, input_md=input_md
            )

            self.assertEqual(len(blockers), 1)
            self.assertIn("input.md", blockers[0])

    def test_aborts_when_chunks_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_md = os.path.join(tmp, "input.md")  # absent
            for i in range(1, 4):
                with open(os.path.join(tmp, f"chunk{i:04d}.md"), "w", encoding="utf-8") as f:
                    f.write("chunk")
            # output_chunk*.md files must not be counted as source chunks.
            with open(os.path.join(tmp, "output_chunk0001.md"), "w", encoding="utf-8") as f:
                f.write("translated")

            blockers = convert._check_strip_page_numbers_cache_conflict(
                strip_flag=True, temp_dir=tmp, input_md=input_md
            )

            self.assertEqual(len(blockers), 1)
            self.assertIn("3 chunk file(s)", blockers[0])


class SourceFingerprintCacheTests(unittest.TestCase):
    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def test_no_conflict_for_fresh_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            self._write(source, "source bytes")

            status, message = convert.check_source_cache(
                str(Path(tmp) / "book_temp"),
                convert.source_fingerprint(str(source)),
            )

        self.assertIsNone(status)
        self.assertIsNone(message)

    def test_adopts_cache_that_predates_fingerprinting(self):
        # Temp dirs created before this feature have no fingerprint file.
        # They must stay resumable (trust-on-first-use), with a warning.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.html", "<html></html>")

            status, message = convert.check_source_cache(
                str(temp_dir), convert.source_fingerprint(str(source))
            )

        self.assertEqual(status, "adopt")
        self.assertIn(convert.SOURCE_FINGERPRINT_FILE, message)

    def test_mismatch_when_source_bytes_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "old source bytes")
            convert._write_source_fingerprint(
                str(temp_dir), convert.source_fingerprint(str(source))
            )
            self._write(temp_dir / "input.html", "<html></html>")

            self._write(source, "new source bytes!!")
            status, message = convert.check_source_cache(
                str(temp_dir), convert.source_fingerprint(str(source))
            )

        self.assertEqual(status, "mismatch")
        self.assertIn("different source bytes", message)

    def test_no_conflict_when_source_bytes_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            convert._write_source_fingerprint(
                str(temp_dir), convert.source_fingerprint(str(source))
            )
            self._write(temp_dir / "input.html", "<html></html>")

            status, message = convert.check_source_cache(
                str(temp_dir), convert.source_fingerprint(str(source))
            )

        self.assertIsNone(status)
        self.assertIsNone(message)

    def test_moving_source_file_does_not_invalidate_cache(self):
        # Only content identity matters — renaming/moving the book is fine.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            convert._write_source_fingerprint(
                str(temp_dir), convert.source_fingerprint(str(source))
            )
            self._write(temp_dir / "input.html", "<html></html>")

            moved = Path(tmp) / "renamed-book.epub"
            source.rename(moved)
            status, _ = convert.check_source_cache(
                str(temp_dir), convert.source_fingerprint(str(moved))
            )

        self.assertIsNone(status)

    def test_corrupt_fingerprint_file_is_adopted_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.html", "<html></html>")
            self._write(temp_dir / convert.SOURCE_FINGERPRINT_FILE, "{not json")

            status, _ = convert.check_source_cache(
                str(temp_dir), convert.source_fingerprint(str(source))
            )

        self.assertEqual(status, "adopt")


class DisplayMathBlockTests(unittest.TestCase):
    def test_multiline_display_math_is_one_block(self):
        content = "\n".join(
            [
                "Text before.",
                "$$",
                "\\begin{aligned}",
                "- a &= b \\\\",
                "| c &= d",
                "\\end{aligned}",
                "$$",
                "Text after.",
            ]
        )

        blocks = convert.parse_structural_blocks(content)

        math = [text for text, btype in blocks if btype == "math_block"]
        self.assertEqual(len(math), 1)
        self.assertTrue(math[0].startswith("$$\n\\begin{aligned}"))
        self.assertTrue(math[0].endswith("\\end{aligned}\n$$"))
        self.assertNotIn("list", [btype for _, btype in blocks])
        self.assertNotIn("table", [btype for _, btype in blocks])

    def test_single_line_display_math(self):
        blocks = convert.parse_structural_blocks("$$E = mc^2$$")
        self.assertEqual(blocks, [("$$E = mc^2$$", "math_block")])

    def test_unclosed_dollar_line_falls_back_to_paragraph(self):
        content = "$$ is not closed\n\n# Heading\n\nBody"
        blocks = convert.parse_structural_blocks(content)
        types = [btype for _, btype in blocks]
        self.assertNotIn("math_block", types)
        self.assertIn("heading", types)

    def test_unclosed_dollar_line_does_not_pair_across_blank_line(self):
        # pandoc never pairs $$ across a blank line; a stray opener must not
        # swallow the following heading into one unsplittable block.
        content = "$$ stray opener\n\n# Heading\n\nBody ends with $$"
        blocks = convert.parse_structural_blocks(content)
        types = [btype for _, btype in blocks]
        self.assertNotIn("math_block", types)
        self.assertIn(("# Heading", "heading"), blocks)

    def test_math_block_is_never_split_across_chunks(self):
        formula = "$$\n" + "\n".join(f"x_{i} = {i}" for i in range(40)) + "\n$$"
        content = "A" * 100 + "\n\n" + formula + "\n\n" + "B" * 100
        blocks = convert.parse_structural_blocks(content)
        chunks = convert.merge_blocks_to_chunks(blocks, target_size=200)
        self.assertEqual(sum(formula in chunk for chunk in chunks), 1)


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png"


class ImportMarkdownTests(unittest.TestCase):
    def _source(self, root, markdown, files=()):
        out = Path(root) / "parser_out"
        out.mkdir()
        for rel, data in files:
            path = out / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        md = out / "paper.md"
        md.write_text(markdown, encoding="utf-8")
        return md

    def test_decodes_data_uri_images_into_files(self):
        import base64

        payload = base64.b64encode(PNG_BYTES).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(tmp, f"![fig](data:image/png;base64,{payload})\n")
            temp_dir = Path(tmp) / "book_temp"

            content = convert.import_markdown(str(md), str(temp_dir))

            self.assertEqual(content, "![fig](images/embedded-0001.png)\n")
            self.assertEqual((temp_dir / "images" / "embedded-0001.png").read_bytes(), PNG_BYTES)

    def test_copies_relative_markdown_and_html_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(
                tmp,
                "![a](_page_0_Picture_1.jpeg)\n\n"
                '<img src="images/page_1_table_2.jpg" alt="t"/>\n\n'
                "![c](sub%20dir/c.png \"title\")\n",
                files=[
                    ("_page_0_Picture_1.jpeg", b"jpeg"),
                    ("images/page_1_table_2.jpg", b"jpg"),
                    ("sub dir/c.png", b"png"),
                ],
            )
            temp_dir = Path(tmp) / "book_temp"

            content = convert.import_markdown(str(md), str(temp_dir))

            self.assertIn("![a](images/_page_0_Picture_1.jpeg)", content)
            self.assertIn('<img src="images/page_1_table_2.jpg" alt="t"/>', content)
            self.assertIn('![c](images/c.png "title")', content)
            self.assertEqual(
                sorted(p.name for p in (temp_dir / "images").iterdir()),
                ["_page_0_Picture_1.jpeg", "c.png", "page_1_table_2.jpg"],
            )

    def test_same_basename_with_different_bytes_does_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(
                tmp,
                "![](p1/fig.png)\n![](p2/fig.png)\n![](p1/fig.png)\n",
                files=[("p1/fig.png", b"one"), ("p2/fig.png", b"two")],
            )
            temp_dir = Path(tmp) / "book_temp"

            content = convert.import_markdown(str(md), str(temp_dir))

            self.assertEqual(content, "![](images/fig.png)\n![](images/fig-2.png)\n![](images/fig.png)\n")
            self.assertEqual((temp_dir / "images" / "fig.png").read_bytes(), b"one")
            self.assertEqual((temp_dir / "images" / "fig-2.png").read_bytes(), b"two")

    def test_leaves_remote_and_missing_images_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = "![r](https://example.com/x.png)\n![m](missing.png)\n"
            md = self._source(tmp, text)
            temp_dir = Path(tmp) / "book_temp"

            content = convert.import_markdown(str(md), str(temp_dir))

            self.assertEqual(content, text)
            self.assertFalse((temp_dir / "images").exists())

    def test_strips_front_matter_from_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(tmp, "---\ntitle: Paper\n---\n# Paper\n\nBody\n")

            content = convert.import_markdown(str(md), str(Path(tmp) / "book_temp"))

            self.assertEqual(content, "# Paper\n\nBody\n")


class MarkdownMetadataTests(unittest.TestCase):
    def test_front_matter_scalars_and_lists(self):
        content = "\n".join(
            [
                "---",
                'title: "Attention Is All You Need"',
                "author:",
                "  - Ashish Vaswani",
                "  - Noam Shazeer",
                "lang: en",
                "tags: [nlp]",
                "---",
                "# Ignored Heading",
            ]
        )
        self.assertEqual(
            convert.markdown_metadata(content),
            {
                "title": "Attention Is All You Need",
                "creator": "Ashish Vaswani, Noam Shazeer",
                "language": "en",
            },
        )

    def test_title_falls_back_to_first_h1(self):
        content = "```\n# not a heading\n```\n\n# Deep Residual Learning {#title}\n\n# 1 Introduction\n"
        self.assertEqual(convert.markdown_metadata(content), {"title": "Deep Residual Learning"})

    def test_no_title_when_first_heading_is_a_section(self):
        self.assertEqual(convert.markdown_metadata("## 1 Introduction\n\n# Later H1\n"), {})

    def test_leading_rule_without_keys_is_not_front_matter(self):
        content = "---\nJust a rule.\n---\n\nBody\n"
        metadata, body = convert.split_front_matter(content)
        self.assertEqual(metadata, {})
        self.assertEqual(body, content)


class MarkdownInputEndToEndTests(unittest.TestCase):
    CONVERT = str(SCRIPT_DIR / "convert.py")

    def _run(self, workdir, *args):
        import subprocess

        return subprocess.run(
            [sys.executable, self.CONVERT, *args],
            cwd=workdir, capture_output=True, text=True,
        )

    def _setup(self, tmp):
        import base64

        workdir = Path(tmp) / "work"
        parser_out = workdir / "mineru_out"
        (parser_out / "images").mkdir(parents=True)
        (parser_out / "images" / "fig1.jpg").write_bytes(b"jpg-bytes")
        payload = base64.b64encode(PNG_BYTES).decode("ascii")
        md = parser_out / "paper.md"
        md.write_text(
            "# Attention Is All You Need\n\n"
            "## 3.2 Attention\n\n"
            "![](images/fig1.jpg)\n\n"
            f"![](data:image/png;base64,{payload})\n\n"
            "$$\nA = \\mathrm{softmax}(QK^T)V\n$$\n\n"
            "<table><tr><td>a</td><td>b</td></tr></table>\n",
            encoding="utf-8",
        )
        return workdir, md

    def test_markdown_input_produces_chunks_images_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, md = self._setup(tmp)

            result = self._run(workdir, str(md.relative_to(workdir)), "--olang", "zh")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Markdown Import", result.stdout)
            temp_dir = workdir / "paper_temp"
            input_md = (temp_dir / "input.md").read_text(encoding="utf-8")
            self.assertIn("![](images/fig1.jpg)", input_md)
            self.assertIn("![](images/embedded-0001.png)", input_md)
            self.assertIn("$$\nA = \\mathrm{softmax}(QK^T)V\n$$", input_md)
            self.assertEqual((temp_dir / "images" / "fig1.jpg").read_bytes(), b"jpg-bytes")
            self.assertEqual((temp_dir / "images" / "embedded-0001.png").read_bytes(), PNG_BYTES)
            self.assertTrue(list(temp_dir.glob("chunk*.md")))
            self.assertTrue((temp_dir / "manifest.json").exists())
            self.assertTrue((temp_dir / convert.SOURCE_FINGERPRINT_FILE).exists())
            self.assertFalse((temp_dir / "input.html").exists())
            config = (temp_dir / "config.txt").read_text(encoding="utf-8")
            self.assertIn("conversion_method=markdown", config)
            self.assertIn("original_title=Attention Is All You Need", config)

            rerun = self._run(workdir, str(md.relative_to(workdir)))
            self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
            self.assertIn("Skipping Markdown import", rerun.stdout)
            self.assertIn("original_title=Attention Is All You Need",
                          (temp_dir / "config.txt").read_text(encoding="utf-8"))

    def test_changed_source_markdown_aborts_instead_of_reusing_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, md = self._setup(tmp)
            self.assertEqual(self._run(workdir, str(md)).returncode, 0)

            md.write_text("# Different paper\n\nOther body\n", encoding="utf-8")
            result = self._run(workdir, str(md))

            self.assertEqual(result.returncode, 1)
            self.assertIn("different source bytes", result.stdout)

    def test_strip_page_numbers_rejected_for_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, md = self._setup(tmp)

            result = self._run(workdir, str(md), "--strip-page-numbers")

            self.assertEqual(result.returncode, 1)
            self.assertIn("does not apply to Markdown input", result.stdout)
            self.assertFalse((workdir / "paper_temp").exists())

    def test_empty_markdown_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "blank.markdown").write_text("---\ntitle: X\n---\n\n", encoding="utf-8")

            result = self._run(workdir, "blank.markdown")

            self.assertEqual(result.returncode, 1)
            self.assertIn("no content to translate", result.stdout)
            self.assertFalse((workdir / "blank_temp" / "input.md").exists())


if __name__ == "__main__":
    unittest.main()
