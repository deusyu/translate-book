#!/usr/bin/env python3
"""
convert.py - Convert PDF/DOCX/EPUB to Markdown chunks via Calibre HTMLZ
Combines the original steps 1-2 into a single script.

Markdown input (.md/.markdown) skips Calibre entirely. Use it for PDFs that
Calibre's coordinate-heuristic reflow mangles (formulas, tables, multi-column
layouts): extract them with a layout-aware parser such as MinerU or Marker
first, then pass the resulting Markdown here.
"""

import os
import sys
import subprocess
import zipfile
import shutil
import tempfile
import argparse
import base64
import binascii
import bisect
import glob
import hashlib
import json
import re
import urllib.parse

from manifest import create_manifest, file_hash


def find_calibre_convert():
    """Find ebook-convert command from Calibre installation"""
    possible_paths = [
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
        "ebook-convert"  # If in PATH
    ]

    for path in possible_paths:
        try:
            result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"Found Calibre ebook-convert: {path}")
                return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return None


def convert_to_htmlz(input_file, htmlz_file, calibre_path):
    """Convert input file to HTMLZ using Calibre"""
    try:
        print(f"Converting {input_file} to HTMLZ...")
        cmd = [calibre_path, input_file, htmlz_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode == 0:
            file_size = os.path.getsize(htmlz_file)
            print(f"HTMLZ conversion successful: {htmlz_file} ({file_size} bytes)")
            return True
        else:
            print(f"HTMLZ conversion failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print("HTMLZ conversion timed out")
        return False
    except Exception as e:
        print(f"HTMLZ conversion error: {e}")
        return False


def extract_metadata_from_htmlz(extract_dir):
    """Extract metadata from metadata.opf file in HTMLZ"""
    try:
        import xml.etree.ElementTree as ET

        metadata_file = None
        for root, dirs, files in os.walk(extract_dir):
            for file in files:
                if file.lower() == 'metadata.opf':
                    metadata_file = os.path.join(root, file)
                    break
            if metadata_file:
                break

        if not metadata_file:
            return {}

        tree = ET.parse(metadata_file)
        root = tree.getroot()

        namespaces = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
            'dcterms': 'http://purl.org/dc/terms/'
        }

        metadata = {}

        title_elem = root.find('.//dc:title', namespaces)
        if title_elem is not None and title_elem.text:
            metadata['title'] = title_elem.text.strip()

        creator_elem = root.find('.//dc:creator', namespaces)
        if creator_elem is not None and creator_elem.text:
            metadata['creator'] = creator_elem.text.strip()

        publisher_elem = root.find('.//dc:publisher', namespaces)
        if publisher_elem is not None and publisher_elem.text:
            metadata['publisher'] = publisher_elem.text.strip()

        language_elem = root.find('.//dc:language', namespaces)
        if language_elem is not None and language_elem.text:
            metadata['language'] = language_elem.text.strip()

        return metadata

    except Exception as e:
        print(f"Warning: Error extracting metadata: {e}")
        return {}


def _html_title(html_file):
    """The <title> of an HTML file, or None. Calibre copies the book title there."""
    try:
        with open(html_file, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(65536)
    except OSError:
        return None
    match = re.search(r'<title[^>]*>(.*?)</title>', head, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    import html as html_lib
    title = html_lib.unescape(re.sub(r'\s+', ' ', match.group(1))).strip()
    return title or None


def extract_htmlz(htmlz_file, temp_dir):
    """Extract HTMLZ file and return paths to HTML and images"""
    try:
        with zipfile.ZipFile(htmlz_file, 'r') as zip_file:
            zip_file.extractall(temp_dir)

        html_file = None
        images_dir = None

        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file.lower() in ['index.html', 'index.htm']:
                    html_file = os.path.join(root, file)
                    break
            for dir_name in dirs:
                if dir_name.lower() in ['images', 'image', 'pics', 'pictures']:
                    images_dir = os.path.join(root, dir_name)
                    break

        if not html_file:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file.lower().endswith(('.html', '.htm')):
                        html_file = os.path.join(root, file)
                        break
                if html_file:
                    break

        return html_file, images_dir

    except Exception as e:
        print(f"Error extracting HTMLZ: {e}")
        return None, None


def build_temp_dir(input_file, temp_root=None):
    """Return the working directory path for an input file.

    Default is the historical cwd-local {book_name}_temp/. When temp_root is
    provided, only the root changes; the leaf directory name stays compatible.
    """
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    leaf = f"{base_name}_temp"
    if temp_root:
        return os.path.join(temp_root, leaf)
    return leaf


SOURCE_FINGERPRINT_FILE = "source_fingerprint.json"

# Files whose presence means the temp dir carries conversion state derived
# from some source book — and must therefore be tied to the current one.
_SOURCE_CACHE_MARKERS = (
    "input.html",
    "input.md",
    "manifest.json",
    "run_state.json",
    "glossary.json",
    "output.md",
)


def source_fingerprint(input_file):
    """Stable identity of the exact source bytes being converted."""
    return {
        "path": os.path.realpath(input_file),
        "size": os.path.getsize(input_file),
        "sha256": file_hash(input_file),
    }


def _write_source_fingerprint(temp_dir, fingerprint):
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(fingerprint, f, indent=2, sort_keys=True)
        f.write('\n')


def _load_source_fingerprint(temp_dir):
    path = os.path.join(temp_dir, SOURCE_FINGERPRINT_FILE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _has_reusable_source_cache(temp_dir):
    if not os.path.isdir(temp_dir):
        return False
    for name in _SOURCE_CACHE_MARKERS:
        if os.path.exists(os.path.join(temp_dir, name)):
            return True
    for pattern in ('chunk*.md', 'output_chunk*.md'):
        if glob.glob(os.path.join(temp_dir, pattern)):
            return True
    return False


def check_source_cache(temp_dir, current_fingerprint):
    """Compare the temp dir's cached source identity against the current input.

    Returns (status, message):
      (None, None)        — fresh temp dir, or fingerprint matches; proceed.
      ('adopt', message)  — cache predates fingerprinting; adopt it and record
                            the current fingerprint (trust-on-first-use, keeps
                            pre-upgrade temp dirs resumable).
      ('mismatch', message) — cache was built from different source bytes;
                              the caller must abort.

    Only content identity (sha256 + size) is compared — moving or renaming the
    source file does not invalidate the cache.
    """
    if not _has_reusable_source_cache(temp_dir):
        return None, None

    stored = _load_source_fingerprint(temp_dir)
    if stored is None:
        return 'adopt', (
            f"{temp_dir}/ contains cached conversion artifacts without a "
            f"{SOURCE_FINGERPRINT_FILE} (created by an older version). "
            f"Assuming they were built from the current input file. "
            f"If you replaced the source file, delete {temp_dir}/ and re-run."
        )

    for key in ("sha256", "size"):
        if stored.get(key) != current_fingerprint.get(key):
            return 'mismatch', (
                f"{temp_dir}/ was created from different source bytes "
                f"(cached sha256 {str(stored.get('sha256', ''))[:12]}..., "
                f"current {current_fingerprint['sha256'][:12]}...). "
                f"Reusing its chunks would translate the wrong book."
            )
    return None, None


def _abort_on_source_cache_mismatch(status, message, temp_dir):
    if status == 'mismatch':
        print(f"Error: {message}")
        print(f"Delete {temp_dir}/ (or use a fresh --temp-root) and re-run.")
        sys.exit(1)
    if status == 'adopt':
        print(f"Warning: {message}")


def setup_temp_directory(input_file, html_file, images_dir, temp_root=None):
    """Setup temp directory with HTML and images"""
    try:
        temp_dir = build_temp_dir(input_file, temp_root)
        os.makedirs(temp_dir, exist_ok=True)

        input_html = os.path.join(temp_dir, "input.html")
        if os.path.exists(input_html):
            print(f"Skipping HTML copy - input.html already exists")
        else:
            shutil.copy2(html_file, input_html)
            print(f"Copied HTML to: {input_html}")

        if images_dir and os.path.exists(images_dir):
            target_images_dir = os.path.join(temp_dir, "images")
            if os.path.exists(target_images_dir):
                print(f"Skipping images copy - images directory already exists")
            else:
                shutil.copytree(images_dir, target_images_dir)
                print(f"Copied images to: {target_images_dir}")

        return temp_dir
    except Exception as e:
        print(f"Error setting up temp directory: {e}")
        return None


# =============================================================================
# Structural cleanup on pandoc's JSON AST (Calibre HTML -> Markdown)
# =============================================================================

_INLINE_TYPES = frozenset({
    "Str", "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
    "SmallCaps", "Quoted", "Cite", "Code", "Space", "SoftBreak", "LineBreak",
    "Math", "RawInline", "Link", "Image", "Note", "Span",
})
_SPACE_TYPES = frozenset({"Space", "SoftBreak", "LineBreak"})
_FORMATTING_TYPES = frozenset({
    "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps",
})
# Digits or note symbols, optionally bracketed and/or followed by a period:
# "1", "[12]", "(3)", "4.", "*", "†".
_FOOTNOTE_MARKER_RE = re.compile(r'^[\[(]?(\d{1,4}|[*†‡§¶‖]{1,3})[\])]?\.?$')
_NOTE_MARKER_TRAILERS = frozenset({".", ")", "]", ":"})


def _map_inline_lists(node, fn, top_down=False):
    """Rewrite every inline list of a pandoc JSON AST in place via fn(list) -> list."""
    if isinstance(node, dict):
        if isinstance(node.get("c"), list):
            _map_inline_lists(node["c"], fn, top_down)
        return
    if not isinstance(node, list):
        return
    is_inline_list = bool(node) and all(
        isinstance(x, dict) and x.get("t") in _INLINE_TYPES for x in node
    )
    if is_inline_list and top_down:
        node[:] = fn(node)
    for item in node:
        _map_inline_lists(item, fn, top_down)
    if is_inline_list and not top_down:
        node[:] = fn(node)


def _inline_text(inlines):
    parts = []
    for x in inlines:
        t = x.get("t")
        if t == "Str":
            parts.append(x["c"])
        elif t in _SPACE_TYPES:
            parts.append(" ")
        elif t in _FORMATTING_TYPES:
            parts.append(_inline_text(x["c"]))
        elif t in ("Span", "Link", "Quoted"):
            parts.append(_inline_text(x["c"][1]))
        elif t == "Code":
            parts.append(x["c"][1])
    return "".join(parts)


def _is_anchor(inline):
    """An element that renders nothing, e.g. an id-only <a id="page_12"/>."""
    if inline.get("t") not in ("Span", "Link"):
        return False
    return all(_is_anchor(x) for x in inline["c"][1])


def _is_filler(inline):
    """Whitespace, or a span/link holding nothing but whitespace and anchors."""
    if inline.get("t") in _SPACE_TYPES:
        return True
    if inline.get("t") not in ("Span", "Link"):
        return False
    return all(_is_filler(x) for x in inline["c"][1])


def _footnote_marker(inlines):
    match = _FOOTNOTE_MARKER_RE.match(_inline_text(inlines).strip())
    return match.group(1) if match else None


def _footnote_link(inline):
    """Return (link, anchor ids) when inline is a footnote-marker link to an in-book target.

    The link may sit alone or inside <sup>/<span> wrappers; the ids are every
    anchor a note's back-link may point at (the link's own id and its wrapping
    spans' ids).
    """
    t = inline.get("t")
    if t == "Link":
        attr, content, (url, _title) = inline["c"]
        if url.startswith("#") and len(url) > 1 and _footnote_marker(content):
            return inline, [attr[0]] if attr[0] else []
        return None
    if t in ("Superscript", "Span"):
        content = inline["c"] if t == "Superscript" else inline["c"][1]
        solid = [x for x in content if not _is_filler(x)]
        if len(solid) == 1:
            found = _footnote_link(solid[0])
            if found and t == "Span" and inline["c"][0][0]:
                found[1].append(inline["c"][0][0])
            return found
    return None


def _index_footnote_refs(blocks):
    """Map anchor id -> footnote reference link, for marker links inside running text.

    A marker link that opens its line is a note's back-link or a contents
    entry, never a reference, so it is not indexed.
    """
    refs = {}

    def visit(inlines):
        after_text = False
        for x in inlines:
            if after_text:
                found = _footnote_link(x)
                if found:
                    link, anchor_ids = found
                    for anchor_id in anchor_ids:
                        refs.setdefault(anchor_id, link)
            if not _is_filler(x):
                after_text = True
        return inlines

    _map_inline_lists(blocks, visit)
    return refs


def _note_body(block, refs):
    """Return (reference link, note inlines) when block opens with a back-link to a known reference."""
    if block.get("t") not in ("Para", "Plain"):
        return None
    inlines = block["c"]
    i = 0
    while i < len(inlines) and _is_filler(inlines[i]):
        i += 1
    found = _footnote_link(inlines[i]) if i < len(inlines) else None
    if not found:
        return None
    backlink = found[0]
    ref = refs.get(backlink["c"][2][0][1:])
    if ref is None or _footnote_marker(ref["c"][1]) != _footnote_marker(backlink["c"][1]):
        return None
    body = inlines[i + 1:]
    while body and (
        _is_filler(body[0])
        or (body[0].get("t") == "Str" and body[0]["c"] in _NOTE_MARKER_TRAILERS)
    ):
        body = body[1:]
    return (ref, body) if body else None


def _collect_notes(blocks, refs, found):
    """Append (ref, container, block, note blocks) for note paragraphs in body/div block lists."""
    for block in blocks:
        if block.get("t") == "Div":
            attr, inner = block["c"]
            solid = [b for b in inner if not (
                b.get("t") in ("Para", "Plain") and all(_is_filler(x) for x in b["c"])
            )]
            hit = _note_body(solid[0], refs) if solid else None
            # A div is the whole note only when the reference points at the div itself.
            if hit and attr[0] and hit[0]["c"][2][0] == "#" + attr[0]:
                found.append((hit[0], blocks, block, [{"t": "Para", "c": hit[1]}] + solid[1:]))
                continue
            _collect_notes(inner, refs, found)
            continue
        hit = _note_body(block, refs)
        if hit:
            found.append((hit[0], blocks, block, [{"t": "Para", "c": hit[1]}]))


def _pandoc_notes(node):
    """Return the block lists of the Note elements found anywhere in node."""
    found = []
    if isinstance(node, dict):
        if node.get("t") == "Note":
            found.append(node["c"])
        if isinstance(node.get("c"), list):
            found.extend(_pandoc_notes(node["c"]))
    elif isinstance(node, list):
        for item in node:
            found.extend(_pandoc_notes(item))
    return found


def convert_footnotes(blocks):
    """Turn EPUB-style footnotes into pandoc notes. Returns the number converted.

    A reference is a short marker link in running text (`<a href="#n1"><sup>1</sup></a>`,
    `<sup><a>1</a></sup>`, `[1]`); its note is the paragraph (or the div the
    reference points at) that opens with a back-link to that reference with the
    same marker. Calibre renames ids and classes, so pairing relies only on this
    two-way link structure. Pandoc's Markdown writer then numbers the notes
    across the whole book, so labels stay unique when per-chapter numbering
    restarts.
    """
    refs = _index_footnote_refs(blocks)
    if not refs:
        return 0
    candidates = []
    _collect_notes(blocks, refs, candidates)

    notes = {}
    for ref, container, block, content in candidates:
        notes.setdefault(id(ref), (container, block, content))
    # Pandoc before 3.13 drops a note written inside another note, so nothing
    # that would end up nested is converted: a reference inside a note (one
    # pandoc already read, e.g. a DPUB-ARIA note, or one converted here) stays
    # inline, and a note whose text holds a pandoc note stays a paragraph.
    nested = set()
    for note in _pandoc_notes(blocks) + [[block] for _container, block, _content in notes.values()]:
        nested.update(id(link) for link in _index_footnote_refs(note).values())
    for key, (_container, _block, content) in notes.items():
        if _pandoc_notes(content):
            nested.add(key)
    for key in nested:
        notes.pop(key, None)

    used = set()

    def replace(inlines):
        out = []
        for x in inlines:
            found = _footnote_link(x)
            key = id(found[0]) if found else None
            if key in notes and key not in used:
                used.add(key)
                out.append({"t": "Note", "c": notes[key][2]})
            else:
                out.append(x)
        return out

    _map_inline_lists(blocks, replace, top_down=True)

    for key in used:
        container, block, _content = notes[key]
        for i, candidate in enumerate(container):
            if candidate is block:
                del container[i]
                break
    return len(used)


_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _is_in_book_target(target):
    """True for a link into the source book rather than out of it.

    Calibre rewrites resolvable in-book links to `#anchor` targets, but a link
    to a part of the book it did not keep (a cover page, a nav file) stays a
    relative file link such as `9780063517578_Cover.xhtml#cover`. Neither kind
    can resolve in the translated book, so both lose their target.
    """
    return target.startswith("#") or not _URL_SCHEME.match(target)


def _unwrap_inline_markup(inlines):
    """Unwrap spans and in-book links, clear the attributes of other links, and
    drop empty anchors inside text.

    Lists made only of anchors are left alone: they become `[]` lines that
    clean_calibre_markers() drops after its page-number pass, which keeps that
    pass seeing the same neighbouring lines as before.
    """
    if all(_is_filler(x) for x in inlines):
        return inlines
    out = []
    # Unwrapped children are processed in turn, so an anchor nested in a span
    # is still dropped while the span's spaces and line breaks are kept.
    pending = list(reversed(inlines))
    while pending:
        x = pending.pop()
        t = x.get("t")
        if _is_anchor(x):
            continue
        if t == "Span" or (t == "Link" and (_is_in_book_target(x["c"][2][0]) or _is_filler(x))):
            pending.extend(reversed(x["c"][1]))
        else:
            if t == "Link":
                x["c"][0] = ["", [], []]
            out.append(x)
    # Re-join text split by an unwrapped element, as pandoc's readers would:
    # the Markdown writer only escapes a leading "4." or "#" it sees in one Str.
    merged = []
    for x in out:
        if merged and x.get("t") == "Str" and merged[-1].get("t") == "Str":
            merged[-1] = {"t": "Str", "c": merged[-1]["c"] + x["c"]}
        else:
            merged.append(x)
    return merged


def _clear_header_attributes(node):
    if isinstance(node, dict):
        if node.get("t") == "Header":
            node["c"][1] = ["", [], []]
        if isinstance(node.get("c"), list):
            _clear_header_attributes(node["c"])
    elif isinstance(node, list):
        for item in node:
            _clear_header_attributes(item)


def clean_calibre_ast(doc):
    """Structural cleanup of a pandoc JSON AST read from Calibre's HTML, in place.

    - EPUB footnotes become real pandoc notes (see convert_footnotes()).
    - In-book links (`#anchor` or a relative file such as `Cover.xhtml#c`) lose
      their targets but keep their text: what they point at does not survive
      into the translated book.
    - Spans are unwrapped, so `{lang="…"}` and class attributes never reach
      the Markdown.
    - Empty anchors inside text (`<a id="page_12"/>`) are dropped.
    - Heading and link attributes are cleared, whatever their id or class
      (external links keep their URL and title).
    """
    blocks = doc.get("blocks", [])
    footnotes = convert_footnotes(blocks)
    _map_inline_lists(blocks, _unwrap_inline_markup)
    _clear_header_attributes(blocks)
    return footnotes


def _pandoc_html_to_json(html_file):
    """Read an HTML file into a pandoc JSON AST string.

    Uses pypandoc when it is installed and falls back to the pandoc binary, so
    a machine with pandoc but without the Python wrapper still converts.
    """
    try:
        import pypandoc
    except ImportError:
        pypandoc = None
    if pypandoc is not None:
        return pypandoc.convert_file(html_file, 'json')
    result = subprocess.run(
        ['pandoc', html_file, '-f', 'html', '-t', 'json'],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _pandoc_json_to_markdown(json_text, md_file, extra_args):
    """Write a pandoc JSON AST string to md_file as pandoc Markdown."""
    try:
        import pypandoc
    except ImportError:
        pypandoc = None
    if pypandoc is not None:
        pypandoc.convert_text(
            json_text, 'markdown-smart', format='json',
            outputfile=md_file, extra_args=extra_args,
        )
        return
    subprocess.run(
        ['pandoc', '-f', 'json', '-t', 'markdown-smart', '-o', md_file, *extra_args],
        input=json_text, capture_output=True, text=True, check=True,
    )


def convert_html_to_markdown(html_file, md_file, strip_page_numbers=False, detect_page_numbers=True):
    """Convert HTML to Markdown using pandoc (pypandoc, or the pandoc binary)"""
    try:
        doc = json.loads(_pandoc_html_to_json(html_file))
        footnotes = clean_calibre_ast(doc)
        if footnotes:
            print(f"Converted {footnotes} footnote(s) to Markdown footnotes")
        _pandoc_json_to_markdown(
            json.dumps(doc),
            md_file,
            # Without smart, typographic dashes and quotes stay Unicode instead
            # of becoming ---, -- and backslash-escaped ASCII quotes.
            # block puts each note right after the paragraph citing it, so a
            # note and its reference usually land in the same chunk.
            ['--wrap=none', '--reference-location=block'],
        )

        if os.path.exists(md_file):
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()

            content = content.replace('\ufeff', '')
            content = content.replace('\u00a0', ' ')
            content = clean_calibre_markers(
                content,
                strip_page_numbers=strip_page_numbers,
                detect_page_numbers=detect_page_numbers,
            )

            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(content)

            print(f"Markdown conversion successful: {md_file}")
            return True
        else:
            print("Markdown file was not created")
            return False
    except FileNotFoundError:
        print("Neither pypandoc nor the pandoc binary was found. "
              "Install pandoc (https://pandoc.org/installing.html) or: pip install pypandoc")
        return False
    except subprocess.CalledProcessError as e:
        print(f"HTML to Markdown conversion failed: {(e.stderr or '').strip()[-500:]}")
        return False
    except Exception as e:
        print(f"HTML to Markdown conversion failed: {e}")
        return False


_PAGE_SEQUENCE_MIN_LENGTH = 4
_PAGE_SEQUENCE_MIN_RATIO = 0.5


def _detect_page_number_lines(lines):
    """Detect standalone-digit lines that form a monotonic page-number sequence.

    Returns a set of line indices that should be dropped as page numbers.

    Algorithm: collect every standalone-digit line in document order, find the
    Longest Non-Decreasing Subsequence (LNDS) of their integer values via
    bisect_right with parent-pointer reconstruction. If the LNDS is long enough
    and covers a large enough fraction of all standalone digits, treat those
    elements as page numbers. Outliers (years like 1984, chapter numbers,
    citation indices) sit off the monotonic spine and stay preserved.
    """
    digit_indices = []
    digit_values = []
    for i, line in enumerate(lines):
        s = line.strip()
        if s.isdigit():
            digit_indices.append(i)
            digit_values.append(int(s))

    n = len(digit_values)
    if n < _PAGE_SEQUENCE_MIN_LENGTH:
        return set()

    tails = []
    tails_idx = []
    parents = [-1] * n

    for i, v in enumerate(digit_values):
        pos = bisect.bisect_right(tails, v)
        if pos > 0:
            parents[i] = tails_idx[pos - 1]
        if pos == len(tails):
            tails.append(v)
            tails_idx.append(i)
        else:
            tails[pos] = v
            tails_idx[pos] = i

    lnds = []
    cur = tails_idx[-1]
    while cur != -1:
        lnds.append(cur)
        cur = parents[cur]
    lnds.reverse()

    if len(lnds) < _PAGE_SEQUENCE_MIN_LENGTH:
        return set()
    if len(lnds) / n < _PAGE_SEQUENCE_MIN_RATIO:
        return set()

    return {digit_indices[i] for i in lnds}


_CODE_FENCE_RE = re.compile(r'^(`{3,}|~{3,})')


def _fenced_code_line_indices(lines):
    found = set()
    fence = None
    for i, line in enumerate(lines):
        match = _CODE_FENCE_RE.match(line)
        if fence:
            found.add(i)
            if (match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence)
                    and not line[match.end():].strip()):
                fence = None
        elif match:
            fence = match.group(1)
            found.add(i)
    return found


def _is_empty_anchor_line(line):
    """True for a body line that only holds empty anchors, e.g. `[]` or `[]{#page_3}`.

    Indented lines are code, never anchors; fenced code is excluded by the caller.
    """
    if line[:1].isspace():
        return False
    residue = re.sub(r'\{[^}]*\}|\(#[^)\s]*\)', '', line)
    return '[' in residue and not residue.strip('[] \t')


def clean_calibre_markers(content, strip_page_numbers=False, detect_page_numbers=True):
    """Clean up Calibre-specific markers from markdown content.

    With detect_page_numbers, standalone digit lines are handled in two layers:
      1. If a line is adjacent to Calibre noise (::: fence, .ct}/.cn} marker),
         drop it — clearly leftover.
      2. Otherwise, run LNDS over all standalone digits to detect a monotonic
         page-number sequence and drop those. Outliers like years (1984),
         chapter numbers, and citation indices stay preserved.

    detect_page_numbers=False keeps every standalone digit line: it is meant
    for formats whose page numbers are not text (EPUB anchors, DOCX footers),
    where such lines are chapter, verse or exercise numbers.

    Pass strip_page_numbers=True to bypass both layers and aggressively delete
    every standalone-digit line (legacy behavior).

    Lines holding only empty anchors are dropped as well, but they still count
    as neighbours for layer 1.
    """
    content = re.sub(r'\{\.calibre[^}]*\}', '', content)
    content = re.sub(r'\(#calibre_link-\d+\)', '', content)

    # Clean heading calibre attribute blocks: {#calibre_link-N .calibreN}
    content = re.sub(r'\s*\{#calibre_link-\d+[^}]*\}', '', content)

    # Clean [**text**] format to **text**
    content = re.sub(r'\[\*\*([^*]+)\*\*\]', r'**\1**', content)

    lines = content.split('\n')

    page_number_lines = set() if strip_page_numbers else _detect_page_number_lines(lines)
    code_lines = _fenced_code_line_indices(lines)

    def is_calibre_noise(line):
        s = line.strip()
        if not s:
            return False
        if s.startswith(':::'):
            return True
        if s.endswith('.ct}') or s.endswith('.cn}'):
            return True
        return False

    def prev_nonblank(idx):
        for j in range(idx - 1, -1, -1):
            if lines[j].strip():
                return lines[j]
        return None

    def next_nonblank(idx):
        for j in range(idx + 1, len(lines)):
            if lines[j].strip():
                return lines[j]
        return None

    cleaned_lines = []
    for i, line in enumerate(lines):
        stripped_line = line.strip()

        if stripped_line.startswith(':::'):
            continue
        if stripped_line.endswith('.ct}') or stripped_line.endswith('.cn}'):
            continue
        if i not in code_lines and _is_empty_anchor_line(line):
            continue

        if re.match(r'^\s*\d+\s*$', line):
            if strip_page_numbers:
                continue
            if detect_page_numbers:
                if i in page_number_lines:
                    continue
                prev = prev_nonblank(i)
                nxt = next_nonblank(i)
                if (prev is not None and is_calibre_noise(prev)) or \
                   (nxt is not None and is_calibre_noise(nxt)):
                    continue
            # else: preserve as real content

        cleaned_lines.append(line)

    content = '\n'.join(cleaned_lines)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content


# =============================================================================
# Markdown input: pre-extracted Markdown (e.g. from MinerU / Marker) skips Calibre
# =============================================================================

MARKDOWN_EXTENSIONS = ('.md', '.markdown')
CALIBRE_CONVERSION_METHOD = "calibre_htmlz"
MARKDOWN_CONVERSION_METHOD = "markdown"

_MD_IMG_RE = re.compile(r'(!\[[^\]]*\]\(\s*)(<[^>]+>|[^)\s]+)')
_HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
_DATA_URI_RE = re.compile(r'^data:image/([A-Za-z0-9.+-]+);base64,(.*)$', re.DOTALL)
_URI_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_DATA_URI_EXTENSIONS = {
    "jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif",
    "webp": ".webp", "svg+xml": ".svg", "bmp": ".bmp",
}

_FRONT_MATTER_RE = re.compile(r'\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)', re.DOTALL)
_FRONT_MATTER_KEYS = {
    "title": "title",
    "author": "creator",
    "creator": "creator",
    "publisher": "publisher",
    "lang": "language",
    "language": "language",
}


def _unquote_yaml_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def split_front_matter(content):
    """Split a leading YAML front matter block off content.

    Returns (metadata, body). Only flat `key: value` scalars and `- item`
    lists for the keys in _FRONT_MATTER_KEYS are read; anything fancier is
    ignored. A leading `---` block without any `key:` line is left alone,
    since it is more likely a thematic break than metadata.
    """
    match = _FRONT_MATTER_RE.match(content)
    if not match:
        return {}, content
    lines = match.group(1).split('\n')
    if not any(re.match(r'^[A-Za-z_][\w-]*\s*:', line) for line in lines):
        return {}, content

    metadata = {}
    current_key = None
    for line in lines:
        key_match = re.match(r'^([A-Za-z_][\w-]*)\s*:\s*(.*)$', line)
        if key_match:
            key = _FRONT_MATTER_KEYS.get(key_match.group(1).lower())
            value = _unquote_yaml_scalar(key_match.group(2))
            current_key = None
            if key and key not in metadata:
                if value:
                    metadata[key] = value
                else:
                    current_key = key  # value follows as a `- item` list
            continue
        item_match = re.match(r'^\s+-\s+(.+)$', line)
        if current_key and item_match:
            item = _unquote_yaml_scalar(item_match.group(1))
            if item:
                existing = metadata.get(current_key)
                metadata[current_key] = f"{existing}, {item}" if existing else item
    return metadata, content[match.end():]


def _first_h1_title(content):
    """Return the text of the document's first heading if it is level 1.

    Layout parsers put a paper's title first, as `# Title`. If the first
    heading is deeper than H1 it is a section, not the title, so return None.
    """
    in_fence = False
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r'^(#{1,6})\s+(.*?)\s*#*\s*$', stripped)
        if heading:
            if len(heading.group(1)) != 1:
                return None
            title = re.sub(r'\s*\{[^}]*\}$', '', heading.group(2)).strip()
            return title or None
    return None


def markdown_metadata(content):
    """Book metadata for Markdown input: front matter first, then the first H1."""
    metadata, body = split_front_matter(content.replace('﻿', ''))
    if 'title' not in metadata:
        title = _first_h1_title(body)
        if title:
            metadata['title'] = title
    return metadata


def import_markdown(md_path, temp_dir):
    """Return Markdown content ready for chunking, with images moved under temp_dir/images/.

    Relative image paths are resolved against the Markdown file's directory
    and copied in; base64 data URIs (MinerU inlines images that way) are
    decoded to files so chunks handed to translators never carry image
    payloads. Remote URLs stay untouched. YAML front matter is dropped from
    the body — its metadata goes to config.txt via markdown_metadata().
    """
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    content = content.replace('﻿', '').replace(' ', ' ')
    _, content = split_front_matter(content)

    md_dir = os.path.dirname(os.path.abspath(md_path))
    images_dir = os.path.join(temp_dir, "images")
    refs = {}
    stored = {}
    embedded_count = 0

    def store(name, data):
        base, ext = os.path.splitext(name)
        digest = hashlib.sha256(data).hexdigest()
        candidate, n = name, 1
        while candidate in stored and stored[candidate] != digest:
            n += 1
            candidate = f"{base}-{n}{ext}"
        if candidate not in stored:
            os.makedirs(images_dir, exist_ok=True)
            with open(os.path.join(images_dir, candidate), 'wb') as out:
                out.write(data)
            stored[candidate] = digest
        return f"images/{candidate}"

    def localize(target):
        nonlocal embedded_count
        raw = target[1:-1] if target.startswith('<') and target.endswith('>') else target
        if raw in refs:
            return refs[raw]
        new_ref = target
        data_uri = _DATA_URI_RE.match(raw)
        if data_uri:
            try:
                data = base64.b64decode(data_uri.group(2))
            except (binascii.Error, ValueError):
                data = b''
            if data:
                subtype = data_uri.group(1).lower()
                ext = _DATA_URI_EXTENSIONS.get(subtype, '.' + re.sub(r'[^a-z0-9]', '', subtype))
                embedded_count += 1
                new_ref = store(f"embedded-{embedded_count:04d}{ext}", data)
            else:
                print("Warning: could not decode an embedded data-URI image; left inline")
        elif not _URI_SCHEME_RE.match(raw) and not raw.startswith('#'):
            src = os.path.normpath(os.path.join(md_dir, urllib.parse.unquote(raw)))
            if os.path.isfile(src):
                with open(src, 'rb') as f:
                    new_ref = store(os.path.basename(src), f.read())
            else:
                print(f"Warning: image not found next to the Markdown file, reference kept as-is: {raw}")
        refs[raw] = new_ref
        return new_ref

    content = _MD_IMG_RE.sub(lambda m: m.group(1) + localize(m.group(2)), content)
    content = _HTML_IMG_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{localize(m.group(3))}{m.group(2)}", content
    )

    if stored:
        print(f"Localized {len(stored)} image(s) into {images_dir}/")
    return content


def import_markdown_input(input_file, input_md, temp_dir):
    """Copy a Markdown source into temp_dir/input.md with its images localized."""
    os.makedirs(temp_dir, exist_ok=True)
    content = import_markdown(input_file, temp_dir)
    if not content.strip():
        print(f"Error: {input_file} has no content to translate")
        return False
    with open(input_md, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Markdown import successful: {input_md}")
    return True


# =============================================================================
# Structural block parsing and chunk splitting (Step 3)
# =============================================================================

# Pandoc footnote labels cannot contain whitespace, ^, [ or ].
_FOOTNOTE_DEF_RE = re.compile(r'^ {0,3}\[\^[^\]\s^\[]+\]:')


def _is_indented_text(line):
    return line.startswith(('    ', '\t')) and bool(line.strip())


def parse_structural_blocks(content):
    """Parse markdown into structural blocks that should not be split.

    Returns list of (text, block_type) tuples where block_type is one of:
    'heading', 'code_block', 'math_block', 'table', 'list', 'blockquote',
    'footnote', 'image', 'paragraph'
    """
    blocks = []
    lines = content.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Code block (fenced)
        if stripped.startswith('```'):
            block_lines = [line]
            i += 1
            while i < len(lines):
                block_lines.append(lines[i])
                if lines[i].strip().startswith('```') and len(block_lines) > 1:
                    i += 1
                    break
                i += 1
            blocks.append(('\n'.join(block_lines), 'code_block'))
            continue

        # Display math ($$ ... $$). Lines inside may look like list items or
        # tables, so the block must be kept whole. Like pandoc, never pair
        # delimiters across a blank line — an unclosed $$ must not swallow
        # the rest of the chapter.
        if stripped.startswith('$$'):
            if len(stripped) >= 4 and stripped.endswith('$$'):
                blocks.append((line, 'math_block'))
                i += 1
                continue
            end = None
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if not s:
                    break
                if s.endswith('$$'):
                    end = j
                    break
            if end is not None:
                blocks.append(('\n'.join(lines[i:end + 1]), 'math_block'))
                i = end + 1
                continue

        # Heading
        if re.match(r'^#{1,6}\s', stripped):
            blocks.append((line, 'heading'))
            i += 1
            continue

        # Blockquote
        if stripped.startswith('>'):
            block_lines = [line]
            i += 1
            while i < len(lines) and (lines[i].strip().startswith('>') or
                                       (lines[i].strip() and not re.match(r'^#{1,6}\s', lines[i].strip())
                                        and not lines[i].strip().startswith('```')
                                        and not lines[i].strip().startswith('|')
                                        and not re.match(r'^[-*+]\s', lines[i].strip())
                                        and not re.match(r'^\d+\.\s', lines[i].strip())
                                        and block_lines[-1].strip().startswith('>'))):
                block_lines.append(lines[i])
                i += 1
            blocks.append(('\n'.join(block_lines), 'blockquote'))
            continue

        # Table (lines starting with |)
        if stripped.startswith('|'):
            block_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith('|'):
                block_lines.append(lines[i])
                i += 1
            blocks.append(('\n'.join(block_lines), 'table'))
            continue

        # List (unordered or ordered)
        if re.match(r'^[-*+]\s', stripped) or re.match(r'^\d+\.\s', stripped):
            block_lines = [line]
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                # Continue list: list items, indented continuation, or blank lines within list
                if (re.match(r'^[-*+]\s', s) or re.match(r'^\d+\.\s', s) or
                        (lines[i].startswith('  ') and s) or
                        (s == '' and i + 1 < len(lines) and
                         (re.match(r'^[-*+]\s', lines[i+1].strip()) or
                          re.match(r'^\d+\.\s', lines[i+1].strip()) or
                          lines[i+1].startswith('  ')))):
                    block_lines.append(lines[i])
                    i += 1
                else:
                    break
            blocks.append(('\n'.join(block_lines), 'list'))
            continue

        # Footnote definition plus its indented continuation paragraphs. Kept
        # whole: merging strips each chunk, which would un-indent a
        # continuation that started the next chunk and detach it from its note.
        if _FOOTNOTE_DEF_RE.match(line):
            block_lines = [line]
            i += 1
            while i < len(lines):
                if _is_indented_text(lines[i]) or (
                    not lines[i].strip() and i + 1 < len(lines) and _is_indented_text(lines[i + 1])
                ):
                    block_lines.append(lines[i])
                    i += 1
                else:
                    break
            blocks.append(('\n'.join(block_lines), 'footnote'))
            continue

        # Image line (standalone or with surrounding caption)
        if re.match(r'!\[', stripped):
            blocks.append((line, 'image'))
            i += 1
            continue

        # Empty line — just a paragraph separator
        if stripped == '':
            blocks.append((line, 'paragraph'))
            i += 1
            continue

        # Regular paragraph — collect contiguous non-empty, non-special lines
        block_lines = [line]
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if (s == '' or s.startswith('```') or s.startswith('$$') or re.match(r'^#{1,6}\s', s) or
                    s.startswith('>') or s.startswith('|') or
                    re.match(r'^[-*+]\s', s) or re.match(r'^\d+\.\s', s) or
                    re.match(r'!\[', s)):
                break
            block_lines.append(lines[i])
            i += 1
        blocks.append(('\n'.join(block_lines), 'paragraph'))
        continue

    return blocks


def merge_blocks_to_chunks(blocks, target_size=6000):
    """Merge structural blocks into chunks respecting target_size.

    Prefers to split at heading boundaries. Never splits within a single
    structural block unless the block itself exceeds target_size * 2.
    """
    chunks = []
    current_parts = []
    current_size = 0

    def flush():
        nonlocal current_parts, current_size
        if current_parts:
            chunks.append('\n'.join(current_parts))
            current_parts = []
            current_size = 0

    for text, btype in blocks:
        block_size = len(text)

        # If a single block is oversized, handle degradation
        if block_size > target_size * 2:
            flush()
            if btype == 'footnote':
                # A continuation paragraph cut loose from its `[^N]:` line
                # loses its indent when chunks are merged and becomes body text.
                print(f"  WARNING: Oversized footnote ({block_size} chars), kept in one chunk")
                chunks.append(text)
                continue
            print(f"  WARNING: Oversized {btype} block ({block_size} chars), force-splitting")
            sub_chunks = _force_split_block(text, target_size)
            chunks.extend(sub_chunks)
            continue

        # Prefer to split at heading boundaries
        if btype == 'heading' and current_size > 0:
            flush()

        # Would adding this block exceed target?
        if current_size + block_size > target_size and current_parts:
            flush()

        current_parts.append(text)
        current_size += block_size

    flush()
    # The blank line after a final oversized block would otherwise be its own
    # chunk, and an empty translation of it fails the merge.
    return [chunk for chunk in chunks if chunk.strip()]


def _force_split_block(text, target_size):
    """Force-split an oversized block by paragraph (empty lines), then by lines.

    For fenced code blocks, each resulting chunk gets proper opening/closing fences
    so it remains valid Markdown.
    """
    stripped = text.strip()
    is_fenced_code = stripped.startswith('```')

    # Extract fence info for code blocks
    fence_opener = ''
    if is_fenced_code:
        first_line = stripped.split('\n', 1)[0]
        fence_opener = first_line  # e.g. "```python"

    # Try splitting by empty lines first (not applicable for code blocks — no empty lines expected)
    if not is_fenced_code:
        paragraphs = re.split(r'\n\n+', text)
        if len(paragraphs) > 1:
            chunks = []
            current = []
            current_size = 0
            for para in paragraphs:
                para_size = len(para)
                if current_size + para_size > target_size and current:
                    chunks.append('\n\n'.join(current))
                    current = [para]
                    current_size = para_size
                else:
                    current.append(para)
                    current_size += para_size
            if current:
                chunks.append('\n\n'.join(current))
            return chunks

    # Split by lines
    lines = text.split('\n')

    # For code blocks, strip the opening and closing fences before splitting content
    if is_fenced_code:
        # Remove opening fence line
        content_lines = lines[1:]
        # Remove closing fence line if present
        if content_lines and content_lines[-1].strip().startswith('```'):
            content_lines = content_lines[:-1]
        lines = content_lines

    chunks = []
    current = []
    current_size = 0
    for line in lines:
        line_size = len(line) + 1
        if current_size + line_size > target_size and current:
            chunks.append('\n'.join(current))
            current = [line]
            current_size = line_size
        else:
            current.append(line)
            current_size += line_size
    if current:
        chunks.append('\n'.join(current))

    # Re-wrap each chunk in fences for code blocks
    if is_fenced_code:
        chunks = [f"{fence_opener}\n{chunk}\n```" for chunk in chunks]

    return chunks


def split_markdown_structured(md_file, temp_dir, target_size=6000):
    """Split markdown into structural chunks.

    Returns list of chunk filenames (e.g. ['chunk0001.md', ...]).
    """
    try:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()

        blocks = parse_structural_blocks(content)
        chunk_texts = merge_blocks_to_chunks(blocks, target_size)

        chunk_files = []
        for i, chunk_text in enumerate(chunk_texts, 1):
            filename = f"chunk{i:04d}.md"
            chunk_file = os.path.join(temp_dir, filename)
            with open(chunk_file, 'w', encoding='utf-8') as f:
                f.write(chunk_text)
            chunk_files.append(filename)

        print(f"Split into {len(chunk_files)} chunks")
        for filename in chunk_files:
            filepath = os.path.join(temp_dir, filename)
            size = os.path.getsize(filepath)
            print(f"  {filename}: {size} characters")

        return chunk_files
    except Exception as e:
        print(f"Error splitting markdown: {e}")
        return []


def _find_existing_chunk_files(temp_dir):
    """Find existing chunk source filenames (excluding output_ prefixed), sorted."""
    chunk_files = glob.glob(os.path.join(temp_dir, 'chunk*.md'))
    chunk_files = [os.path.basename(f) for f in chunk_files if not os.path.basename(f).startswith('output_')]
    return sorted(chunk_files)


def create_config_file(temp_dir, input_file, input_lang, output_lang, metadata=None,
                       conversion_method=CALIBRE_CONVERSION_METHOD):
    """Create config.txt file for the pipeline"""
    try:
        config_file = os.path.join(temp_dir, "config.txt")

        config_content = f"""# Translation Configuration
input_file={input_file}
input_lang={input_lang}
output_lang={output_lang}
conversion_method={conversion_method}
"""
        if metadata:
            config_content += f"\n# Book Metadata\n"
            if 'title' in metadata:
                config_content += f"original_title={metadata['title']}\n"
            if 'creator' in metadata:
                config_content += f"creator={metadata['creator']}\n"
            if 'publisher' in metadata:
                config_content += f"publisher={metadata['publisher']}\n"
            if 'language' in metadata:
                config_content += f"source_language={metadata['language']}\n"

        with open(config_file, 'w', encoding='utf-8') as f:
            f.write(config_content)

        print(f"Created config file: {config_file}")
        return True
    except Exception as e:
        print(f"Error creating config file: {e}")
        return False


def _do_split_and_manifest(temp_dir, input_md, chunk_size):
    """Split markdown and create manifest. Returns chunk count or 0 on failure."""
    existing = _find_existing_chunk_files(temp_dir)
    if existing:
        print(f"Skipping markdown splitting - found {len(existing)} existing chunk files")
        # Create/update manifest for existing files
        create_manifest(temp_dir, existing, input_md)
        return len(existing)

    chunk_files = split_markdown_structured(input_md, temp_dir, chunk_size)
    if not chunk_files:
        return 0
    create_manifest(temp_dir, chunk_files, input_md)
    return len(chunk_files)


def _check_strip_page_numbers_cache_conflict(strip_flag, temp_dir, input_md):
    """Return cached files that would silently neutralize a page-number flag.

    --strip-page-numbers and --detect-page-numbers only take effect inside
    clean_calibre_markers, which runs during HTML→Markdown conversion. If
    input.md or chunk*.md already exist from a prior run, both are reused
    as-is and the flag becomes a no-op. Surface that conflict so the user
    knows to clean up.
    """
    if not strip_flag:
        return []
    if not os.path.isdir(temp_dir):
        return []

    blockers = []
    if os.path.exists(input_md):
        blockers.append(input_md)

    existing_chunks = [
        f for f in glob.glob(os.path.join(temp_dir, 'chunk*.md'))
        if not os.path.basename(f).startswith('output_')
    ]
    if existing_chunks:
        blockers.append(f"{len(existing_chunks)} chunk file(s) under {temp_dir}/")

    return blockers


def _abort_on_strip_cache_conflict(blockers, temp_dir):
    if not blockers:
        return
    print("Error: page-number options cannot take effect because cached files exist:")
    for b in blockers:
        print(f"  - {b}")
    print(f"Delete the cached files (or remove the entire {temp_dir}/ directory) and re-run.")
    sys.exit(1)


def main():
    """Main conversion function"""
    parser = argparse.ArgumentParser(
        description="Convert PDF/DOCX/EPUB (via Calibre HTMLZ) or Markdown to markdown chunks"
    )
    parser.add_argument(
        "input_file",
        help="Input file: PDF, DOCX, or EPUB (converted by Calibre), or Markdown (.md/.markdown), "
             "e.g. the output of a layout-aware PDF parser such as MinerU or Marker",
    )
    parser.add_argument("-l", "--ilang", default="auto", help="Input language (default: auto)")
    parser.add_argument("--olang", default="zh", help="Output language (default: zh)")
    parser.add_argument("--chunk-size", type=int, default=6000, help="Target chunk size in characters (default: 6000)")
    parser.add_argument(
        "--temp-root",
        default=None,
        help="Directory under which {book_name}_temp/ will be created (default: current working directory)",
    )
    parser.add_argument(
        "--strip-page-numbers",
        action="store_true",
        help="Aggressively delete every standalone-digit line (legacy behavior).",
    )
    parser.add_argument(
        "--detect-page-numbers",
        action="store_true",
        help="Drop detected page-number lines from EPUB/DOCX too. By default only "
             "PDF input is cleaned this way; EPUB/DOCX keep every standalone number.",
    )

    args = parser.parse_args()
    input_file = args.input_file

    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)

    file_ext = os.path.splitext(input_file)[1].lower()
    is_markdown = file_ext in MARKDOWN_EXTENSIONS
    if file_ext not in ['.pdf', '.docx', '.epub'] and not is_markdown:
        print(f"Error: Unsupported file type: {file_ext}")
        sys.exit(1)
    page_number_flag = args.strip_page_numbers or args.detect_page_numbers
    if is_markdown and page_number_flag:
        flag = "--strip-page-numbers" if args.strip_page_numbers else "--detect-page-numbers"
        print(f"Error: {flag} only cleans Calibre output; it does not apply to Markdown input.")
        sys.exit(1)
    # Only PDFs carry page numbers as text. EPUB page numbers are invisible
    # anchors and Calibre drops DOCX footers, so their digit lines are content.
    detect_page_numbers = args.detect_page_numbers or file_ext == '.pdf'

    if is_markdown:
        print("=== Markdown Import (no Calibre) ===")
    else:
        print("=== File Conversion via Calibre HTMLZ ===")
    print(f"Input file: {input_file}")
    print(f"Target chunk size: {args.chunk_size} characters")
    if args.temp_root:
        print(f"Temp root: {args.temp_root}")

    try:
        temp_dir = build_temp_dir(input_file, args.temp_root)
        current_fingerprint = source_fingerprint(input_file)
        _abort_on_source_cache_mismatch(
            *check_source_cache(temp_dir, current_fingerprint), temp_dir=temp_dir
        )

        if is_markdown:
            input_md = os.path.join(temp_dir, "input.md")
            with open(input_file, 'r', encoding='utf-8') as f:
                metadata = markdown_metadata(f.read())
            if os.path.exists(input_md):
                print("Skipping Markdown import - input.md already exists")
            elif not import_markdown_input(input_file, input_md, temp_dir):
                sys.exit(1)

            chunk_count = _do_split_and_manifest(temp_dir, input_md, args.chunk_size)
            if chunk_count == 0:
                sys.exit(1)

            create_config_file(temp_dir, input_file, args.ilang, args.olang, metadata,
                               conversion_method=MARKDOWN_CONVERSION_METHOD)
            _write_source_fingerprint(temp_dir, current_fingerprint)
            print("Conversion completed successfully!")
            print(f"Temp directory: {temp_dir}")
            print(f"Markdown chunks: {chunk_count} files")
            return

        calibre_path = find_calibre_convert()
        if not calibre_path:
            print("Error: Calibre ebook-convert not found")
            print("Please install Calibre: https://calibre-ebook.com/")
            sys.exit(1)

        input_html_path = os.path.join(temp_dir, "input.html")

        if os.path.exists(input_html_path):
            print(f"Skipping HTMLZ conversion - input.html already exists")

            metadata = {}
            config_file = os.path.join(temp_dir, "config.txt")
            if os.path.exists(config_file):
                try:
                    with open(config_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if '=' in line:
                                key, value = line.strip().split('=', 1)
                                if key == 'original_title':
                                    metadata['title'] = value
                                elif key == 'creator':
                                    metadata['creator'] = value
                                elif key == 'publisher':
                                    metadata['publisher'] = value
                                elif key == 'source_language':
                                    metadata['language'] = value
                except Exception as e:
                    print(f"Warning: Could not read metadata from config: {e}")
            if 'title' not in metadata:
                html_title = _html_title(input_html_path)
                if html_title:
                    metadata['title'] = html_title

            input_md = os.path.join(temp_dir, "input.md")
            _abort_on_strip_cache_conflict(
                _check_strip_page_numbers_cache_conflict(page_number_flag, temp_dir, input_md),
                temp_dir,
            )
            if os.path.exists(input_md):
                print(f"Skipping HTML to Markdown conversion - input.md already exists")
            else:
                if not convert_html_to_markdown(input_html_path, input_md,
                                                strip_page_numbers=args.strip_page_numbers,
                                                detect_page_numbers=detect_page_numbers):
                    sys.exit(1)

            chunk_count = _do_split_and_manifest(temp_dir, input_md, args.chunk_size)
            if chunk_count == 0:
                sys.exit(1)

            create_config_file(temp_dir, input_file, args.ilang, args.olang, metadata)
            _write_source_fingerprint(temp_dir, current_fingerprint)
            print("Conversion completed successfully!")
            print(f"Temp directory: {temp_dir}")
            return

        with tempfile.TemporaryDirectory() as extract_dir:
            # Never next to the source: that path may be the user's own
            # <book>.htmlz, which Calibre would overwrite and we would delete.
            htmlz_name = os.path.splitext(os.path.basename(input_file))[0] + ".htmlz"
            htmlz_file = os.path.join(extract_dir, htmlz_name)
            if not convert_to_htmlz(input_file, htmlz_file, calibre_path):
                sys.exit(1)

            html_file, images_dir = extract_htmlz(htmlz_file, extract_dir)
            if not html_file:
                sys.exit(1)

            metadata = extract_metadata_from_htmlz(extract_dir)

            temp_dir = setup_temp_directory(input_file, html_file, images_dir, temp_root=args.temp_root)
            if not temp_dir:
                sys.exit(1)
            # Write the metadata now: later steps can fail (no pandoc, say), and
            # a re-run takes the cached input.html path, which only finds the
            # book's title and author in config.txt.
            create_config_file(temp_dir, input_file, args.ilang, args.olang, metadata)

            input_html = os.path.join(temp_dir, "input.html")
            input_md = os.path.join(temp_dir, "input.md")

            _abort_on_strip_cache_conflict(
                _check_strip_page_numbers_cache_conflict(page_number_flag, temp_dir, input_md),
                temp_dir,
            )
            if os.path.exists(input_md):
                print(f"Skipping HTML to Markdown conversion - input.md already exists")
            else:
                if not convert_html_to_markdown(input_html, input_md,
                                                strip_page_numbers=args.strip_page_numbers,
                                                detect_page_numbers=detect_page_numbers):
                    sys.exit(1)

            chunk_count = _do_split_and_manifest(temp_dir, input_md, args.chunk_size)
            if chunk_count == 0:
                sys.exit(1)

            create_config_file(temp_dir, input_file, args.ilang, args.olang, metadata)
            _write_source_fingerprint(temp_dir, current_fingerprint)

            print("Conversion completed successfully!")
            print(f"Temp directory: {temp_dir}")
            print(f"Markdown chunks: {chunk_count} files")

    except KeyboardInterrupt:
        print("\nConversion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
