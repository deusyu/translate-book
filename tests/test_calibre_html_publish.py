import process_guard  # noqa: F401  first, so it guards every process the tests below start
import contextlib
import inspect
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import calibre_html_publish  # noqa: E402
import merge_and_build  # noqa: E402

# Characters that are special in HTML, and a backslash.
TITLE = "O'Brien's \"Third\" Policeman & List<T>"
AUTHOR = "Flann O'Brien & \"Myles\" <na gCopaleen> \\1"


def _write_book_doc(path):
    """Write book_doc.html the way merge_and_build.py does."""
    return merge_and_build.apply_template_to_html(
        "<p>x</p>", str(SCRIPT_DIR / "template_ebook.html"), str(path),
        TITLE, merge_and_build.get_lang_config("en"), AUTHOR,
    )


class ExtractHtmlMetadataTests(unittest.TestCase):
    def _extract(self, html):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "in.html"
            path.write_text(html, encoding="utf-8")
            return calibre_html_publish.extract_html_metadata(str(path))

    def test_reads_back_what_merge_and_build_writes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            book = Path(temp_dir) / "book_doc.html"
            self.assertTrue(_write_book_doc(book))
            self.assertEqual(calibre_html_publish.extract_html_metadata(str(book)), (TITLE, AUTHOR))

    def test_reads_a_quote_inside_the_other_kind_of_quote(self):
        self.assertEqual(
            self._extract('<html><head><title>T</title><meta name="author" content="Flann O\'Brien">'),
            ("T", "Flann O'Brien"),
        )
        self.assertEqual(
            self._extract("<html><head><title>T</title><meta name='author' content='Myles \"na gCopaleen\"'>"),
            ("T", 'Myles "na gCopaleen"'),
        )

    def test_decodes_character_references(self):
        html = (
            '<html><head><meta name="author" content="Tom &amp; Jerry &lt;TJ&gt; &#39;s"></head>'
            "<body><h1>Q&amp;A <em>&lt;T&gt;</em></h1></body></html>"
        )
        self.assertEqual(self._extract(html), ("Q&A <T>", "Tom & Jerry <TJ> 's"))


class FontForLanguageTests(unittest.TestCase):
    def test_fonts_follow_the_primary_language(self):
        # kok is Konkani, kos Kosraean, jav Javanese, jam Jamaican Creole and zha Zhuang.
        cases = {
            "zh-CN": "FangSong", "zh-TW": "FangSong", "zh_TW": "FangSong", "zh-Hant-HK": "FangSong",
            "ja": "Hiragino Mincho ProN", "ja-JP": "Hiragino Mincho ProN",
            "ko": "Nanum Myeongjo", "ko-KR": "Nanum Myeongjo",
            "kok": "Georgia", "kos": "Georgia", "jav": "Georgia", "jam": "Georgia", "zha": "Georgia",
            "it": "Georgia", "pt-BR": "Georgia",
        }
        for lang, font in cases.items():
            with self.subTest(lang=lang):
                family = calibre_html_publish._get_font_family_for_lang(lang)
                self.assertEqual(family.split(",")[0].strip('"'), font)
                self.assertEqual(calibre_html_publish._get_pdf_font_for_lang(lang), font)

    def test_formats_pick_fonts_from_the_same_entry_as_book_html(self):
        for code in ("zh", "zh-TW", "ja", "ko-KR", "en", "it", "kok", "jav", "zha", "zho", "kor"):
            with self.subTest(code=code):
                cfg = merge_and_build.get_lang_config(code)
                lang = cfg["lang_attr"]
                self.assertEqual(calibre_html_publish._get_font_family_for_lang(lang), cfg["font_family_ebook"])
                self.assertEqual(calibre_html_publish._get_pdf_font_for_lang(lang), cfg["pdf_font"])


class ConvertHtmlWithCalibreTests(unittest.TestCase):
    def test_builds_expected_epub_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_html = Path(temp_dir) / "input.html"
            output_file = Path(temp_dir) / "output.epub"
            input_html.write_text("<html><head><title>Book</title></head></html>", encoding="utf-8")

            def fake_run(cmd, capture_output, text, timeout):
                output_file.write_text("epub", encoding="utf-8")
                return mock.Mock(returncode=0, stderr="")

            with mock.patch.object(
                calibre_html_publish, "find_calibre_convert", return_value="/usr/bin/ebook-convert"
            ), mock.patch.object(
                calibre_html_publish, "extract_html_metadata", return_value=("Book", "Author")
            ), mock.patch.object(
                calibre_html_publish.subprocess, "run", side_effect=fake_run
            ) as run_mock:
                ok = calibre_html_publish.convert_html_with_calibre(
                    str(input_html), str(output_file), "epub", timeout=12, lang="ja"
                )

            self.assertTrue(ok)
            cmd = run_mock.call_args.args[0]
            self.assertEqual(cmd[0], "/usr/bin/ebook-convert")
            self.assertEqual(cmd[1], str(input_html))
            self.assertEqual(cmd[2], str(output_file))
            self.assertIn("--title", cmd)
            self.assertIn("--authors", cmd)
            self.assertIn("--language", cmd)
            self.assertIn("ja", cmd)
            self.assertIn("--epub-version", cmd)
            self.assertIn("3", cmd)
            self.assertNotIn("--disable-font-rescaling", cmd)
            # TOC comes from heading levels, in any language.
            self.assertEqual(cmd[cmd.index("--level1-toc") + 1], "//h:h1")
            self.assertEqual(cmd[cmd.index("--level2-toc") + 1], "//h:h2")

    def test_h2_chapters_share_toc_level_one_with_the_title(self):
        # Pandoc's title block is the only h1 when the chapters are h2. With
        # `//h:h1` as level 1, every chapter would nest under the book title.
        with tempfile.TemporaryDirectory() as temp_dir:
            input_html = Path(temp_dir) / "input.html"
            output_file = Path(temp_dir) / "output.epub"
            input_html.write_text(
                '<html><body><header id="title-block-header"><h1 class="title">Book</h1></header>'
                '<h2 id="one">One</h2><h3 id="one-1">1.1</h3><h2 id="two">Two</h2></body></html>',
                encoding="utf-8",
            )

            def fake_run(cmd, capture_output, text, timeout):
                output_file.write_text("epub", encoding="utf-8")
                return mock.Mock(returncode=0, stderr="")

            with mock.patch.object(
                calibre_html_publish, "find_calibre_convert", return_value="/usr/bin/ebook-convert"
            ), mock.patch.object(
                calibre_html_publish, "extract_html_metadata", return_value=("Book", "Author")
            ), mock.patch.object(
                calibre_html_publish.subprocess, "run", side_effect=fake_run
            ) as run_mock:
                ok = calibre_html_publish.convert_html_with_calibre(
                    str(input_html), str(output_file), "epub", timeout=12, lang="zh-CN"
                )

            self.assertTrue(ok)
            cmd = run_mock.call_args.args[0]
            self.assertEqual(cmd[cmd.index("--level1-toc") + 1], "//h:h1 | //h:h2")
            self.assertEqual(cmd[cmd.index("--level2-toc") + 1], "//h:h3")

    @unittest.skipUnless(
        "cover" in inspect.signature(calibre_html_publish.convert_html_with_calibre).parameters,
        "cover parameter unavailable",
    )
    def test_includes_cover_argument_when_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_html = Path(temp_dir) / "input.html"
            output_file = Path(temp_dir) / "output.epub"
            cover_file = Path(temp_dir) / "cover.jpg"
            input_html.write_text("<html><head><title>Book</title></head></html>", encoding="utf-8")
            cover_file.write_text("img", encoding="utf-8")

            def fake_run(cmd, capture_output, text, timeout):
                output_file.write_text("epub", encoding="utf-8")
                return mock.Mock(returncode=0, stderr="")

            with mock.patch.object(
                calibre_html_publish, "find_calibre_convert", return_value="/usr/bin/ebook-convert"
            ), mock.patch.object(
                calibre_html_publish, "extract_html_metadata", return_value=("Book", "Author")
            ), mock.patch.object(
                calibre_html_publish.subprocess, "run", side_effect=fake_run
            ) as run_mock:
                ok = calibre_html_publish.convert_html_with_calibre(
                    str(input_html),
                    str(output_file),
                    "epub",
                    timeout=12,
                    lang="ja",
                    cover=str(cover_file),
                )

            self.assertTrue(ok)
            cmd = run_mock.call_args.args[0]
            self.assertIn("--cover", cmd)
            self.assertIn(str(cover_file), cmd)

    def test_passes_the_whole_title_and_author_for_every_format(self):
        def fake_run(cmd, capture_output, text, timeout):
            Path(cmd[2]).write_text("x", encoding="utf-8")
            return mock.Mock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            book = Path(temp_dir) / "book_doc.html"
            self.assertTrue(_write_book_doc(book))
            for fmt in ("docx", "epub", "pdf"):
                with self.subTest(fmt=fmt):
                    argv = ["calibre_html_publish.py", str(book), "-o", str(Path(temp_dir) / f"book.{fmt}")]
                    with mock.patch.object(sys, "argv", argv), mock.patch.object(
                        calibre_html_publish, "find_calibre_convert", return_value="ebook-convert"
                    ), mock.patch.object(
                        calibre_html_publish.subprocess, "run", side_effect=fake_run
                    ) as run_mock, contextlib.redirect_stdout(io.StringIO()):
                        calibre_html_publish.main()

                    cmd = run_mock.call_args.args[0]
                    self.assertEqual(cmd[cmd.index("--authors") + 1], AUTHOR)
                    self.assertEqual(cmd[cmd.index("--title") + 1], TITLE)


class WorkingDirectoryTests(unittest.TestCase):
    """Calibre reads from a directory of the run's own, which goes afterwards; nothing else is touched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.book = self.root / "book_doc.html"
        self.assertTrue(_write_book_doc(self.book))
        (self.root / "images").mkdir()
        (self.root / "images" / "fig1.png").write_bytes(b"png")
        # The HTML each Calibre run read, and what was beside it then.
        self.seen = []

    def convert(self, calibre=None):
        def run(cmd, capture_output, text, timeout):
            work = Path(cmd[1]).parent
            self.seen.append((Path(cmd[1]), sorted(p.relative_to(work).as_posix() for p in work.rglob("*"))))
            if calibre:
                return calibre(cmd)
            Path(cmd[2]).write_text("x", encoding="utf-8")
            return mock.Mock(returncode=0, stderr="")

        out = io.StringIO()
        argv = ["calibre_html_publish.py", str(self.book), "-o", str(self.root / "book.docx")]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            calibre_html_publish, "find_calibre_convert", return_value="ebook-convert"
        ), mock.patch.object(calibre_html_publish.subprocess, "run", side_effect=run), \
                contextlib.redirect_stdout(out):
            try:
                calibre_html_publish.main()
            except SystemExit as e:
                return e.code, out.getvalue()
        return 0, out.getvalue()

    def tree(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))

    def test_a_directory_with_the_old_fixed_name_is_left_alone(self):
        # Earlier versions worked in <base>_conversion_temp, made with exist_ok
        # and removed with rmtree whatever was in it.
        old = self.root / "book_doc_conversion_temp"
        old.mkdir()
        (old / "keep.txt").write_text("keep", encoding="utf-8")
        before = self.tree()

        code, _ = self.convert()

        self.assertEqual(code, 0)
        self.assertEqual(self.tree(), sorted(before + ["book.docx"]))
        self.assertEqual((old / "keep.txt").read_text(encoding="utf-8"), "keep")
        html, beside = self.seen[0]
        self.assertEqual(html.parent.parent, self.root)
        self.assertTrue(html.parent.name.startswith("book_doc_conversion_"))
        self.assertNotEqual(html.parent, old)
        self.assertEqual(beside, ["images", "images/fig1.png", "work.html"])

    def test_each_run_works_in_a_new_directory_that_goes_afterwards(self):
        before = self.tree()
        for _ in range(2):
            self.assertEqual(self.convert()[0], 0)
        first, second = (html.parent for html, _ in self.seen)
        self.assertNotEqual(first, second)
        self.assertEqual(self.tree(), sorted(before + ["book.docx"]))

    def test_the_working_directory_goes_when_the_conversion_fails(self):
        def fails(cmd):
            return mock.Mock(returncode=1, stderr="Calibre failed")

        def crashes(cmd):
            raise OSError("ebook-convert crashed")

        def is_interrupted(cmd):
            raise KeyboardInterrupt

        before = self.tree()
        for calibre in [fails, crashes, is_interrupted]:
            with self.subTest(calibre=calibre.__name__):
                code, _ = self.convert(calibre)
                self.assertEqual(code, 1)
                self.assertEqual(self.tree(), before)

    def test_a_directory_put_in_place_of_the_working_directory_is_left(self):
        def swap(cmd):
            work = Path(cmd[1]).parent
            work.rename(self.root / "moved")
            work.mkdir()
            (work / "theirs.txt").write_text("theirs", encoding="utf-8")
            Path(cmd[2]).write_text("x", encoding="utf-8")
            return mock.Mock(returncode=0, stderr="")

        code, out = self.convert(swap)

        self.assertEqual(code, 0)
        work = self.seen[0][0].parent
        self.assertEqual((work / "theirs.txt").read_text(encoding="utf-8"), "theirs")
        self.assertIn(f"Warning: {work} was left as it is", out)
        # The run's own directory, emptied through the descriptor it held.
        self.assertEqual(list((self.root / "moved").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
