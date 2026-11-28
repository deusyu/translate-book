import process_guard  # noqa: F401  first, so it guards every process the tests below start
import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unicodedata
import unittest
import zipfile
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


class EmptyAnchorLineTests(unittest.TestCase):
    def test_drops_lines_holding_only_empty_anchors(self):
        content = "\n".join(
            [
                "[]",
                "",
                "## Chapter",
                "",
                "Text.",
                "",
                "[]{#page_3 .calibre1}",
                "",
                "[[]{#a}](#b)",
            ]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertNotIn("[", cleaned)
        self.assertIn("## Chapter", cleaned)
        self.assertIn("Text.", cleaned)

    def test_anchor_line_still_shields_digit_from_calibre_fence(self):
        # The digit's nearest neighbour is the anchor line, not the ::: fence,
        # exactly as before anchor lines were dropped.
        content = "\n".join(["::: {.calibre}", "[]", "", "1", "", "## Chapter One"])

        cleaned = convert.clean_calibre_markers(content)

        self.assertIn("\n1\n", f"\n{cleaned}\n")
        self.assertNotIn("[]", cleaned)

    def test_keeps_bracketed_text_lines(self):
        cleaned = convert.clean_calibre_markers("[1]\n\n[Note]\n")

        self.assertIn("[1]", cleaned)
        self.assertIn("[Note]", cleaned)

    def test_keeps_empty_list_literals_in_code_blocks(self):
        # Pandoc writes attributed code fenced at column 0 and plain code indented.
        content = "\n".join(
            ["``` python", "y = [", "[]", "]", "```", "", "    x = (", "    []", "    )"]
        )

        cleaned = convert.clean_calibre_markers(content)

        self.assertIn("y = [\n[]\n]", cleaned)
        self.assertIn("    x = (\n    []\n    )", cleaned)


SPACE = {"t": "Space"}


def _str(text):
    return {"t": "Str", "c": text}


def _words(text):
    inlines = []
    for word in text.split(" "):
        if inlines:
            inlines.append(SPACE)
        inlines.append(_str(word))
    return inlines


def _sup(*inlines):
    return {"t": "Superscript", "c": list(inlines)}


def _link(target, *inlines, ident=""):
    return {"t": "Link", "c": [[ident, [], []], list(inlines), [target, ""]]}


def _span(*inlines, ident="", attrs=()):
    return {"t": "Span", "c": [[ident, [], [list(a) for a in attrs]], list(inlines)]}


def _para(*inlines):
    return {"t": "Para", "c": list(inlines)}


def _div(ident, *blocks):
    return {"t": "Div", "c": [[ident, ["calibre"], []], list(blocks)]}


def _doc(*blocks):
    return {"pandoc-api-version": [1, 23, 1], "meta": {}, "blocks": list(blocks)}


def _notes(node):
    if isinstance(node, dict) and "blocks" in node:
        node = node["blocks"]
    found = []
    if isinstance(node, dict):
        if node.get("t") == "Note":
            found.append(node)
        node = node.get("c")
    if isinstance(node, list):
        for item in node:
            found.extend(_notes(item))
    return found


class CleanCalibreAstFootnoteTests(unittest.TestCase):
    def test_converts_sup_footnote_pair_into_note(self):
        # Calibre output for <a id="fr1" href="#fn1"><sup>1</sup></a> and
        # <p id="fn1"><a href="#fr1"><sup>1</sup></a> Note.</p>
        doc = _doc(
            _para(*_words("A footnote ref."),
                  _link("#calibre_link-2", _sup(_str("1")), ident="calibre_link-3")),
            _para(_link("#calibre_link-3", _sup(_str("1"))), SPACE, *_words("First chapter note.")),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 1)

        self.assertEqual(len(doc["blocks"]), 1)
        self.assertEqual(
            doc["blocks"][0]["c"][-1],
            {"t": "Note", "c": [_para(*_words("First chapter note."))]},
        )

    def test_restarted_numbering_gives_each_reference_its_own_note(self):
        doc = _doc(
            _div("calibre_link-0",
                 _para(_str("One."), _link("#c1-fn1", _sup(_str("1")), ident="c1-fr1")),
                 _para(_link("#c1-fr1", _sup(_str("1"))), SPACE, *_words("First note."))),
            _div("calibre_link-4",
                 _para(_str("Two."), _link("#c2-fn1", _sup(_str("1")), ident="c2-fr1")),
                 _para(_link("#c2-fr1", _sup(_str("1"))), SPACE, *_words("Second note."))),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 2)

        notes = _notes(doc)
        self.assertEqual(
            [note["c"] for note in notes],
            [[_para(*_words("First note."))], [_para(*_words("Second note."))]],
        )
        for div in doc["blocks"]:
            self.assertEqual(len(div["c"][1]), 1)

    def test_sup_outside_link_and_span_wrapped_and_bracketed_markers(self):
        doc = _doc(
            _para(_str("Sup."), _sup(_link("#n2", _str("2"), ident="r2")),
                  SPACE, _str("Span."), _span(_link("#n3", _sup(_str("3"))), ident="r3"),
                  SPACE, _str("Gutenberg."), _link("#Footnote_5", _str("[5]"), ident="FNanchor_5")),
            _para(_sup(_link("#r2", _str("2"))), SPACE, *_words("Note two.")),
            _para(_span(ident="n3"), _link("#r3", _sup(_str("3"))), SPACE, *_words("Note three.")),
            _para(_link("#FNanchor_5", _str("[5]"), ident="Footnote_5"), SPACE, *_words("Note five.")),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 3)

        self.assertEqual(len(doc["blocks"]), 1)
        inlines = doc["blocks"][0]["c"]
        self.assertNotIn("Superscript", [x["t"] for x in inlines])
        self.assertEqual(
            [note["c"] for note in _notes(doc)],
            [[_para(*_words("Note two."))], [_para(*_words("Note three."))],
             [_para(*_words("Note five."))]],
        )

    def test_div_note_keeps_every_paragraph_when_reference_targets_the_div(self):
        doc = _doc(
            _para(_str("Text."), _link("#n4", _sup(_str("4")), ident="r4")),
            _div("n4",
                 _para(_link("#r4", _str("4")), _str("."), SPACE, *_words("First paragraph.")),
                 _para(*_words("Second paragraph."))),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 1)

        self.assertEqual(len(doc["blocks"]), 1)
        self.assertEqual(
            _notes(doc)[0]["c"],
            [_para(*_words("First paragraph.")), _para(*_words("Second paragraph."))],
        )

    def test_marker_mismatch_is_not_converted(self):
        doc = _doc(
            _para(_str("Text."), _link("#n9", _sup(_str("9")), ident="r9")),
            _para(_link("#r9", _sup(_str("10"))), SPACE, *_words("Wrong note.")),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 0)

        self.assertEqual(len(doc["blocks"]), 2)
        self.assertEqual(doc["blocks"][0]["c"], [_str("Text."), _sup(_str("9"))])

    def test_link_opening_its_line_is_not_a_reference(self):
        # A contents entry and the chapter it points at link to each other with
        # the same number, but neither is a footnote.
        doc = _doc(
            _para(_link("#ch1", _str("1"), ident="toc1"), SPACE, *_words("Down the Rabbit-Hole")),
            _para(_link("#toc1", _str("1"), ident="ch1"), SPACE, *_words("Down the Rabbit-Hole")),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 0)

        self.assertEqual(len(doc["blocks"]), 2)
        for block in doc["blocks"]:
            self.assertEqual(block["c"], [_str("1"), SPACE, *_words("Down the Rabbit-Hole")])

    def test_reference_inside_a_note_stays_inline(self):
        # Pandoc cannot nest notes: note 8 must stay a paragraph rather than vanish.
        doc = _doc(
            _para(_str("Text."), _link("#n7", _sup(_str("7")), ident="r7")),
            _para(_link("#r7", _sup(_str("7"))), SPACE, *_words("Note seven cites"),
                  _link("#n8", _sup(_str("8")), ident="r8"), _str(".")),
            _para(_link("#r8", _sup(_str("8"))), SPACE, *_words("Note eight.")),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 1)

        self.assertEqual(len(doc["blocks"]), 2)
        self.assertEqual(
            _notes(doc)[0]["c"],
            [_para(*_words("Note seven cites"), _sup(_str("8")), _str("."))],
        )
        self.assertEqual(doc["blocks"][1]["c"], [_sup(_str("8")), SPACE, *_words("Note eight.")])


class CleanCalibreAstPandocNoteTests(unittest.TestCase):
    # pandoc 3.6+ reads DPUB-ARIA notes (role="doc-noteref") as Note elements
    # of its own, and before 3.13 it drops a note nested in another note.

    def test_reference_inside_a_pandoc_note_stays_inline(self):
        native = {"t": "Note", "c": [_para(*_words("Native note citing another."),
                                            _link("#n2", _sup(_str("2")), ident="r2"))]}
        doc = _doc(
            _para(*_words("Main text."), native),
            _para(_link("#r2", _sup(_str("2"))), SPACE, *_words("Backlinked note.")),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 0)

        self.assertEqual(len(_notes(doc)), 1)
        self.assertEqual(doc["blocks"][1], _para(_sup(_str("2")), SPACE, *_words("Backlinked note.")))

    def test_note_whose_text_holds_a_pandoc_note_stays_a_paragraph(self):
        native = {"t": "Note", "c": [_para(*_words("Native note."))]}
        doc = _doc(
            _para(*_words("Main text."), _link("#n1", _sup(_str("1")), ident="r1")),
            _para(_link("#r1", _sup(_str("1"))), SPACE, *_words("Backlinked note citing"), native),
        )

        self.assertEqual(convert.clean_calibre_ast(doc), 0)

        self.assertEqual(len(doc["blocks"]), 2)
        self.assertEqual(len(_notes(doc)), 1)


class CleanCalibreAstMarkupTests(unittest.TestCase):
    def test_unwraps_in_book_links_and_spans_and_drops_inline_anchors(self):
        doc = _doc(
            _para(*_words("A page anchor"), _span(ident="calibre_link-11"), SPACE,
                  _link("#calibre_link-1", _str("cross-reference")), _str(","), SPACE,
                  _span(*_words("sic erat"), attrs=[("lang", "lat")]), SPACE,
                  _link("https://example.com", _str("site"))),
        )

        convert.clean_calibre_ast(doc)

        self.assertEqual(
            doc["blocks"][0]["c"],
            [*_words("A page anchor"), SPACE, _str("cross-reference,"), SPACE,
             *_words("sic erat"), SPACE, _link("https://example.com", _str("site"))],
        )

    def test_unwrapped_number_and_period_stay_one_token(self):
        # Split as Str "4", Str ".", pandoc's writer would not escape the
        # paragraph start and it would read back as an ordered list.
        doc = _doc(_para(_span(ident="x1"), _link("#x9", _str("4")), _str("."), SPACE, *_words("Text.")))

        convert.clean_calibre_ast(doc)

        self.assertEqual(doc["blocks"][0]["c"], [_str("4."), SPACE, *_words("Text.")])

    def test_keeps_anchor_only_lines_for_the_page_number_pass(self):
        anchor_line = {"t": "Plain", "c": [_span(ident="calibre_link-8")]}
        doc = _doc(anchor_line)

        convert.clean_calibre_ast(doc)

        self.assertEqual(doc["blocks"], [{"t": "Plain", "c": [_span(ident="calibre_link-8")]}])

    def test_clears_heading_attributes(self):
        doc = _doc(
            {"t": "Header", "c": [2, ["ch1-title", ["ch_title"], []],
                                  [_span(ident="calibre_link-10"), *_words("Chapter One")]]},
        )

        convert.clean_calibre_ast(doc)

        self.assertEqual(doc["blocks"][0]["c"], [2, ["", [], []], _words("Chapter One")])

    def test_whitespace_only_spans_keep_their_whitespace(self):
        # `Hello<span> </span>World` and a `<span><br/></span>` line break.
        doc = _doc(
            _para(_str("Hello"), _span(SPACE), _str("World")),
            _para(*_words("line one"), _span({"t": "LineBreak"}), *_words("line two")),
        )

        convert.clean_calibre_ast(doc)

        self.assertEqual(doc["blocks"][0]["c"], _words("Hello World"))
        self.assertEqual(
            doc["blocks"][1]["c"],
            [*_words("line one"), {"t": "LineBreak"}, *_words("line two")],
        )

    def test_anchor_nested_in_a_whitespace_span_is_still_dropped(self):
        doc = _doc(_para(
            _str("A"), _span(SPACE, _span(ident="page_3")), _str("B"),
            _link("https://example.com", SPACE), _str("C"),
        ))

        convert.clean_calibre_ast(doc)

        self.assertEqual(doc["blocks"][0]["c"], [_str("A"), SPACE, _str("B"), SPACE, _str("C")])

    def test_unwraps_links_to_other_files_of_the_source_book(self):
        # Calibre leaves a link to a part of the EPUB it did not keep (the
        # cover page) as a relative file link; it cannot resolve after
        # translation and readers built a one-entry "Cover" TOC from it.
        doc = _doc(_para(
            _link("9780063517578_Cover.xhtml#cover", _str("Cover")), SPACE,
            _link("mailto:me@example.com", _str("mail")), SPACE,
            _link("http://www.harpercollins.com", _str("hc.com")),
        ))

        convert.clean_calibre_ast(doc)

        self.assertEqual(
            doc["blocks"][0]["c"],
            [_str("Cover"), SPACE, _link("mailto:me@example.com", _str("mail")), SPACE,
             _link("http://www.harpercollins.com", _str("hc.com"))],
        )

    def test_clears_external_link_attributes(self):
        # Calibre classes such as `pcalibre1` survive on external links and
        # would be written out as `[site](https://example.com){.pcalibre1}`.
        link = {"t": "Link", "c": [["calibre_link-5", ["pcalibre1", "pcalibre"], [["type", "x"]]],
                                   [_str("site")], ["https://example.com", "Example"]]}
        doc = _doc(_para(*_words("Visit"), SPACE, link))

        convert.clean_calibre_ast(doc)

        self.assertEqual(
            doc["blocks"][0]["c"][-1],
            {"t": "Link", "c": [["", [], []], [_str("site")], ["https://example.com", "Example"]]},
        )


# Body of the input.html Calibre 7.6 writes for the reproduction EPUB attached
# to issues #49 and #50.
ISSUE_49_50_CALIBRE_HTML = """<html><head><meta http-equiv="Content-Type" content="text/html;charset=utf-8" /><title>Repro</title></head><body><div class="calibre" id="calibre_link-0">
<a id="calibre_link-8"></a>
<h2 class="ch_title" id="calibre_link-9"><a id="calibre_link-10" class="calibre1"></a>Chapter One</h2>
<p class="calibre2">A paragraph with a page anchor<a id="calibre_link-11"></a> in the middle, an
internal <a href="#calibre_link-1">cross-reference</a>, a
<span lang="lat">sic erat scriptum</span> span, an em dash&mdash;like this,
an en dash (pp. 12&ndash;34), and a footnote ref.<a href="#calibre_link-2" id="calibre_link-3"><sup class="calibre3">1</sup></a></p>
<p class="calibre2" id="calibre_link-2"><a href="#calibre_link-3"><sup class="calibre3">1</sup></a> First chapter note.</p>
</div>
<div class="calibre" id="calibre_link-4">
<a id="calibre_link-12"></a>
<h2 class="ch_title" id="calibre_link-13">Chapter Two</h2>
<p class="calibre2">Another chapter, whose footnote numbering restarts at one.<a href="#calibre_link-5" id="calibre_link-6"><sup class="calibre3">1</sup></a></p>
<p class="calibre2" id="calibre_link-5"><a href="#calibre_link-6"><sup class="calibre3">1</sup></a> Second chapter note.</p>
</div>
<div class="calibre" id="calibre_link-7">
<p class="calibre2" id="calibre_link-1">Doe, J. <em class="calibre4">Some Book</em>. Press, 2001.</p>
<p class="calibre2">&mdash;&mdash;&mdash; <em class="calibre4">Another Book</em>. Press, 2005.</p>
</div>
</body></html>
"""


@process_guard.needs("pandoc")
class ConvertHtmlToMarkdownPandocTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(process_guard.library_processes("pandoc"))

    def test_issue_49_50_reproduction(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "input.html"
            md = Path(tmp) / "input.md"
            html.write_text(ISSUE_49_50_CALIBRE_HTML, encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(convert.convert_html_to_markdown(str(html), str(md)))

            self.assertEqual(
                md.read_text(encoding="utf-8").strip(),
                "\n".join(
                    [
                        "## Chapter One",
                        "",
                        "A paragraph with a page anchor in the middle, an internal cross-reference, "
                        "a sic erat scriptum span, an em dash—like this, an en dash (pp. 12–34), "
                        "and a footnote ref.[^1]",
                        "",
                        "[^1]: First chapter note.",
                        "",
                        "## Chapter Two",
                        "",
                        "Another chapter, whose footnote numbering restarts at one.[^2]",
                        "",
                        "[^2]: Second chapter note.",
                        "",
                        "Doe, J. *Some Book*. Press, 2001.",
                        "",
                        "——— *Another Book*. Press, 2005.",
                    ]
                ),
            )

    def test_chapter_number_after_page_anchor_survives_and_unwrapped_number_is_escaped(self):
        # Dropping the anchor line too early would put the "1" next to the
        # ::: fence of the chapter div, where the Calibre-noise rule deletes it.
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "input.html"
            md = Path(tmp) / "input.md"
            html.write_text(
                '<html><body><div class="calibre" id="calibre_link-0">'
                '<a id="calibre_link-5"></a><p>1</p><h2>Chapter One</h2><p>Opening text.</p></div>'
                '<p><a href="#calibre_link-9">4</a>. Not a list.</p></body></html>',
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(convert.convert_html_to_markdown(str(html), str(md)))

            self.assertEqual(
                md.read_text(encoding="utf-8").strip(),
                "1\n\n## Chapter One\n\nOpening text.\n\n4\\. Not a list.",
            )


class PandocWithoutPypandocTests(unittest.TestCase):
    """The pandoc binary is enough: pypandoc is only a convenience wrapper."""

    def test_falls_back_to_the_pandoc_binary(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "-t" in cmd and cmd[cmd.index("-t") + 1] == "json":
                return mock.Mock(stdout='{"pandoc-api-version":[1,23],"meta":{},"blocks":[]}')
            Path(cmd[cmd.index("-o") + 1]).write_text("Body text.\n", encoding="utf-8")
            return mock.Mock(stdout="")

        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "input.html"
            md = Path(tmp) / "input.md"
            html.write_text("<p>Body text.</p>", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"pypandoc": None}), \
                    mock.patch.object(convert.subprocess, "run", side_effect=fake_run), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(convert.convert_html_to_markdown(str(html), str(md)))
            self.assertEqual(md.read_text(encoding="utf-8").strip(), "Body text.")

        self.assertEqual([c[0] for c in calls], ["pandoc", "pandoc"])
        self.assertIn("--reference-location=block", calls[1])

    def test_reports_missing_pandoc(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "input.html"
            html.write_text("<p>x</p>", encoding="utf-8")
            with mock.patch.dict(sys.modules, {"pypandoc": None}), \
                    mock.patch.object(convert.subprocess, "run", side_effect=FileNotFoundError), \
                    contextlib.redirect_stdout(out):
                self.assertFalse(convert.convert_html_to_markdown(str(html), str(Path(tmp) / "o.md")))
        self.assertIn("pandoc", out.getvalue())


class HtmlTitleTests(unittest.TestCase):
    def test_reads_the_title_calibre_copies_into_input_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "input.html"
            html.write_text(
                "<html><head><title>Profits, Prophets &amp;amp;\n Kings</title></head><body/></html>",
                encoding="utf-8",
            )
            self.assertEqual(convert._html_title(str(html)), "Profits, Prophets &amp; Kings")

    def test_missing_title_or_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "input.html"
            html.write_text("<html><body>x</body></html>", encoding="utf-8")
            self.assertIsNone(convert._html_title(str(html)))
            self.assertIsNone(convert._html_title(str(Path(tmp) / "none.html")))


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
                convert.build_temp_dir("/books/Alice.epub", str(root)),
                str(html_file),
                str(images_dir),
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


class PageNumberDetectionDefaultTests(unittest.TestCase):
    def _detect_flag(self, filename, *extra_args):
        """Run main() on a cached input.html and return detect_page_numbers."""
        from unittest import mock

        calls = []

        def fake_markdown(html_file, md_file, strip_page_numbers=False, detect_page_numbers=True):
            calls.append(detect_page_numbers)
            Path(md_file).write_text("# Title\n\nBody text.\n", encoding="utf-8")
            return True

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / filename
            source.write_bytes(b"book bytes")
            temp_dir = Path(convert.build_temp_dir(str(source), tmp))
            temp_dir.mkdir()
            convert._write_source_fingerprint(str(temp_dir), convert.run_identity(str(source), "zh"))
            (temp_dir / "input.html").write_text("<html></html>", encoding="utf-8")
            argv = ["convert.py", str(source), "--temp-root", tmp, *extra_args]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(convert, "find_calibre_convert", return_value="ebook-convert"), \
                    mock.patch.object(convert, "convert_html_to_markdown", side_effect=fake_markdown), \
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


class SourceFingerprintCacheTests(unittest.TestCase):
    def _write(self, path, content):
        Path(path).write_text(content, encoding="utf-8")

    def test_no_conflict_for_fresh_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            self._write(source, "source bytes")

            status, message = convert.check_source_cache(
                str(Path(tmp) / "book_temp"),
                convert.run_identity(str(source), "zh"),
            )

        self.assertIsNone(status)
        self.assertIsNone(message)

    def test_adopts_cache_that_predates_fingerprinting(self):
        # Temp dirs created before this feature have no fingerprint file.
        # They stay resumable (trust-on-first-use), with a warning, when
        # their config.txt names a file of the same name.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.html", "<html></html>")
            self._write(temp_dir / "config.txt", "input_file=/elsewhere/book.epub\noutput_lang=zh_CN\n")

            status, message = convert.check_source_cache(
                str(temp_dir), convert.run_identity(str(source), "zh-CN"), str(source)
            )

        self.assertEqual(status, "adopt")
        self.assertIn(convert.SOURCE_FINGERPRINT_FILE, message)

    def test_unfingerprinted_cache_for_other_settings_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.pdf"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "output_chunk0001.md", "English translation\n")
            cases = [
                ("output_lang=en\n", convert.run_identity(str(source), "zh"), "mismatch", "translation into en"),
                ("", convert.run_identity(str(source), "zh"), "unknown", "target language"),
                ("output_lang=zh; rm\n", convert.run_identity(str(source), "zh"), "unknown", "target language"),
                ("output_lang=zh\n", convert.run_identity(str(source), "zh", "docling"), "mismatch",
                 "without a parser"),
            ]
            for config, identity, expected, needle in cases:
                with self.subTest(config=config, parser=identity["parser"]):
                    self._write(temp_dir / "config.txt", "input_file=/old/book.pdf\n" + config)
                    status, message = convert.check_source_cache(str(temp_dir), identity, str(source))
                    self.assertEqual(status, expected)
                    self.assertIn(needle, message)

    def test_unfingerprinted_cache_of_another_file_name_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.html", "<html></html>")
            for config in ("input_file=/elsewhere/Book (1).epub\n", None):
                if config is None:
                    (temp_dir / "config.txt").unlink()
                else:
                    self._write(temp_dir / "config.txt", config)
                status, message = convert.check_source_cache(
                    str(temp_dir), convert.run_identity(str(source), "zh"), str(source)
                )
                self.assertEqual(status, "unknown")
                self.assertNotIn("elete", message)

    def test_mismatch_when_source_bytes_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "old source bytes")
            convert._write_source_fingerprint(
                str(temp_dir), convert.run_identity(str(source), "zh")
            )
            self._write(temp_dir / "input.html", "<html></html>")

            self._write(source, "new source bytes!!")
            status, message = convert.check_source_cache(
                str(temp_dir), convert.run_identity(str(source), "zh")
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
                str(temp_dir), convert.run_identity(str(source), "zh")
            )
            self._write(temp_dir / "input.html", "<html></html>")

            status, message = convert.check_source_cache(
                str(temp_dir), convert.run_identity(str(source), "zh")
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
                str(temp_dir), convert.run_identity(str(source), "zh")
            )
            self._write(temp_dir / "input.html", "<html></html>")

            moved = Path(tmp) / "renamed-book.epub"
            source.rename(moved)
            status, _ = convert.check_source_cache(
                str(temp_dir), convert.run_identity(str(moved), "zh")
            )

        self.assertIsNone(status)

    def test_identity_records_every_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.pdf"
            self._write(source, "source bytes")
            identity = convert.run_identity(str(source), "zh-TW", "marker")
            self.assertEqual(identity, {"version": 2, "path": os.path.realpath(source), "size": 12,
                                        "sha256": convert.file_hash(str(source)), "parser": "marker",
                                        "target_lang": "zh-TW"})

    def test_fingerprint_of_an_older_version_needs_the_language_in_config(self):
        # Versions before 2 wrote path, size and sha256 only, and converted
        # without a parser; the target language is in config.txt or unknown.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.md", "# Book\n")
            identity = convert.run_identity(str(source), "zh")
            older = {key: identity[key] for key in ("path", "size", "sha256")}
            self._write(temp_dir / convert.SOURCE_FINGERPRINT_FILE, json.dumps(older))
            cases = [
                ("output_lang=zh\n", identity, None),
                ("output_lang=ja\n", identity, "mismatch"),
                ("", identity, "unknown"),
                ("output_lang=zh; rm\n", identity, "unknown"),
                ("output_lang=zh\n", convert.run_identity(str(source), "zh", "docling"), "mismatch"),
            ]
            for config, wanted, expected in cases:
                with self.subTest(config=config, parser=wanted["parser"]):
                    self._write(temp_dir / "config.txt", config)
                    status, message = convert.check_source_cache(str(temp_dir), wanted, str(source))
                    self.assertEqual(status, expected, message)

    def test_partial_fingerprint_without_a_language_is_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.md", "# Book\n")
            identity = convert.run_identity(str(source), "zh")
            self._write(temp_dir / convert.SOURCE_FINGERPRINT_FILE,
                        json.dumps({"sha256": identity["sha256"], "size": identity["size"]}))
            status, message = convert.check_source_cache(str(temp_dir), identity, str(source))
        self.assertEqual(status, "unknown")
        self.assertIn("cannot be read", message)

    def test_unreadable_fingerprint_is_refused_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "book.epub"
            temp_dir = Path(tmp) / "book_temp"
            temp_dir.mkdir()
            self._write(source, "source bytes")
            self._write(temp_dir / "input.html", "<html></html>")
            self._write(temp_dir / "config.txt", "input_file=book.epub\noutput_lang=zh\n")
            identity = convert.run_identity(str(source), "zh")
            older = {key: identity[key] for key in ("path", "size", "sha256")}
            cases = ["{not json", "[]", "null", b"\xff\xfe{}", None]
            for record in (identity, older):
                cases += [json.dumps({k: v for k, v in record.items() if k != key}) for key in record]
                cases.append(json.dumps(dict(record, parser_args=[])))
            cases += [json.dumps(dict(identity, **{key: value})) for key, value in (
                ("version", 1), ("version", 3), ("version", "2"), ("version", True), ("path", ""),
                ("path", 7), ("size", "12"), ("size", True), ("size", -1), ("size", 1.5),
                ("sha256", 5), ("sha256", "0" * 63), ("sha256", "A" * 64), ("parser", "bash"),
                ("parser", []), ("parser", ""), ("target_lang", 7), ("target_lang", None),
                ("target_lang", "zh;x"), ("target_lang", "zh_TW"))]
            cases += [json.dumps(dict(older, **{key: value})) for key, value in (
                ("path", None), ("size", "12"), ("sha256", "0" * 63))]
            fingerprint = temp_dir / convert.SOURCE_FINGERPRINT_FILE
            for content in cases:
                with self.subTest(content=content):
                    if fingerprint.is_file():
                        fingerprint.unlink()
                    if content is None:
                        fingerprint.mkdir()
                    elif isinstance(content, bytes):
                        fingerprint.write_bytes(content)
                    else:
                        self._write(fingerprint, content)
                    status, message = convert.check_source_cache(
                        str(temp_dir), convert.run_identity(str(source), "zh"), str(source)
                    )
                    self.assertEqual(status, "unknown")
                    self.assertIn(f"{convert.SOURCE_FINGERPRINT_FILE} cannot be read", message)
                    _assert_no_deletion_advice(self, message)
                    if content is None:
                        fingerprint.rmdir()


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


class FootnoteBlockTests(unittest.TestCase):
    NOTE = "[^1]: First paragraph of the note.\n\n    Second paragraph of the note."

    def test_definition_and_indented_continuation_form_one_block(self):
        content = f"Text.[^1]\n\n{self.NOTE}\n\nNext paragraph."

        blocks = convert.parse_structural_blocks(content)

        self.assertIn((self.NOTE, "footnote"), blocks)
        self.assertIn(("Next paragraph.", "paragraph"), blocks)

    def test_note_continuation_is_never_split_from_its_definition(self):
        content = "A" * 150 + "[^1]\n\n" + self.NOTE + "\n\n" + "B" * 150
        blocks = convert.parse_structural_blocks(content)
        chunks = convert.merge_blocks_to_chunks(blocks, target_size=60)
        self.assertEqual(sum(self.NOTE in chunk for chunk in chunks), 1)

    def test_oversized_note_stays_in_one_chunk(self):
        # Force-splitting it would open the next chunk with the indented
        # continuation, which loses its indent when chunks are merged.
        note = "[^1]: " + "First paragraph. " * 10 + "\n\n    " + "Second paragraph. " * 10
        blocks = convert.parse_structural_blocks(f"Text.[^1]\n\n{note}\n\nNext paragraph.")

        with contextlib.redirect_stdout(io.StringIO()):
            chunks = convert.merge_blocks_to_chunks(blocks, target_size=60)

        self.assertIn(note, chunks)

    def test_long_final_note_leaves_no_blank_chunk(self):
        note = "[^1]: " + "First paragraph. " * 10 + "\n\n    " + "Second paragraph. " * 10
        blocks = convert.parse_structural_blocks(f"Text.[^1]\n\n{note}\n")

        with contextlib.redirect_stdout(io.StringIO()):
            chunks = convert.merge_blocks_to_chunks(blocks, target_size=60)

        self.assertEqual([chunk for chunk in chunks if not chunk.strip()], [])


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

    def test_copies_images_from_parser_output_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            # docling's referenced image mode writes absolute paths when --output is absolute.
            absolute = Path(tmp) / "parser_out" / "paper_artifacts" / "image_000001_cd34.png"
            md = self._source(
                tmp,
                "![](_page_0_Picture_1.jpeg)\n"
                "![](./images/3f2a9c.jpg)\n"
                "![Image](paper_artifacts/image_000000_ab12.png)\n"
                f"![Image]({absolute})\n",
                files=[
                    ("_page_0_Picture_1.jpeg", b"marker"),
                    ("images/3f2a9c.jpg", b"mineru"),
                    ("paper_artifacts/image_000000_ab12.png", b"docling"),
                    ("paper_artifacts/image_000001_cd34.png", b"docling-abs"),
                ],
            )
            temp_dir = Path(tmp) / "book_temp"
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                content = convert.import_markdown(str(md), str(temp_dir))

            self.assertEqual(
                content,
                "![](images/_page_0_Picture_1.jpeg)\n"
                "![](images/3f2a9c.jpg)\n"
                "![Image](images/image_000000_ab12.png)\n"
                "![Image](images/image_000001_cd34.png)\n",
            )
            self.assertEqual((temp_dir / "images" / "image_000001_cd34.png").read_bytes(), b"docling-abs")
            self.assertNotIn("Warning", out.getvalue())

    def test_does_not_copy_images_outside_the_markdown_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret.png"
            secret.write_bytes(b"secret")
            text = (
                "![ok](images/ok.png)\n"
                f"![abs]({secret})\n"
                "![up](../secret.png)\n"
                "![encoded](%2E%2E/secret.png)\n"
                "![nested](images/../../secret.png)\n"
                f'<img src="{secret}" alt="html"/>\n'
            )
            md = self._source(tmp, text, files=[("images/ok.png", b"ok")])
            temp_dir = Path(tmp) / "book_temp"
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                content = convert.import_markdown(str(md), str(temp_dir))

            self.assertEqual(content, text)
            self.assertEqual([p.name for p in (temp_dir / "images").iterdir()], ["ok.png"])
            log = out.getvalue()
            for raw in (secret, "../secret.png", "%2E%2E/secret.png", "images/../../secret.png"):
                self.assertIn(
                    f"Warning: image outside the Markdown file's directory, not copied, "
                    f"reference kept as-is: {raw}\n",
                    log,
                )

    def test_follows_symlinks_only_within_the_markdown_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.png").write_bytes(b"secret")
            text = "![in](link_in.png)\n![out](link_out.png)\n![dir](linked/secret.png)\n"
            md = self._source(tmp, text, files=[("images/fig.png", b"fig")])
            try:
                (md.parent / "link_in.png").symlink_to(md.parent / "images" / "fig.png")
                (md.parent / "link_out.png").symlink_to(outside / "secret.png")
                (md.parent / "linked").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"cannot create symlinks here: {exc}")
            temp_dir = Path(tmp) / "book_temp"
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                content = convert.import_markdown(str(md), str(temp_dir))

            self.assertEqual(content, "![in](images/link_in.png)\n![out](link_out.png)\n![dir](linked/secret.png)\n")
            self.assertEqual([p.name for p in (temp_dir / "images").iterdir()], ["link_in.png"])
            self.assertEqual((temp_dir / "images" / "link_in.png").read_bytes(), b"fig")
            log = out.getvalue()
            self.assertIn("not copied, reference kept as-is: link_out.png\n", log)
            self.assertIn("not copied, reference kept as-is: linked/secret.png\n", log)

    def test_markdown_directory_reached_through_a_symlink_keeps_its_images(self):
        # macOS temp dirs are like this: /var is a symlink to /private/var.
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "alias"
            md = self._source(
                tmp,
                f"![](images/a.png)\n![]({alias / 'images' / 'b.png'})\n",
                files=[("images/a.png", b"a"), ("images/b.png", b"b")],
            )
            try:
                alias.symlink_to(md.parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"cannot create symlinks here: {exc}")
            temp_dir = Path(tmp) / "book_temp"
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                content = convert.import_markdown(str(alias / md.name), str(temp_dir))

            self.assertEqual(content, "![](images/a.png)\n![](images/b.png)\n")
            self.assertEqual(sorted(p.name for p in (temp_dir / "images").iterdir()), ["a.png", "b.png"])
            self.assertNotIn("Warning", out.getvalue())

    def test_strips_front_matter_from_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(tmp, "---\ntitle: Paper\n---\n# Paper\n\nBody\n")

            content = convert.import_markdown(str(md), str(Path(tmp) / "book_temp"))

            self.assertEqual(content, "# Paper\n\nBody\n")

    def test_docling_output_keeps_math_and_tables_and_warns_on_placeholders(self):
        import base64

        payload = base64.b64encode(PNG_BYTES).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(
                tmp,
                "## 3.2 Scaled Dot-Product Attention\n\n"
                f"![Image](data:image/png;base64,{payload})\n\n"
                "$$\\mathrm{Attention}(Q, K, V) = \\mathrm{softmax}(QK^T / \\sqrt{d_k})V$$\n\n"
                "| Model | BLEU |\n|---|---|\n| Transformer | 28.4 |\n\n"
                "<!-- formula-not-decoded -->\n\n"
                "<!-- formula-not-decoded -->\n\n"
                "<!-- image -->\n",
            )
            temp_dir = Path(tmp) / "book_temp"
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                content = convert.import_markdown(str(md), str(temp_dir))

            self.assertIn("![Image](images/embedded-0001.png)", content)
            self.assertIn("$$\\mathrm{Attention}(Q, K, V) = \\mathrm{softmax}(QK^T / \\sqrt{d_k})V$$", content)
            self.assertIn("| Transformer | 28.4 |", content)
            self.assertEqual((temp_dir / "images" / "embedded-0001.png").read_bytes(), PNG_BYTES)
            log = out.getvalue()
            self.assertIn("Warning: 2 formula(s) were not decoded", log)
            self.assertIn("--enrich-formula", log)
            self.assertIn("Warning: 1 image(s) were exported as placeholders", log)
            self.assertIn("Warning: 3 display formula(s) but no inline math", log)

    def test_clean_markdown_has_no_placeholder_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(tmp, "$$x_1 + x_2$$\n\n| a | b |\n|---|---|\n")
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                convert.import_markdown(str(md), str(Path(tmp) / "book_temp"))

            self.assertNotIn("Warning", out.getvalue())

    # Shaped like docling's formula output for arXiv 1706.03762: an equation
    # number column, a two-row formula, and a trailing row break.
    DOCLING_FORMULAS = [
        r"$$A t t e n t i o n ( Q , K , V ) = \text {softmax} ( \frac { Q K ^ { T } } { \sqrt { d _ { k } } } ) V & & ( 1 )$$",
        r"$$P E _ { ( p o s , 2 i ) } & = s i n ( p o s / 1 0 0 0 0 ^ { 2 i / d } ) \\ P E _ { ( p o s , 2 i + 1 ) } & = c o s ( p o s / 1 0 0 0 0 ^ { 2 i / d } )$$",
        r"$$l r a t e = d _ { \text {model} } ^ { - 0 . 5 } \cdot \min ( s t e p ^ { - 0 . 5 } , w a r m u p ^ { - 1 . 5 } ) \quad \\$$",
    ]

    def test_wraps_display_math_with_bare_alignment_rows(self):
        unchanged = [
            r"$$\text{R\&D} = \begin{cases} a & b \\ c & d \end{cases}$$",
            r"$$x_1 + x_2$$",
            "Inline $a & b$ stays.",
        ]
        multiline = "$$\na &= b \\\\\nc &= d\n$$"
        code = "```latex\n$$a & b$$\n```"
        content = "\n\n".join(self.DOCLING_FORMULAS + unchanged + [multiline, code]) + "\n"

        aligned, count = convert.align_display_math(content)

        self.assertEqual(count, 4)
        for formula in self.DOCLING_FORMULAS:
            self.assertIn("$$\\begin{aligned}" + formula[2:-2] + "\\end{aligned}$$", aligned)
        for text in unchanged + [code]:
            self.assertIn(text, aligned)
        self.assertIn("$$\\begin{aligned}\na &= b \\\\\nc &= d\n\\end{aligned}$$", aligned)
        self.assertEqual(convert.align_display_math(aligned), (aligned, 0))

    def test_import_reports_wrapped_formulas(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = self._source(tmp, self.DOCLING_FORMULAS[0] + "\n")
            out = io.StringIO()

            with contextlib.redirect_stdout(out):
                content = convert.import_markdown(str(md), str(Path(tmp) / "book_temp"))

        self.assertTrue(content.startswith("$$\\begin{aligned}A t t e n t i o n"))
        self.assertIn("Wrapped 1 display formula(s)", out.getvalue())

    def test_formula_counts(self):
        content = (
            "$$a + b$$\n\n"
            "Text $x$ and $\\{y\\}$, but prices $5 and $10 and \\$3 are not math.\n\n"
            "```\n$z$\n$$w$$\n```\n\n"
            "<!-- formula-not-decoded -->\n"
        )

        self.assertEqual(convert.formula_counts(content), (2, 2))

    def test_warns_when_display_formulas_come_without_inline_math(self):
        display = "$$a + b$$\n\n$$c^2$$\n\n$$x_1$$\n\n"
        cases = {
            "flattened": (display + "Queries live in Rdmodel×dk and cost $5 and $10.\n", True),
            "inline kept": (display + "Queries live in $\\mathbb{R}^{d}$.\n", False),
            "few formulas": ("$$a + b$$\n\n$$c^2$$\n\nNo inline math.\n", False),
            "placeholders": ("<!-- formula-not-decoded -->\n\n" * 3, True),
            "inline only in code": (display + "```\n$x$\n```\n", True),
        }
        for name, (markdown, warns) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                md = self._source(tmp, markdown)
                out = io.StringIO()

                with contextlib.redirect_stdout(out):
                    convert.import_markdown(str(md), str(Path(tmp) / "book_temp"))

                self.assertEqual("but no inline math" in out.getvalue(), warns)

    @process_guard.needs("pandoc")
    def test_wrapped_formulas_render_as_mathml(self):
        content, _ = convert.align_display_math("\n\n".join(self.DOCLING_FORMULAS) + "\n")
        result = process_guard.run_tool(
            "pandoc", "--from", "markdown+smart+east_asian_line_breaks", "--to", "html5", "--mathml", input=content)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("<math"), 3)
        self.assertNotIn("Could not convert TeX math", result.stderr)


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
    def _run(self, workdir, *args):
        return process_guard.run_script("convert.py", *args, cwd=workdir)

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


class CalibreHtmlzLocationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "book.epub"
        self.source.write_bytes(b"epub bytes")
        self.users_htmlz = self.root / "book.htmlz"
        self.users_htmlz.write_text("user-owned file", encoding="utf-8")
        self.htmlz_paths = []

    def _main(self, conversion_succeeds=True):
        import contextlib
        import io
        import zipfile
        from unittest import mock

        def fake_calibre(input_file, htmlz_file, calibre_path):
            self.htmlz_paths.append(Path(htmlz_file))
            with zipfile.ZipFile(htmlz_file, "w") as z:
                z.writestr("index.html", "<html><body><p>Body text.</p></body></html>")
            return conversion_succeeds

        def fake_markdown(html_file, md_file, *args, **kwargs):
            Path(md_file).write_text("# Title\n\nBody text.\n", encoding="utf-8")
            return True

        argv = ["convert.py", str(self.source), "--temp-root", str(self.root / "work")]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(convert, "find_calibre_convert", return_value="ebook-convert"), \
                mock.patch.object(convert, "convert_to_htmlz", side_effect=fake_calibre), \
                mock.patch.object(convert, "convert_html_to_markdown", side_effect=fake_markdown), \
                contextlib.redirect_stdout(io.StringIO()):
            convert.main()

    def test_users_htmlz_next_to_the_source_survives_a_conversion(self):
        self._main()

        self.assertEqual(self.users_htmlz.read_text(encoding="utf-8"), "user-owned file")
        [htmlz] = self.htmlz_paths
        self.assertNotEqual(htmlz.parent, self.root)
        self.assertFalse(htmlz.exists())
        self.assertTrue((self.root / "work" / "book_temp" / "chunk0001.md").exists())

    def test_failed_conversion_leaves_no_htmlz_behind(self):
        with self.assertRaises(SystemExit):
            self._main(conversion_succeeds=False)

        self.assertEqual(self.users_htmlz.read_text(encoding="utf-8"), "user-owned file")
        [htmlz] = self.htmlz_paths
        self.assertFalse(htmlz.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["book.epub", "book.htmlz", "work"])
        # The run converted nothing, so it gave up the temp dir it had claimed.
        self.assertEqual(list((self.root / "work").iterdir()), [])



SHELL_SAFE_ASCII = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _assert_no_deletion_advice(test, message):
    test.assertNotRegex(message.lower(), r"delet|remov|\brm\b")


class SafeTempStemTests(unittest.TestCase):
    def assert_shell_safe(self, stem):
        self.assertTrue(stem)
        self.assertLessEqual(len(stem.encode("utf-8")), convert.TEMP_STEM_MAX_BYTES)
        self.assertNotIn("__", stem)
        self.assertFalse(stem[0] in "_.-" or stem[-1] in "_.-", stem)
        for ch in stem:
            if ch.isascii():
                self.assertIn(ch, SHELL_SAFE_ASCII, stem)
            else:
                self.assertIn(unicodedata.category(ch)[0], "LNM", (stem, ch))
                self.assertFalse(convert._is_variation_selector(ch))

    def test_examples(self):
        cases = {
            "《三体》（刘慈欣）": "三体_刘慈欣",
            "O'Brien's \"tale\" $(touch x) `y` $HOME": "O_Brien_s_tale_touch_x_y_HOME",
            "-rf --no-preserve-root": "rf_--no-preserve-root",
            "Café": "Café",
            "Cafe\u0301": "Café",
            "ＡＢＣ１２３": "ABC123",
            "\ufb01le": "file",
            "हिन्दी पुस्तक": "हिन्दी_पुस्तक",
            "\u0301abc": "abc",
            "a\u202eb\u200bc": "a_b_c",
            "line\nbreak\ttab": "line_break_tab",
            "Book [2nd ed.] *?{x,y}~#!": "Book_2nd_ed._x_y",
            "..": "book",
            "": "book",
            "\u2764\ufe0f": "book",
            "\udc80\udcff": "book",
            "a.tar": "a.tar",
        }
        for name, stem in cases.items():
            with self.subTest(name=name):
                self.assertEqual(convert.safe_temp_stem(name), stem)
                self.assert_shell_safe(convert.safe_temp_stem(name))

    def test_cuts_at_a_character_boundary(self):
        four_byte = "\U00020000"
        self.assertEqual(convert.safe_temp_stem(four_byte * 41), four_byte * 40)
        self.assertEqual(convert.safe_temp_stem("a" + four_byte * 40), "a" + four_byte * 39)
        self.assertEqual(convert.safe_temp_stem("a" * 159 + "_bc"), "a" * 159)
        for size in range(150, 170):
            with self.subTest(size=size):
                self.assert_shell_safe(convert.safe_temp_stem("é" * size))

    def test_random_hostile_names_give_safe_stems(self):
        import random
        alphabet = ([chr(c) for c in range(32, 127)] + list("\t\n\r\x00\x7f")
                    + list("《》（）·…—「」é\u0301\u20dd\ufe0f\u200b\u202e\u2028\ufeff中文한국어ـا")
                    + ["\U00020000", "\U0001F600", "\udc80"])
        rng = random.Random(1)
        for _ in range(2000):
            name = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 200)))
            self.assert_shell_safe(convert.safe_temp_stem(name))

    def test_temp_dir_names(self):
        self.assertEqual(convert.build_temp_dir("/books/《三体》（刘慈欣）.epub"), "三体_刘慈欣_temp")
        self.assertEqual(convert.build_temp_dir("/books/$(x).pdf", "work/a"), "work/a/x_temp")
        self.assertEqual(convert.legacy_temp_dir("/books/$(x).pdf", "work/a"), "work/a/$(x)_temp")


class ValidateTempRootTests(unittest.TestCase):
    def test_accepts_safe_paths(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": "/home/me"}):
            cases = {
                "work": "work",
                "work/sub-1.d/": "work/sub-1.d",
                "/srv/books_2": "/srv/books_2",
                "~/books": "/home/me/books",
                "书/译本": "书/译本",
                tmp: tmp,
            }
            for root, expected in cases.items():
                with self.subTest(root=root):
                    self.assertEqual(convert.validate_temp_root(root), expected)

    def test_rejects_unsafe_paths(self):
        cases = ["-x", "work/-x", "..", "work/../x", ".", "a/./b", "a//b", "my books",
                 "a'b", "a$b", "a`b`", 'a"b', "a\\b", "a;b", "~nobody-here/x", "/", "",
                 "a\nb", "a\u200bb", "\u0301work", "work/\u0903x"]
        for root in cases:
            with self.subTest(root=root), self.assertRaises(ValueError) as raised:
                convert.validate_temp_root(root)
            _assert_no_deletion_advice(self, str(raised.exception))

    def test_names_the_bad_part(self):
        with self.assertRaises(ValueError) as raised:
            convert.validate_temp_root("work/my books/x")
        self.assertIn("'my books'", str(raised.exception))
        self.assertIn("leave temp_root out", str(raised.exception))

    def test_rejects_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "file").write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                convert.validate_temp_root(str(Path(tmp, "file")))


class ReadRequestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.book = self.root / "《书》 O'Brien $(x).epub"
        self.book.write_bytes(b"epub")
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"%PDF")
        self.cover = self.root / "Cover.PNG"
        self.cover.write_bytes(b"png")
        self.path = self.root / "translate-book-request.json"

    def read(self, data):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False)
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.path.write_bytes(data)
        return convert.read_request(str(self.path))

    def assert_rejected(self, data, needle):
        self.path.write_bytes(b"")
        before = sorted(p.name for p in self.root.iterdir())
        with self.assertRaises(ValueError) as raised:
            self.read(data)
        self.assertIn(needle, str(raised.exception))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)

    def test_minimal_request_gets_defaults(self):
        raw, request, read_from = self.read({"input": str(self.book)})
        self.assertEqual(raw, self.path.read_bytes())
        self.assertTrue(read_from.is_this(self.path.stat()))
        self.assertEqual(read_from.data, raw)
        self.assertEqual(request, {"input": str(self.book), "target_lang": "zh", "temp_root": None,
                                   "cover": None, "parser": None})

    def test_full_request(self):
        _, request, _ = self.read({"input": str(self.pdf), "target_lang": "zh_TW", "temp_root": "work/a",
                                "cover": str(self.cover), "parser": "marker"})
        self.assertEqual(request, {"input": str(self.pdf), "target_lang": "zh-TW", "temp_root": "work/a",
                                   "cover": str(self.cover), "parser": "marker"})

    def test_expands_home_and_accepts_bom_and_nulls(self):
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            _, request, _ = self.read("\ufeff" + json.dumps(
                {"input": "~/paper.pdf", "temp_root": None, "cover": None, "parser": None}))
        self.assertEqual(request["input"], str(self.pdf))

    def test_parser_args_are_refused_with_the_way_to_pass_other_options(self):
        for value in (["--force_ocr"], [], None, "--x"):
            with self.subTest(parser_args=value):
                self.assert_rejected({"input": str(self.pdf), "parser": "marker", "parser_args": value},
                                     "'parser_args' is not supported")
        self.path.write_bytes(json.dumps({"input": str(self.pdf), "parser_args": []}).encode())
        with self.assertRaises(ValueError) as raised:
            convert.read_request(str(self.path))
        self.assertIn("run the parser yourself", str(raised.exception))
        self.assertIn("as input, with parser null", str(raised.exception))

    def test_rejections(self):
        cases = [
            (b"\xff\xfe", "not UTF-8"),
            ("{'input': 'x'}", "double quotes"),
            ('{"input": "a", "input": "b"}', "more than once"),
            ([str(self.book)], "JSON object"),
            ({"input": str(self.book), "olang": "zh"}, "unknown key"),
            ({}, "'input' is required"),
            ({"input": 7}, "'input' must be a string"),
            ({"input": "  "}, "'input' is empty"),
            ('{"input": "C:\\new\\book.epub"}', "control character"),
            ('{"input": "/books/a\\udcff.epub"}', "lone surrogate \\udcff"),
            ({"input": str(self.root / "missing.epub")}, "not found"),
            ({"input": str(self.root / "translate-book-request.json")}, "Unsupported file type"),
            ({"input": str(self.book), "target_lang": "zh; rm -rf ~"}, "language code"),
            ({"input": str(self.book), "temp_root": "../out"}, "temp_root"),
            ({"input": str(self.book), "temp_root": "my books"}, "temp_root"),
            ({"input": str(self.book), "cover": str(self.book)}, "Cover image"),
            ({"input": str(self.book), "cover": str(self.root / "none.png")}, "not found"),
            ({"input": str(self.pdf), "parser": "bash"}, "is not one of"),
            ({"input": str(self.pdf), "parser": []}, "is not one of"),
            ({"input": str(self.pdf), "parser": {"name": "marker"}}, "is not one of"),
            ({"input": str(self.pdf), "parser": 1}, "is not one of"),
            ({"input": str(self.book), "parser": "marker"}, "only reads PDF"),
            ({"input": str(self.book), "pad": "x" * 70000}, "larger than"),
        ]
        for data, needle in cases:
            with self.subTest(data=str(data)[:60]):
                self.assert_rejected(data, needle)

    def test_missing_request_file(self):
        with self.assertRaises(ValueError) as raised:
            convert.read_request(str(self.root / "none.json"))
        self.assertIn("cannot read the request file", str(raised.exception))
        self.assertIn("Write it before each run", str(raised.exception))


class StoreRequestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.temp_dir = self.root / "book_temp"
        self.temp_dir.mkdir()
        self.path = self.root / "translate-book-request.json"
        self.raw = b'{"input": "/books/a.epub"}'
        self.request = {"input": "/books/a.epub", "target_lang": "zh", "temp_root": None,
                        "cover": None, "parser": None}

    def stored(self):
        return json.loads((self.temp_dir / "request.json").read_text(encoding="utf-8"))

    def read_from(self, path):
        """The request file at path as read_request() hands it on."""
        return convert.Own(os.open(path, os.O_RDONLY), Path(path).read_bytes())

    def store(self, path=None, request=None):
        """store_request for the request file at path as this run read it, if it is there."""
        path = path or self.path
        read_from = self.read_from(path) if path.exists() else convert.Own(None, b"")
        convert.store_request(str(self.temp_dir), str(path), request or self.request, read_from)

    def test_only_a_request_convert_stored_is_read_back(self):
        full = dict(self.request, temp_root="work/a", cover="/books/c.png", parser="marker",
                    target_lang="pt-BR")
        for data in (self.request, full):
            self.assertEqual(convert.check_stored_request(data), data)
        bad = [[], None, {}, dict(self.request, parser_args=[]), dict(self.request, extra=1),
               dict(self.request, input="a.epub"), dict(self.request, input=7),
               dict(self.request, input="/books/a.txt"), dict(self.request, target_lang="zh_TW"),
               dict(self.request, target_lang=None), dict(self.request, temp_root="../x"),
               dict(self.request, temp_root=""), dict(self.request, cover="c.png"),
               dict(self.request, cover="/books/c.txt"), dict(self.request, cover=7),
               dict(self.request, parser="bash"), dict(self.request, parser=[])]
        bad += [{k: v for k, v in self.request.items() if k != key} for key in self.request]
        for data in bad:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    convert.check_stored_request(data)

    def test_stored_request_file_is_read_strictly(self):
        path = self.temp_dir / "request.json"
        self.assertIsNone(convert.load_stored_request(str(self.temp_dir)))
        path.write_text(json.dumps(self.request), encoding="utf-8")
        self.assertEqual(convert.load_stored_request(str(self.temp_dir)), self.request)
        elsewhere = self.root / "elsewhere.json"
        elsewhere.write_text(json.dumps(self.request), encoding="utf-8")
        for content in ("{", '{"my": "notes"}', b"\xff", elsewhere, None):
            with self.subTest(content=content):
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
                if content is None:
                    path.mkdir()
                elif isinstance(content, Path):
                    path.symlink_to(content)
                else:
                    path.write_bytes(content if isinstance(content, bytes) else content.encode())
                with self.assertRaises(ValueError) as raised:
                    convert.load_stored_request(str(self.temp_dir))
                self.assertIn("request.json", str(raised.exception))

    def leftovers(self):
        return sorted(p.name for p in self.root.iterdir() if p.name.startswith("."))

    def test_a_fifo_put_in_place_of_the_request_is_left_and_nothing_waits(self):
        # Opening a FIFO waits for a writer; the check must not open what it finds.
        self.path.write_bytes(self.raw)
        read_from = self.read_from(self.path)
        self.addCleanup(read_from.close)
        os.rename(self.path, self.root / "moved.json")
        os.mkfifo(self.path)
        removed = []
        worker = threading.Thread(target=lambda: removed.append(convert._remove_own_file(str(self.path), read_from)),
                                  daemon=True)
        worker.start()
        worker.join(10)
        if worker.is_alive():
            os.close(os.open(self.path, os.O_WRONLY | os.O_NONBLOCK))
            self.fail("removing the request waited on a FIFO")
        self.assertEqual(removed, [False])
        self.assertTrue(stat.S_ISFIFO(os.lstat(self.path).st_mode))
        self.assertEqual(self.leftovers(), [])

    def test_a_file_that_is_not_the_one_read_is_not_moved_at_all(self):
        self.path.write_bytes(self.raw)
        read_from = self.read_from(self.path)
        self.addCleanup(read_from.close)
        replacement = self.root / "replacement.json"
        replacement.write_bytes(self.raw)
        replacement.replace(self.path)
        with mock.patch.object(convert.os, "rename", side_effect=AssertionError("moved")):
            self.assertFalse(convert._remove_own_file(str(self.path), read_from))
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.leftovers(), [])

    def test_stores_and_removes_an_unchanged_request(self):
        self.path.write_bytes(self.raw)
        self.store()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.stored(), self.request)
        self.assertEqual(self.leftovers(), [])

    def test_keeps_a_request_rewritten_meanwhile(self):
        self.path.write_bytes(self.raw)
        read_from = self.read_from(self.path)
        with open(self.path, "r+b") as f:
            f.write(b'{"input": "/books/other.epub"}')
            f.truncate()
        convert.store_request(str(self.temp_dir), str(self.path), self.request, read_from)
        self.assertEqual(self.path.read_bytes(), b'{"input": "/books/other.epub"}')
        self.assertEqual(self.stored(), self.request)
        self.assertEqual(self.leftovers(), [])

    def test_keeps_another_file_with_the_same_bytes(self):
        # Another run wrote the same request anew: not the file this run read.
        self.path.write_bytes(self.raw)
        read_from = self.read_from(self.path)
        replacement = self.root / "replacement.json"
        replacement.write_bytes(self.raw)
        replacement.replace(self.path)
        convert.store_request(str(self.temp_dir), str(self.path), self.request, read_from)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.leftovers(), [])

    def test_keeps_a_request_written_after_the_check(self):
        # The file read is moved aside before it is checked, so a request
        # written to the fixed name after that is not what gets removed.
        self.path.write_bytes(self.raw)
        unlink = os.unlink

        def write_then_unlink(path, *args, **kwargs):
            self.path.write_bytes(b'{"input": "/books/next.epub"}')
            unlink(path, *args, **kwargs)

        with mock.patch.object(convert.os, "unlink", write_then_unlink):
            self.store()
        self.assertEqual(self.path.read_bytes(), b'{"input": "/books/next.epub"}')
        self.assertEqual(self.leftovers(), [])

    def test_puts_back_what_is_not_the_file_read(self):
        target = self.root / "target.json"
        target.write_bytes(self.raw)
        self.path.symlink_to(target)
        read_from = self.read_from(target)
        convert.store_request(str(self.temp_dir), str(self.path), self.request, read_from)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), self.raw)
        self.assertEqual(self.leftovers(), [])

    def test_a_request_written_while_it_was_aside_is_not_overwritten(self):
        self.path.write_bytes(self.raw)
        read_from = self.read_from(self.path)
        replacement = self.root / "replacement.json"
        replacement.write_bytes(self.raw)
        rename = os.rename

        def rename_then_write(source, destination, *args, **kwargs):
            # The file is replaced after it was checked and before it goes aside.
            aside = Path(destination).name.startswith(".translate-book-")
            if aside:
                replacement.replace(source)
            rename(source, destination, *args, **kwargs)
            if aside:
                self.path.write_bytes(b'{"input": "/books/next.epub"}')

        with mock.patch.object(convert.os, "rename", rename_then_write), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            convert.store_request(str(self.temp_dir), str(self.path), self.request, read_from)
        self.assertEqual(self.path.read_bytes(), b'{"input": "/books/next.epub"}')
        aside = self.leftovers()
        self.assertEqual(len(aside), 1)
        self.assertEqual((self.root / aside[0]).read_bytes(), self.raw)
        self.assertIn(f"Warning: {self.path} changed while this run was checking it", out.getvalue())

    def test_request_read_from_the_stored_copy_stays(self):
        # --request "<temp_dir>/request.json" reads the bytes an earlier run
        # stored, which storing again writes unchanged.
        path = self.temp_dir / "request.json"
        self.store()
        raw = path.read_bytes()
        self.store(path)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.stored(), self.request)

    def test_stored_request_keeps_non_ascii_text_readable(self):
        request = dict(self.request, input="/books/《三体》.epub")
        raw = json.dumps(request, ensure_ascii=False).encode("utf-8")
        self.path.write_bytes(raw)
        self.store(request=request)
        self.assertIn("《三体》", (self.temp_dir / "request.json").read_text(encoding="utf-8"))
        self.assertEqual(self.stored(), request)


class RequestRunTests(unittest.TestCase):
    """convert.py --request, run the way SKILL.md's Step 2 runs it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.path_dirs, self.fake_env = [], {}
        self.book = self.root / "books" / "《书》 O'Brien \"$(touch pwned)\" `x`.md"
        self.book.parent.mkdir()
        self.book.write_text("# 书\n\nBody text.\n", encoding="utf-8")
        self.request_path = self.cwd / convert.REQUEST_FILE_NAME

    def write_request(self, **fields):
        request = {"input": str(self.book), "target_lang": "zh", **fields}
        self.request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")

    def run_step_2(self, *extra, env=None):
        return process_guard.run_script("convert.py", "--request", convert.REQUEST_FILE_NAME, *extra,
                                        cwd=self.cwd, path_dirs=self.path_dirs, env={**self.fake_env, **(env or {})})

    def temp_dir(self, temp_root=None):
        path = Path(convert.build_temp_dir(str(self.book), temp_root))
        return path if path.is_absolute() else self.cwd / path

    def test_request_is_stored_in_the_temp_dir_and_every_rerun_works(self):
        self.write_request(temp_root="work/a")
        first = self.run_step_2()
        self.assertEqual(first.returncode, 0, first.stdout)
        temp_dir = self.temp_dir("work/a")
        self.assertIn(f"Input file: {self.book}\n", first.stdout)
        self.assertIn(f"Temp directory: {convert.build_temp_dir(str(self.book), 'work/a')}\n", first.stdout)
        self.assertFalse(self.request_path.exists())
        stored = json.loads((temp_dir / "request.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["input"], str(self.book))

        missing = self.run_step_2()
        self.assertEqual(missing.returncode, 1)
        self.assertIn("cannot read the request file", missing.stdout)

        self.write_request(temp_root="work/a")
        rerun = self.run_step_2()
        self.assertEqual(rerun.returncode, 0, rerun.stdout)
        self.assertIn("Skipping Markdown import", rerun.stdout)

        shutil.rmtree(temp_dir)
        self.write_request(temp_root="work/a")
        fresh = self.run_step_2()
        self.assertEqual(fresh.returncode, 0, fresh.stdout)
        self.assertTrue((temp_dir / "chunk0001.md").is_file())
        self.assertEqual(list(self.root.rglob("pwned*")), [])

    def test_rejected_request_is_kept_and_creates_nothing(self):
        self.write_request(temp_root="../out")
        result = self.run_step_2()
        self.assertEqual(result.returncode, 1)
        self.assertIn("temp_root", result.stdout)
        self.assertEqual(sorted(p.name for p in self.cwd.iterdir()), [convert.REQUEST_FILE_NAME])

    def fake_parsers(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for executable in convert.PARSERS.values():
            path = bin_dir / executable
            path.write_text(FAKE_PARSER.format(python=sys.executable), encoding="utf-8")
            path.chmod(0o755)
        self.path_dirs = [bin_dir]
        self.fake_env = {"FAKE_PARSER_RECORD": str(self.root / "record.json")}

    def test_failed_parser_run_leaves_nothing_and_says_how_to_go_on(self):
        self.fake_parsers()
        self.book = self.book.with_suffix(".pdf")
        self.book.write_bytes(b"%PDF-1.4 paper")
        self.write_request(parser="docling")
        failed = self.run_step_2(env={"FAKE_EXIT": "2", "FAKE_OUTPUTS": "[]"})
        self.assertEqual(failed.returncode, 1, failed.stdout)
        self.assertIn("exited with status 2", failed.stdout)
        self.assertIn("parser output line", failed.stdout)
        self.assertIn("run docling yourself", failed.stdout)
        self.assertTrue(self.request_path.exists())
        self.assertFalse(os.path.lexists(self.temp_dir()))

        self.write_request(parser="docling", target_lang="ja")
        fixed = self.run_step_2(env={"FAKE_EXIT": "0", "FAKE_OUTPUTS": json.dumps(["paper.md"])})
        self.assertEqual(fixed.returncode, 0, fixed.stdout)
        self.assertTrue((self.temp_dir() / "chunk0001.md").is_file())
        self.assertFalse(os.path.lexists(self.temp_dir() / convert.PARSED_DIR))
        stored = convert._load_source_fingerprint(str(self.temp_dir()))
        self.assertEqual((stored["parser"], stored["target_lang"]), ("docling", "ja"))

    def test_failed_import_keeps_this_books_claim(self):
        def import_then_fail(markdown_file, input_md, temp_dir, **kwargs):
            os.mkdir(os.path.join(temp_dir, "images"))
            return False

        self.write_request()
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["convert.py", "--request", convert.REQUEST_FILE_NAME]), \
                mock.patch.object(convert, "import_markdown_input", side_effect=import_then_fail), \
                contextlib.chdir(self.cwd), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit):
            convert.main()
        self.assertEqual(sorted(p.name for p in self.temp_dir().iterdir()),
                         sorted([convert.SOURCE_FINGERPRINT_FILE, "images"]))
        shutil.rmtree(self.temp_dir() / "images")
        rerun = self.run_step_2()
        self.assertEqual(rerun.returncode, 0, rerun.stdout)

    def test_files_the_user_keeps_in_the_temp_dir_are_never_deleted(self):
        self.write_request(temp_root="work/a")
        unclaimed = self.temp_dir("work/a")
        unclaimed.mkdir(parents=True)
        (unclaimed / "source").mkdir()
        (unclaimed / "source" / "keep.txt").write_text("mine", encoding="utf-8")
        refused = self.run_step_2()
        self.assertEqual(refused.returncode, 1, refused.stdout)
        self.assertIn("holds no translate-book conversion", refused.stdout)
        self.assertEqual((unclaimed / "source" / "keep.txt").read_text(encoding="utf-8"), "mine")

        self.write_request()
        self.assertEqual(self.run_step_2().returncode, 0)
        temp_dir = self.temp_dir()
        kept = {"source/keep.txt": "a", "parsed/keep.txt": "b", "parser.log": "c", "cover.png": "d",
                "cover.jpg": "e"}
        for name, text in kept.items():
            (temp_dir / name).parent.mkdir(exist_ok=True)
            (temp_dir / name).write_text(text, encoding="utf-8")
        cover = self.root / "c.webp"
        cover.write_bytes(b"webp")
        for fields in ({}, {"cover": str(cover)}, {}):
            with self.subTest(fields=fields):
                self.write_request(**fields)
                result = self.run_step_2()
                self.assertEqual(result.returncode, 0, result.stdout)
                for name, text in kept.items():
                    self.assertEqual((temp_dir / name).read_text(encoding="utf-8"), text, name)
                self.assertEqual(sorted(p.name for p in temp_dir.glob("cover*")), ["cover.jpg", "cover.png"])

    def test_a_directory_put_in_place_of_parsed_is_left_as_it_is(self):
        # While the parser runs, parsed/ is renamed and another directory takes its name.
        self.fake_parsers()
        self.book = self.book.with_suffix(".pdf")
        self.book.write_bytes(b"%PDF-1.4 paper")
        self.write_request(parser="mineru")
        result = self.run_step_2(env={"FAKE_EXIT": "1", "FAKE_SWAP": "1", "FAKE_OUTPUTS": json.dumps(["book.md"])})
        self.assertEqual(result.returncode, 1, result.stdout)
        parsed = self.temp_dir() / convert.PARSED_DIR
        self.assertEqual((parsed / "secret.txt").read_text(encoding="utf-8"), "not the parser's")
        self.assertIn(f"Warning: {convert.build_temp_dir(str(self.book), None)}/parsed was left as it is",
                      result.stdout)
        # The directory this run created is emptied wherever it went, and nothing else is.
        self.assertEqual(os.listdir(f"{parsed}-held"), [])

    def test_a_directory_put_in_place_of_the_scratch_dir_is_left_as_it_is(self):
        # While the run converts, the directory it copied the book into is renamed and another
        # directory takes its name.
        real_import, real_rename, swapped, moved = convert.import_markdown_input, os.rename, [], []

        def import_then_swap(markdown_copy, *args, **kwargs):
            imported = real_import(markdown_copy, *args, **kwargs)
            scratch = os.path.dirname(markdown_copy)
            real_rename(scratch, f"{scratch}-held")
            os.mkdir(scratch)
            Path(scratch, "secret.txt").write_text("not this run's", encoding="utf-8")
            swapped.append(scratch)
            return imported

        def rename(source, *args, **kwargs):
            moved.append(os.fspath(source))
            return real_rename(source, *args, **kwargs)

        self.write_request()
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["convert.py", "--request", convert.REQUEST_FILE_NAME]), \
                mock.patch.object(convert, "import_markdown_input", side_effect=import_then_swap), \
                mock.patch.object(convert.os, "rename", side_effect=rename), \
                contextlib.chdir(self.cwd), contextlib.redirect_stdout(out):
            convert.main()
        [scratch] = swapped
        self.addCleanup(shutil.rmtree, scratch)
        self.addCleanup(os.rmdir, f"{scratch}-held")
        self.assertTrue((self.temp_dir() / "chunk0001.md").is_file())
        self.assertEqual(Path(scratch, "secret.txt").read_text(encoding="utf-8"), "not this run's")
        self.assertNotIn(scratch, moved)
        self.assertIn(f"Warning: {scratch} was left as it is", out.getvalue())
        self.assertEqual(os.listdir(f"{scratch}-held"), [])

    def test_a_directory_that_cannot_be_listed_is_not_held_open(self):
        opened, real_open = [], os.open

        def open_and_note(*args, **kwargs):
            opened.append(real_open(*args, **kwargs))
            return opened[-1]

        with mock.patch.object(convert.os, "open", side_effect=open_and_note), \
                mock.patch.object(convert.os, "listdir", side_effect=OSError("cannot list")):
            try:
                convert.OwnDir(str(self.root))
            except OSError as e:
                # Its traceback keeps the half-made OwnDir alive, as a caller that keeps the error would.
                raised = e
            else:
                self.fail("OwnDir took a directory it could not list")
        self.assertEqual(str(raised), "cannot list")
        [fd] = opened
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_parser_output_dir_made_by_someone_else_is_not_used(self):
        self.fake_parsers()
        self.book = self.book.with_suffix(".pdf")
        self.book.write_bytes(b"%PDF-1.4 paper")
        temp_dir = self.temp_dir()
        temp_dir.mkdir()
        convert._write_source_fingerprint(str(temp_dir), convert.run_identity(str(self.book), "zh", "docling"))
        (temp_dir / "parsed").mkdir()
        (temp_dir / "parsed" / "keep.md").write_text("mine", encoding="utf-8")
        self.write_request(parser="docling")
        result = self.run_step_2(env={"FAKE_OUTPUTS": json.dumps(["paper.md"])})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("already exists", result.stdout)
        self.assertEqual((temp_dir / "parsed" / "keep.md").read_text(encoding="utf-8"), "mine")
        self.assertFalse((self.root / "record.json").exists())

    def test_request_json_written_by_someone_else_is_not_replaced(self):
        self.write_request()
        self.assertEqual(self.run_step_2().returncode, 0)
        stored = self.temp_dir() / "request.json"
        stored.write_text('{"my": "notes"}', encoding="utf-8")
        self.write_request()
        result = self.run_step_2()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("request.json", result.stdout)
        self.assertEqual(stored.read_text(encoding="utf-8"), '{"my": "notes"}')
        self.assertTrue(self.request_path.exists())

    def test_missing_calibre_creates_nothing(self):
        self.book = self.book.with_suffix(".epub")
        self.book.write_bytes(b"epub bytes")
        for target_lang in ("zh", "ja"):
            with self.subTest(target_lang=target_lang):
                self.write_request(target_lang=target_lang)
                out = io.StringIO()
                with mock.patch.object(sys, "argv", ["convert.py", "--request", convert.REQUEST_FILE_NAME]), \
                        mock.patch.object(convert, "find_calibre_convert", return_value=None), \
                        contextlib.chdir(self.cwd), contextlib.redirect_stdout(out), \
                        self.assertRaises(SystemExit) as raised:
                    convert.main()
                self.assertEqual(raised.exception.code, 1)
                self.assertIn("Calibre ebook-convert not found", out.getvalue())
                self.assertEqual(sorted(p.name for p in self.cwd.iterdir()), [convert.REQUEST_FILE_NAME])

    def test_book_replaced_during_the_run_is_not_converted(self):
        real_identity = convert.run_identity

        def identify_then_replace(*args, **kwargs):
            identity = real_identity(*args, **kwargs)
            self.book.write_text("# Other\n\nAnother book entirely.\n", encoding="utf-8")
            return identity

        self.write_request()
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["convert.py", "--request", convert.REQUEST_FILE_NAME]), \
                mock.patch.object(convert, "run_identity", side_effect=identify_then_replace), \
                contextlib.chdir(self.cwd), contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit) as raised:
            convert.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("changed while it was being read", out.getvalue())
        self.assertEqual(sorted(p.name for p in self.cwd.iterdir()), [convert.REQUEST_FILE_NAME])

        rerun = self.run_step_2()
        self.assertEqual(rerun.returncode, 0, rerun.stdout)
        self.assertIn("Another book entirely.", (self.temp_dir() / "chunk0001.md").read_text(encoding="utf-8"))

    def test_conversion_reads_a_verified_copy_of_the_book(self):
        seen = []

        def fake_calibre(input_file, htmlz_file, calibre_path):
            seen.append((input_file, Path(input_file).read_bytes()))
            with zipfile.ZipFile(htmlz_file, "w") as z:
                z.writestr("index.html", "<html><body><p>Body text.</p></body></html>")
            return True

        def fake_markdown(html_file, md_file, *args, **kwargs):
            Path(md_file).write_text("# Title\n\nBody text.\n", encoding="utf-8")
            return True

        self.book = self.book.with_suffix(".epub")
        self.book.write_bytes(b"epub bytes")
        self.write_request()
        with mock.patch.object(sys, "argv", ["convert.py", "--request", convert.REQUEST_FILE_NAME]), \
                mock.patch.object(convert, "find_calibre_convert", return_value="ebook-convert"), \
                mock.patch.object(convert, "convert_to_htmlz", side_effect=fake_calibre), \
                mock.patch.object(convert, "convert_html_to_markdown", side_effect=fake_markdown), \
                contextlib.chdir(self.cwd), contextlib.redirect_stdout(io.StringIO()):
            convert.main()
        [(path, data)] = seen
        self.assertEqual(data, b"epub bytes")
        self.assertEqual(Path(path).name, self.book.name)
        self.assertFalse(Path(path).is_relative_to(self.root), path)
        self.assertFalse(Path(path).parent.exists())
        self.assertNotIn("source", os.listdir(self.temp_dir()))
        self.assertEqual(convert._load_source_fingerprint(str(self.temp_dir()))["sha256"],
                         convert.file_hash(str(self.book)))

    def test_parser_reads_a_verified_copy_of_the_book(self):
        self.fake_parsers()
        self.book = self.book.with_suffix(".pdf")
        self.book.write_bytes(b"%PDF-1.4 paper")
        self.write_request(parser="docling")
        result = self.run_step_2(env={"FAKE_OUTPUTS": json.dumps(["paper.md"])})
        self.assertEqual(result.returncode, 0, result.stdout)
        argv = json.loads((self.root / "record.json").read_text(encoding="utf-8"))["argv"]
        copy = Path(argv[0])
        self.assertEqual(copy.name, self.book.name)
        self.assertNotEqual(copy, self.book)
        self.assertFalse(copy.is_relative_to(self.root), copy)
        self.assertFalse(copy.parent.exists())
        self.assertEqual(sorted(os.listdir(self.temp_dir())),
                         sorted(["chunk0001.md", "config.txt", "input.md", "manifest.json", "request.json",
                                 convert.SOURCE_FINGERPRINT_FILE]))

    def test_markdown_images_still_come_from_the_books_directory(self):
        (self.book.parent / "img").mkdir()
        (self.book.parent / "img" / "fig.png").write_bytes(b"png bytes")
        self.book.write_text("# 书\n\n![a figure](img/fig.png)\n\nBody text.\n", encoding="utf-8")
        self.write_request()
        result = self.run_step_2()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.temp_dir() / "images" / "fig.png").read_bytes(), b"png bytes")
        self.assertIn("images/fig.png", (self.temp_dir() / "chunk0001.md").read_text(encoding="utf-8"))

    def test_request_excludes_command_line_values(self):
        self.write_request()
        result = self.run_step_2(str(self.book))
        self.assertEqual(result.returncode, 2)
        self.assertIn("give the input file, the language and temp_root in the request file", result.stderr)

    def test_cover_is_named_in_the_stored_request_and_not_copied(self):
        png = self.root / "c.png"
        png.write_bytes(b"png bytes")
        for cover in (png, None):
            with self.subTest(cover=cover):
                self.write_request(cover=str(cover) if cover else None)
                result = self.run_step_2()
                self.assertEqual(result.returncode, 0, result.stdout)
                stored = json.loads((self.temp_dir() / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["cover"], str(cover) if cover else None)
                self.assertEqual(list(self.temp_dir().glob("cover*")), [])

    def test_temp_dir_on_another_file_system(self):
        shm = Path("/dev/shm")
        if not shm.is_dir() or shm.stat().st_dev == self.root.stat().st_dev:
            self.skipTest("no second file system")
        with tempfile.TemporaryDirectory(dir=shm) as other:
            try:
                root = convert.validate_temp_root(other)
            except ValueError:
                self.skipTest("the second file system's path is not a safe temp_root")
            self.write_request(temp_root=root)
            result = self.run_step_2()
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(self.request_path.exists())
            self.assertTrue((self.temp_dir(root) / "request.json").is_file())

    def test_second_book_with_the_same_temp_dir_name_is_refused(self):
        self.write_request()
        self.assertEqual(self.run_step_2().returncode, 0)
        other = self.book.with_name("《书》 O_Brien _$(touch pwned)_ `x`.md")
        other.write_text("# Another book\n", encoding="utf-8")
        self.assertEqual(convert.build_temp_dir(str(other)), convert.build_temp_dir(str(self.book)))
        self.request_path.write_text(json.dumps({"input": str(other)}, ensure_ascii=False), encoding="utf-8")

        result = self.run_step_2()

        self.assertEqual(result.returncode, 1)
        self.assertIn("different source bytes", result.stdout)
        self.assertIn("Nothing was changed", result.stdout)
        _assert_no_deletion_advice(self, result.stdout)
        self.assertTrue(self.request_path.exists())

    def test_another_target_language_is_refused(self):
        self.write_request()
        self.assertEqual(self.run_step_2().returncode, 0)
        self.write_request(target_lang="ja")
        result = self.run_step_2()
        self.assertEqual(result.returncode, 1)
        self.assertIn("translation into zh", result.stdout)
        _assert_no_deletion_advice(self, result.stdout)


class PrepareTempDirTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.book = self.root / "《三体》 (1).epub"
        self.book.write_bytes(b"book bytes")
        self.identity = convert.run_identity(str(self.book), "zh")
        self.legacy = Path(convert.legacy_temp_dir(str(self.book), str(self.work)))
        self.new = Path(convert.build_temp_dir(str(self.book), str(self.work)))
        self.assertEqual(self.new.name, "三体_1_temp")

    def prepare(self, book=None, identity=None):
        return self.claim(book, identity)[:2]

    def claim(self, book=None, identity=None):
        book = book or self.book
        return convert.prepare_temp_dir(str(book), str(self.work), identity or self.identity)

    def make_cache(self, path, identity=None, config_name=None):
        path.mkdir()
        (path / "chunk0001.md").write_text("cached chunk\n", encoding="utf-8")
        if identity:
            convert._write_source_fingerprint(str(path), identity)
        if config_name:
            (path / "config.txt").write_text(f"input_file=/old/{config_name}\n", encoding="utf-8")

    def assert_refused(self, needle, book=None, identity=None):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        with self.assertRaises(convert.TempDirError) as raised:
            self.prepare(book, identity)
        self.assertIn(needle, str(raised.exception))
        _assert_no_deletion_advice(self, str(raised.exception))
        self.assertEqual(sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")), before)

    def other_identity(self):
        other = self.root / "other.epub"
        other.write_bytes(b"another book")
        return convert.run_identity(str(other), "zh")

    def test_fresh_temp_dir_is_claimed_at_once(self):
        temp_dir, notes = self.prepare()
        self.assertEqual((temp_dir, notes), (str(self.new), []))
        self.assertEqual(os.listdir(temp_dir), [convert.SOURCE_FINGERPRINT_FILE])
        other = self.root / "三体 1.epub"
        other.write_bytes(b"another book")
        self.assertEqual(convert.build_temp_dir(str(other), str(self.work)), str(self.new))
        self.assert_refused("different source bytes", other, convert.run_identity(str(other), "zh"))

    def test_this_books_fingerprinted_legacy_dir_is_renamed(self):
        self.make_cache(self.legacy, self.identity)
        temp_dir, notes = self.prepare()
        self.assertFalse(self.legacy.exists())
        self.assertEqual((self.new / "chunk0001.md").read_text(encoding="utf-8"), "cached chunk\n")
        self.assertTrue(notes[0].startswith(f"Renamed {self.legacy} to {self.new}"), notes)

    def test_unfingerprinted_legacy_dir_of_the_same_file_name_is_renamed_and_adopted(self):
        self.make_cache(self.legacy, config_name=self.book.name)
        with open(self.legacy / "config.txt", "a", encoding="utf-8") as f:
            f.write("output_lang=zh\n")
        temp_dir, notes = self.prepare()
        self.assertFalse(self.legacy.exists())
        self.assertTrue(notes[0].startswith("Renamed"), notes)
        self.assertTrue(notes[1].startswith("Warning:"), notes)
        self.assertEqual(convert._load_source_fingerprint(temp_dir)["sha256"], self.identity["sha256"])

    def test_legacy_dir_of_another_book_or_file_is_left_alone(self):
        for identity, config_name in ((self.other_identity(), None), (None, "other.epub"), (None, None)):
            with self.subTest(identity=bool(identity), config_name=config_name):
                self.make_cache(self.legacy, identity, config_name)
                temp_dir, notes = self.prepare()
                self.assertTrue(notes[0].startswith(f"Note: {self.legacy} was left as it is"), notes)
                self.assertTrue((self.legacy / "chunk0001.md").exists())
                self.assertEqual(os.listdir(temp_dir), [convert.SOURCE_FINGERPRINT_FILE])
                shutil.rmtree(self.legacy)
                shutil.rmtree(self.new)

    def test_symlinked_legacy_dir_is_refused(self):
        real = self.root / "real_temp"
        self.make_cache(real, self.identity)
        self.legacy.symlink_to(real, target_is_directory=True)
        self.assert_refused("is a symlink")

    def test_legacy_file_is_ignored(self):
        self.legacy.write_text("not a directory", encoding="utf-8")
        temp_dir, notes = self.prepare()
        self.assertEqual(notes, [])
        self.assertTrue(self.legacy.is_file())

    def test_new_dir_holding_this_book_wins_over_the_legacy_dir(self):
        self.make_cache(self.legacy, self.identity)
        self.make_cache(self.new, self.identity)
        temp_dir, notes = self.prepare()
        self.assertTrue(notes[0].startswith(f"Note: {self.legacy} was left as it is; using"), notes)
        self.assertTrue(self.legacy.exists())

    def test_taken_new_name_stops_the_rename(self):
        self.make_cache(self.legacy, self.identity)
        for setup in ("other book", "empty", "foreign file"):
            with self.subTest(setup=setup):
                if setup == "other book":
                    self.make_cache(self.new, self.other_identity())
                else:
                    self.new.mkdir()
                    if setup == "foreign file":
                        (self.new / "notes.txt").write_text("mine", encoding="utf-8")
                self.assert_refused("already exists")
                shutil.rmtree(self.new)

    def test_same_directory_under_another_spelling_is_not_renamed(self):
        self.make_cache(self.new, self.identity)
        spelling = os.path.join(str(self.work), ".", self.new.name)
        with mock.patch.object(convert, "legacy_temp_dir", return_value=spelling):
            temp_dir, notes = self.prepare()
        self.assertEqual((temp_dir, notes), (str(self.new), []))
        self.assertTrue((self.new / "chunk0001.md").exists())

    def test_symlink_or_file_at_the_temp_dir_is_refused(self):
        target = self.root / "elsewhere"
        target.mkdir()
        self.new.symlink_to(target, target_is_directory=True)
        self.assert_refused("is a symlink")
        self.new.unlink()
        self.new.write_text("x", encoding="utf-8")
        self.assert_refused("is not a directory")

    def test_foreign_files_in_the_temp_dir_are_refused(self):
        self.new.mkdir()
        (self.new / "notes.txt").write_text("mine", encoding="utf-8")
        self.assert_refused("holds no translate-book conversion")

    def test_other_parser_settings_are_refused(self):
        marker = dict(self.identity, parser="marker")
        self.prepare(identity=marker)
        (self.new / "input.md").write_text("# Parsed\n", encoding="utf-8")
        for other in (self.identity, dict(marker, parser="docling")):
            with self.subTest(parser=other["parser"]):
                self.assert_refused("which would give different chunks", identity=other)
        self.assertEqual(self.prepare(identity=dict(marker))[0], str(self.new))

    def test_same_book_without_conversion_state_takes_new_settings(self):
        # An earlier run of this book stopped (or was killed) before it made
        # anything: its settings must not lock the directory.
        failed = dict(self.identity, parser="docling")
        self.prepare(identity=failed)
        (self.new / "images").mkdir()
        for fixed in (dict(failed, target_lang="ja"), self.identity):
            with self.subTest(parser=fixed["parser"], target_lang=fixed["target_lang"]):
                temp_dir, notes = self.prepare(identity=fixed)
                self.assertEqual(temp_dir, str(self.new))
                self.assertEqual(convert._load_source_fingerprint(temp_dir), fixed)
        self.assert_refused("different source bytes", identity=self.other_identity())

    def assert_no_leftovers(self):
        self.assertEqual([p.name for p in self.work.iterdir() if p.name.startswith(".")], [])
        if self.new.is_dir():
            self.assertEqual([p.name for p in self.new.iterdir() if p.name.startswith(".")], [])

    def test_failed_run_gives_up_only_a_claim_that_holds_nothing_else(self):
        fingerprint = self.new / convert.SOURCE_FINGERPRINT_FILE
        for made_here in (True, False):
            with self.subTest(made_here=made_here):
                if not made_here:
                    self.new.mkdir()
                _, _, claim = self.claim()
                self.assertEqual(claim.directory is not None, made_here)
                convert.release_temp_dir(str(self.new), claim)
                self.assertEqual(os.path.lexists(self.new), not made_here)
                self.assertFalse(os.path.lexists(fingerprint))
                if not made_here:
                    self.new.rmdir()
        _, _, claim = self.claim()
        (self.new / "images").mkdir()
        convert.release_temp_dir(str(self.new), claim)
        self.assertEqual(convert._load_source_fingerprint(str(self.new)), self.identity)
        self.assert_no_leftovers()

    def test_failed_run_that_joined_removes_nothing(self):
        # Another run of this book claimed the temp dir; this one joined it.
        self.claim()
        fingerprint = self.new / convert.SOURCE_FINGERPRINT_FILE
        before = (fingerprint.stat().st_ino, fingerprint.read_bytes())
        _, _, joined = self.claim()
        self.assertEqual(joined, convert.TempDirClaim(None, None))
        convert.release_temp_dir(str(self.new), joined)
        self.assertEqual((fingerprint.stat().st_ino, fingerprint.read_bytes()), before)

    def test_failed_run_keeps_a_fingerprint_written_anew_meanwhile(self):
        _, _, claim = self.claim()
        # Another run of this book with other settings restarts the temp dir,
        # then a third with this run's settings writes this run's bytes anew.
        self.claim(identity=dict(self.identity, target_lang="ja"))
        _, _, restarted = self.claim()
        self.assertIsNone(restarted.directory)
        self.assertIsNotNone(restarted.fingerprint)
        convert.release_temp_dir(str(self.new), claim)
        self.assertEqual(convert._load_source_fingerprint(str(self.new)), self.identity)
        convert.release_temp_dir(str(self.new), restarted)
        self.assertFalse(os.path.lexists(self.new / convert.SOURCE_FINGERPRINT_FILE))
        self.assertTrue(self.new.is_dir())
        self.assert_no_leftovers()

    def test_failed_run_keeps_a_temp_dir_made_anew_meanwhile(self):
        _, _, claim = self.claim()
        shutil.rmtree(self.new)
        _, _, other = self.claim()
        self.assertIsNotNone(other.directory)
        self.assertFalse(claim.directory.is_this(self.new.stat()))
        convert.release_temp_dir(str(self.new), claim)
        self.assertEqual(convert._load_source_fingerprint(str(self.new)), self.identity)
        self.assert_no_leftovers()

    def test_failed_run_keeps_a_temp_dir_put_in_its_place_after_it_was_checked(self):
        # Between the check where it is and the move aside, another directory takes its name.
        _, _, claim = self.claim()
        held, rename = self.work / "held", os.rename

        def swap_then_rename(source, destination, *args, **kwargs):
            if os.fspath(source) == str(self.new) and Path(destination).name.startswith(".translate-book-"):
                rename(source, held)
                os.mkdir(source)
            return rename(source, destination, *args, **kwargs)

        with mock.patch.object(convert.os, "rename", swap_then_rename):
            convert.release_temp_dir(str(self.new), claim)
        self.assertEqual(os.listdir(self.new), [])
        self.assertEqual(os.listdir(held), [])
        self.assert_no_leftovers()

    def test_failed_run_keeps_an_empty_temp_dir_made_anew_after_its_fingerprint_went(self):
        _, _, claim = self.claim()
        remove_own_file = convert._remove_own_file

        def then_another_run_makes_the_dir_anew(path, own):
            removed = remove_own_file(path, own)
            os.rmdir(self.new)
            os.mkdir(self.new)
            return removed

        with mock.patch.object(convert, "_remove_own_file", then_another_run_makes_the_dir_anew):
            convert.release_temp_dir(str(self.new), claim)
        self.assertEqual(os.listdir(self.new), [])
        self.assert_no_leftovers()

    def test_unreadable_fingerprint_is_refused(self):
        self.make_cache(self.new)
        (self.new / convert.SOURCE_FINGERPRINT_FILE).write_text('{"sha256": "x"', encoding="utf-8")
        (self.new / "config.txt").write_text(
            f"input_file=/old/{self.book.name}\noutput_lang=zh\n", encoding="utf-8")
        self.assert_refused(f"{convert.SOURCE_FINGERPRINT_FILE} cannot be read")

    def test_legacy_dir_with_unreadable_fingerprint_or_other_language_is_left_alone(self):
        for fingerprint, config in (("{", "output_lang=zh\n"), (None, "output_lang=ja\n")):
            with self.subTest(fingerprint=fingerprint, config=config):
                self.make_cache(self.legacy, config_name=self.book.name)
                with open(self.legacy / "config.txt", "a", encoding="utf-8") as f:
                    f.write(config)
                if fingerprint:
                    (self.legacy / convert.SOURCE_FINGERPRINT_FILE).write_text(fingerprint, encoding="utf-8")
                temp_dir, notes = self.prepare()
                self.assertTrue(notes[0].startswith(f"Note: {self.legacy} was left as it is"), notes)
                self.assertEqual(os.listdir(temp_dir), [convert.SOURCE_FINGERPRINT_FILE])
                shutil.rmtree(self.legacy)
                shutil.rmtree(self.new)


FAKE_PARSER = """#!{python}
import json, os, sys
with open(os.environ["FAKE_PARSER_RECORD"], "w", encoding="utf-8") as f:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, f)
print("parser output line")
for name in json.loads(os.environ.get("FAKE_OUTPUTS", "[]")):
    os.makedirs(os.path.dirname(name) or ".", exist_ok=True)
    with open(name, "w", encoding="utf-8") as f:
        f.write("# Parsed\\n\\nBody.\\n")
for link, target in json.loads(os.environ.get("FAKE_LINKS", "[]")):
    os.symlink(target, link)
if os.environ.get("FAKE_SWAP"):
    # Another process renames the directory the parser writes into and puts its own in its place.
    here = os.getcwd()
    os.rename(here, here + "-held")
    os.mkdir(here)
    with open(os.path.join(here, "secret.txt"), "w", encoding="utf-8") as f:
        f.write("not the parser's")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


class RunParserTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for executable in convert.PARSERS.values():
            path = self.bin / executable
            path.write_text(FAKE_PARSER.format(python=sys.executable), encoding="utf-8")
            path.chmod(0o755)
        self.pdf = self.root / "my book.pdf"
        self.pdf.write_bytes(b"%PDF-1.4")
        self.temp_dir = self.root / "work" / "my_book_temp"
        self.temp_dir.mkdir(parents=True)
        self.record = self.root / "record.json"

    def run_parser(self, parser, outputs, exit_code=0, links=()):
        env = {"FAKE_PARSER_RECORD": str(self.record), "FAKE_OUTPUTS": json.dumps(outputs),
               "FAKE_EXIT": str(exit_code), "FAKE_LINKS": json.dumps(list(links))}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                process_guard.library_processes(convert.PARSERS[parser], path_dirs=[self.bin], env=env):
            result = convert.run_parser(parser, str(self.pdf), str(self.temp_dir))
        return result, out.getvalue()

    def assert_failed_and_left_nothing(self, result, out, needle):
        self.assertEqual(result, (None, None))
        self.assertIn(needle, out)
        self.assertEqual(os.listdir(self.temp_dir), [])

    def test_each_parser_gets_its_fixed_arguments_only(self):
        parsed = str(self.temp_dir / "parsed")
        cases = {
            "mineru": (["parse", str(self.pdf), "-o", os.path.join(parsed, "book.md")], ["book.md"]),
            "marker": ([str(self.pdf), "--output_dir", parsed], ["my book/my book.md"]),
            "docling": ([str(self.pdf), "--enrich-formula", "--output", parsed], ["my book.md"]),
        }
        for parser, (argv, outputs) in cases.items():
            with self.subTest(parser=parser):
                result, out = self.run_parser(parser, outputs)
                record = json.loads(self.record.read_text(encoding="utf-8"))
                self.assertEqual(record["argv"], argv)
                self.assertEqual(os.path.realpath(record["cwd"]), os.path.realpath(parsed))
                markdown_file, held = result
                self.assertEqual((markdown_file, held.path), (os.path.join(parsed, outputs[0]), parsed))
                self.assertEqual(os.listdir(self.temp_dir), ["parsed"])
                held.remove()
                self.assertEqual(os.listdir(self.temp_dir), [])

    def test_needs_exactly_one_markdown_file(self):
        for outputs in ([], ["a.md", "sub/b.md"]):
            with self.subTest(outputs=outputs):
                result, out = self.run_parser("docling", outputs)
                self.assert_failed_and_left_nothing(result, out, "expected one Markdown file")
                self.assertIn("run docling yourself", out)

    def test_markdown_output_must_be_a_regular_file_inside_parsed(self):
        outside = self.root / "outside.md"
        outside.write_text("# Another text\n", encoding="utf-8")
        for target in (str(outside), "notes.txt"):
            with self.subTest(target=target):
                result, out = self.run_parser("docling", ["notes.txt"], links=[["book.md", target]])
                self.assert_failed_and_left_nothing(result, out, "expected one Markdown file")
        self.assertEqual(outside.read_text(encoding="utf-8"), "# Another text\n")

    def test_existing_parsed_dir_is_neither_used_nor_changed(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.md").write_text("mine", encoding="utf-8")
        parsed = self.temp_dir / "parsed"
        for kind in ("directory", "file", "symlink"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    parsed.mkdir()
                    (parsed / "stale.md").write_text("old", encoding="utf-8")
                elif kind == "file":
                    parsed.write_text("x", encoding="utf-8")
                else:
                    parsed.symlink_to(elsewhere, target_is_directory=True)
                before = sorted(str(p) for p in self.root.rglob("*"))
                result, out = self.run_parser("docling", ["my book.md"])
                self.assertEqual(result, (None, None))
                self.assertIn("already exists", out)
                self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)
                self.assertFalse(self.record.exists())
                if kind == "directory":
                    shutil.rmtree(parsed)
                else:
                    parsed.unlink()
        self.assertEqual((elsewhere / "keep.md").read_text(encoding="utf-8"), "mine")

    def test_failure_shows_the_end_of_the_output_and_the_way_to_go_on(self):
        result, out = self.run_parser("marker", ["partial.md"], exit_code=3)
        self.assert_failed_and_left_nothing(result, out, "exited with status 3")
        self.assertIn("parser output line", out)
        self.assertIn("run marker_single yourself", out)
        self.assertIn("as input, with parser null", out)

    def test_missing_executable(self):
        (self.bin / "marker_single").unlink()
        with mock.patch.object(convert.shutil, "which", return_value=None):
            result, out = self.run_parser("marker", ["x.md"])
        self.assert_failed_and_left_nothing(result, out, "marker_single not found")


class ConfigValueTests(unittest.TestCase):
    def test_values_stay_on_their_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            convert.create_config_file(
                tmp, "/books/a\nb.epub", "auto", "zh",
                {"title": "Line one\nouput_lang=xx\r\x00end", "creator": "\udc80Author"},
            )
            values = convert._config_values(tmp)
            self.assertEqual(values["input_file"], "/books/a b.epub")
            self.assertEqual(values["output_lang"], "zh")
            self.assertEqual(values["original_title"], "Line one ouput_lang=xx  end")
            self.assertEqual(values["creator"], "\ufffdAuthor")
            self.assertNotIn("ouput_lang", values)


class SplitResumeTests(unittest.TestCase):
    """However a split stops, or the disk loses chunk files, a rerun ends with every chunk or an error."""

    CHUNK_SIZE = 1000
    LEFT_BY_A_KILL = r"\.(chunk[0-9]{4,}\.md|manifest\.json)\.[0-9a-f]{16}\.part"

    class Killed(BaseException):
        pass

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.book = "".join(
            f"# Chapter {n}\n\n" + "".join(f"Paragraph {n}.{p}: " + "words " * 25 + "\n\n" for p in range(3))
            for n in range(1, 9)
        )
        reference = self.book_dir("reference")
        self.run_split(reference)
        self.expected = self.chunks(reference)
        self.assertGreater(len(self.expected), 5)
        self.temp = self.book_dir("book_temp")

    def book_dir(self, name):
        d = self.root / name
        d.mkdir()
        (d / "input.md").write_text(self.book, encoding="utf-8")
        return d

    def run_split(self, d):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            count = convert._do_split_and_manifest(str(d), str(d / "input.md"), self.CHUNK_SIZE)
        return count, out.getvalue()

    @staticmethod
    def chunks(d):
        return {p.name: p.read_bytes() for p in sorted(d.glob("chunk*.md"))}

    @staticmethod
    def snapshot(d):
        return {p.name: (os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None,
                         p.lstat().st_ino)
                for p in sorted(d.iterdir())}

    def finished_split(self):
        """self.temp as a split that finished leaves it, with a translation and a file of the user's."""
        self.run_split(self.temp)
        (self.temp / "output_chunk0001.md").write_text("译文\n", encoding="utf-8")
        (self.temp / "notes.txt").write_text("mine\n", encoding="utf-8")

    def assert_complete(self, d, count, killed=False, expected=None):
        import hashlib
        import re
        expected = self.expected if expected is None else expected
        self.assertEqual(count, len(expected))
        self.assertEqual(self.chunks(d), expected)
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({c["source_file"]: c["source_hash"] for c in manifest["chunks"]},
                         {name: hashlib.sha256(data).hexdigest() for name, data in expected.items()})
        for p in d.iterdir():
            if p.name.startswith("."):
                self.assertTrue(killed and re.fullmatch(self.LEFT_BY_A_KILL, p.name), p.name)

    def assert_refused(self, why):
        before = self.snapshot(self.temp)
        with self.assertRaises(ValueError) as cm:
            self.run_split(self.temp)
        self.assertIn(why, str(cm.exception))
        self.assertIn("Nothing was changed", str(cm.exception))
        self.assertEqual(self.snapshot(self.temp), before)

    @contextlib.contextmanager
    def stop_at(self, k, copy_to, exception):
        """Raise exception at the k-th change the split makes on disk: a write
        to a file (before it, and after half of it), or a link, rename, replace
        or unlink. Just before, copy the temp dir to copy_to: that copy is what
        a SIGKILL there leaves, since no cleanup of the run has touched it."""
        seen = [0]
        temp = str(self.temp)

        def change(half=None):
            seen[0] += 1
            if seen[0] == k:
                if half:
                    half()
                shutil.copytree(temp, copy_to, symlinks=True)
                raise exception

        class Watched:
            def __init__(self, f):
                self.f = f

            def write(self, data):
                change()
                change(lambda: (self.f.write(data[:len(data) // 2]), self.f.flush()))
                return self.f.write(data)

            def __getattr__(self, name):
                return getattr(self.f, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.f.close()

        def watched_open(file, mode="r", *args, **kwargs):
            f = open(file, mode, *args, **kwargs)
            return Watched(f) if any(c in mode for c in "wxa+") else f

        def watched(real):
            def call(*args, **kwargs):
                if any(str(a).startswith(temp) for a in args[:2]):
                    change()
                return real(*args, **kwargs)
            return call

        import manifest
        with contextlib.ExitStack() as stack:
            for module in (convert, manifest):
                stack.enter_context(mock.patch.object(module, "open", watched_open, create=True))
            for name, real in (("link", os.link), ("rename", os.rename), ("replace", os.replace),
                               ("unlink", os.unlink)):
                stack.enter_context(mock.patch.object(os, name, watched(real)))
            yield seen

    def stop_everywhere(self, prepare, check):
        """For each change a run makes on disk and three ways to stop there, prepare
        a new self.temp, run the split, stop it, and call check(the temp dir a
        rerun has to complete, whether a kill left it). A SIGKILL leaves the copy
        that stop_at() takes; after Ctrl-C or a full disk the run has cleaned up.
        Returns the number of stops."""
        import errno
        k = stops = 0
        while True:
            k += 1
            for exception in (self.Killed(), KeyboardInterrupt(), OSError(errno.ENOSPC, "No space left on device")):
                with self.subTest(change=k, exception=type(exception).__name__):
                    shutil.rmtree(self.temp)
                    self.temp = self.book_dir("book_temp")
                    prepare()
                    killed = self.root / "killed"
                    shutil.rmtree(killed, ignore_errors=True)
                    try:
                        with self.stop_at(k, killed, exception) as seen:
                            self.run_split(self.temp)
                    except type(exception):
                        pass
                    else:
                        # The run made fewer than k changes: every point has been tried.
                        self.assertGreater(k, 3)
                        return stops
                    stops += 1
                    if isinstance(exception, self.Killed):
                        check(killed, True)
                    else:
                        check(self.temp, False)

    def test_a_rerun_completes_a_split_stopped_at_any_point(self):
        def prepare():
            (self.temp / "output_chunk0001.md").write_text("译文\n", encoding="utf-8")
            (self.temp / "notes.txt").write_text("mine\n", encoding="utf-8")

        def check(d, killed):
            count, _ = self.run_split(d)
            self.assert_complete(d, count, killed)
            self.assertEqual((d / "output_chunk0001.md").read_text(encoding="utf-8"), "译文\n")
            self.assertEqual((d / "notes.txt").read_text(encoding="utf-8"), "mine\n")

        self.assertGreater(self.stop_everywhere(prepare, check), 3 * 4 * len(self.expected))

    def test_a_rerun_that_writes_a_lost_chunk_and_new_hashes_can_be_stopped_too(self):
        # Step 9 of SKILL.md reruns Step 2 after a source chunk was edited.
        edited = dict(self.expected, **{"chunk0002.md": "edited by hand\n".encode()})

        def prepare():
            self.finished_split()
            (self.temp / "chunk0002.md").write_bytes(edited["chunk0002.md"])
            (self.temp / "chunk0005.md").unlink()

        def check(d, killed):
            count, _ = self.run_split(d)
            self.assert_complete(d, count, killed, expected=edited)

        self.stop_everywhere(prepare, check)

    # The state table of handoff.md: what manifest.json there is, and the state of one chunk
    # file, chunk0003.md (chunk0099.md for "outside"). The outcome is "written", "kept",
    # or the words of the error the rerun stops with, changing nothing.
    STATE_TABLE = [
        ("none", "missing", "written"),
        ("none", "empty", "written"),
        ("none", "all NUL", "written"),
        ("none", "matching", "kept"),
        ("none", "differing", "chunk0003.md in {temp}/ is not the chunk of that name that this run splits off"),
        ("none", "outside, empty", "chunk0099.md in {temp}/ is not a chunk of {temp}/input.md: this run splits it into {n} chunks, none named chunk0099.md, and there is no readable manifest.json"),
        ("none", "outside, with text", "chunk0099.md in {temp}/ is not a chunk of {temp}/input.md: this run splits it into {n} chunks, none named chunk0099.md, and there is no readable manifest.json"),
        ("listed", "missing", "written"),
        ("listed", "empty", "written"),
        ("listed", "all NUL", "written"),
        ("listed", "matching", "kept"),
        ("listed", "differing", "kept"),
        ("listed", "outside, empty", "chunk0099.md in {temp}/ is not listed in manifest.json"),
        ("listed", "outside, with text", "chunk0099.md in {temp}/ is not listed in manifest.json"),
        ("records an edit", "missing", "chunk0003.md in {temp}/ is missing, and the chunk of that name that this run splits off {temp}/input.md does not have the hash manifest.json records for it"),
        ("records an edit", "empty", "chunk0003.md in {temp}/ is empty, and the chunk"),
        ("records an edit", "all NUL", "chunk0003.md in {temp}/ is all NUL bytes, and the chunk"),
        ("records an edit", "differing", "kept"),
    ]
    EDIT = "edited by hand\n".encode()

    def set_state(self, state):
        chunk = self.temp / "chunk0003.md"
        if state == "missing":
            chunk.unlink()
        elif state == "empty":
            chunk.write_bytes(b"")
        elif state == "all NUL":
            chunk.write_bytes(b"\0" * len(self.expected["chunk0003.md"]))
        elif state == "differing":
            chunk.write_bytes(self.EDIT)
        elif state.startswith("outside"):
            (self.temp / "chunk0099.md").write_bytes(b"" if state == "outside, empty" else b"not of the split\n")

    def test_each_state_of_a_chunk_with_and_without_a_manifest(self):
        for manifest, state, outcome in self.STATE_TABLE:
            with self.subTest(manifest=manifest, chunk=state):
                shutil.rmtree(self.temp)
                self.temp = self.book_dir("book_temp")
                self.finished_split()
                if manifest == "none":
                    (self.temp / "manifest.json").unlink()
                elif manifest == "records an edit":
                    (self.temp / "chunk0003.md").write_bytes(self.EDIT)
                    self.run_split(self.temp)
                self.set_state(state)
                if outcome not in ("written", "kept"):
                    self.assert_refused(outcome.format(temp=self.temp, n=len(self.expected)))
                    continue
                kept = {p.name: p.stat().st_ino for p in self.temp.iterdir()
                        if p.name not in ("chunk0003.md", "manifest.json")}

                count, out = self.run_split(self.temp)

                expected = dict(self.expected, **{"chunk0003.md": self.EDIT}) if state == "differing" else None
                self.assert_complete(self.temp, count, expected=expected)
                self.assertEqual({name: (self.temp / name).stat().st_ino for name in kept}, kept)
                if outcome == "written":
                    self.assertIn("wrote the 1 missing or with no content: chunk0003.md", out)
                else:
                    self.assertIn(f"Skipping markdown splitting - found {len(self.expected)} existing chunk files", out)

    def test_a_finished_split_that_lost_every_chunk(self):
        self.finished_split()
        for p in self.temp.glob("chunk*.md"):
            p.unlink()

        count, out = self.run_split(self.temp)

        self.assert_complete(self.temp, count)
        self.assertIn(f"Found 0 existing chunk files; wrote the {len(self.expected)} missing", out)
        # manifest.json records an edit of chunk0003.md: no chunk is written and the record stays.
        (self.temp / "chunk0003.md").write_bytes(self.EDIT)
        self.run_split(self.temp)
        for p in self.temp.glob("chunk*.md"):
            p.unlink()
        self.assert_refused("chunk0003.md in")

    def test_a_manifest_that_is_not_a_regular_file_stops_the_run_before_it_writes(self):
        import signal

        def fifo(path):
            os.mkfifo(path)

        def symlink(path):
            path.symlink_to(self.root / "elsewhere.json")

        def directory(path):
            path.mkdir()

        def hung(signum, frame):
            raise AssertionError("the split waited on manifest.json")

        for make in (fifo, symlink, directory):
            for with_chunks in (True, False):
                with self.subTest(make.__name__, chunks=with_chunks):
                    shutil.rmtree(self.temp)
                    self.temp = self.book_dir("book_temp")
                    if with_chunks:
                        self.finished_split()
                        (self.temp / "manifest.json").unlink()
                    make(self.temp / "manifest.json")
                    # Opening a FIFO waits for the other end; the alarm makes that a failure, not a hang.
                    previous = signal.signal(signal.SIGALRM, hung)
                    signal.alarm(10)
                    try:
                        self.assert_refused("manifest.json is not a regular file")
                    finally:
                        signal.alarm(0)
                        signal.signal(signal.SIGALRM, previous)

    def test_a_manifest_that_lists_no_chunks_stops_the_run(self):
        for with_chunks in (True, False):
            with self.subTest(chunks=with_chunks):
                shutil.rmtree(self.temp)
                self.temp = self.book_dir("book_temp")
                if with_chunks:
                    self.finished_split()
                (self.temp / "manifest.json").write_text('{"chunk_count": 0, "source_hash": "", "chunks": []}')
                self.assert_refused("manifest.json lists no chunks")

    def test_an_unreadable_or_deleted_manifest_is_written_again_for_chunks_of_the_split(self):
        for damage in ("remove", "truncate"):
            with self.subTest(damage):
                shutil.rmtree(self.temp)
                self.temp = self.book_dir("book_temp")
                self.finished_split()
                manifest = self.temp / "manifest.json"
                if damage == "remove":
                    manifest.unlink()
                else:
                    manifest.write_bytes(manifest.read_bytes()[:50])
                kept = {k: v for k, v in self.snapshot(self.temp).items() if k != "manifest.json"}

                count, _ = self.run_split(self.temp)

                self.assert_complete(self.temp, count)
                self.assertEqual({k: v for k, v in self.snapshot(self.temp).items() if k != "manifest.json"}, kept)

    def test_a_chunk_is_never_written_over_a_file_that_is_there(self):
        import errno
        target = self.temp / "chunk0001.md"
        for link_error in (None, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS):
            with self.subTest(link_error=link_error and errno.errorcode[link_error]):
                no_links = mock.patch.object(os, "link", side_effect=OSError(link_error, "no hard links"))
                target.write_text("someone else's\n", encoding="utf-8")
                with no_links if link_error else contextlib.nullcontext():
                    with self.assertRaises(FileExistsError):
                        convert._write_chunk(str(target), b"chunk\n")
                self.assertEqual(target.read_text(encoding="utf-8"), "someone else's\n")
                self.assertEqual(sorted(p.name for p in self.temp.iterdir()), ["chunk0001.md", "input.md"])
                target.unlink()
                with no_links if link_error else contextlib.nullcontext():
                    convert._write_chunk(str(target), b"chunk\n")
                self.assertEqual(target.read_bytes(), b"chunk\n")
                self.assertEqual(sorted(p.name for p in self.temp.iterdir()), ["chunk0001.md", "input.md"])
                target.unlink()
        with mock.patch.object(os, "link", side_effect=OSError(errno.EIO, "I/O error")):
            with self.assertRaises(OSError) as cm:
                convert._write_chunk(str(target), b"chunk\n")
        self.assertEqual(cm.exception.errno, errno.EIO)
        self.assertEqual(sorted(p.name for p in self.temp.iterdir()), ["input.md"])

    def test_save_manifest_keeps_the_error_that_stopped_it(self):
        import manifest
        real_replace = os.replace

        def replace_then_stop(src, dst):
            real_replace(src, dst)
            raise KeyboardInterrupt

        with mock.patch.object(os, "replace", replace_then_stop):
            with self.assertRaises(KeyboardInterrupt):
                manifest.save_manifest(str(self.temp), {"chunk_count": 0, "source_hash": "", "chunks": []})
        self.assertEqual(sorted(p.name for p in self.temp.iterdir()), ["input.md", "manifest.json"])

if __name__ == "__main__":
    unittest.main()
