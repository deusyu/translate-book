#!/usr/bin/env python3
"""
merge_and_build.py - Merge translated pages and build final outputs
Combines original steps 4-7: merge -> HTML -> TOC -> DOCX/EPUB/PDF

Usage: merge_and_build.py --temp-dir <path> [--title <title>] [--author <author>] [--lang <lang>]
"""

import os
import sys
import re
import glob
import json
import shutil
import stat
import subprocess
import tempfile
import argparse
import html as _html_lib
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from manifest import (chunk_file_name, chunk_id_from_file_name, list_chunk_files, load_manifest,
                      nth_chunk_id, read_output_text, validate_for_merge)
from convert import _fenced_code_line_indices, check_cover_file, load_stored_request

if os.name == "nt":
    sys.exit("translate-book runs on macOS and Linux only. "
             "On Windows, run it in WSL (Windows Subsystem for Linux).")


# =============================================================================
# Image structure validation helpers
# =============================================================================

# Markdown image: `![alt](url)` or `![alt](url "title")`.
# - Negative lookbehind on `\` skips escaped `\![...]` (renders as literal text).
# - Closing `)` is required — a missing `)` means the image won't render, so
#   such a fragment must NOT count as a preserved image reference.
_MD_IMG_RE = re.compile(r'(?<!\\)!\[[^\]]*\]\(\s*([^)\s]+)[^)]*\)')
_VALID_ATTR_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_:.\-]*$')


class _ImgTagCollector(HTMLParser):
    """Collects every <img> tag found in fed text. Uses stdlib HTMLParser, which
    correctly handles `>` inside quoted attribute values — unlike a plain
    `<img\\b[^>]*>` regex, which would truncate at the first `>`."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.records = []  # list of (raw_tag_text, attrs_list)

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.records.append((self.get_starttag_text(), list(attrs)))

    handle_startendtag = handle_starttag


def _scan_img_tags(text):
    """Return (Counter of <img> srcs, list of (raw_tag, bad_attr_name) tuples).

    Feeds the entire text to HTMLParser rather than pre-extracting tags via regex,
    so quoted attribute values containing `>` are handled correctly."""
    src_counts = Counter()
    bad_attrs = []
    parser = _ImgTagCollector()
    try:
        parser.feed(text)
        parser.close()
    except Exception as e:
        bad_attrs.append(('<unparseable input>', f'<parser error: {e}>'))
        return src_counts, bad_attrs
    for raw_tag, attrs in parser.records:
        for name, _ in attrs:
            if not _VALID_ATTR_NAME_RE.match(name):
                bad_attrs.append((raw_tag, name))
        for name, val in attrs:
            if name == 'src' and val:
                src_counts[val] += 1
    return src_counts, bad_attrs


def _scan_image_refs(text):
    """Return (Counter html_srcs, Counter md_srcs, list bad_attrs)."""
    html_srcs, bad_attrs = _scan_img_tags(text)
    md_srcs = Counter(_MD_IMG_RE.findall(text))
    return html_srcs, md_srcs, bad_attrs


def _validate_chunk_images(temp_dir):
    """Verify each output_chunk*.md preserves the image structure of its chunk*.md.

    Bad-attribute detection uses a per-chunk DELTA: a malformed <img> attribute
    is flagged only if it appears in the output chunk but not in the source
    chunk. This avoids false positives on code blocks that legitimately contain
    deliberately-broken <img> examples — both chunks carry the same example, so
    the delta is empty.

    Returns False on any divergence; collects all errors and prints them
    together so an agent can fix many chunks in one pass.
    """
    temp_path = Path(temp_dir)
    errors = []
    for src_chunk in (temp_path / name for name in list_chunk_files(temp_dir)):
        out_chunk = temp_path / f'output_{src_chunk.name}'
        if not out_chunk.exists():
            continue  # missing-output is the manifest validator's job
        src_html, src_md, src_bad = _scan_image_refs(src_chunk.read_text(encoding='utf-8'))
        out_html, out_md, out_bad = _scan_image_refs(out_chunk.read_text(encoding='utf-8'))

        src_bad_counts = Counter(name for _, name in src_bad)
        out_bad_counts = Counter(name for _, name in out_bad)
        new_bad_counts = out_bad_counts - src_bad_counts
        if new_bad_counts:
            new_bad_examples = [
                (raw_tag, attr_name)
                for raw_tag, attr_name in out_bad
                if new_bad_counts.get(attr_name, 0) > 0
            ]
            for raw_tag, attr_name in new_bad_examples:
                errors.append(
                    f"ERROR: {out_chunk.name} introduced malformed <img> tag (not present in source)\n"
                    f"  tag: {raw_tag}\n"
                    f"  problem: attribute name '{attr_name}' is not a valid HTML identifier\n"
                    f"  likely cause: an unescaped quote inside alt=\"...\" or title=\"...\" closed the attribute early\n"
                    f"  fix: in {out_chunk.name}, replace the inner quote with a curly quote in the target language or with &quot; / &#39;\n"
                    f"  source chunk for reference: {src_chunk.name}"
                )

        if src_html != out_html or src_md != out_md:
            errors.append(
                f"ERROR: {out_chunk.name} image references diverge from {src_chunk.name}\n"
                f"  missing <img src> (count): {sorted((src_html - out_html).items()) or 'none'}\n"
                f"  extra   <img src> (count): {sorted((out_html - src_html).items()) or 'none'}\n"
                f"  missing ![](path) (count): {sorted((src_md - out_md).items()) or 'none'}\n"
                f"  extra   ![](path) (count): {sorted((out_md - src_md).items()) or 'none'}\n"
                f"  fix: restore the missing image refs in {out_chunk.name} from {src_chunk.name}"
            )

    if errors:
        print("\n=== Image validation failed ===")
        for e in errors:
            print(e)
            print()
        return False
    return True


# Pandoc footnote labels cannot contain whitespace, ^, [ or ]; this also keeps
# `[^1^]` pseudo-footnotes from older conversions out of the comparison.
_FOOTNOTE_LABEL_RE = re.compile(r'\[\^([^\]\s^\[]+)\]')


def _scan_footnote_labels(text):
    """Return (Counter of reference labels, Counter of definition labels)."""
    refs, defs = Counter(), Counter()
    for m in _FOOTNOTE_LABEL_RE.finditer(text):
        line_start = text.rfind('\n', 0, m.start()) + 1
        indent = text[line_start:m.start()]
        is_definition = len(indent) <= 3 and not indent.strip() and text.startswith(':', m.end())
        (defs if is_definition else refs)[m.group(1)] += 1
    return refs, defs


def _validate_chunk_footnotes(temp_dir):
    """Verify each output_chunk*.md keeps the footnote labels of its chunk*.md.

    A dropped or renumbered `[^N]` / `[^N]:` leaves a dangling reference or an
    orphaned note in the merged book. Returns False on any divergence and
    prints every error at once.
    """
    temp_path = Path(temp_dir)
    errors = []
    for src_chunk in (temp_path / name for name in list_chunk_files(temp_dir)):
        out_chunk = temp_path / f'output_{src_chunk.name}'
        if not out_chunk.exists():
            continue
        src_text = read_output_text(src_chunk)
        out_text = read_output_text(out_chunk)
        if src_text is None or out_text is None:
            continue  # unreadable files are the manifest validator's job
        src_refs, src_defs = _scan_footnote_labels(src_text)
        out_refs, out_defs = _scan_footnote_labels(out_text)
        if src_refs != out_refs or src_defs != out_defs:
            errors.append(
                f"ERROR: {out_chunk.name} footnote markers diverge from {src_chunk.name}\n"
                f"  missing references [^N] (count): {sorted((src_refs - out_refs).items()) or 'none'}\n"
                f"  extra   references [^N] (count): {sorted((out_refs - src_refs).items()) or 'none'}\n"
                f"  missing definitions [^N]: (count): {sorted((src_defs - out_defs).items()) or 'none'}\n"
                f"  extra   definitions [^N]: (count): {sorted((out_defs - src_defs).items()) or 'none'}\n"
                f"  fix: in {out_chunk.name}, restore every footnote marker exactly as in {src_chunk.name}; "
                f"translate only the note text"
            )

    if errors:
        print("\n=== Footnote validation failed ===")
        for e in errors:
            print(e)
            print()
        return False
    return True


# Figure text from figure_text.py is a pandoc fenced div. A dropped closing `:::`
# pulls the rest of the book into the div; an altered opening line prints as text.
_FIGURE_TEXT_OPEN_RE = re.compile(r'^:{3,}\s*\{\.figure-text\b')
_FENCE_CLOSE_RE = re.compile(r'^:{3,}$')


def _scan_figure_text(text):
    """Return (Counter of figure-text opening lines, number of them left unclosed)."""
    openers = Counter()
    unclosed = 0
    inside = False
    for line in text.split('\n'):
        stripped = line.strip()
        if _FIGURE_TEXT_OPEN_RE.match(stripped):
            openers[stripped] += 1
            unclosed += inside
            inside = True
        elif inside and _FENCE_CLOSE_RE.match(stripped):
            inside = False
    return openers, unclosed + inside


def _validate_chunk_figure_text(temp_dir):
    """Verify each output_chunk*.md keeps the figure-text blocks of its chunk*.md.

    Returns False on any divergence and prints every error at once.
    """
    temp_path = Path(temp_dir)
    errors = []
    for src_chunk in (temp_path / name for name in list_chunk_files(temp_dir)):
        out_chunk = temp_path / f'output_{src_chunk.name}'
        if not out_chunk.exists():
            continue
        src_text = read_output_text(src_chunk)
        out_text = read_output_text(out_chunk)
        if src_text is None or out_text is None:
            continue  # unreadable files are the manifest validator's job
        src_open, _ = _scan_figure_text(src_text)
        out_open, out_unclosed = _scan_figure_text(out_text)
        if src_open != out_open or out_unclosed:
            errors.append(
                f"ERROR: {out_chunk.name} figure-text blocks diverge from {src_chunk.name}\n"
                f"  missing opening lines (count): {sorted((src_open - out_open).items()) or 'none'}\n"
                f"  extra   opening lines (count): {sorted((out_open - src_open).items()) or 'none'}\n"
                f"  blocks without a closing ::: line: {out_unclosed}\n"
                f"  fix: in {out_chunk.name}, copy each `::: {{.figure-text ...}}` line and the `:::` line "
                f"closing it unchanged from {src_chunk.name}; translate only the list items between them. "
                f"If the chunk was translated before figure text was injected, translate it again"
            )

    if errors:
        print("\n=== Figure text validation failed ===")
        for e in errors:
            print(e)
            print()
        return False
    return True


def _check_generated_html_sanity(html_path):
    """Sanity-check generated HTML for malformed <img> tags. Returns False on problems.

    Note: we deliberately do NOT flag `&lt;img` in the rendered HTML — books that
    discuss HTML in prose or code blocks legitimately render escaped `<img>` text,
    and that's not a corruption signal. Real corruption produces a malformed
    actual `<img>` tag, which the attribute-name check catches."""
    try:
        text = Path(html_path).read_text(encoding='utf-8')
    except Exception as e:
        print(f"ERROR: cannot read {html_path}: {e}")
        return False

    _, bad_attrs = _scan_img_tags(text)
    if not bad_attrs:
        return True

    print(f"ERROR: image sanity check failed on {Path(html_path).name}")
    for raw_tag, attr_name in bad_attrs:
        print(f"  - malformed <img>: {raw_tag}")
        print(f"    bad attribute name: '{attr_name}'")
    print(
        "  fix: inspect output.md and the corresponding output_chunk*.md;\n"
        "       if alt text contains literal quotes, replace with curly quotes or HTML entity"
    )
    return False

# Try to import BeautifulSoup for TOC generation
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# Try to import markdown
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
# Language configuration — single source of truth for lang-dependent values
# =============================================================================

LANG_CONFIG = {
    'zh': {
        'lang_attr': 'zh-CN',
        'font_family': "'FangSong', '仿宋', 'STFangSong', '华文仿宋', serif",
        'font_family_ebook': '"FangSong", "FangSong_GB2312", "仿宋", "仿宋_GB2312", "STFangSong", "SimSun", serif',
        'toc_label': '目录',
        'pdf_font': 'FangSong',
    },
    'en': {
        'lang_attr': 'en',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Contents',
        'pdf_font': 'Georgia',
    },
    'ja': {
        'lang_attr': 'ja',
        'font_family': "'Hiragino Mincho ProN', 'Yu Mincho', 'MS Mincho', serif",
        'font_family_ebook': '"Hiragino Mincho ProN", "Yu Mincho", "MS Mincho", serif',
        'toc_label': '目次',
        'pdf_font': 'Hiragino Mincho ProN',
    },
    'ko': {
        'lang_attr': 'ko',
        'font_family': "'Nanum Myeongjo', 'Batang', serif",
        'font_family_ebook': '"Nanum Myeongjo", "Batang", serif',
        'toc_label': '목차',
        'pdf_font': 'Nanum Myeongjo',
    },
    'fr': {
        'lang_attr': 'fr',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Table des matières',
        'pdf_font': 'Georgia',
    },
    'de': {
        'lang_attr': 'de',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Inhaltsverzeichnis',
        'pdf_font': 'Georgia',
    },
    'es': {
        'lang_attr': 'es',
        'font_family': "Georgia, 'Times New Roman', Times, serif",
        'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
        'toc_label': 'Índice',
        'pdf_font': 'Georgia',
    },
}

# Default fallback for unknown languages
_DEFAULT_LANG_CONFIG = {
    'lang_attr': 'en',
    'font_family': "Georgia, 'Times New Roman', Times, serif",
    'font_family_ebook': 'Georgia, "Times New Roman", Times, serif',
    'toc_label': 'Contents',
    'pdf_font': 'Georgia',
}

# A BCP 47 language tag such as it, pt-BR or zh-Hant-TW.
_LANG_TAG_RE = re.compile(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*')


def _is_traditional_chinese(tag):
    """Whether a zh tag asks for Traditional Chinese.

    A script subtag decides: Hant is Traditional, Hans is Simplified. Without
    one, Taiwan, Hong Kong and Macau mean Traditional.
    """
    subtags = [s.lower() for s in tag.split('-')[1:]]
    scripts = [s for s in subtags if len(s) == 4 and s.isalpha()]
    if scripts:
        return scripts[0] == 'hant'
    return any(s in ('tw', 'hk', 'mo') for s in subtags)


def get_lang_config(lang_code):
    """Get language config for a language tag, such as it, pt-BR or zh-TW.

    The tag becomes lang_attr as given, with an underscore read as a hyphen.
    Fonts and the TOC label come from the table entry for its primary subtag
    (zh for zh-TW), or from the defaults when there is none; Traditional
    Chinese gets its own TOC label. A bare code that is in the table keeps
    its entry's lang_attr, so zh is written as zh-CN.
    """
    tag = (lang_code or '').strip().replace('_', '-')
    if not _LANG_TAG_RE.fullmatch(tag):
        print(f"Warning: '{lang_code}' is not a language code such as it, pt-BR or zh-TW; "
              f"marking the book as {_DEFAULT_LANG_CONFIG['lang_attr']}")
        return _DEFAULT_LANG_CONFIG
    primary = tag.split('-')[0].lower()
    if tag.lower() == primary and primary in LANG_CONFIG:
        return LANG_CONFIG[primary]
    cfg = {**LANG_CONFIG.get(primary, _DEFAULT_LANG_CONFIG), 'lang_attr': tag}
    if primary == 'zh' and _is_traditional_chinese(tag):
        cfg['toc_label'] = '目錄'
    return cfg


def load_config(temp_dir):
    """Load configuration from config.txt"""
    config_file = os.path.join(temp_dir, 'config.txt')
    if not os.path.exists(config_file):
        print("Error: config.txt not found in temp directory.")
        sys.exit(1)

    config = {}
    with open(config_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\r\n')
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                config[key.strip()] = value
    return config


# SKILL.md's build command takes no book text: the agent writes the translated
# title, the export name and an author override to these files instead.
TITLE_FILE = 'title.txt'
EXPORT_NAME_FILE = 'export_name.txt'
AUTHOR_FILE = 'author.txt'


def read_name_file(temp_dir, name):
    """The text of <temp_dir>/<name> with surrounding whitespace removed, or None
    when the file is missing or blank. Raises ValueError for text that is not UTF-8.
    newline='' keeps a carriage return inside the text, as --delete-outputs reads it."""
    try:
        with open(os.path.join(temp_dir, name), 'r', encoding='utf-8', newline='') as f:
            text = f.read().strip()
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        raise ValueError(f"{name} in {temp_dir} is not UTF-8 text") from None
    return text or None


def stored_request_cover(temp_dir):
    """The cover image named in the request convert.py stored in temp_dir, or None.
    Raises ValueError when request.json is not one convert.py stored, or the
    image it names is gone."""
    try:
        request = load_stored_request(temp_dir)
    except ValueError as e:
        raise ValueError(f"{e}. Rename or move it; the conversion step stores the request "
                         f"again") from None
    if request is None or request["cover"] is None:
        return None
    try:
        return check_cover_file(request["cover"])
    except ValueError as e:
        raise ValueError(f"{e}, the cover that request.json names. Run the conversion step "
                         f"again with the cover's current path, or with cover null") from None


def natural_sort_key(text):
    """Natural sorting key for filenames with numbers"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', text)]


# An ATX heading as pandoc reads it: no indent, and a space or the line end after
# the #s, so `#hashtag` is text.
_ATX_HEADING_RE = re.compile(r'^#+(?:[ \t]|$)')


def heading_outline(temp_dir):
    """List the headings of the output chunks in book order, as `file:line: heading`.

    Lines in fenced code are skipped: a `# comment` there is code, not a heading.
    """
    rows = []
    paths = [os.path.join(temp_dir, name) for name in list_chunk_files(temp_dir, 'output')]
    for path in paths:
        text = read_output_text(path)
        if text is None:
            continue
        lines = text.split('\n')
        code = _fenced_code_line_indices(lines)
        name = os.path.basename(path)
        rows.extend(f"{name}:{i + 1}: {line.rstrip()}"
                    for i, line in enumerate(lines)
                    if i not in code and _ATX_HEADING_RE.match(line))
    return rows


# =============================================================================
# Step 4: Merge translated markdown files
# =============================================================================

def merge_markdown_files(temp_dir):
    """Merge all translated output files into output.md"""
    print("=== Merging translated markdown files ===")

    output_md = os.path.join(temp_dir, 'output.md')

    # Always validate manifest, even if output.md exists (catch stale/corrupt outputs)
    ok, ordered_files, warnings = validate_for_merge(temp_dir)

    # Image, footnote and figure-text validation run unconditionally — bad chunks invalidate any cached output.md
    images_ok = _validate_chunk_images(temp_dir)
    footnotes_ok = _validate_chunk_footnotes(temp_dir)
    figure_text_ok = _validate_chunk_figure_text(temp_dir)
    if not (images_ok and footnotes_ok and figure_text_ok):
        if os.path.exists(output_md):
            print("Removing stale output.md (built from chunks that failed validation)")
            os.remove(output_md)
        return False

    if os.path.exists(output_md):
        if not ok:
            print(f"WARNING: output.md exists but manifest validation failed — deleting stale output.md")
            os.remove(output_md)
        else:
            # Check if any output_chunk is newer than output.md (re-translated chunks)
            output_md_mtime = os.path.getmtime(output_md)
            newer_chunks = []
            if ordered_files:
                newer_chunks = [
                    os.path.basename(f) for f in ordered_files
                    if os.path.getmtime(f) > output_md_mtime
                ]
            if newer_chunks:
                print(f"Re-merging — {len(newer_chunks)} chunk(s) newer than output.md: {', '.join(newer_chunks[:5])}{'...' if len(newer_chunks) > 5 else ''}")
                os.remove(output_md)
            else:
                print(f"Skipping merge - output.md already exists and is up to date")
                return True

    if not ok:
        print("ERROR: Merge validation failed. Fix the issues above before merging.")
        return False

    if ordered_files is not None:
        # Manifest-based merge
        print(f"Merging {len(ordered_files)} translated files (manifest-ordered)")
        merged_content = ""
        for file_path in ordered_files:
            content = read_output_text(file_path)
            if content is None:
                print(f"ERROR: Cannot read {os.path.basename(file_path)} — aborting merge")
                return False
            content = content.strip()
            if not content:
                # validate_for_merge already rejects blank outputs; this is a
                # last line of defense so a chunk can never vanish silently.
                print(f"ERROR: Blank output {os.path.basename(file_path)} — aborting merge")
                return False
            merged_content += content + "\n\n"
    else:
        # Legacy fallback: glob-based merge (no manifest)
        print("WARNING: No manifest.json found — using legacy glob-based merge.")
        print("  For hash validation, re-run convert.py to generate manifest.json")

        output_files = [os.path.join(temp_dir, name) for name in list_chunk_files(temp_dir, 'output')]
        source_basenames = list_chunk_files(temp_dir)

        if not output_files:
            print("Error: No translated markdown files found.")
            return False

        # Verify a 1:1 match between source chunks and outputs
        expected_outputs = set(f"output_{name}" for name in source_basenames)
        actual_outputs = set(os.path.basename(f) for f in output_files)

        missing = expected_outputs - actual_outputs
        orphaned = actual_outputs - expected_outputs

        if missing or orphaned:
            if missing:
                print(f"ERROR: Missing translations for: {', '.join(sorted(missing, key=natural_sort_key))}")
            if orphaned:
                print(f"ERROR: Orphaned outputs (no matching source): {', '.join(sorted(orphaned, key=natural_sort_key))}")
            return False

        # Verify no empty, unreadable, or whitespace-only output files
        for fp in output_files:
            if os.path.getsize(fp) == 0:
                print(f"ERROR: Empty output file: {os.path.basename(fp)}")
                return False
            text = read_output_text(fp)
            if text is None:
                print(f"ERROR: Unreadable output file: {os.path.basename(fp)}")
                return False
            if not text.strip():
                print(f"ERROR: Blank output file: {os.path.basename(fp)}")
                return False

        # Use source order to determine merge order (via expected output names)
        output_files = [
            os.path.join(temp_dir, f"output_{name}")
            for name in source_basenames
        ]
        print(f"Merging {len(output_files)} translated files (legacy glob)")

        merged_content = ""
        for file_path in output_files:
            content = read_output_text(file_path)
            if content is None:
                print(f"ERROR: Cannot read {os.path.basename(file_path)} — aborting merge")
                return False
            content = content.strip()
            if not content:
                print(f"ERROR: Blank output {os.path.basename(file_path)} — aborting merge")
                return False
            merged_content += content + "\n\n"

    try:
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(merged_content)
        file_size = os.path.getsize(output_md)
        print(f"Merged into output.md ({file_size:,} bytes)")
        return True
    except Exception as e:
        print(f"Error saving merged file: {e}")
        return False


# =============================================================================
# Step 5: Convert markdown to HTML
# =============================================================================

_TEX_MATH_WARNING_RE = re.compile(r'Could not convert TeX math (.*?), rendering as TeX', re.DOTALL)


def check_pandoc_available():
    """Check if pandoc is available"""
    try:
        subprocess.run(['pandoc', '--version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def convert_with_pandoc(md_file, html_file, title, lang_attr):
    """Convert markdown to HTML using pandoc"""
    cmd = [
        'pandoc', md_file, '-o', html_file,
        '--standalone',
        '--metadata', f'title={title}',
        '--metadata', f'lang={lang_attr}',
        # ascii_identifiers: heading ids such as `section-1` instead of
        # `第一章-掉进兔子洞`. Calibre drops non-ASCII anchors from its TOC
        # entries when it splits the book, so every entry pointed at the
        # first file.
        '--from', 'markdown+smart+east_asian_line_breaks+ascii_identifiers',
        '--to', 'html5',
        # Without this, pandoc emits any TeX it cannot approximate in plain
        # HTML (fractions, roots, display math) as raw `$...$` text.
        '--mathml'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("Converted with pandoc")
        unconverted = _TEX_MATH_WARNING_RE.findall(result.stderr)
        if unconverted:
            print(f"Warning: pandoc left {len(unconverted)} formula(s) as raw TeX instead of MathML:")
            for tex in unconverted[:5]:
                tex = ' '.join(tex.split())
                print(f"  {tex[:70]}{'...' if len(tex) > 70 else ''}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Pandoc conversion failed: {e.stderr}")
        return False


def convert_with_python_markdown(md_file, html_file, title):
    """Convert markdown to HTML using python-markdown (fallback 1)"""
    if not MARKDOWN_AVAILABLE:
        return False

    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        extensions = ['toc', 'tables', 'fenced_code', 'codehilite', 'nl2br', 'footnotes']
        md = markdown.Markdown(extensions=extensions)
        html_content = md.convert(md_content)

        full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body>
{html_content}
</body>
</html>"""

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(full_html)

        print("Converted with python-markdown (fallback)")
        return True
    except Exception as e:
        print(f"python-markdown conversion failed: {e}")
        return False


def convert_with_basic_regex(md_file, html_file, title):
    """Convert markdown to HTML using basic regex (fallback 2)"""
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        html_content = md_content

        # Headers
        html_content = re.sub(r'^#### (.*?)$', r'<h4>\1</h4>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html_content, flags=re.MULTILINE)
        html_content = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html_content, flags=re.MULTILINE)

        # Bold and italic
        html_content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html_content)
        html_content = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html_content)
        html_content = re.sub(r'_(.*?)_', r'<em>\1</em>', html_content)

        # Images — escape alt and src so quotes in alt text don't break the tag
        def _md_img_to_html(m):
            alt = _html_lib.escape(m.group(1), quote=True)
            src = _html_lib.escape(m.group(2), quote=True)
            return f'<img src="{src}" alt="{alt}">'
        html_content = re.sub(r'!\[([^\]]*)\]\(([^)]*)\)', _md_img_to_html, html_content)

        # Links
        html_content = re.sub(r'\[([^\]]*)\]\(([^)]*)\)', r'<a href="\2">\1</a>', html_content)

        # Lists and paragraphs
        lines = html_content.split('\n')
        result_lines = []
        in_list = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('- '):
                if not in_list:
                    result_lines.append('<ul>')
                    in_list = 'ul'
                item = stripped[2:]
                result_lines.append(f'<li>{item}</li>')
            elif re.match(r'^\d+\. ', stripped):
                if not in_list:
                    result_lines.append('<ol>')
                    in_list = 'ol'
                item = re.sub(r'^\d+\. ', '', stripped)
                result_lines.append(f'<li>{item}</li>')
            else:
                if in_list:
                    result_lines.append(f'</{in_list}>')
                    in_list = False
                if stripped and not stripped.startswith('<'):
                    result_lines.append(f'<p>{line}</p>')
                else:
                    result_lines.append(line)

        if in_list:
            result_lines.append(f'</{in_list}>')

        html_content = '\n'.join(result_lines)

        # Page separators
        html_content = re.sub(r'<p>---</p>', '<div class="page-separator"></div>', html_content)

        full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{title}</title>
</head>
<body>
{html_content}
</body>
</html>"""

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(full_html)

        print("Converted with basic regex (fallback 2)")
        return True
    except Exception as e:
        print(f"Basic regex conversion failed: {e}")
        return False


def apply_template_to_html(html_content, template_file, output_file, title, lang_cfg, author=None):
    """Apply a template to HTML content with language-aware substitutions"""
    if not template_file or not os.path.exists(template_file):
        print(f"Warning: Template {template_file} not found")
        return False

    try:
        with open(template_file, 'r', encoding='utf-8') as f:
            template_content = f.read()

        if '$body$' in template_content:
            parts, body = template_content.split('$body$'), html_content
        elif '{{content}}' in template_content:
            parts, body = template_content.split('{{content}}'), html_content
        else:
            if '</body>' in template_content:
                parts, body = template_content.split('</body>'), f'{html_content}\n</body>'
            else:
                parts, body = [template_content, ''], html_content

        # Fill in the template around the body, all placeholders in one pass:
        # the body and the title come from the book, and a $title$ or $lang$
        # written in them must come through as written.
        fields = {
            'title': _html_lib.escape(title),
            'lang': lang_cfg['lang_attr'],
            'body_font': lang_cfg['font_family'],
            'toc_label': lang_cfg['toc_label'],
        }
        full_html = body.join(
            re.sub(r'\$(title|lang|body_font|toc_label)\$', lambda m: fields[m.group(1)], part)
            for part in parts
        )

        # Inject author meta tag into <head> so calibre_html_publish.py can extract it
        if author:
            author_meta = f'<meta name="author" content="{_html_lib.escape(author)}">'
            if '<head>' in full_html or '<head ' in full_html:
                # A function, not a replacement string: re.sub would read a
                # backslash in the name as an escape or a group reference.
                full_html = re.sub(
                    r'(<head[^>]*>)',
                    lambda m: m.group(1) + '\n    ' + author_meta,
                    full_html,
                    count=1,
                    flags=re.IGNORECASE
                )

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(full_html)
        return True
    except Exception as e:
        print(f"Error applying template: {e}")
        return False


def process_html_separators(html_file):
    """Process page separators in HTML"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()

        content = re.sub(r'<hr\s*/?>', '<div class="page-separator"></div>', content)
        content = re.sub(r'<p>\s*---\s*</p>', '<div class="page-separator"></div>', content)

        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        print(f"Error processing separators: {e}")


def convert_md_to_html(temp_dir, title, lang_cfg, author=None):
    """Convert output.md to HTML with templates"""
    print("=== Converting markdown to HTML ===")

    md_file = os.path.join(temp_dir, 'output.md')
    if not os.path.exists(md_file):
        print("Error: output.md not found.")
        return False

    book_doc_file = os.path.join(temp_dir, 'book_doc.html')

    # Skip HTML generation if book_doc.html exists and is newer than output.md
    if os.path.exists(book_doc_file):
        if os.path.getmtime(book_doc_file) > os.path.getmtime(md_file):
            if _check_generated_html_sanity(book_doc_file):
                print("Skipping HTML generation - book_doc.html is up to date")
                return True
            print("Stale book_doc.html failed image sanity — regenerating")
            os.remove(book_doc_file)
        else:
            print("Re-generating HTML - output.md is newer")

    temp_html_file = os.path.join(temp_dir, 'output.html')

    # Try pandoc -> python-markdown -> basic regex
    success = False
    if check_pandoc_available():
        success = convert_with_pandoc(md_file, temp_html_file, title, lang_cfg['lang_attr'])

    if not success:
        success = convert_with_python_markdown(md_file, temp_html_file, title)

    if not success:
        success = convert_with_basic_regex(md_file, temp_html_file, title)

    if not success:
        print("Error: All markdown-to-HTML converters failed")
        return False

    process_html_separators(temp_html_file)

    # Extract body content
    try:
        with open(temp_html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading HTML file: {e}")
        return False

    body_match = re.search(r'<body[^>]*>(.*?)</body>', html_content, re.DOTALL | re.IGNORECASE)
    body_content = body_match.group(1).strip() if body_match else html_content

    # Generate book_doc.html with ebook template
    template_ebook = os.path.join(SCRIPT_DIR, 'template_ebook.html')
    book_doc_file = os.path.join(temp_dir, 'book_doc.html')
    apply_template_to_html(body_content, template_ebook, book_doc_file, title, lang_cfg, author)

    # Generate book.html with web template
    template_web = os.path.join(SCRIPT_DIR, 'template.html')
    book_file = os.path.join(temp_dir, 'book.html')
    apply_template_to_html(body_content, template_web, book_file, title, lang_cfg, author)

    if not _check_generated_html_sanity(book_doc_file):
        return False
    if not _check_generated_html_sanity(book_file):
        return False

    print(f"Generated: output.html, book_doc.html, book.html")
    return True


# =============================================================================
# Step 6: Add TOC
# =============================================================================

def generate_heading_id(text, existing_ids):
    """Generate unique ID for heading"""
    base_id = re.sub(r'[^\w\s-]', '', text.lower())
    base_id = re.sub(r'[-\s]+', '-', base_id)
    base_id = base_id.strip('-')

    if not base_id:
        base_id = 'heading'

    heading_id = base_id
    counter = 1
    while heading_id in existing_ids:
        heading_id = f"{base_id}-{counter}"
        counter += 1

    return heading_id


def generate_simple_toc_html(toc_data):
    """Generate simple HTML for table of contents"""
    if not toc_data:
        return ""

    toc_html = '<ul>\n'
    current_level = 1

    for item in toc_data:
        level = item['level']
        text = item['text']
        heading_id = item['id']

        if level > current_level:
            while current_level < level:
                toc_html += '<li><ul>\n'
                current_level += 1
        elif level < current_level:
            while current_level > level:
                toc_html += '</ul></li>\n'
                current_level -= 1

        toc_html += f'<li><a href="#{heading_id}">{text}</a></li>\n'

    while current_level > 1:
        toc_html += '</ul></li>\n'
        current_level -= 1

    toc_html += '</ul>\n'
    return toc_html


def insert_toc_with_bs4(html_file):
    """Insert TOC using BeautifulSoup"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading HTML file: {e}")
        return False

    soup = BeautifulSoup(html_content, 'html.parser')

    toc_data = []
    existing_ids = []

    for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        level = int(heading.name[1])
        text = heading.get_text().strip()
        if not text:
            continue

        heading_id = generate_heading_id(text, existing_ids)
        existing_ids.append(heading_id)
        heading['id'] = heading_id
        toc_data.append({'level': level, 'text': text, 'id': heading_id})

    if not toc_data:
        print("No headings found for TOC")
        return False

    toc_html = generate_simple_toc_html(toc_data)

    toc_content_div = soup.find('div', class_='toc-content')
    if toc_content_div:
        toc_content_div.clear()
        toc_soup = BeautifulSoup(toc_html, 'html.parser')
        toc_content_div.append(toc_soup)
        print(f"TOC inserted ({len(toc_data)} headings)")
    else:
        print("Warning: .toc-content div not found, TOC not inserted")
        return False

    try:
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(str(soup))
        return True
    except Exception as e:
        print(f"Error saving HTML file: {e}")
        return False


def insert_toc_with_regex(html_file):
    """Insert TOC using regex (fallback)"""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading HTML file: {e}")
        return False

    heading_pattern = r'<(h[1-6])(?:[^>]*)>(.*?)</\1>'
    headings = re.findall(heading_pattern, html_content, re.IGNORECASE | re.DOTALL)

    if not headings:
        print("No headings found for TOC")
        return False

    toc_html = '<ul>\n'
    for i, (tag, text) in enumerate(headings):
        level = int(tag[1])
        clean_text = re.sub(r'<[^>]+>', '', text).strip()
        heading_id = f"heading-{i+1}"

        old_heading = f'<{tag}>{text}</{tag}>'
        new_heading = f'<{tag} id="{heading_id}">{text}</{tag}>'
        html_content = html_content.replace(old_heading, new_heading, 1)

        if level > 1:
            for _ in range(level - 1):
                toc_html += '  '
        toc_html += f'<li><a href="#{heading_id}">{clean_text}</a></li>\n'

    toc_html += '</ul>\n'

    toc_content_pattern = r'(<div[^>]*class="toc-content[^"]*"[^>]*>).*?(</div>)'
    if re.search(toc_content_pattern, html_content, re.DOTALL):
        html_content = re.sub(
            toc_content_pattern,
            r'\1' + toc_html + r'\2',
            html_content,
            flags=re.DOTALL
        )
        print(f"TOC inserted ({len(headings)} headings)")
    else:
        print("Warning: .toc-content div not found")
        return False

    try:
        with open(html_file, 'w', encoding='utf-8') as f:
            f.write(html_content)
        return True
    except Exception as e:
        print(f"Error saving HTML file: {e}")
        return False


def add_toc(temp_dir):
    """Add TOC to book.html"""
    print("=== Adding Table of Contents ===")

    book_file = os.path.join(temp_dir, 'book.html')
    if not os.path.exists(book_file):
        print("Warning: book.html not found, skipping TOC")
        return False

    if BS4_AVAILABLE:
        return insert_toc_with_bs4(book_file)
    else:
        return insert_toc_with_regex(book_file)


# =============================================================================
# Step 7: Generate DOCX/EPUB/PDF with error transparency
# =============================================================================

def generate_format(html_file, temp_dir, output_ext, lang_attr, cover=None):
    """Generate a specific format using calibre_html_publish.py"""
    output_file = os.path.join(temp_dir, f"book{output_ext}")
    cover = cover if output_ext == '.epub' else None
    if cover and not os.path.isfile(cover):
        print(f"Cover image not found: {cover}")
        return None

    if os.path.exists(output_file):
        output_mtime = os.path.getmtime(output_file)

        # Check if source HTML is newer
        html_newer = os.path.getmtime(html_file) > output_mtime

        # Check if any image asset is newer (Calibre embeds these)
        images_newer = False
        images_dir = os.path.join(temp_dir, 'images')
        if os.path.isdir(images_dir):
            for img in os.listdir(images_dir):
                img_path = os.path.join(images_dir, img)
                if os.path.isfile(img_path) and os.path.getmtime(img_path) > output_mtime:
                    images_newer = True
                    break

        cover_newer = bool(cover and os.path.getmtime(cover) > output_mtime)

        if not html_newer and not images_newer and not cover_newer:
            file_size = os.path.getsize(output_file)
            print(f"Skipping {output_ext} - already exists and up to date ({file_size:,} bytes)")
            return output_file
        else:
            reasons = []
            if html_newer:
                reasons.append("source HTML changed")
            if images_newer:
                reasons.append("image assets changed")
            if cover_newer:
                reasons.append("cover image changed")
            print(f"Rebuilding {output_ext} - {', '.join(reasons)}")

    publish_script = os.path.join(SCRIPT_DIR, "calibre_html_publish.py")
    if not os.path.exists(publish_script):
        print(f"calibre_html_publish.py not found at: {publish_script}")
        return None

    try:
        cmd = ["python3", publish_script, html_file, "-o", output_file, "--lang", lang_attr]
        if cover:
            cmd.extend(["--cover", cover])
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)

        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file)
            return output_file
        else:
            print(f"Failed to generate {output_ext}")
            if result.stdout:
                print(f"  stdout: {result.stdout[-500:]}")
            return None
    except subprocess.CalledProcessError as e:
        print(f"Failed to generate {output_ext}")
        if e.stdout:
            print(f"  stdout: {e.stdout[-500:]}")
        if e.stderr:
            print(f"  stderr: {e.stderr[-500:]}")
        return None
    except Exception as e:
        print(f"Error generating {output_ext}: {e}")
        return None


def _metadata_title(title):
    """Drop the 《》 a Chinese title is often wrapped in.

    Readers show the metadata title in page headers and library views, where
    书名号 look wrong; they belong around a title quoted inside prose.
    """
    title = title.strip()
    if title.startswith('《') and title.endswith('》') and title.count('《') == 1:
        title = title[1:-1].strip()
    return title


def generate_formats(temp_dir, lang_attr, cover=None):
    """Generate DOCX, EPUB, and PDF with result summary"""
    print("=== Generating output formats ===")

    html_file = os.path.join(temp_dir, "book_doc.html")
    if not os.path.exists(html_file):
        html_files = glob.glob(os.path.join(glob.escape(temp_dir), "*.html"))
        if html_files:
            html_file = max(html_files, key=os.path.getmtime)
        else:
            print("No HTML files found for format generation")
            return

    results = {}
    for ext in ['.docx', '.epub', '.pdf']:
        result = generate_format(html_file, temp_dir, ext, lang_attr, cover=cover)
        if not result and ext == '.pdf':
            # Calibre's PDF renderer (Qt WebEngine) fails intermittently on
            # macOS with GPU backend errors; a second run usually succeeds.
            print("Retrying .pdf once")
            result = generate_format(html_file, temp_dir, ext, lang_attr, cover=cover)
        if result:
            file_size = os.path.getsize(result)
            results[ext] = ('OK', f"{file_size:,} bytes")
        else:
            results[ext] = ('FAILED', '')

    # Print summary table
    print("\nFormat results:")
    has_failures = False
    for ext, (status, detail) in results.items():
        if status == 'OK':
            print(f"  {ext}: {status} ({detail})")
        else:
            print(f"  {ext}: {status}")
            has_failures = True

    return not has_failures


# Files the pipeline keeps in temp_dir under the name the HTML alias of these
# stems would take.
_RESERVED_EXPORT_STEMS = {
    "book_doc": "book_doc.html",
    "input": "input.html",
    "output": "output.html",
}

# What --delete-outputs removes besides the export copies; the next build
# regenerates all of them.
BUILD_OUTPUTS = ("book.html", "book_doc.html", "output.html", "book.docx", "book.epub", "book.pdf")

EXPORT_COPIES_FILE = "export_copies.json"

# The usual file name limit (NAME_MAX), in bytes.
_MAX_FILE_NAME_BYTES = 255


def _validate_export_name(name, label='--export-name'):
    """Validate an export filename stem. Keep aliases inside temp_dir."""
    if not name or not name.strip():
        raise ValueError(f"{label} must not be empty")
    if '\x00' in name or '/' in name or '\\' in name:
        raise ValueError(f"{label} must be a filename stem, not a path")
    stem = name.strip()
    try:
        size = len(os.fsencode(stem))
    except UnicodeError:
        raise ValueError(f"{label} contains characters a file name cannot hold") from None
    # Each copy name must fit in a file name. Fail here, before the build records the
    # copies: a copy that fails halfway leaves a name --delete-outputs cannot delete.
    longest_suffix = max(_export_copy_names("").values(), key=len)
    room = _MAX_FILE_NAME_BYTES - len(longest_suffix)
    if size > room:
        raise ValueError(f"{label} is too long: it takes {size} bytes in UTF-8, so its "
                         f"{longest_suffix} copy would need a {size + len(longest_suffix)}-byte file "
                         f"name, and a file name can take {_MAX_FILE_NAME_BYTES}; shorten it to {room} "
                         f"bytes or fewer, such as {room // 3} CJK characters")
    # Check case-insensitively so the same names are safe on macOS/Windows.
    reserved = _RESERVED_EXPORT_STEMS.get(stem.casefold())
    if reserved:
        raise ValueError(f"{label} would overwrite {reserved}, which the pipeline keeps "
                         f"in the temp dir; choose another name")
    return stem


def _export_copy_names(stem):
    """Map each canonical output to the name of its copy for this stem."""
    return {
        "book.html": f"{stem}.html",
        "book_doc.html": f"{stem}_doc.html",
        "book.docx": f"{stem}.docx",
        "book.epub": f"{stem}.epub",
        "book.pdf": f"{stem}.pdf",
    }


def _export_copy_path(temp_dir, name):
    """Return temp_dir/name if some valid export name gives a copy called name, else None."""
    def made_with(suffix):
        stem = name[:-len(suffix)]
        try:
            return name.endswith(suffix) and _validate_export_name(stem) == stem
        except ValueError:
            return False

    if not any(made_with(suffix) for suffix in _export_copy_names("").values()):
        return None
    path = os.path.join(temp_dir, name)
    # A Windows drive prefix such as "D:" would still lead out of temp_dir.
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(temp_dir):
        return None
    return path


def _read_export_copies(temp_dir):
    """Return the names listed in export_copies.json, or None if temp_dir has no such file.

    Raises ValueError if the file is there but unusable: unreadable, not the
    JSON the build writes, or listing a name that no export copy in temp_dir has.
    """
    path = os.path.join(temp_dir, EXPORT_COPIES_FILE)
    try:
        with open(path, encoding='utf-8', errors='surrogateescape') as f:
            data = json.load(f)
    except FileNotFoundError:
        if os.path.lexists(path):
            raise ValueError("it is a symlink to a missing file") from None
        return None
    except (OSError, ValueError) as e:
        raise ValueError(str(e)) from None
    names = data.get("copies") if isinstance(data, dict) else None
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError('"copies" is not a list of file names')
    bad = [name for name in names if not _export_copy_path(temp_dir, name)]
    if bad:
        raise ValueError("it lists names no export copy has: " + ", ".join(map(repr, bad)))
    return names


def _unusable_record(reason):
    """Explain an export_copies.json that _read_export_copies() rejected."""
    return (f"{EXPORT_COPIES_FILE} cannot be used ({reason}). It lists the export copies of "
            f"earlier builds; repair it, or delete it so that --delete-outputs deletes the "
            f"copies named after export_name.txt instead.")


def _write_export_copies(temp_dir, names):
    path = os.path.join(temp_dir, EXPORT_COPIES_FILE)
    fd, tmp_path = tempfile.mkstemp(prefix='.export_copies.', suffix='.json', dir=temp_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', errors='surrogateescape') as f:
            json.dump({"copies": names}, f, ensure_ascii=False, indent=2)
            f.write('\n')
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def export_named_aliases(temp_dir, export_name):
    """Copy canonical outputs to optional user-facing filenames.

    Canonical artifacts remain untouched. The alias names use export_name as a
    filename stem, with book_doc.html receiving a _doc suffix to avoid colliding
    with the web HTML alias. Their names are added to export_copies.json before
    the files are written, so --delete-outputs still finds the copies of an
    earlier export name after it changes. If that file is there but unusable,
    raises ValueError and writes nothing.
    """
    stem = _validate_export_name(export_name)
    pending = []
    for src_name, dst_name in _export_copy_names(stem).items():
        src = os.path.join(temp_dir, src_name)
        if not os.path.exists(src):
            continue
        dst = os.path.join(temp_dir, dst_name)
        if os.path.exists(dst) and os.path.samefile(src, dst):
            continue
        pending.append((src, dst, dst_name))
    if pending:
        recorded = [name for name in _read_export_copies(temp_dir) or []
                    if os.path.lexists(os.path.join(temp_dir, name))]
        _write_export_copies(temp_dir, list(dict.fromkeys(recorded + [name for _, _, name in pending])))
    copied = []
    for src, dst, dst_name in pending:
        shutil.copy2(src, dst)
        copied.append(dst_name)
    return copied


def _copies_named_in_export_name_file(temp_dir):
    """Copy names for a temp dir built before export_copies.json existed.

    Those builds took the stem from `--export-name "$(cat export_name.txt)"`,
    which _validate_export_name() strips, so the file is read the same way:
    newline='' keeps a carriage return inside the name. Returns [] if there is
    no export_name.txt and None if it is blank, as no build makes copies then;
    raises ValueError if it is there but names no copies.
    """
    path = os.path.join(temp_dir, 'export_name.txt')
    try:
        with open(path, encoding='utf-8', errors='surrogateescape', newline='') as f:
            text = f.read()
    except FileNotFoundError:
        if os.path.lexists(path):
            raise ValueError("export_name.txt is a symlink to a missing file") from None
        return []
    except OSError as e:
        raise ValueError(f"export_name.txt cannot be read ({e})") from None
    if not text.strip():
        return None
    try:
        stem = _validate_export_name(text)
    except ValueError as e:
        raise ValueError(f"export_name.txt holds no usable export name ({e})") from None
    names = list(_export_copy_names(stem).values())
    # Only a drive prefix such as "D:" on Windows leads a valid stem out of temp_dir.
    if not all(_export_copy_path(temp_dir, name) for name in names):
        raise ValueError("export_name.txt names copies outside the temp dir")
    print(f"No {EXPORT_COPIES_FILE}; deleting the copies named after export_name.txt")
    return names


def _delete_file(path):
    """Delete path if it is a regular file or a symlink, never a symlink's target.

    Returns False when nothing is there. Raises OSError for anything else.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return False
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
        raise OSError("not a regular file")
    os.unlink(path)
    return True


def _conversion_problem(temp_dir):
    """Why temp_dir does not look like a temp dir convert.py split a book into, or None.

    Checks the shape of the manifest.json that create_manifest() writes and
    nothing more: its three fields, at least one chunk and as many as
    chunk_count says, a source_hash of 64 lowercase hex digits, orders 1 to
    N, the entry of order n naming convert.py's n-th chunk file, and exactly
    those chunk files in temp_dir, as regular files. No hash is compared with
    a file, as chunks may change after the conversion, so a made-up manifest
    of that shape passes.
    """
    try:
        manifest = load_manifest(temp_dir)
    except (OSError, ValueError) as e:
        return str(e)
    if manifest is None:
        return "there is none"
    if set(manifest) != {"chunk_count", "source_hash", "chunks"}:
        return f"it has the fields {', '.join(sorted(manifest))}, not chunk_count, chunks and source_hash"
    chunks = manifest["chunks"]
    if not chunks or type(manifest["chunk_count"]) is not int or manifest["chunk_count"] != len(chunks):
        return "it lists no chunks, or not as many as its chunk_count says"
    if not isinstance(manifest["source_hash"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["source_hash"]):
        return "its source_hash is not 64 lowercase hex digits"
    if sorted(chunk["order"] for chunk in chunks) != list(range(1, len(chunks) + 1)):
        return "its orders do not run from 1 without a gap"
    listed = []
    for n, chunk in enumerate(sorted(chunks, key=lambda chunk: chunk["order"]), 1):
        name = chunk_file_name(nth_chunk_id(n))
        if chunk["source_file"] != name:
            return f"its entry of order {n} is {chunk['source_file']}, not {name}"
        listed.append(name)
    try:
        on_disk = list_chunk_files(temp_dir)
    except (OSError, ValueError) as e:
        return str(e)
    on_disk_names, listed_names = set(on_disk), set(listed)
    missing = [name for name in listed if name not in on_disk_names]
    if missing:
        return f"{missing[0]}, which it lists, is not there as a file"
    unlisted = [name for name in on_disk if name not in listed_names]
    if unlisted:
        return f"it does not list {unlisted[0]}, which is there"
    return None


def delete_outputs(temp_dir):
    """Delete the outputs and export copies of earlier builds, so a rebuild regenerates them.

    Deletes BUILD_OUTPUTS and the copies listed in export_copies.json, or for
    an older temp dir without that record, the copies named after
    export_name.txt. Only regular files and symlinks directly inside temp_dir
    go; chunks, translations and the other intermediates stay. Every name is
    checked before the first file goes, and if the record or export_name.txt
    is there but unusable, nothing is deleted. Returns False then, or if a
    file could not be deleted.
    """
    print("=== Deleting build outputs ===")
    try:
        recorded = _read_export_copies(temp_dir)
    except ValueError as e:
        print(f"Error: {_unusable_record(e)} Nothing was deleted.")
        return False
    if recorded is None:
        try:
            copies = _copies_named_in_export_name_file(temp_dir)
        except ValueError as e:
            print(f"Error: {e}. Without {EXPORT_COPIES_FILE}, that file is how the export copies "
                  f"of earlier builds are found: write the export name they were made with to "
                  f"export_name.txt, or leave export_name.txt empty if there are none. "
                  f"Nothing was deleted.")
            return False
        if copies is None:
            # An empty export_name.txt means no copies only in what looks like a translate-book temp dir.
            problem = _conversion_problem(temp_dir)
            if problem:
                print(f"Error: export_name.txt is empty and {temp_dir} holds no usable manifest.json "
                      f"({problem}), so it is not known to be a translate-book temp directory. "
                      f"Nothing was deleted.")
                return False
            copies = []
    else:
        copies = recorded

    deleted, left = [], []
    for name in list(BUILD_OUTPUTS) + [n for n in dict.fromkeys(copies) if n not in BUILD_OUTPUTS]:
        try:
            if _delete_file(os.path.join(temp_dir, name)):
                deleted.append(name)
        except OSError as e:
            print(f"Error: could not delete {name!r}: {e}")
            left.append(name)

    if deleted:
        print(f"Deleted {len(deleted)} file(s):")
        for name in deleted:
            print(f"  {name!r}")
    else:
        print("No build outputs to delete.")

    ok = not left
    if recorded is not None:
        try:
            _write_export_copies(temp_dir, [name for name in left if name not in BUILD_OUTPUTS])
        except OSError as e:
            print(f"Error: could not update {EXPORT_COPIES_FILE}: {e}")
            ok = False
    if ok:
        print("Nothing was built; run the build command to rebuild.")
    else:
        print("Nothing was built. Fix the errors above and run --delete-outputs again first.")
    return ok


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Merge translated pages and build final outputs')
    parser.add_argument('--temp-dir', required=True, help='Temp directory path')
    parser.add_argument('--title', default=None, help='Translated book title (override config)')
    parser.add_argument('--author', default=None, help='Author name (override config)')
    parser.add_argument('--lang', default=None, help='Output language code (override config)')
    parser.add_argument('--cover', default=None, help='Cover image path for EPUB output')
    parser.add_argument('--export-name', default=None, help='Optional filename stem for exported alias copies')
    parser.add_argument('--cleanup', action='store_true', help='Remove intermediate artifacts after successful build')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--headings', action='store_true',
                      help='List the headings of output_chunk*.md, skipping fenced code, and exit without building')
    mode.add_argument('--delete-outputs', action='store_true',
                      help='Delete book.html, book_doc.html, output.html, book.docx, book.epub, book.pdf and '
                           'the export copies, keeping chunks and translations, and exit without building')

    args = parser.parse_args()
    temp_dir = args.temp_dir

    if not os.path.isdir(temp_dir):
        print(f"Error: Temp directory not found: {temp_dir}")
        sys.exit(1)

    if not args.delete_outputs:
        try:
            load_manifest(temp_dir)
            list_chunk_files(temp_dir)
            list_chunk_files(temp_dir, 'output')
        except (OSError, ValueError) as e:
            print(f"Error: {e}")
            sys.exit(1)

    if args.headings:
        rows = heading_outline(temp_dir)
        print('\n'.join(rows) if rows else "No headings in output_chunk*.md")
        return

    if args.delete_outputs:
        if not delete_outputs(temp_dir):
            sys.exit(1)
        return

    try:
        title_override = args.title or read_name_file(temp_dir, TITLE_FILE)
        author_override = args.author or read_name_file(temp_dir, AUTHOR_FILE)
        export_name_given = args.export_name or read_name_file(temp_dir, EXPORT_NAME_FILE)
        cover = args.cover or stored_request_cover(temp_dir)
    except (OSError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    if cover:
        if not os.path.isfile(cover):
            print(f"Error: Cover image not found: {cover}")
            sys.exit(1)
        cover = os.path.abspath(cover)

    export_name = None
    if export_name_given:
        try:
            export_name = _validate_export_name(
                export_name_given, '--export-name' if args.export_name else EXPORT_NAME_FILE)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        try:
            _read_export_copies(temp_dir)
        except ValueError as e:
            print(f"Error: {_unusable_record(e)} Nothing was built.")
            sys.exit(1)

    # Load config as base, CLI args override
    config = load_config(temp_dir)

    lang_code = args.lang or config.get('output_lang', 'zh')
    lang_cfg = get_lang_config(lang_code)

    title = _metadata_title(title_override or config.get('original_title', 'Translated Book'))
    author = author_override or config.get('creator', 'Unknown Author')

    print(f"=== Merge and Build ===")
    print(f"Temp directory: {temp_dir}")
    print(f"Title: {title}")
    print(f"Author: {author}")
    print(f"Language: {lang_code} (attr: {lang_cfg['lang_attr']})")

    # Step 4: Merge
    if not merge_markdown_files(temp_dir):
        sys.exit(1)

    # Step 5: Convert to HTML
    if not convert_md_to_html(temp_dir, title, lang_cfg, author):
        sys.exit(1)

    # Step 6: Add TOC
    add_toc(temp_dir)

    # Step 7: Generate formats
    all_formats_ok = generate_formats(temp_dir, lang_cfg['lang_attr'], cover=cover)

    if export_name:
        if all_formats_ok:
            aliases = export_named_aliases(temp_dir, export_name)
            if aliases:
                print("\nExport aliases:")
                for name in aliases:
                    print(f"  {name}")
        else:
            print("\nSkipping export aliases — some formats failed.")

    print("\n=== Build Complete ===")
    print(f"All outputs saved to: {temp_dir}")

    # List generated files
    for ext in ['book.html', 'book_doc.html', 'book.docx', 'book.epub', 'book.pdf']:
        filepath = os.path.join(temp_dir, ext)
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
            print(f"  {ext}: {size:,} bytes")

    # Cleanup intermediate artifacts if requested (skip if any format failed)
    if args.cleanup:
        if all_formats_ok:
            cleanup_intermediate_files(temp_dir)
        else:
            print("\nSkipping cleanup — some formats failed. Intermediate files kept for diagnosis/retry.")


def cleanup_intermediate_files(temp_dir):
    """Remove intermediate artifacts, keeping only final outputs."""
    print("\n=== Cleaning up intermediate files ===")

    removed = []

    # Remove chunk*.md and output_chunk*.md
    for pattern in ['chunk*.md', 'output_chunk*.md']:
        for filepath in glob.glob(os.path.join(glob.escape(temp_dir), pattern)):
            os.remove(filepath)
            removed.append(os.path.basename(filepath))

    # Remove specific intermediate files
    for name in ['input.html', 'input.md', 'output.html']:
        filepath = os.path.join(temp_dir, name)
        if os.path.exists(filepath):
            os.remove(filepath)
            removed.append(name)

    if removed:
        print(f"Removed {len(removed)} intermediate file(s):")
        # Summarize chunk files instead of listing each one
        chunk_files = [f for f in removed if 'chunk' in f]
        other_files = [f for f in removed if 'chunk' not in f]
        if chunk_files:
            print(f"  {len(chunk_files)} chunk files (chunk*.md, output_chunk*.md)")
        for f in other_files:
            print(f"  {f}")
    else:
        print("No intermediate files to remove.")


if __name__ == "__main__":
    main()
