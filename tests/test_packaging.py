import process_guard  # noqa: F401  first, so it guards every process the tests below start
import ast
import fnmatch
import functools
import html
import json
import os
import re
import string
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts"
TESTS_DIR = Path(__file__).resolve().parent
SKILL_PATH = REPO_ROOT / "SKILL.md"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import merge_and_build  # noqa: E402
from process_guard import run_skill_command  # noqa: E402
from test_platform import runnable_scripts  # noqa: E402


def skill_text():
    """SKILL.md as the tests here read it; SkillCommandRunGuardTest points SKILL_PATH at a changed copy."""
    return SKILL_PATH.read_text(encoding="utf-8")


class MarketplaceManifestTest(unittest.TestCase):
    def setUp(self):
        path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
        self.manifest = json.loads(path.read_text(encoding="utf-8"))

    def test_single_plugin_points_at_skill_root(self):
        plugins = self.manifest["plugins"]
        self.assertEqual(len(plugins), 1)
        plugin = plugins[0]
        self.assertEqual(plugin["name"], "translate-book")
        source = (REPO_ROOT / plugin["source"]).resolve()
        self.assertTrue((source / "SKILL.md").is_file())
        for skill in plugin.get("skills", []):
            self.assertTrue((source / skill / "SKILL.md").is_file())

    def test_no_version_field(self):
        self.assertNotIn("version", self.manifest)
        for plugin in self.manifest["plugins"]:
            self.assertNotIn("version", plugin)


class ClawHubIgnoreTest(unittest.TestCase):
    """The package published to ClawHub is SKILL.md, LICENSE and scripts/.

    ClawHub audits every file it ships, so `.clawhubignore` keeps out the docs,
    assets and tests that only serve the GitHub repository.
    """

    REQUIRED_RULES = {"/README*.md", "/AGENTS.md", "/CLAUDE.md", "/assets/", "/tests/"}

    def setUp(self):
        text = (REPO_ROOT / ".clawhubignore").read_text(encoding="utf-8")
        self.rules = [line.strip() for line in text.splitlines()
                      if line.strip() and not line.startswith("#")]

    def test_keeps_docs_assets_and_tests_out(self):
        self.assertEqual(self.REQUIRED_RULES - set(self.rules), set())

    def test_no_rule_excludes_skill_md_license_or_scripts(self):
        skill_files = ["SKILL.md", "LICENSE"] + sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in (REPO_ROOT / "scripts").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        self.assertIn("scripts/convert.py", skill_files)
        hits = [(rule, path) for rule in self.rules if not rule.startswith("!")
                for path in skill_files if self.may_exclude(rule, path)]
        self.assertEqual(hits, [])

    @staticmethod
    def may_exclude(rule, path):
        """Loose gitignore match that errs towards a hit: anchors and trailing `/`
        are dropped, every level of the path is tried, and `*` may cross `/`."""
        pattern = rule.strip("/")
        variants = {pattern, pattern.replace("/**/", "/"), pattern.removeprefix("**/")}
        parts = path.split("/")
        names = {"/".join(parts[:end]) for end in range(1, len(parts) + 1)} | set(parts)
        return any(fnmatch.fnmatchcase(name, variant) for name in names for variant in variants)


class SkillDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.text = skill_text()

    def test_frontmatter_fields_are_single_line(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "---")
        end = lines.index("---", 1)
        for line in lines[1:end]:
            self.assertRegex(line, r"^[a-z][a-z-]*: \S")

    def test_defines_base_dir(self):
        self.assertIn(
            "`{baseDir}` below is the directory containing this SKILL.md.",
            self.text,
        )


# The line forms of SKILL.md; markdown_parts says what each may be.
FRONTMATTER_LINE = re.compile(r"[a-z][a-z-]*: [^`<]*\S")
FENCE_LIKE = re.compile(r" *(?:```|~~~)")
FENCE_OPEN = re.compile(r"```(?P<info>bash|json)")
HEADING = re.compile(r"#{1,6} \S")
LIST_ITEM = re.compile(r"(?P<marker>-|[0-9]{1,2}\.) (?=\S)")
# Text that starts a block other than a paragraph in CommonMark, GFM or Pandoc:
# headings, quotes, HTML, table rows and line blocks, Pandoc's definition lists,
# divs, grid tables and title blocks, fences, thematic breaks, setext
# underlines, list items of every Pandoc kind, link and footnote definitions, TeX.
BLOCK_START = re.compile(
    r"[#><:~+=%|]|```|[-*_](?: *[-*_])* *$|[-*](?: |$)"
    r"|\(?(?:[0-9]+|[A-Za-z]|[ivxlcdm]+|[IVXLCDM]+|#|@[\w-]*)[.)](?: |$)"
    r"|\[[^\]]*\]:|\\[A-Za-z]"
)
# A table's delimiter row; the line before it is the header row.
DELIMITER_ROW = re.compile(r"(?=.*\|)\|? *:?-+:? *(?:\| *:?-+:? *)*\|? *")
CODE_ELEMENT = re.compile(r"<\s*/?\s*(?:pre|code|samp|kbd|tt|xmp|listing|plaintext)\b", re.IGNORECASE)
# A renderer may read these as spacing or as a line end (CommonMark ends a line
# at a lone carriage return) where a reading split at "\n" sees neither.
UNSEEN_CHARACTER = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]")
# Outside inline code, prose holds none of these. Each starts something in
# which CommonMark, GFM or Pandoc may read a backtick as text, so that the
# backticks after it pair differently, on its line or a later one (Pandoc reads
# a closing tag up to the next >, across empty lines): raw HTML and autolinks,
# link targets, reference links and footnotes, URLs, TeX math and commands,
# and attributes. A placeholder in braces may stay, since it holds no backtick.
LITERAL_BACKTICK_START = re.compile(
    r"<|\$|\\|\]\(|\]\[|\[\^|(?:https?|ftp)://|www\.|mailto:|xmpp:"
    r"|[\])`]\{|\{(?![A-Za-z_][A-Za-z0-9_ ]*\})"
)
ASCII_PUNCTUATION = frozenset(string.punctuation)
SCRIPT_NAMES = frozenset(path.name for path in SCRIPT_DIR.glob("*.py"))
# Files in <temp_dir> that a command may give a script on standard input.
STDIN_FILES = frozenset({"merge_decisions.json", "record_ids.txt"})
QUOTED_ARGUMENTS = frozenset({'"<temp_dir>"', '"chunk<NNNN>.md"'})
LITERAL_WORD = re.compile(r"(--?)?[a-z]+(-[a-z]+)*(\.json)?")
GREP_COMMAND = 'grep -F -f "<temp_dir>/search.txt" "<temp_dir>"/output_chunk*.md'
# Inline code may name shell syntax only through these exact spans.
SHELL_SYNTAX_SPANS = frozenset({"$(...)", "$VAR", "$...$", "$$...$$", "<", ">", "&"})
PROMPT_PLACEHOLDERS = frozenset({"TARGET_LANGUAGE", "TERM_TABLE", "NEIGHBOR_CONTEXT"})
HTML_TAGS = frozenset({"a", "img", "table"})
ANGLE_PLACEHOLDERS = frozenset({"temp_dir", "NNNN"}) | HTML_TAGS
BRACE_PLACEHOLDERS = frozenset({"baseDir"}) | PROMPT_PLACEHOLDERS
# What a shell reads as syntax; inline code holds none of it outside the exact
# spans listed here, HTML character references and the placeholders above.
SHELL_OPERATORS = frozenset("`$|;&<>()")
HTML_REFERENCE = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);")
# Inline code that is prose, though it has a space (which makes inline code a
# command) or shell syntax.
PROSE_SPANS = frozenset({
    "![alt](path)",
    "![图1：数据流](media/image-001.png)",
    "Input file:",
    "Temp directory:",
    "chunk_count < 5",
    "原文 | 别名 | 译文",
    "| ... |",
    '::: {.figure-text image="..."}',
    "![Figure 1: Data Flow](media/image-001.png)",
    '<img alt="..." />',
    '<a title="...">',
    "第11章 为什么……",
    "第一部分 政治领袖",
    "{variant, candidate_source, evidence}",
    "{entity_source, field, current, proposed, evidence}",
    "{proposed_source, currently_alias_of, promoted_variants: "
    "[{target_proposal, category, evidence, evidence_chunks}, ...]}",
    "{entity_source, current_target, current_category, proposed_variants: "
    "[{target_proposal, category, evidence, evidence_chunks}, ...]}",
    "{variant, alias_candidates: [{candidate_source, evidence, evidence_chunks}, ...], "
    "standalone_variants: [{target_proposal, category, evidence, evidence_chunks}, ...]}",
    "{source, variants: [{target_proposal, category, evidence, evidence_chunks}, ...]}",
    '{"auto_apply": [], "decisions": [], "consumed_chunk_ids": [...]}',
    '{"auto_apply": [...], "decisions": [...], "consumed_chunk_ids": [...]}',
    "Taighi → Taig",
    "Taig → Tai",
    "unmerged_meta_files > 0",
    "malformed_meta_files > 0",
    "meta_files_found < translated_chunks",
    "pandoc left N formula(s) as raw TeX",
})
# The json blocks of SKILL.md are examples of files the agent writes. A
# renderer shows them as code, so each is one object whose fields are listed
# here and whose strings pass the rules for inline code or are listed here.
JSON_FIELDS = frozenset({
    "input", "target_lang", "temp_root", "cover", "parser",
    "version", "terms", "id", "source", "target", "category", "aliases", "gender",
    "confidence", "frequency", "evidence_refs", "notes", "high_frequency_top_n",
    "applied_meta_hashes",
    "schema_version", "new_entities", "target_proposal", "evidence", "alias_hypotheses",
    "variant", "may_be_alias_of_source", "attribute_hypotheses", "entity_source",
    "attribute", "value", "used_term_sources", "conflicts", "field", "injected",
    "observed_better",
    "kind", "candidate_source", "choice",
})
JSON_STRINGS = frozenset({"<≤200-char quote from the chunk>", "<≤200-char quote>"})


def markdown_parts(text):
    """Split SKILL.md into fenced blocks and prose lines, and list what lies
    outside the subset of Markdown it is written in.

    CommonMark, GFM and Pandoc read the subset alike, so a line-by-line reading
    finds all the code they show. Lines end with \\n and hold no tab, other
    control character or line separator (UNSEEN_CHARACTER). Each line of
    SKILL.md is one of:
    - frontmatter at the top: `---`, `name: value` lines without a backtick, `---`;
    - an empty line;
    - a heading `# text` at the margin, after an empty line;
    - `---` alone at the margin between empty lines;
    - `> text` alone at the margin between empty lines;
    - a list item `- text` or `N. text`, one space after the marker;
    - a fence: ```bash or ```json, lines indented at least as far, and ``` at
      the fence's own indentation; no line inside looks like a fence;
    - a table row, or a line of a paragraph.
    No line is indented past the margin, or past the text of the list item it is
    in, so none can start an indented code block. A list at the margin follows
    an empty line: Pandoc reads a list that follows text as more of that text,
    and then the list's deeper lines as code. A nested list that follows text
    starts with `-` or `1.`, as CommonMark requires. The text of a paragraph
    line, a list item or a quote starts right after its marker's space and
    starts no other block in any of the three (BLOCK_START). Inline code closes
    on its line, and no prose line holds anything outside inline code in which
    a renderer may read a backtick, on that line or a later one, as text
    (LITERAL_BACKTICK_START), so each renderer pairs the backticks the same
    way. HTML that shows code fails anywhere.
    Returns (blocks, prose, problems): blocks as (info word, first line number,
    lines), prose as (line number, line, whether it may be a table row) and
    problems as (line number, line, reason).
    """
    lines = text.split("\n")
    blocks, prose, problems = [], [], []

    def flag(number, reason):
        problems.append((number, lines[number - 1], reason))

    for number, line in enumerate(lines, 1):
        unseen = UNSEEN_CHARACTER.search(line)
        if unseen:
            flag(number, f"{unseen.group()!r}: a tab, control character or line separator; "
                         "indent with spaces and end lines with \\n")
    i = 0
    if lines[0] == "---":
        if "---" not in lines[1:]:
            flag(1, "frontmatter that does not close")
            return blocks, prose, problems
        i = lines.index("---", 1) + 1
        for number in range(2, i):
            if not FRONTMATTER_LINE.fullmatch(lines[number - 1]):
                flag(number, "a frontmatter line is `name: value` on one line, without a backtick")
    items = []  # (marker indentation, text column, ordered) of each open list item, innermost last
    previous, in_table = "empty", False
    while i < len(lines):
        number, line = i + 1, lines[i]
        i += 1
        if not line.strip():
            if line:
                flag(number, "a line of spaces only; leave it empty")
            previous, in_table = "empty", False
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line[indent:]
        closed = []
        while items and indent < items[-1][1]:
            closed.append(items.pop())
        if indent != (items[-1][1] if items else 0):
            flag(number, "indented past the margin, or past the text of its list item, "
                         "where a renderer may read it as an indented code block")
        alone = previous == "empty" and (i == len(lines) or not lines[i].strip())
        if FENCE_LIKE.match(content):
            opening = FENCE_OPEN.fullmatch(content)
            if not opening:
                flag(number, "a fence opens with ```bash or ```json")
            margin = " " * indent
            first, body = i + 1, []
            while i < len(lines) and lines[i] != margin + "```":
                if lines[i].strip() and not lines[i].startswith(margin):
                    flag(i + 1, "a line in a fence is indented at least as far as the fence")
                elif FENCE_LIKE.match(lines[i]):
                    flag(i + 1, "a line in a fence that a renderer may take for its end")
                body.append(lines[i].strip())
                i += 1
            if i == len(lines):
                flag(number, "a fence closes with ``` on its own line, at its own indentation")
            i += 1
            blocks.append((opening["info"] if opening else "", first, body))
            previous, in_table = "fence", False
            continue
        item = LIST_ITEM.match(content)
        if HEADING.match(content):
            if indent or previous != "empty":
                flag(number, "a heading is at the margin after an empty line")
            kind = "heading"
        elif content == "---":
            if indent or not alone:
                flag(number, "--- is alone at the margin between empty lines")
            kind = "break"
        elif content.startswith(">"):
            if indent or not alone or not re.match(r"> \S", content) or BLOCK_START.match(content[2:]):
                flag(number, "a blockquote is one line of text, `> text` alone at the margin "
                             "between empty lines")
            kind = "quote"
        elif item:
            ordered = item["marker"] != "-"
            sibling = closed and closed[-1][0] == indent and closed[-1][2] == ordered
            if not sibling and not indent and previous != "empty":
                flag(number, "a list at the margin follows an empty line: Pandoc reads a list that "
                             "follows text as more of that text, and its deeper lines as code")
            elif not sibling and previous != "empty" and item["marker"] not in ("-", "1."):
                flag(number, "a nested list that follows text starts with - or 1.")
            if BLOCK_START.match(content[item.end():]):
                flag(number, "the text of a list item starts another block")
            items.append((indent, indent + len(item["marker"]) + 1, ordered))
            kind = "item"
        elif content.startswith("|") or DELIMITER_ROW.fullmatch(content):
            kind = "row"
        else:
            if BLOCK_START.match(content):
                flag(number, "a line that starts a block outside the subset of Markdown SKILL.md keeps to")
            kind = "text"
        if DELIMITER_ROW.fullmatch(content):
            in_table = True
            if prose and prose[-1][0] == number - 1:
                prose[-1] = prose[-1][:2] + (True,)
        table_row = in_table or kind == "row"
        prose.append((number, line, table_row))
        problem = inline_problem(line, table_row)
        if problem:
            flag(number, problem)
        previous = kind
    return blocks, prose, problems


def code_spans(line):
    """The inline code spans of one line, read as CommonMark reads them; whether
    a backtick on the line opens a span that does not close on it; and the text
    outside the spans, with a ` in place of each span."""
    spans, unclosed, outside, pos = [], False, [], 0
    while pos < len(line):
        if line[pos] == "\\" and line[pos + 1:pos + 2] in ASCII_PUNCTUATION:
            outside.append(line[pos:pos + 2])
            pos += 2
        elif line[pos] != "`":
            outside.append(line[pos])
            pos += 1
        else:
            ticks = re.match(r"`+", line[pos:]).group()
            closing = re.compile(rf"(?<!`){ticks}(?!`)").search(line, pos + len(ticks))
            if not closing:
                unclosed = True
                outside.append(ticks)
                pos += len(ticks)
                continue
            span = line[pos + len(ticks):closing.start()]
            if len(span) > 2 and span[0] == span[-1] == " " and span.strip():
                span = span[1:-1]
            spans.append(span)
            outside.append("`")
            pos = closing.end()
    return spans, unclosed, "".join(outside)


def _cells(line):
    return re.split(r"(?<!\\)\|", line)


def inline_problem(line, table_row):
    """Why a renderer may show code on a prose line that line_spans does not
    find, or None. A table row is read whole and cell by cell."""
    if CODE_ELEMENT.search(line):
        return "an HTML element shown as code"
    _, unclosed, outside = code_spans(line)
    if unclosed:
        return "a backtick that opens inline code that does not close on its line"
    cells = [code_spans(cell)[2] for cell in _cells(line)] if table_row else []
    if any(LITERAL_BACKTICK_START.search(text) for text in [outside] + cells):
        return ("outside inline code, prose holds no <, $, \\, link target, URL or attribute "
                "braces, in which a renderer may read a backtick on this line or a later one as text")
    return None


def line_spans(line, table_row):
    """The spans a renderer may show as inline code on a line: reading it as a
    paragraph line and, if it may be a table row, cell by cell."""
    spans = code_spans(line)[0]
    if table_row:
        for cell in _cells(line):
            spans += code_spans(cell)[0]
    return list(dict.fromkeys(spans))


def command_problem(command):
    """Why a command is not one of the shapes SKILL.md may use, or None."""
    if command == GREP_COMMAND:
        return None
    words = command.split()
    if words[0] != "python3":
        return "not an allowed command shape"
    script = re.fullmatch(r'"\{baseDir\}/scripts/([a-z_]+\.py)"', words[1]) if len(words) > 1 else None
    if not script or script[1] not in SCRIPT_NAMES:
        return 'the script must be a quoted "{baseDir}/scripts/<name>.py" that exists'
    args = words[2:]
    if "<" in args:
        if args.index("<") != len(args) - 2:
            return "a < redirect must come last, once"
        source = re.fullmatch(r'"<temp_dir>/([^"/]+)"', args[-1])
        if not source or source[1] not in STDIN_FILES:
            return f"standard input may come only from {sorted(STDIN_FILES)} in <temp_dir>"
        args = args[:-2]
    for arg in args:
        if arg not in QUOTED_ARGUMENTS and not LITERAL_WORD.fullmatch(arg):
            return f"{arg!r} is neither a literal word nor an allowed quoted placeholder"
    return None


def mention_problem(span):
    """Why inline code that is not a command still may not stand in SKILL.md, or None.

    A span that a renderer pairs differently (because raw HTML or a link target
    holds a backtick) runs past the tag's > or the link's ) and fails here.
    """
    if span in SHELL_SYNTAX_SPANS or span in PROSE_SPANS:
        return None
    for name in re.findall(r"<([^<>]*)>", span):
        if name not in ANGLE_PLACEHOLDERS:
            return f"placeholder <{name}>: only <temp_dir> and <NNNN> are allowed"
    for name in re.findall(r"\{([^{}]*)\}", span):
        if name not in BRACE_PLACEHOLDERS:
            return f"placeholder {{{name}}}: only {{baseDir}} is allowed"
    rest = re.sub("|".join(f"<{name}>" for name in sorted(ANGLE_PLACEHOLDERS)), "", span)
    operators = sorted(set(HTML_REFERENCE.sub("", rest)) & SHELL_OPERATORS)
    if operators:
        return f"shell syntax {''.join(operators)} in inline code"
    return None


def _unique_fields(pairs):
    fields = [field for field, _ in pairs]
    if len(set(fields)) != len(fields):
        raise ValueError(f"a field appears twice in {fields}")
    return dict(pairs)


def _not_json(name):
    raise ValueError(f"{name} is not JSON")


def json_values(data):
    """(fields, strings) of a parsed JSON value, at every depth."""
    fields, strings, pending = [], [], [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            fields += value
            pending += value.values()
        elif isinstance(value, list):
            pending += value
        elif isinstance(value, str):
            strings.append(value)
    return fields, strings


def json_block_problem(lines):
    """Why a json block is not an example SKILL.md may show, or None."""
    text = "\n".join(lines)
    if "\\" in text:
        return "a backslash in a json block, which may stand for any character"
    try:
        data = json.loads(text, object_pairs_hook=_unique_fields, parse_constant=_not_json)
    except ValueError as error:
        return f"a json block that does not parse ({error})"
    if not isinstance(data, dict):
        return "a json block that is not one object"
    fields, strings = json_values(data)
    for field in fields:
        if field not in JSON_FIELDS:
            return f"field {field!r} in a json block is not in JSON_FIELDS"
    for value in strings:
        problem = None if value in JSON_STRINGS else mention_problem(value)
        if problem:
            return f"{value!r} in a json block: {problem}"
    return None


def skill_parts(text):
    """(commands, mentions, problems) for SKILL.md.

    Commands are the lines of fenced blocks, except json blocks that are
    examples SKILL.md may show, and inline code with a space that is not a
    listed prose span; mentions are the other inline code; problems are those
    of markdown_parts and of json blocks.
    """
    blocks, prose, problems = markdown_parts(text)
    commands, mentions = [], []
    for info, first, body in blocks:
        if info == "json":
            problem = json_block_problem(body)
            if problem is None:
                continue
            problems.append((first - 1, text.split("\n")[first - 2], problem + "; its lines are read as commands"))
        commands += [(first + offset, line) for offset, line in enumerate(body) if line]
    for number, line, table_row in prose:
        for span in line_spans(line, table_row):
            if len(span.split()) > 1 and span not in PROSE_SPANS:
                commands.append((number, span))
            else:
                mentions.append((number, span))
    return commands, mentions, problems


def skill_commands(text):
    """(commands, mentions) of SKILL.md, as (line number, text)."""
    commands, mentions, _ = skill_parts(text)
    return commands, mentions


def skill_command_problems(text):
    """(line, text, reason) for each command, inline code span or piece of
    Markdown that CI rejects."""
    commands, mentions, problems = skill_parts(text)
    problems = problems + [(line, command, command_problem(command)) for line, command in commands]
    problems += [(line, span, mention_problem(span)) for line, span in mentions]
    return [problem for problem in problems if problem[2]]


@functools.lru_cache(maxsize=4)
def _skill_check(text):
    return tuple(skill_command_problems(text)), frozenset(command for _, command in skill_parts(text)[0])


def _checked_commands(text):
    """The commands of a SKILL.md text, if it passes skill_command_problems."""
    problems, commands = _skill_check(text)
    if problems:
        raise AssertionError(f"SKILL.md fails the shape check, so none of its commands is run: {problems[:3]}")
    return commands


class SkillCommandShapeTest(unittest.TestCase):
    """Every command in SKILL.md has one of a few fixed shapes.

    A command holds fixed text and the placeholders {baseDir} (quoted),
    <temp_dir> and <NNNN> only; other values reach the scripts through files.
    Commands are what a renderer shows as code: fenced blocks and inline code.
    Anything else fails: a new command shape needs a change to this test, and
    Markdown outside the subset markdown_parts reads fails too.
    """

    STEP_2 = 'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json'

    def setUp(self):
        self.text = skill_text()

    def test_skill_md_passes(self):
        self.assertEqual(skill_command_problems(self.text), [], (
            "Inline code that is prose, such as an example message, belongs in PROSE_SPANS; "
            "a fenced block that holds no command is a json example of listed fields."))

    def test_every_prose_span_is_still_in_skill_md(self):
        _, mentions = skill_commands(self.text)
        self.assertEqual(PROSE_SPANS - {span for _, span in mentions}, set())

    def test_every_json_field_and_string_is_still_in_skill_md(self):
        fields, strings = set(), set()
        for info, _, body in markdown_parts(self.text)[0]:
            if info == "json":
                found = json_values(json.loads("\n".join(body)))
                fields.update(found[0])
                strings.update(found[1])
        self.assertEqual(JSON_FIELDS - fields, set())
        self.assertEqual(JSON_STRINGS - strings, set())

    def test_finds_the_commands_agents_run(self):
        commands = [command for _, command in skill_commands(self.text)[0]]
        self.assertIn(self.STEP_2, commands)
        self.assertIn(GREP_COMMAND, commands)
        self.assertIn('python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>"', commands)
        self.assertGreaterEqual(len(commands), 15)

    @process_guard.needs("pandoc")
    def test_renderers_show_only_the_code_this_check_reads(self):
        # SKILL.md as CommonMark, GFM and Pandoc render it: each code block is
        # a fenced block read here, and each piece of inline code a span read here.
        blocks, prose, _ = markdown_parts(self.text)
        fenced = sorted(tuple(line for line in body if line) for _, _, body in blocks)
        spans = {span for _, line, table_row in prose for span in line_spans(line, table_row)}
        for reader, page in pandoc_pages(self.text).items():
            with self.subTest(reader=reader):
                shown, inline = rendered_code(page)
                self.assertEqual(shown, fenced)
                self.assertEqual(inline - spans, set())

    def assert_rejected(self, mutated, needle):
        problems = skill_command_problems(mutated)
        self.assertTrue(any(needle in text for _, text, _ in problems), problems)

    def test_rejects_changed_commands(self):
        mutants = [
            'python3 "{baseDir}/scripts/convert.py" "$(cat translate-book-request.json)"',
            'python3 "{baseDir}/scripts/convert.py" --request `cat name.txt`',
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json | bash',
            "python3 {baseDir}/scripts/convert.py --request translate-book-request.json",
            "python3 '{baseDir}/scripts/convert.py' --request translate-book-request.json",
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json; rm -rf ~',
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json && true',
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json &',
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json > "$HOME/.bashrc"',
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json 2> "<temp_dir>/log"',
            'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" < /etc/passwd',
            'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" < "<temp_dir>/../../../etc/passwd"',
            'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" < "<temp_dir>/title.txt"',
            'python3 "{baseDir}/scripts/run_state.py" record < "<temp_dir>/record_ids.txt" "<temp_dir>"',
            'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" chunk0001 chunk0002 ...',
            'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" chunk0001',
            'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" chunk<NNNN>',
            'python3 "{baseDir}/scripts/convert.py" "<file_path>" --olang "<target_lang>"',
            "python3 \"{baseDir}/scripts/run_state.py\" plan '<temp_dir>'",
            'python3 "{baseDir}/scripts/run_state.py" plan <temp_dir>',
            'python3 "{baseDir}/scripts/run_state.py" plan "{filename}_temp"',
            'python3 "{baseDir}/scripts/run_state.py" plan "$HOME"',
            'python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>" --cover "<epub_cover>"',
            'python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>" --title "$(cat "<temp_dir>/title.txt")"',
            'python3 "{baseDir}/scripts/missing.py" --request translate-book-request.json',
            'python3 -c "import os"',
            'python3 "{baseDir}/scripts/convert.py" --request "\\$x"',
            'bash -c "$(cat "<temp_dir>/x")"',
            'sh -c "python3 x"',
            'eval "$(cat "<temp_dir>/x")"',
            "rm -rf <temp_dir>",
            'grep -F -f "<temp_dir>/search.txt" "<temp_dir>"/output_chunk*.md | sh',
            'grep -F "$(cat search.txt)" "<temp_dir>"/output_chunk*.md',
        ]
        for mutant in mutants:
            with self.subTest(mutant=mutant):
                self.assertIn(self.STEP_2 + "\n", self.text)
                self.assert_rejected(self.text.replace(self.STEP_2 + "\n", mutant + "\n", 1), mutant)

    def test_rejects_commands_in_any_fence(self):
        fences = [
            "```sh\necho hi\n```",
            "```\nGlob: <temp_dir>/chunk*.md\n```",
            "~~~bash\npython3 {baseDir}/scripts/convert.py x\n~~~",
            "   ```text\n   cat \"<temp_dir>/title.txt\"\n   ```",
            "````\npython3 \"{baseDir}/scripts/convert.py\" `x`\n````",
            "```bash\npython3 \"{baseDir}/scripts/convert.py\" --request $x\n",
        ]
        for fence in fences:
            body = fence.split("\n")[1].strip()
            with self.subTest(fence=fence):
                self.assert_rejected(self.text + "\n" + fence + "\n", body)

    def test_json_blocks_hold_listed_fields_and_plain_strings(self):
        # A renderer shows a json block as code, so parsing as JSON is not
        # enough: what it holds is checked like inline code.
        examples = ['{"input": "/books/alice.epub", "target_lang": "zh", "cover": null}',
                    '{"terms": [{"id": "Tai", "evidence": "<≤200-char quote>"}], "version": 2}']
        for data in examples:
            with self.subTest(data=data):
                self.assertEqual(skill_command_problems(self.text + "\n```json\n" + data + "\n```\n"), [])
        refused = [
            '{"input": "/books/$(touch x).epub"}',
            '{"input": "$HOME"}',
            '"$HOME"',
            '"rm -rf ~"',
            '["rm -rf ~"]',
            '{"input": ["`id`"]}',
            '{"input": "<file_path>"}',
            '{"command": "ls"}',
            '{"input": "a", "input": "b"}',
            '{"input": "\\u0024HOME"}',
            '{"version": NaN}',
            'rm -rf "$HOME"',
        ]
        for data in refused:
            with self.subTest(data=data):
                self.assert_rejected(self.text + "\n```json\n" + data + "\n```\n", data)
        self.assert_rejected(self.text + '\n```bash\n{"input": "/books/alice.epub"}\n```\n', '{"input"')

    def test_rejects_code_in_every_markdown_form(self):
        # A renderer shows each of these as code, or may: fences in a
        # blockquote, after tabs or a list marker, indented past their list
        # item or not closing where they opened; indented code blocks after
        # any kind of line; HTML; Pandoc's lists, definition lists, divs, grid
        # tables and TeX; and lists that Pandoc reads as paragraph text.
        forms = [
            ('> ~~~bash\n> rm -rf "$HOME"\n> ~~~', "> ~~~bash"),
            ('> ```bash\n> rm -rf "$HOME"\n> ```', "> ```bash"),
            ('\t~~~bash\n\trm -rf "$HOME"\n\t~~~', "\t~~~bash"),
            ('- ```bash\n  rm -rf "$HOME"\n  ```', "- ```bash"),
            ('1. Step\n\n       ```bash\n       rm -rf "$HOME"\n       ```', "       ```bash"),
            ('1. Step\n   ```bash\n   python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"\n'
             '```\nrm -rf "$HOME"\n```', "```"),
            ('1. Step\n   ```bash\n   python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"\n'
             '    ```\n   rm -rf "$HOME"\n   ```', "    ```"),
            ('Text.\n\n    rm -rf "$HOME"', '    rm -rf "$HOME"'),
            ('- Item.\n\n      rm -rf "$HOME"', '      rm -rf "$HOME"'),
            ('-     rm -rf "$HOME"', '-     rm -rf "$HOME"'),
            ('> Quote.\n>\n>     rm -rf "$HOME"', '>     rm -rf "$HOME"'),
            ('> - ***\n        rm -rf "$HOME"', '        rm -rf "$HOME"'),
            ('- Item.\n     ***\n        rm -rf "$HOME"', '        rm -rf "$HOME"'),
            ('Title\n==\n    rm -rf "$HOME"', '    rm -rf "$HOME"'),
            ('Title\n=\n    rm -rf "$HOME"', '    rm -rf "$HOME"'),
            ('Title\n--\n    rm -rf "$HOME"', '    rm -rf "$HOME"'),
            ('Title\n-\n    rm -rf "$HOME"', '    rm -rf "$HOME"'),
            ('Title\n===\n    rm -rf "$HOME"', "==="),
            ('Text.\n\n***\n\n    rm -rf "$HOME"', "***"),
            ('Intro:\n- Item.\n  - Nested.\n\n    rm -rf "$HOME"', "- Item."),
            ('Rules:\n1. Keep.\n   - Sub.\n\n     rm -rf "$HOME"', "1. Keep."),
            ('* Item.\n\n      rm -rf "$HOME"', "* Item."),
            ("+ Item.", "+ Item."),
            ("1) Item.", "1) Item."),
            ('a. Item.\n\n       rm -rf "$HOME"', "a. Item."),
            ('(@) Item.\n\n        rm -rf "$HOME"', "(@) Item."),
            ('Term\n\n:   ~~~bash\n    rm -rf "$HOME"\n    ~~~', ":   ~~~bash"),
            ('::: note\n```bash\nrm -rf "$HOME"\n```\n:::', "::: note"),
            ("+---+\n| `x` |\n+---+", "+---+"),
            ('[x]: /url\n    rm -rf "$HOME"', "[x]: /url"),
            ('\\begin{verbatim}\nrm -rf "$HOME"\n\\end{verbatim}', "\\begin{verbatim}"),
            ('<!--\n```bash\n-->\nrm -rf "$HOME"\n```', "<!--"),
            ('<pre>\nrm -rf "$HOME"\n</pre>', "<pre>"),
            ('Run <code>rm -rf "$HOME"</code> now.', "<code>"),
            ('>     rm -rf "$HOME"', '>     rm -rf "$HOME"'),
            ('sh</etc\n\nSee `>` rm -rf ~ `y`.', "sh</etc"),
            ('Text.\r\r    rm -rf "$HOME"', "Text."),
            ('Text.\r\n\r\n    rm -rf "$HOME"', "Text."),
            ('Text.\x85\x85    rm -rf "$HOME"', "Text."),
            ('Text.\u2028\u2028    rm -rf "$HOME"', "Text."),
            ('Text.\x0c    rm -rf "$HOME"', "Text."),
            ('```bash\npython3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"\r```\rrm -rf "$HOME"\n```',
             'python3 "{baseDir}'),
        ]
        for form, needle in forms:
            with self.subTest(form=form):
                self.assert_rejected(self.text + "\n" + form + "\n", needle)

    def test_rejects_shell_syntax_in_inline_code(self):
        # Inline code of any backtick count holds no command substitution,
        # redirect, pipe, list or subshell, with or without spaces.
        lines = [
            ("See `` `id` `` here.", "`id`"),
            ("See ``` `id` ``` here.", "`id`"),
            ("See `sh</etc/passwd` here.", "sh</etc/passwd"),
            ("See `sh < /etc/passwd` here.", "sh < /etc/passwd"),
            ("See `id|sh` here.", "id|sh"),
            ("See `id | sh` here.", "id | sh"),
            ("See `a;id` here.", "a;id"),
            ("See `a&&id` here.", "a&&id"),
            ("See `(id)` here.", "(id)"),
            ("See `$(id)` here.", "$(id)"),
            ("See `a>b` here.", "a>b"),
        ]
        for line, needle in lines:
            with self.subTest(line=line):
                self.assert_rejected(self.text + "\n" + line + "\n", needle)

    def test_rejects_inline_code_a_renderer_pairs_differently(self):
        # Raw HTML, a link target, a URL, a backslash, a table cell, another
        # line, TeX math or a TeX command, or attributes can take a backtick,
        # so a renderer pairs the rest differently.
        lines = [
            ('See <a href="`">`rm -rf ~` `.', '">'),
            ("See [x](a`)`rm -rf ~` `.", ")"),
            ('See http://x`python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json`'
             " rm -rf ~ `y`.", "http://x`"),
            ("\\``rm -rf ~` `", "rm -rf ~"),
            ("| a | b | c |\n|---|---|---|\n| `原文 | 别名 | 译文` rm -rf ~ `x` |", "rm -rf ~"),
            ("A `\n- `rm -rf ~` `", "A `"),
            ("See $a`$ `rm -rf ~` b`.", "$a`$"),
            ("See \\textbf{`} `rm -rf ~` `.", "\\textbf"),
            ('See [x]{.c title="`"} `rm -rf ~` `.', "]{.c"),
            ("See `x`{.c title='`'} `rm -rf ~` `.", "`{.c"),
            ("See [x][a`] `rm -rf ~` `.", "][a`]"),
            ("See [^a`] `rm -rf ~` `.", "[^a`]"),
            ("See <http://a`b> `rm -rf ~` `.", "<http://a`b>"),
            ("See www.a`b `rm -rf ~` `.", "www.a`b"),
        ]
        for line, needle in lines:
            with self.subTest(line=line):
                self.assert_rejected(self.text + "\n" + line + "\n", needle)

    def test_allows_entities_placeholders_and_listed_spans(self):
        line = ("See `&quot;`, `&#39;`, `<temp_dir>/title.txt`, `chunk<NNNN>.md`, "
                '`{TARGET_LANGUAGE}`, `output_chunk*.md`, `\\"`, `<`, `$(...)` and `![alt](path)`.')
        self.assertEqual(skill_command_problems(self.text + "\n" + line + "\n"), [])

    def test_rejects_inline_commands_and_placeholders(self):
        spans = [
            '--cover "<epub_cover>"',
            'python3 {baseDir}/scripts/convert.py "<file_path>"',
            'python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json | sh',
            'glossary.py print-terms-for-chunk "<temp_dir>" "chunk<NNNN>.md"',
            "rm -rf <temp_dir>",
            "$HOME",
            '"$(cat x)"',
            "<file_path>",
            "<export_name>.epub",
            "<path/to/book>",
            "{filename}_temp/",
            "{book_title}",
            "{file-path}",
        ]
        for span in spans:
            with self.subTest(span=span):
                self.assert_rejected(self.text + f"\nSee `{span}` here.\n", span)
                self.assert_rejected(self.text + f"\nSee `` {span} `` here.\n", span)


def _unescape(html_text):
    return html.unescape(re.sub(r"<[^>]+>", "", html_text))


def pandoc_pages(text):
    """The HTML that Pandoc's CommonMark, GFM and Pandoc Markdown readers make of text."""
    pages = {}
    for reader in ("commonmark", "gfm", "markdown"):
        result = process_guard.run_tool("pandoc", "--from", reader, "--to", "html", "--no-highlight", input=text)
        if result.returncode != 0:
            raise AssertionError(f"pandoc --from {reader} failed: {result.stderr}")
        pages[reader] = result.stdout
    return pages


PRE_BLOCK = re.compile(r"<pre[^>]*><code[^>]*>(.*?)</code></pre>", re.S)


def rendered_code(page):
    """The code blocks of an HTML page, as sorted tuples of non-empty stripped
    lines, and the set of its inline code."""
    blocks = sorted(tuple(line.strip() for line in _unescape(body).split("\n") if line.strip())
                    for body in PRE_BLOCK.findall(page))
    inline = {_unescape(code).replace("\n", " ")
              for code in re.findall(r"<code[^>]*>(.*?)</code>", PRE_BLOCK.sub("", page), re.S)}
    return blocks, inline


class SkillCommandArgumentTest(unittest.TestCase):
    """Each SKILL.md command names flags and subcommands its script accepts.

    The commands run against a temp dir that does not exist, so none gets past
    its argument parser into real work; an argparse usage error fails.
    """

    def test_scripts_accept_every_command(self):
        self.check_arguments(skill_commands(skill_text())[0])

    def test_every_script_it_runs_stops_on_native_windows(self):
        # test_platform.py runs each script with a command line as native Windows would.
        scripts = {name for _, command in skill_commands(skill_text())[0]
                   for name in re.findall(r'"\{baseDir\}/scripts/([a-z_]+\.py)"', command)}
        self.assertGreaterEqual(len(scripts), 6)
        self.assertLessEqual(scripts, set(runnable_scripts()))

    def check_arguments(self, commands):
        checked = 0
        with tempfile.TemporaryDirectory() as scratch:
            values = {"{baseDir}": str(REPO_ROOT), "<temp_dir>": os.path.join(scratch, "missing_temp"),
                      "<NNNN>": "0001"}
            for line, command in commands:
                if command == GREP_COMMAND:
                    continue
                result = run_skill_command(command, shell=None, cwd=scratch, values=values)
                with self.subTest(line=line, command=command):
                    self.assertNotRegex(result.stderr, r"(?m)^usage: ")
                checked += 1
            self.assertEqual(os.listdir(scratch), [])
        self.assertGreaterEqual(checked, 15)


HOSTILE_BASE_DIR = "My Skills (v1); it's & co | *#~{x}"
# Book file names whose temp dirs the runtime test uses, with the temp_root of each.
HOSTILE_BOOKS = [
    ("O'Brien \"tale\" $(touch pwned-name) `touch pwned-tick` $HOME.epub", None),
    ("-rf --no-preserve-root ;|&<>*?[x]{a,b}~#!.pdf", "work/sub-1.d"),
    ("《三体》（刘慈欣） \u202e\u200bévil\u0301 名.docx", "ABSOLUTE"),
]
RECORDER = """#!/bin/sh
call=$(mktemp "$RECORD_DIR/call.XXXXXX") || exit 97
printf '%s\\0' "$@" > "$call"
cat > "$call.stdin"
"""


def hostile_text(label):
    return (f"{label}: O'Brien's \"tale\" $(touch pwned-{label}) `touch pwned-tick-{label}` "
            f"$HOME \\ ; | & end\n")


class SkillCommandRuntimeTest(unittest.TestCase):
    """Each SKILL.md command, run by a shell the way an agent runs it.

    {baseDir} is replaced by a path full of shell syntax, <temp_dir> by the
    temp dir convert.py names after hostile book file names, and <NNNN> by
    digits. Stand-ins for python3 and grep record what reaches them: the
    arguments must be the command's own words and standard input the file it
    names, with nothing run or expanded on the way.
    """

    def setUp(self):
        import convert
        self.convert = convert
        self.commands = [command for _, command in skill_commands(skill_text())[0]]

    def command(self, needle, without=None):
        found = {command for command in self.commands
                 if needle in command and not (without and without in command)}
        self.assertEqual(len(found), 1, needle)
        return found.pop()

    def expected_call(self, command, base_dir, temp_dir):
        if command == GREP_COMMAND:
            return (["-F", "-f", f"{temp_dir}/search.txt",
                     f"{temp_dir}/output_chunk0001.md", f"{temp_dir}/output_chunk0002.md"], None)
        words = command.split()
        stdin_file = None
        if "<" in words:
            stdin_file = words[-1].strip('"').replace("<temp_dir>", temp_dir)
            words = words[:-2]
        argv = [word[1:-1] if word.startswith('"') else word for word in words[1:]]
        return ([arg.replace("{baseDir}", base_dir).replace("<temp_dir>", temp_dir)
                 .replace("<NNNN>", "0001") for arg in argv], stdin_file)

    def run_all_commands(self, shell):
        if not process_guard.installed(shell):
            if shell != "zsh":
                self.fail(f"{shell} is not installed")
            self.skipTest("zsh is not installed; CI installs it, so run this test there or locally before a release")
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            bin_dir, cwd, records = (scratch / name for name in ("bin", "cwd", "records"))
            for directory in (bin_dir, cwd, records):
                directory.mkdir()
            for name in ("python3", "grep"):
                (bin_dir / name).write_text(RECORDER, encoding="utf-8")
                (bin_dir / name).chmod(0o755)
            base_dir = str(scratch / HOSTILE_BASE_DIR / "translate-book")
            for book, root in HOSTILE_BOOKS:
                if root == "ABSOLUTE":
                    root = self.convert.validate_temp_root(str(scratch / "abs-root.d"))
                temp_dir = self.convert.build_temp_dir(book, root)
                self.assertTrue(all(ch.isalnum() or ch in "._-/" or unicodedata.category(ch)[0] == "M"
                                    for ch in temp_dir), temp_dir)
                self.assertNotRegex(temp_dir, r"(^|/)-")
                physical = cwd / temp_dir
                physical.mkdir(parents=True)
                files = ["merge_decisions.json", "record_ids.txt", "search.txt",
                         "output_chunk0001.md", "output_chunk0002.md"]
                for name in files:
                    (physical / name).write_text(hostile_text(name), encoding="utf-8")
                for command in self.commands:
                    with self.subTest(shell=shell, book=book, command=command):
                        result = run_skill_command(
                            command, shell=shell, cwd=cwd, timeout=60,
                            values={"{baseDir}": base_dir, "<temp_dir>": temp_dir, "<NNNN>": "0001"},
                            path_dirs=[bin_dir], env={"RECORD_DIR": str(records)})
                        self.assertEqual(result.returncode, 0, result.stderr)
                        calls = sorted(p for p in records.iterdir() if p.suffix != ".stdin")
                        self.assertEqual(len(calls), 1, command)
                        argv = calls[0].read_bytes().split(b"\0")[:-1]
                        stdin = calls[0].with_name(calls[0].name + ".stdin").read_bytes()
                        expected_argv, stdin_file = self.expected_call(command, base_dir, temp_dir)
                        self.assertEqual([arg.decode("utf-8") for arg in argv], expected_argv)
                        self.assertEqual(stdin, (cwd / stdin_file).read_bytes() if stdin_file else b"")
                        for path in records.iterdir():
                            path.unlink()
                        self.assertEqual(list(scratch.rglob("pwned*")), [])
                        self.assertEqual(sorted(p.name for p in physical.iterdir()), sorted(files))

    def test_sh(self):
        self.run_all_commands("sh")

    def test_bash(self):
        self.run_all_commands("bash")

    def test_zsh(self):
        self.run_all_commands("zsh")

    def test_step_2_takes_only_literals(self):
        step_2 = SkillCommandShapeTest.STEP_2
        self.assertIn(step_2, self.commands)
        self.assertEqual(self.expected_call(step_2, "/skill", "t"),
                         (["/skill/scripts/convert.py", "--request", "translate-book-request.json"], None))

    def test_step_2_converts_a_book_with_a_hostile_name(self):
        shells = [shell for shell in ("sh", "bash", "zsh") if process_guard.installed(shell)]
        if not shells:
            self.skipTest("no POSIX shell")
        step_2 = self.command('convert.py" --request', without="--detect-page-numbers")
        book_name = "《爱丽丝》 O'Brien \"$(touch pwned)\" `touch pwned-tick` $HOME;|&.md"
        for shell in shells:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                scratch = Path(tmp)
                base_dir = scratch / HOSTILE_BASE_DIR / "translate-book"
                base_dir.parent.mkdir()
                base_dir.symlink_to(REPO_ROOT, target_is_directory=True)
                cwd, books = scratch / "cwd", scratch / "books"
                cwd.mkdir()
                books.mkdir()
                book = books / book_name
                book.write_text("# Alice\n\nDown the rabbit hole.\n", encoding="utf-8")
                request = {"input": str(book), "target_lang": "zh", "temp_root": "work/sub-1.d",
                           "cover": None, "parser": None}
                (cwd / "translate-book-request.json").write_text(
                    json.dumps(request, ensure_ascii=False), encoding="utf-8")

                result = run_skill_command(step_2, shell=shell, cwd=cwd, values={"{baseDir}": str(base_dir)})

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                temp_dir = self.convert.build_temp_dir(str(book), "work/sub-1.d")
                self.assertIn(f"Input file: {book}\n", result.stdout)
                self.assertIn(f"Temp directory: {temp_dir}\n", result.stdout)
                self.assertTrue((cwd / temp_dir / "chunk0001.md").is_file())
                self.assertFalse((cwd / "translate-book-request.json").exists())
                stored = json.loads((cwd / temp_dir / "request.json").read_text(encoding="utf-8"))
                self.assertEqual(stored["input"], str(book))
                self.assertEqual(list(scratch.rglob("pwned*")), [])


class SkillShellCommandTest(unittest.TestCase):
    """Book text reaches the scripts through files, never inside shell quotes.

    The SKILL.md commands run here through run_skill_command, the way an agent
    runs them, with text that breaks or runs inside quotes: apostrophes, `"`,
    `$(...)`, backticks, `$HOME`.
    """

    BOOK_TEXT = "O'Brien's \"tale\" $(touch pwned) `touch pwned-tick` $HOME \\ end"
    EXPORT_NAME = "O'Brien's $(touch pwned) `touch pwned-tick` $HOME"
    BUILD = 'python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>"'

    def setUp(self):
        self.text = skill_text()
        self.commands = [command for _, command in skill_commands(self.text)[0]]
        self.shells = [s for s in ("sh", "bash", "zsh") if process_guard.installed(s)]

    def command(self, needle):
        lines = [line for line in self.commands if needle in line]
        self.assertEqual(len(set(lines)), 1, needle)
        return lines[0]

    def run_command(self, shell, cmd, cwd, temp_dir="book_temp"):
        return run_skill_command(cmd, shell=shell, cwd=cwd,
                                 values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": temp_dir})

    def test_apply_merge_reads_book_text_from_a_file(self):
        if not self.shells:
            self.skipTest("no POSIX shell")
        cmd = self.command("merge_meta.py\" apply-merge")
        glossary = {
            "version": 2,
            "terms": [{"id": "Tai", "source": "Tai", "target": "泰", "category": "person",
                       "aliases": [], "gender": "unknown", "confidence": "medium",
                       "frequency": 0, "evidence_refs": [], "notes": ""}],
            "high_frequency_top_n": 20,
            "applied_meta_hashes": {},
        }
        meta = {
            "schema_version": 1,
            "new_entities": [{"source": self.BOOK_TEXT, "target_proposal": self.BOOK_TEXT,
                              "category": "person", "evidence": self.BOOK_TEXT}],
            "alias_hypotheses": [],
            "attribute_hypotheses": [],
            "used_term_sources": [],
            "conflicts": [{"entity_source": "Tai", "field": "target", "injected": "泰",
                           "observed_better": self.BOOK_TEXT, "evidence": self.BOOK_TEXT}],
        }
        for shell in self.shells:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                temp_dir = Path(tmp) / "book_temp"
                temp_dir.mkdir()
                (temp_dir / "glossary.json").write_text(json.dumps(glossary), encoding="utf-8")
                (temp_dir / "output_chunk0001.meta.json").write_text(json.dumps(meta), encoding="utf-8")
                prepared = self.run_command(shell, self.command('merge_meta.py" prepare-merge'), tmp)
                self.assertEqual(prepared.returncode, 0, prepared.stderr)
                proposal = json.loads(prepared.stdout)
                decisions = {
                    "auto_apply": proposal["auto_apply"],
                    "decisions": [dict(d, choice="record_in_notes")
                                  for d in proposal["decisions_needed"]],
                    "consumed_chunk_ids": proposal["consumed_chunk_ids"],
                }
                (temp_dir / "merge_decisions.json").write_text(json.dumps(decisions), encoding="utf-8")

                result = self.run_command(shell, cmd, tmp)

                self.assertEqual(result.returncode, 0, result.stderr)
                saved = json.loads((temp_dir / "glossary.json").read_text(encoding="utf-8"))
                terms = {t["source"]: t for t in saved["terms"]}
                self.assertEqual(terms[self.BOOK_TEXT]["target"], self.BOOK_TEXT)
                self.assertIn(f"evidence={self.BOOK_TEXT!r}", terms["Tai"]["notes"])
                self.assertIn("chunk0001", saved["applied_meta_hashes"])
                self.assertEqual(list(Path(tmp).glob("pwned*")), [])

    def test_record_reads_chunk_ids_from_a_file(self):
        if not self.shells:
            self.skipTest("no POSIX shell")
        commands = {line for line in self.commands if 'run_state.py" record' in line}
        self.assertEqual(commands, {'python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" '
                                    '< "<temp_dir>/record_ids.txt"'})
        for shell in self.shells:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                temp_dir = Path(tmp) / "book_temp"
                temp_dir.mkdir()
                for n in (1, 2):
                    (temp_dir / f"chunk000{n}.md").write_text(f"source {n}\n", encoding="utf-8")
                    (temp_dir / f"output_chunk000{n}.md").write_text(f"译文 {n}\n", encoding="utf-8")
                (temp_dir / "record_ids.txt").write_text("chunk0001\nchunk0002\n", encoding="utf-8")

                result = self.run_command(shell, self.command('run_state.py" record'), tmp)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                state = json.loads((temp_dir / "run_state.json").read_text(encoding="utf-8"))
                self.assertEqual(sorted(state["chunks"]), ["chunk0001", "chunk0002"])

    def test_build_takes_no_book_text(self):
        self.assertIn(self.BUILD, self.commands)
        step = self.text[self.text.index("### 7."):self.text.index("### 8.")]
        for name in ("title.txt", "export_name.txt", "author.txt"):
            self.assertIn(f"`<temp_dir>/{name}`", step)

    def test_step_7_deletes_outputs_before_writing_the_name_files(self):
        # A temp dir built before export_copies.json finds its copies through
        # the old export_name.txt, so a rebuild must delete before writing.
        step = self.text[self.text.index("### 7."):self.text.index("### 8.")]
        delete = step.index(self.command("--delete-outputs"))
        build = re.search(rf"^{re.escape(self.BUILD)}$", step, re.M).start()
        for later in ["title.txt", "export_name.txt", "author.txt"]:
            with self.subTest(later=later):
                self.assertLess(delete, step.index(later))
        self.assertLess(delete, build)

    def test_rebuild_deletes_outputs_through_the_script(self):
        if not self.shells:
            self.skipTest("no POSIX shell")
        import convert
        cmd = self.command("--delete-outputs")
        outputs = ["book.html", "book_doc.html", "output.html", "book.docx", "book.epub", "book.pdf"]
        # A rebuild keeps these, including a file whose name starts with the export name.
        kept = ["input.html", "input.md", "chunk0001.md", "output_chunk0001.md", "output.md",
                "title.txt", "export_name.txt", self.EXPORT_NAME + ".md"]
        temp_name = convert.build_temp_dir("-O'Brien's book.epub")
        for shell in self.shells:
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as tmp:
                temp_dir = Path(tmp) / temp_name
                temp_dir.mkdir()
                for name in outputs + kept:
                    (temp_dir / name).write_text(name, encoding="utf-8")
                copies = merge_and_build.export_named_aliases(temp_dir, self.EXPORT_NAME)
                self.assertEqual(len(copies), 5)
                # The agent may already have written the next export name.
                (temp_dir / "export_name.txt").write_text("Next " + self.EXPORT_NAME + "\n",
                                                          encoding="utf-8")

                result = self.run_command(shell, cmd, tmp, temp_dir=temp_name)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(sorted(p.name for p in temp_dir.iterdir()),
                                 sorted(kept + [merge_and_build.EXPORT_COPIES_FILE]))
                self.assertEqual(list(Path(tmp).glob("pwned*")), [])


class SkillCommandRunGuardTest(unittest.TestCase):
    """Tests run SKILL.md commands only through run_skill_command, which runs
    none that SKILL.md as it now passes the shape check does not hold."""

    # Every test that runs SKILL.md commands; test_every_runner_is_listed keeps the list whole.
    RUNNERS = [
        ("SkillCommandArgumentTest", "test_scripts_accept_every_command"),
        ("SkillCommandRuntimeTest", "test_sh"),
        ("SkillCommandRuntimeTest", "test_bash"),
        ("SkillCommandRuntimeTest", "test_zsh"),
        ("SkillCommandRuntimeTest", "test_step_2_converts_a_book_with_a_hostile_name"),
        ("SkillShellCommandTest", "test_apply_merge_reads_book_text_from_a_file"),
        ("SkillShellCommandTest", "test_record_reads_chunk_ids_from_a_file"),
        ("SkillShellCommandTest", "test_rebuild_deletes_outputs_through_the_script"),
    ]
    DELETE_OUTPUTS = 'python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>" --delete-outputs'

    def run_runners(self, marker):
        outcomes = {}
        for class_name, method in self.RUNNERS:
            result = unittest.TestResult()
            globals()[class_name](method).run(result)
            outcomes[f"{class_name}.{method}"] = (
                "skipped" if result.skipped else "passed" if result.wasSuccessful() else "failed")
        self.assertFalse(marker.exists(), "a changed command ran")
        self.assertNotIn("passed", outcomes.values(), outcomes)

    def test_commands_changed_in_a_test_are_not_run(self):
        original = skill_commands
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran"

            def changed(text):
                commands, mentions = original(text)
                return [(line, f'{command} ; touch "{marker}"') for line, command in commands], mentions

            with mock.patch(f"{__name__}.skill_commands", changed):
                self.run_runners(marker)

    def test_commands_of_a_changed_skill_md_are_not_run(self):
        text = skill_text()
        self.assertIn(self.DELETE_OUTPUTS + "\n", text)
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran"
            changed = Path(tmp) / "SKILL.md"
            changed.write_text(text.replace(self.DELETE_OUTPUTS + "\n",
                                            f'{self.DELETE_OUTPUTS} ; touch "{marker}"\n'), encoding="utf-8")
            with mock.patch(f"{__name__}.SKILL_PATH", changed):
                self.run_runners(marker)

    def test_entry_refuses_bad_values_and_places(self):
        command = 'python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"'
        self.assertIn(command, _checked_commands(skill_text()))
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "ran"
            refused = [
                dict(values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": f'x"; touch "{marker}'}, cwd=tmp),
                dict(values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "$(touch ran)"}, cwd=tmp),
                dict(values={"{baseDir}": str(REPO_ROOT)}, cwd=tmp),
                dict(values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "t"}, cwd="/"),
                dict(values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "t"}, cwd=tmp,
                     env={"BASH_ENV": str(marker)}),
                dict(values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "t"}, cwd=tmp, env={"FAKE_EXIT": "1"}),
                dict(values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "t", "<file>": "x"}, cwd=tmp),
            ]
            for arguments in refused:
                with self.subTest(arguments=arguments), self.assertRaises(AssertionError):
                    run_skill_command(command, shell="sh", **arguments)
            with self.assertRaises(AssertionError):
                run_skill_command(f'{command} ; touch "{marker}"', shell="sh", cwd=tmp,
                                  values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "t"})
            self.assertFalse(marker.exists())

    def test_entry_fails_a_command_that_writes_into_home(self):
        command = 'python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"'
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "bin"
            fake.mkdir()
            (fake / "python3").write_text('#!/bin/sh\ntouch "$HOME/.written"\n', encoding="utf-8")
            (fake / "python3").chmod(0o755)
            with self.assertRaisesRegex(AssertionError, r"wrote into HOME: \['\.written'\]"):
                run_skill_command(command, shell="sh", cwd=tmp, path_dirs=[fake],
                                  values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "t"})

    def test_entry_runs_in_an_environment_of_its_own(self):
        # BASH_ENV, ENV and a .zshenv in the real HOME would run code before the
        # command; none of them reaches it, and the python3 it finds is this one.
        command = 'python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"'
        with tempfile.TemporaryDirectory() as tmp:
            hook = Path(tmp) / "hook.sh"
            hook.write_text(f'touch "{tmp}/hooked"\n', encoding="utf-8")
            outside = {"BASH_ENV": str(hook), "ENV": str(hook), "CDPATH": tmp, "ZDOTDIR": tmp,
                       "PYTHONSTARTUP": str(hook), "PYTHONPATH": tmp}
            Path(tmp, ".zshenv").write_text(f'touch "{tmp}/hooked"\n', encoding="utf-8")
            shells = [shell for shell in ("sh", "bash", "zsh") if process_guard.installed(shell)]
            with mock.patch.dict(os.environ, outside):
                for shell in shells:
                    with self.subTest(shell=shell):
                        result = run_skill_command(command, shell=shell, cwd=tmp,
                                                   values={"{baseDir}": str(REPO_ROOT), "<temp_dir>": "missing"})
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotRegex(result.stderr, r"(?m)^usage: ")
            self.assertFalse(Path(tmp, "hooked").exists())

    def test_every_runner_is_listed(self):
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        methods = {(cls.name, node.name): node for cls in tree.body if isinstance(cls, ast.ClassDef)
                   for node in cls.body if isinstance(node, ast.FunctionDef)}

        def reaches_entry(node, class_name, seen):
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                if isinstance(call.func, ast.Name):
                    if call.func.id == "run_skill_command":
                        return True
                    key, target = (None, call.func.id), functions.get(call.func.id)
                elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) \
                        and call.func.value.id == "self":
                    key, target = (class_name, call.func.attr), methods.get((class_name, call.func.attr))
                else:
                    continue
                if target is not None and key not in seen:
                    seen.add(key)
                    if reaches_entry(target, key[0], seen):
                        return True
            return False

        # The tests of this class call the entry to test the entry itself; the
        # mutations above are for the tests that run SKILL.md steps.
        runners = {key for key, node in methods.items()
                   if key[0] != type(self).__name__ and key[1].startswith("test")
                   and reaches_entry(node, key[0], set())}
        self.assertEqual(runners, set(self.RUNNERS))


if __name__ == "__main__":
    unittest.main()
