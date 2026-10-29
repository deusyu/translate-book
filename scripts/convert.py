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


def convert_html_to_markdown(html_file, md_file, strip_page_numbers=False, detect_page_numbers=True):
    """Convert HTML to Markdown using pandoc"""
    try:
        import pypandoc

        ast = json.loads(pypandoc.convert_file(html_file, 'json', format='html'))
        content = pypandoc.convert_text(
            json.dumps(normalize_calibre_ast(ast)),
            # With pandoc's default `smart`, the writer turns —/–/… and curly
            # quotes into ---/--/... and straight quotes, and a paragraph of
            # ——— into a thematic break.
            'markdown-smart',
            format='json',
            # Each footnote definition goes right after the paragraph citing
            # it, so both normally land in the same chunk.
            extra_args=['--wrap=none', '--reference-location=block'],
        )

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
    except ImportError:
        print("pypandoc not found. Install with: pip install pypandoc")
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
    """
    content = re.sub(r'\{\.calibre[^}]*\}', '', content)
    content = re.sub(r'\(#calibre_link-\d+\)', '', content)

    # Clean heading calibre attribute blocks: {#calibre_link-N .calibreN}
    content = re.sub(r'\s*\{#calibre_link-\d+[^}]*\}', '', content)

    # Clean [**text**] format to **text**
    content = re.sub(r'\[\*\*([^*]+)\*\*\]', r'**\1**', content)

    lines = content.split('\n')

    detect = detect_page_numbers and not strip_page_numbers
    page_number_lines = _detect_page_number_lines(lines) if detect else set()

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

        if re.match(r'^\s*\d+\s*$', line):
            if strip_page_numbers:
                continue
            if detect:
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
# Calibre HTML → pandoc AST normalization (runs before Markdown is written)
# =============================================================================

_PANDOC_INLINE_TYPES = frozenset({
    'Str', 'Emph', 'Underline', 'Strong', 'Strikeout', 'Superscript', 'Subscript',
    'SmallCaps', 'Quoted', 'Cite', 'Code', 'Space', 'SoftBreak', 'LineBreak',
    'Math', 'RawInline', 'Link', 'Image', 'Note', 'Span',
})
_PANDOC_BLOCK_TYPES = frozenset({
    'Plain', 'Para', 'LineBlock', 'CodeBlock', 'RawBlock', 'BlockQuote',
    'OrderedList', 'BulletList', 'DefinitionList', 'Header', 'HorizontalRule',
    'Table', 'Figure', 'Div', 'Null',
})
_WHITESPACE_INLINES = frozenset({'Space', 'SoftBreak', 'LineBreak'})
_NOTE_MARKER_MAX_LEN = 8


def _empty_attr():
    return ["", [], []]


def _pandoc_list_kind(items):
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    types = {x.get('t') for x in items}
    if types <= _PANDOC_INLINE_TYPES:
        return 'inline'
    if types <= _PANDOC_BLOCK_TYPES:
        return 'block'
    return None


def _map_pandoc_lists(node, fn, children_first=False):
    """Rewrite every inline/block list of a pandoc JSON AST in place.

    fn(items, kind) returns the replacement list. By default a list is
    rewritten before the walk descends into it; children_first reverses that.
    """
    if isinstance(node, list):
        kind = _pandoc_list_kind(node)
        if kind and not children_first:
            node[:] = fn(node, kind)
        for item in node:
            _map_pandoc_lists(item, fn, children_first)
        if kind and children_first:
            node[:] = fn(node, kind)
    elif isinstance(node, dict):
        for value in node.values():
            _map_pandoc_lists(value, fn, children_first)


def _note_marker_text(inlines):
    """Return the marker of a note number such as `1`, `[1]`, `1.` or `*`, else None."""
    parts = []
    for el in inlines:
        t = el.get('t')
        if t == 'Str':
            parts.append(el['c'])
        elif t in ('Superscript', 'Strong', 'Emph'):
            inner = _note_marker_text(el['c'])
            if inner is None:
                return None
            parts.append(inner)
        else:
            return None
    marker = ''.join(parts).strip('[]().')
    if not marker or len(marker) > _NOTE_MARKER_MAX_LEN:
        return None
    return marker


def _without_whitespace(inlines):
    return [el for el in inlines if el.get('t') not in _WHITESPACE_INLINES]


def _superscript_note_link(el):
    """Return (link, marker) for <a><sup>N</sup></a> or <sup><a>N</a></sup>, else None."""
    t = el.get('t')
    if t == 'Link':
        content = _without_whitespace(el['c'][1])
        if len(content) == 1 and content[0].get('t') == 'Superscript':
            marker = _note_marker_text(content[0]['c'])
            return (el, marker) if marker else None
    elif t == 'Superscript':
        content = _without_whitespace(el['c'])
        if len(content) == 1 and content[0].get('t') == 'Link':
            marker = _note_marker_text(content[0]['c'][1])
            return (content[0], marker) if marker else None
    return None


def _internal_target(link):
    url = link['c'][2][0]
    return url[1:] if url.startswith('#') and len(url) > 1 else None


def _collect_note_refs(doc):
    """Map each superscript note reference's own id to (marker, target id).

    Calibre renames every id to a book-wide unique `calibre_link-N`, so the
    reference id identifies one note even when numbering restarts per chapter.
    """
    refs = {}

    def visit(items, kind):
        if kind == 'inline':
            for el in items:
                found = _superscript_note_link(el)
                if not found:
                    continue
                link, marker = found
                ref_id, target = link['c'][0][0], _internal_target(link)
                if ref_id and target and ref_id not in refs:
                    refs[ref_id] = (marker, target)
        return items

    _map_pandoc_lists(doc, visit)
    return refs


def _note_start(block, refs):
    """Return (ref_id, body inlines) if block opens a note that links back to a reference.

    The backlink must point at the reference's own id and carry the same
    marker, which keeps ordinary internal links (TOC entries, cross-references)
    out. Class names are useless here because Calibre rewrites them.
    """
    if block.get('t') not in ('Para', 'Plain'):
        return None
    inlines = block['c']
    i = 0
    while i < len(inlines) and inlines[i].get('t') in _WHITESPACE_INLINES:
        i += 1
    if i == len(inlines):
        return None
    first = inlines[i]
    found = _superscript_note_link(first)
    if found:
        link, marker = found
    elif first.get('t') == 'Link':
        link, marker = first, _note_marker_text(first['c'][1])
    else:
        return None
    ref_id = _internal_target(link)
    if ref_id not in refs or refs[ref_id][0] != marker:
        return None
    body = inlines[i + 1:]
    if body and body[0].get('t') == 'Str' and body[0]['c'] in ('.', ')', ':'):
        body = body[1:]
    while body and body[0].get('t') in _WHITESPACE_INLINES:
        body = body[1:]
    if not body:
        return None
    return ref_id, body


def _extract_note_bodies(doc, refs):
    """Detach paired note bodies from the document; return {ref_id: (blocks, original)}.

    A note is the paragraph opening with the backlink, or the whole div when
    the reference targets a div holding only that note (multi-paragraph notes).
    """
    notes = {}

    def div_note(div):
        div_id, children = div['c'][0][0], div['c'][1]
        if not div_id or not children:
            return None
        start = _note_start(children[0], refs)
        if not start or start[0] in notes or refs[start[0]][1] != div_id:
            return None
        rest = children[1:]
        if any(b.get('t') == 'Header' or _note_start(b, refs) for b in rest):
            return None
        ref_id, body = start
        return ref_id, [{'t': 'Para', 'c': body}] + rest

    def visit(items, kind):
        if kind != 'block':
            return items
        kept = []
        for block in items:
            found = div_note(block) if block.get('t') == 'Div' else None
            if not found:
                start = _note_start(block, refs)
                if start and start[0] not in notes:
                    found = (start[0], [{'t': block['t'], 'c': start[1]}])
            if found:
                notes[found[0]] = (found[1], block)
            else:
                kept.append(block)
        return kept

    _map_pandoc_lists(doc, visit)
    return notes


def _insert_notes(doc, notes):
    """Replace each paired reference with a pandoc Note; return unused originals."""
    def visit(items, kind):
        if kind != 'inline':
            return items
        out = []
        for el in items:
            found = _superscript_note_link(el)
            ref_id = found[0]['c'][0][0] if found else None
            if ref_id in notes:
                out.append({'t': 'Note', 'c': notes.pop(ref_id)[0]})
            else:
                out.append(el)
        return out

    _map_pandoc_lists(doc, visit)
    return [original for _, original in notes.values()]


def _strip_calibre_markup(doc):
    """Drop markup that has no visible content once written as Markdown.

    Spans and internal links are unwrapped (an emptied one was a page/id
    anchor); heading and link attributes are cleared. Left in, pandoc writes
    `[]`, `[text]{lang="la"}`, `[text](#calibre_link-N)` and `{#id .class}`,
    which `clean_calibre_markers()` can only half-remove.
    """
    def visit(items, kind):
        if kind == 'block':
            kept = []
            for block in items:
                t = block.get('t')
                if t in ('Para', 'Plain') and not _without_whitespace(block['c']):
                    continue
                if t == 'Header':
                    block['c'][1] = _empty_attr()
                # Endnote lists are left with empty items once their notes move.
                elif t == 'BulletList':
                    block['c'] = [item for item in block['c'] if item]
                    if not block['c']:
                        continue
                elif t == 'OrderedList':
                    block['c'][1] = [item for item in block['c'][1] if item]
                    if not block['c'][1]:
                        continue
                kept.append(block)
            return kept
        out = []
        pending = list(reversed(items))
        while pending:
            el = pending.pop()
            t = el.get('t')
            if t == 'Span' or (t == 'Link' and _internal_target(el)):
                pending.extend(reversed(el['c'][1]))
                continue
            if t == 'Link':
                el['c'][0] = _empty_attr()
            # pandoc only escapes a leading "3." as a list marker when it is
            # one Str, so re-join text split by an unwrapped link.
            if t == 'Str' and out and out[-1].get('t') == 'Str':
                out[-1] = {'t': 'Str', 'c': out[-1]['c'] + el['c']}
                continue
            out.append(el)
        return out

    _map_pandoc_lists(doc, visit, children_first=True)


def normalize_calibre_ast(doc):
    """Rewrite pandoc's JSON AST of Calibre HTML before it is written as Markdown.

    1. `<a><sup>N</sup></a>` references paired with a note that starts with a
       backlink become real pandoc footnotes; pandoc then numbers them
       `[^1]`, `[^2]`, ... across the whole book, so labels never collide
       when every chapter restarts at 1.
    2. Empty anchors, spans, internal links and attribute blocks are removed
       (see `_strip_calibre_markup`).
    """
    refs = _collect_note_refs(doc)
    if refs:
        notes = _extract_note_bodies(doc, refs)
        if notes:
            doc['blocks'].extend(_insert_notes(doc, notes))
    _strip_calibre_markup(doc)
    return doc


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

def parse_structural_blocks(content):
    """Parse markdown into structural blocks that should not be split.

    Returns list of (text, block_type) tuples where block_type is one of:
    'heading', 'code_block', 'math_block', 'table', 'list', 'blockquote', 'image', 'paragraph'
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
    return chunks


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

    htmlz_file = f"{os.path.splitext(input_file)[0]}.htmlz"

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

        if not convert_to_htmlz(input_file, htmlz_file, calibre_path):
            sys.exit(1)

        with tempfile.TemporaryDirectory() as extract_dir:
            html_file, images_dir = extract_htmlz(htmlz_file, extract_dir)
            if not html_file:
                sys.exit(1)

            metadata = extract_metadata_from_htmlz(extract_dir)

            temp_dir = setup_temp_directory(input_file, html_file, images_dir, temp_root=args.temp_root)
            if not temp_dir:
                sys.exit(1)

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

            if os.path.exists(htmlz_file):
                os.remove(htmlz_file)

    except KeyboardInterrupt:
        print("\nConversion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
