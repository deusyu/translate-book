import contextlib
import io
import os
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


def _pandoc_available():
    try:
        import pypandoc

        pypandoc.get_pandoc_version()
        return True
    except Exception:
        return False


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


@unittest.skipUnless(_pandoc_available(), "pypandoc and pandoc are required")
class ConvertHtmlToMarkdownPandocTests(unittest.TestCase):
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
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["book.epub", "book.htmlz"])


if __name__ == "__main__":
    unittest.main()
