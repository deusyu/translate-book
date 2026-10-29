import contextlib
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    def test_detection_off_keeps_every_standalone_digit(self):
        # EPUB/DOCX: chapter numbers next to a chapter div and a numbered
        # stanza sequence are content, though both layers would drop them.
        content = "\n".join(
            [
                "::: {.calibre}",
                "4",
                "",
                "## Chapter Four",
                ":::",
                "",
                "1",
                "",
                "Stanza one.",
                "",
                "2",
                "",
                "Stanza two.",
                "",
                "3",
                "",
                "Stanza three.",
                "",
                "4",
                "",
                "Stanza four.",
            ]
        )

        def digit_lines(text):
            return [line for line in text.split("\n") if line.isdigit()]

        self.assertEqual(digit_lines(convert.clean_calibre_markers(content)), [])
        cleaned = convert.clean_calibre_markers(content, detect_page_numbers=False)

        self.assertNotIn(":::", cleaned)
        self.assertEqual(digit_lines(cleaned), ["4", "1", "2", "3", "4"])

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


def _str(text):
    return {"t": "Str", "c": text}


def _sp():
    return {"t": "Space"}


def _words(text):
    out = []
    for i, word in enumerate(text.split(" ")):
        if i:
            out.append(_sp())
        out.append(_str(word))
    return out


def _link(inlines, url, ident=""):
    return {"t": "Link", "c": [[ident, [], []], inlines, [url, ""]]}


def _sup(inlines):
    return {"t": "Superscript", "c": inlines}


def _span(inlines, ident="", classes=(), attrs=()):
    return {"t": "Span", "c": [[ident, list(classes), [list(kv) for kv in attrs]], inlines]}


def _para(inlines):
    return {"t": "Para", "c": inlines}


def _plain(inlines):
    return {"t": "Plain", "c": inlines}


def _div(blocks, ident=""):
    return {"t": "Div", "c": [[ident, ["calibre"], []], blocks]}


def _header(level, inlines, ident="", classes=()):
    return {"t": "Header", "c": [level, [ident, list(classes), []], inlines]}


def _note(blocks):
    return {"t": "Note", "c": blocks}


def _doc(blocks):
    return {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": blocks}


def _sup_ref(marker, target, ident):
    """<a href="#target" id="ident"><sup>marker</sup></a>, as pandoc reads it."""
    return _link([_sup([_str(marker)])], "#" + target, ident)


class NormalizeCalibreAstTests(unittest.TestCase):
    def test_sup_reference_and_backlinked_note_become_a_footnote(self):
        doc = _doc([
            _para(_words("A footnote ref.") + [_sup_ref("1", "calibre_link-2", "calibre_link-3")]),
            _para([_sup_ref("1", "calibre_link-3", "")] + [_sp()] + _words("First chapter note.")),
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _para(_words("A footnote ref.") + [_note([_para(_words("First chapter note."))])]),
        ])

    def test_numbering_that_restarts_per_chapter_pairs_by_reference_id(self):
        doc = _doc([
            _div([
                _para(_words("Chapter one.") + [_sup_ref("1", "n1", "r1")]),
                _para([_sup_ref("1", "r1", "")] + [_sp()] + _words("Note of chapter one.")),
            ], "ch1"),
            _div([
                _para(_words("Chapter two.") + [_sup_ref("1", "n2", "r2")]),
                _para([_sup_ref("1", "r2", "")] + [_sp()] + _words("Note of chapter two.")),
            ], "ch2"),
        ])

        convert.normalize_calibre_ast(doc)

        first, second = (div["c"][1] for div in doc["blocks"])
        self.assertEqual(first, [_para(_words("Chapter one.") + [_note([_para(_words("Note of chapter one."))])])])
        self.assertEqual(second, [_para(_words("Chapter two.") + [_note([_para(_words("Note of chapter two."))])])])

    def test_link_inside_superscript_with_plain_number_backlink(self):
        doc = _doc([
            _para(_words("Text.") + [_sup([_link([_str("2")], "#n2", "r2")])]),
            _para([_link([_str("2")], "#r2"), _str("."), _sp()] + _words("Note two.")),
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _para(_words("Text.") + [_note([_para(_words("Note two."))])]),
        ])

    def test_div_wrapped_note_keeps_every_paragraph(self):
        doc = _doc([
            _para(_words("Text.") + [_sup_ref("4", "n4", "r4")]),
            _div([
                _para([_link([_str("4")], "#r4"), _sp()] + _words("Para one.")),
                _para(_words("Para two.")),
            ], "n4"),
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _para(_words("Text.") + [_note([_para(_words("Para one.")), _para(_words("Para two."))])]),
        ])

    def test_endnote_list_disappears_once_its_notes_move(self):
        doc = _doc([
            _para(_words("Text.") + [_sup_ref("1", "n1", "r1")]),
            {"t": "OrderedList", "c": [
                [1, {"t": "Decimal"}, {"t": "Period"}],
                [[_para([_link([_str("1")], "#r1"), _sp()] + _words("Endnote."))]],
            ]},
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _para(_words("Text.") + [_note([_para(_words("Endnote."))])]),
        ])

    def test_unpaired_reference_degrades_to_a_plain_superscript(self):
        doc = _doc([_para(_words("Text.") + [_sup_ref("7", "nowhere", "r7")])])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [_para(_words("Text.") + [_sup([_str("7")])])])

    def test_backlink_with_a_different_marker_is_not_paired(self):
        doc = _doc([
            _para(_words("Text.") + [_sup_ref("1", "n1", "r1")]),
            _para([_sup_ref("2", "r1", "")] + [_sp()] + _words("Some note.")),
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _para(_words("Text.") + [_sup([_str("1")])]),
            _para([_sup([_str("2")]), _sp()] + _words("Some note.")),
        ])

    def test_two_way_links_without_superscript_are_not_footnotes(self):
        # A table of contents links to headings that link back — same shape
        # as a note, minus the superscript reference.
        doc = _doc([
            _para([_link([_str("1")], "#c1", "t1"), _sp()] + _words("Chapter One")),
            _para([_link([_str("1")], "#t1"), _sp()] + _words("Chapter One")),
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _para(_words("1 Chapter One")),
            _para(_words("1 Chapter One")),
        ])

    def test_note_whose_reference_cannot_be_placed_is_kept(self):
        # Each note cites the other, so neither reference survives in the
        # body; both notes must still end up in the document.
        doc = _doc([
            _para([_sup_ref("1", "rA", ""), _sp()] + _words("Alpha") + [_sup_ref("2", "nB", "rB")]),
            _para([_sup_ref("2", "rB", ""), _sp()] + _words("Beta") + [_sup_ref("1", "nA", "rA")]),
        ])

        convert.normalize_calibre_ast(doc)

        text = repr(doc["blocks"])
        self.assertIn("'Alpha'", text)
        self.assertIn("'Beta'", text)

    def test_empty_anchors_spans_and_internal_links_are_unwrapped(self):
        doc = _doc([
            _plain([_span([], "calibre_link-8")]),
            _header(2, [_span([], "calibre_link-10", ["calibre1"])] + _words("Chapter One"),
                    "calibre_link-9", ["ch_title"]),
            _para(
                _words("An anchor") + [_span([], "calibre_link-11"), _sp()]
                + _words("an internal") + [_sp(), _link(_words("cross-reference"), "#calibre_link-1")]
                + [_sp(), _span(_words("sic erat"), attrs=[("lang", "lat")])]
                + [_sp(), _link([_str("site")], "https://example.com", "calibre_link-5")]
            ),
        ])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [
            _header(2, _words("Chapter One")),
            _para(
                _words("An anchor an internal cross-reference sic erat")
                + [_sp(), _link([_str("site")], "https://example.com")]
            ),
        ])

    def test_text_split_by_an_unwrapped_link_is_rejoined(self):
        doc = _doc([_para([_link([_str("3")], "#x"), _str("."), _sp(), _str("Note")])])

        convert.normalize_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [_para([_str("3."), _sp(), _str("Note")])])


def _pandoc_available():
    try:
        import pypandoc

        pypandoc.get_pandoc_version()
        return True
    except Exception:
        return False


# Calibre's HTMLZ output for the reproduction EPUB attached to GitHub issues
# #49 and #50 (calibre 7.6): page anchors, a cross-reference, a lang span,
# Unicode dashes, and per-chapter footnotes that both restart at 1.
CALIBRE_REPRO_HTML = """<html><head><meta http-equiv="Content-Type" content="text/html;charset=utf-8" /><title>Repro</title></head><body><div class="calibre" id="calibre_link-0">
<a id="calibre_link-8"></a>
<h2 class="ch_title" id="calibre_link-9"><a id="calibre_link-10" class="calibre1"></a>Chapter One</h2>
<p class="calibre2">A paragraph with a page anchor<a id="calibre_link-11"></a> in the middle, an
internal <a href="#calibre_link-1">cross-reference</a>, a
<span lang="lat">sic erat scriptum</span> span, an em dash&mdash;like this,
an en dash (pp. 12&ndash;34), and a footnote ref.<a href="#calibre_link-2" id="calibre_link-3"><sup class="calibre3">1</sup></a></p>
<p class="calibre2" id="calibre_link-2"><a href="#calibre_link-3"><sup class="calibre3">1</sup></a> First chapter note.</p>
</div>

<div class="calibre" id="calibre_link-5">
<a id="calibre_link-12"></a>
<h2 class="ch_title" id="calibre_link-13">Chapter Two</h2>
<p class="calibre2">Another chapter, whose footnote numbering restarts at one.<a href="#calibre_link-6" id="calibre_link-7"><sup class="calibre3">1</sup></a></p>
<p class="calibre2" id="calibre_link-6"><a href="#calibre_link-7"><sup class="calibre3">1</sup></a> Second chapter note.</p>
</div>

<div class="calibre" id="calibre_link-4">
<p class="calibre2" id="calibre_link-1">Doe, J. <em class="calibre4">Some Book</em>. Press, 2001.</p>
<p class="calibre2">&mdash;&mdash;&mdash; <em class="calibre4">Another Book</em>. Press, 2005.</p>
<p class="calibre2">&mdash;&mdash;&mdash;</p>
</div>

</body></html>"""


@unittest.skipUnless(_pandoc_available(), "pandoc and pypandoc are required")
class CalibreHtmlToMarkdownTests(unittest.TestCase):
    def _convert(self, html):
        with tempfile.TemporaryDirectory() as tmp:
            html_file = Path(tmp) / "input.html"
            md_file = Path(tmp) / "input.md"
            html_file.write_text(html, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(convert.convert_html_to_markdown(str(html_file), str(md_file)))
            return md_file.read_text(encoding="utf-8")

    def test_issue_49_50_reproduction(self):
        md = self._convert(CALIBRE_REPRO_HTML)

        self.assertIn("## Chapter One\n", md)
        self.assertIn(
            "A paragraph with a page anchor in the middle, an internal cross-reference, "
            "a sic erat scriptum span, an em dash—like this, an en dash (pp. 12–34), "
            "and a footnote ref.[^1]",
            md,
        )
        self.assertIn("[^1]: First chapter note.", md)
        self.assertIn("restarts at one.[^2]", md)
        self.assertIn("[^2]: Second chapter note.", md)
        self.assertIn("\n——— *Another Book*. Press, 2005.\n", md)
        self.assertIn("\n———\n", md)
        for artifact in ("[]", "{", "[cross-reference]", "^1^", "---", "--"):
            self.assertNotIn(artifact, md)

    def test_every_footnote_reference_has_one_definition(self):
        md = self._convert(CALIBRE_REPRO_HTML)

        definitions = re.findall(r"^\[\^([^\]]+)\]:", md, flags=re.MULTILINE)
        references = re.findall(r"\[\^([^\]]+)\](?!:)", md)
        self.assertEqual(sorted(definitions), ["1", "2"])
        self.assertEqual(sorted(references), sorted(definitions))


class PageNumberDetectionDefaultTests(unittest.TestCase):
    def _detect_flag(self, filename, *extra_args):
        """Run main() on a cached input.html and return detect_page_numbers."""
        calls = []

        def fake_convert(html_file, md_file, strip_page_numbers=False, detect_page_numbers=True):
            calls.append(detect_page_numbers)
            Path(md_file).write_text("# Title\n\nBody text.\n", encoding="utf-8")
            return True

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / filename
            source.write_bytes(b"book bytes")
            temp_dir = Path(convert.build_temp_dir(str(source), tmp))
            temp_dir.mkdir()
            (temp_dir / "input.html").write_text("<html></html>", encoding="utf-8")
            argv = ["convert.py", str(source), "--temp-root", tmp, *extra_args]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(convert, "find_calibre_convert", return_value="ebook-convert"), \
                    mock.patch.object(convert, "convert_html_to_markdown", side_effect=fake_convert), \
                    contextlib.redirect_stdout(io.StringIO()):
                convert.main()
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_only_pdf_input_detects_page_numbers_by_default(self):
        self.assertTrue(self._detect_flag("book.pdf"))
        self.assertFalse(self._detect_flag("book.epub"))
        self.assertFalse(self._detect_flag("book.docx"))

    def test_flag_enables_detection_for_epub(self):
        self.assertTrue(self._detect_flag("book.epub", "--detect-page-numbers"))


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

    def test_detect_page_numbers_rejected_for_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, md = self._setup(tmp)

            result = self._run(workdir, str(md), "--detect-page-numbers")

            self.assertEqual(result.returncode, 1)
            self.assertIn("--detect-page-numbers only cleans Calibre output", result.stdout)
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
