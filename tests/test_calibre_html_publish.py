import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import calibre_html_publish  # noqa: E402


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


class LanguageFontTests(unittest.TestCase):
    def test_vietnamese_font_family(self):
        family = calibre_html_publish._get_font_family_for_lang("vi")

        self.assertIn("Times New Roman", family)
        self.assertIn("Noto Serif", family)
        self.assertIn("DejaVu Serif", family)

    def test_vietnamese_pdf_font(self):
        self.assertEqual(calibre_html_publish._get_pdf_font_for_lang("vi"), "Times New Roman")

    def test_vietnamese_matching_is_case_insensitive(self):
        self.assertEqual(
            calibre_html_publish._get_font_family_for_lang("VI"),
            calibre_html_publish._get_font_family_for_lang("vi"),
        )

    def test_vietnamese_does_not_fall_through_to_generic_default(self):
        self.assertNotEqual(
            calibre_html_publish._get_font_family_for_lang("vi"),
            calibre_html_publish._get_font_family_for_lang("xx"),
        )
        self.assertNotEqual(
            calibre_html_publish._get_pdf_font_for_lang("vi"),
            calibre_html_publish._get_pdf_font_for_lang("xx"),
        )

    def test_other_languages_unchanged(self):
        self.assertEqual(calibre_html_publish._get_pdf_font_for_lang("zh-CN"), "FangSong")
        self.assertEqual(calibre_html_publish._get_pdf_font_for_lang("ko"), "Nanum Myeongjo")
        self.assertEqual(calibre_html_publish._get_pdf_font_for_lang("xx"), "Georgia")

    def test_cli_lang_default_is_vietnamese(self):
        # Callers may invoke the CLI without --lang, so the parser default is
        # what lands in the exported metadata.
        args = calibre_html_publish.build_arg_parser().parse_args(["in.html", "-o", "out.epub"])
        self.assertEqual(args.lang, "vi")

    def test_default_lang_resolves_to_the_vietnamese_font_stack(self):
        args = calibre_html_publish.build_arg_parser().parse_args(["in.html", "-o", "out.epub"])
        self.assertIn("Noto Serif", calibre_html_publish._get_font_family_for_lang(args.lang))


if __name__ == "__main__":
    unittest.main()
