import inspect
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import calibre_html_publish  # noqa: E402
import merge_and_build  # noqa: E402


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
            ) as run_mock, mock.patch.object(
                calibre_html_publish.signal, "signal"
            ), mock.patch.object(
                calibre_html_publish.signal, "alarm"
            ):
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
            ) as run_mock, mock.patch.object(
                calibre_html_publish.signal, "signal"
            ), mock.patch.object(
                calibre_html_publish.signal, "alarm"
            ):
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

class ExtractCoverFromEpubTests(unittest.TestCase):
    @unittest.skipUnless(
        hasattr(merge_and_build, "extract_cover_from_epub"),
        "cover extraction support not merged yet",
    )
    def test_extracts_cover_from_opf_cover_image_item(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_file = Path(temp_dir) / "source.epub"
            output_dir = Path(temp_dir) / "extract"
            cover_bytes = b"fake-image"

            with zipfile.ZipFile(epub_file, "w") as zf:
                zf.writestr(
                    "META-INF/container.xml",
                    """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
                )
                zf.writestr(
                    "decoy/unused.opf",
                    """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata />
  <manifest />
</package>
""",
                )
                zf.writestr(
                    "OEBPS/content.opf",
                    """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata />
  <manifest>
    <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" />
  </manifest>
</package>
""",
                )
                zf.writestr("OEBPS/images/cover.jpg", cover_bytes)

            extracted = merge_and_build.extract_cover_from_epub(
                str(epub_file), str(output_dir)
            )

            self.assertIsNotNone(extracted)
            self.assertTrue(Path(extracted).exists())
            self.assertEqual(Path(extracted).read_bytes(), cover_bytes)

    @unittest.skipUnless(
        hasattr(merge_and_build, "extract_cover_from_epub"),
        "cover extraction support not merged yet",
    )
    def test_extracts_cover_via_meta_cover_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            epub_file = Path(temp_dir) / "source.epub"
            output_dir = Path(temp_dir) / "extract"
            cover_bytes = b"fallback-image"

            with zipfile.ZipFile(epub_file, "w") as zf:
                zf.writestr(
                    "META-INF/container.xml",
                    """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
                )
                zf.writestr(
                    "decoy/unused.opf",
                    """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata />
  <manifest />
</package>
""",
                )
                zf.writestr(
                    "OPS/package.opf",
                    """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata>
    <meta name="cover" content="cover-item" />
  </metadata>
  <manifest>
    <item id="cover-item" href="images/fallback.jpg" media-type="image/jpeg" />
  </manifest>
</package>
""",
                )
                zf.writestr("OPS/images/fallback.jpg", cover_bytes)

            extracted = merge_and_build.extract_cover_from_epub(
                str(epub_file), str(output_dir)
            )

            self.assertIsNotNone(extracted)
            self.assertTrue(Path(extracted).exists())
            self.assertEqual(Path(extracted).read_bytes(), cover_bytes)


if __name__ == "__main__":
    unittest.main()
