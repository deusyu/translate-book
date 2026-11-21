#!/usr/bin/env python3
"""
figure_text.py - Translate the text inside figures through the normal chunk flow.

A vision-capable sub-agent transcribes the text it can see in a figure into a
sidecar file. `inject` writes that text into the chunk right after the figure
as a pandoc fenced div, so the regular translation pass, the glossary and
run_state cover it like any other text. Image files are only ever read.

  plan <temp_dir>      JSON list of the referenced images that still need recognition
  validate <temp_dir>  Check every sidecar under <temp_dir>/figure_text/
  inject <temp_dir>    Insert or refresh the figure-text blocks in chunk*.md

Sidecar <temp_dir>/figure_text/<SHA-256 of the image file>.json:

  {"version": 1, "sha256": "<same hash>", "kind": "diagram", "blocks": ["Client", "Load balancer"]}

Blocks hold the text as it appears in the image, untranslated. Only kinds in
RENDERED_KINDS are injected. SVG images need no sidecar: the text of their
<text> elements is used unless a sidecar exists for them.

Sidecars stay out of images/ because merge_and_build.py rebuilds the ebook
formats whenever a file there is newer than they are.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote

from manifest import file_hash, load_manifest, save_manifest


SIDECAR_VERSION = 1
SIDECAR_DIR = "figure_text"
RENDERED_KINDS = ("diagram", "chart", "screenshot", "table", "text")
KINDS = RENDERED_KINDS + ("photo", "decorative", "formula", "code", "other")
MAX_BLOCKS = 100
MAX_BLOCK_CHARS = 500
MAX_TOTAL_CHARS = 4000
# Image formats that vision-capable runtimes commonly accept as input.
VISION_FORMATS = ("png", "jpeg", "gif", "webp")
DEFAULT_MIN_SIDE = 48
DEFAULT_MIN_AREA = 100 * 100

_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_MD_IMG_RE = re.compile(r'(?<!\\)!\[[^\]]*\]\(\s*(<[^>]+>|[^)\s]+)')
_HTML_IMG_RE = re.compile(r'<img\b[^>]*?\bsrc\s*=\s*(["\'])(.*?)\1', re.IGNORECASE)
_URI_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.-]*:')
_CODE_FENCE_RE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
_BLOCK_OPEN_RE = re.compile(r'^::: \{\.figure-text image="[^"]*"\}$')
_CAPTION_RE = re.compile(
    r'^[*_]{0,2}(?:figure|fig\.|figura|abbildung|abb\.|рисунок|рис\.|图|圖|図)\s*(?:[a-z][.\-])?\d',
    re.IGNORECASE,
)
_INLINE_SPECIAL_RE = re.compile(r'([\\`*_\[\]<>$~^])')
_CITATION_RE = re.compile(r'(?<!\w)@')
_LIST_MARKER_RE = re.compile(r'^(\(?)(\d+|[A-Za-z]|[IVXLCDMivxlcdm]+)([.)])(?=\s|$)')
_JPEG_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_SODIPODI_ROLE = '{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}role'


# =============================================================================
# Image files
# =============================================================================

def image_info(data):
    """Return (format, width, height) read from the file header.

    format is None when unrecognized; width and height are None when the
    header cannot be read.
    """
    if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR' and len(data) >= 24:
        return ('png',) + struct.unpack('>II', data[16:24])
    if data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        return ('gif',) + struct.unpack('<HH', data[6:10])
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return ('webp',) + _webp_size(data)
    if data.startswith(b'\xff\xd8'):
        return ('jpeg',) + _jpeg_size(data)
    head = data[:4096]
    if head.lstrip(b'\xef\xbb\xbf \t\r\n')[:1] == b'<' and b'<svg' in head:
        return 'svg', None, None
    return None, None, None


def _webp_size(data):
    chunk = data[12:16]
    if chunk == b'VP8 ' and len(data) >= 30:
        width, height = struct.unpack('<HH', data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b'VP8L' and len(data) >= 25:
        bits = int.from_bytes(data[21:25], 'little')
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b'VP8X' and len(data) >= 30:
        return int.from_bytes(data[24:27], 'little') + 1, int.from_bytes(data[27:30], 'little') + 1
    return None, None


def _jpeg_size(data):
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD9:
            i += 2
            continue
        if marker in _JPEG_SOF_MARKERS and i + 9 <= len(data):
            height, width = struct.unpack('>HH', data[i + 5:i + 9])
            return width, height
        i += 2 + struct.unpack('>H', data[i + 2:i + 4])[0]
    return None, None


def _local_name(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def svg_text_blocks(data):
    """Return the text of each <text> element of an SVG file, in document order."""
    if b'<!ENTITY' in data:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    blocks = []
    for elem in root.iter():
        if _local_name(elem.tag) == 'text':
            text = _svg_element_text(elem)
            if text:
                blocks.append(text)
    return blocks


def _svg_element_text(elem):
    # A <tspan> that starts a new line (Inkscape line, new y, or a dy shift)
    # begins a new word; tspans on the same line are kerning runs of one word.
    parts = [elem.text or '']
    line_y = elem.get('y')
    for child in elem:
        if _local_name(child.tag) == 'tspan':
            y = child.get('y')
            if (child.get(_SODIPODI_ROLE) == 'line' or child.get('dy') not in (None, '', '0')
                    or (y is not None and y != line_y)):
                parts.append(' ')
            if y is not None:
                line_y = y
        parts.append(''.join(child.itertext()))
        parts.append(child.tail or '')
    return ' '.join(''.join(parts).split())


# =============================================================================
# Sidecars
# =============================================================================

def check_sidecar(data, sha256):
    """Return (sidecar, None) when data is a valid sidecar for sha256, else (None, reason)."""
    if not _SHA256_RE.match(sha256):
        return None, "file name must be the image's SHA-256 (64 lowercase hex digits)"
    if not isinstance(data, dict):
        return None, "top level must be a JSON object"
    if data.get("version") != SIDECAR_VERSION:
        return None, f"version must be {SIDECAR_VERSION}"
    if data.get("sha256") != sha256:
        return None, "sha256 must match the file name"
    if data.get("kind") not in KINDS:
        return None, f"kind must be one of: {', '.join(KINDS)}"
    blocks = data.get("blocks")
    if not isinstance(blocks, list) or not all(isinstance(b, str) for b in blocks):
        return None, "blocks must be a list of strings"
    if len(blocks) > MAX_BLOCKS:
        return None, f"{len(blocks)} blocks; at most {MAX_BLOCKS} allowed"
    for n, block in enumerate(blocks, 1):
        if len(block) > MAX_BLOCK_CHARS:
            return None, f"block {n} has {len(block)} characters; at most {MAX_BLOCK_CHARS} allowed"
    total = sum(len(b) for b in blocks)
    if total > MAX_TOTAL_CHARS:
        return None, f"blocks have {total} characters in total; at most {MAX_TOTAL_CHARS} allowed"
    return data, None


def load_sidecar(path):
    """Return (sidecar, None) for a valid sidecar file, else (None, reason)."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return None, f"cannot read JSON: {e}"
    return check_sidecar(data, Path(path).stem)


def _sidecar_rel(sha256):
    return f"{SIDECAR_DIR}/{sha256}.json"


# =============================================================================
# Image references in chunks
# =============================================================================

def _chunk_files(temp_dir):
    return sorted(Path(temp_dir).glob('chunk*.md'))


def _line_image_refs(line):
    found = [(m.start(), m.group(1)) for m in _MD_IMG_RE.finditer(line)]
    found += [(m.start(), m.group(2)) for m in _HTML_IMG_RE.finditer(line)]
    return [ref for _, ref in sorted(found)]


def _image_lines(lines):
    """Yield (index, refs) for each line outside fenced code that references images."""
    fence = None
    for i, line in enumerate(lines):
        if fence:
            stripped = line.strip()
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None
            continue
        m = _CODE_FENCE_RE.match(line)
        if m:
            fence = m.group(1)
            continue
        refs = _line_image_refs(line)
        if refs:
            yield i, refs


def resolve_ref(temp_dir, ref):
    """Return the referenced file as a POSIX path relative to temp_dir, or None
    for remote, data, absolute and out-of-tree references."""
    ref = ref.strip()
    if ref.startswith('<') and ref.endswith('>'):
        ref = ref[1:-1]
    if not ref or ref.startswith(('#', '/', '\\')) or _URI_SCHEME_RE.match(ref):
        return None
    root = os.path.realpath(temp_dir)
    full = os.path.realpath(os.path.join(root, unquote(ref.split('#', 1)[0].split('?', 1)[0])))
    if not full.startswith(root + os.sep):
        return None
    return Path(os.path.relpath(full, root)).as_posix()


def _referenced_images(temp_dir):
    """Return ({local path: [chunk names]}, {other ref: [chunk names]}) in order of first use."""
    local, other = {}, {}
    for chunk in _chunk_files(temp_dir):
        lines = chunk.read_text(encoding='utf-8').split('\n')
        for _, refs in _image_lines(lines):
            for ref in refs:
                path = resolve_ref(temp_dir, ref)
                chunks = local.setdefault(path, []) if path else other.setdefault(ref, [])
                if chunk.name not in chunks:
                    chunks.append(chunk.name)
    return local, other


def _read_image(temp_dir, path):
    full = os.path.join(temp_dir, path)
    if not os.path.isfile(full):
        return None, None
    data = Path(full).read_bytes()
    return data, hashlib.sha256(data).hexdigest()


# =============================================================================
# plan / validate
# =============================================================================

def _add_by_hash(entries, sha256, path, chunks, **fields):
    entry = entries.setdefault(sha256, {"sha256": sha256, "paths": [], "chunks": [], **fields})
    entry["paths"].append(path)
    entry["chunks"].extend(c for c in chunks if c not in entry["chunks"])


def plan(temp_dir, min_side=DEFAULT_MIN_SIDE, min_area=DEFAULT_MIN_AREA):
    """List the images referenced by chunk*.md by what they still need. Read-only.

    queue:   raster images without a valid sidecar, for a vision sub-agent
    done:    images with a valid sidecar
    svg:     SVG images, whose text is read from the file itself
    skipped: missing, non-local, too small, or in a format vision input does not take
    """
    local, other = _referenced_images(temp_dir)
    queue, done, svg, skipped = {}, {}, [], []
    for path, chunks in local.items():
        data, sha256 = _read_image(temp_dir, path)
        if data is None:
            skipped.append({"path": path, "reason": "missing", "chunks": chunks})
            continue
        fmt, width, height = image_info(data)
        sidecar_rel = _sidecar_rel(sha256)
        sidecar_path = os.path.join(temp_dir, sidecar_rel)
        sidecar, error = load_sidecar(sidecar_path) if os.path.exists(sidecar_path) else (None, None)
        if sidecar:
            _add_by_hash(done, sha256, path, chunks, kind=sidecar["kind"], blocks=len(sidecar["blocks"]))
        elif fmt == 'svg':
            entry = {"path": path, "chunks": chunks, "blocks": len(_renderable(svg_text_blocks(data)))}
            if error:
                entry["sidecar_error"] = error
            svg.append(entry)
        elif fmt not in VISION_FORMATS:
            skipped.append({"path": path, "reason": "unsupported_format", "chunks": chunks})
        elif width is not None and (min(width, height) < min_side or width * height < min_area):
            skipped.append({"path": path, "reason": "small", "width": width, "height": height, "chunks": chunks})
        else:
            fields = {"format": fmt, "width": width, "height": height, "sidecar": sidecar_rel}
            if error:
                fields["sidecar_error"] = error
            _add_by_hash(queue, sha256, path, chunks, **fields)
    skipped += [{"path": ref, "reason": "not_local", "chunks": chunks} for ref, chunks in other.items()]
    return {
        "temp_dir": temp_dir,
        "sidecar_dir": os.path.join(temp_dir, SIDECAR_DIR),
        "queue": list(queue.values()),
        "done": list(done.values()),
        "svg": svg,
        "skipped": skipped,
        "counts": {"queue": len(queue), "done": len(done), "svg": len(svg), "skipped": len(skipped)},
    }


def validate(temp_dir):
    """Check every sidecar file. Unused ones match no image the chunks reference."""
    sidecar_dir = Path(temp_dir) / SIDECAR_DIR
    files = sorted(sidecar_dir.glob('*.json')) if sidecar_dir.is_dir() else []
    referenced = set()
    for path in _referenced_images(temp_dir)[0]:
        sha256 = _read_image(temp_dir, path)[1]
        if sha256:
            referenced.add(sha256)
    result = {"valid": [], "invalid": [], "unused": []}
    for path in files:
        rel = f"{SIDECAR_DIR}/{path.name}"
        sidecar, error = load_sidecar(path)
        if sidecar is None:
            result["invalid"].append({"file": rel, "error": error})
            continue
        result["valid"].append(rel)
        if path.stem not in referenced:
            result["unused"].append(rel)
    return result


# =============================================================================
# inject
# =============================================================================

def _renderable(blocks):
    """Whitespace-collapsed, de-duplicated blocks; text without letters needs no translation."""
    items = []
    for block in blocks:
        text = ' '.join(block.split())
        if any(ch.isalpha() for ch in text) and text not in items:
            items.append(text)
    return items


def escape_markdown(text):
    """Escape text so pandoc reads it as the literal text of one list item."""
    text = _INLINE_SPECIAL_RE.sub(r'\\\1', ' '.join(text.split()))
    text = _CITATION_RE.sub(r'\\@', text)
    if text[:1] in ('#', '+', '-', ':', '|'):
        return '\\' + text
    return _LIST_MARKER_RE.sub(
        lambda m: ('\\(' if m.group(1) else '') + m.group(2) + '\\' + m.group(3), text, count=1
    )


def render_block(path, blocks):
    """Return the fenced-div lines for one image, or None when nothing is worth showing."""
    items = _renderable(blocks)
    if not items:
        return None
    return [f'::: {{.figure-text image="{quote(path)}"}}'] + [f'- {escape_markdown(t)}' for t in items] + [':::']


def remove_blocks(lines):
    """Drop injected figure-text blocks and the blank line inserted before each."""
    out = []
    i = 0
    while i < len(lines):
        if not _BLOCK_OPEN_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        if out and out[-1] == '':
            out.pop()
        i += 1
        while i < len(lines) and lines[i].startswith('- '):
            i += 1
        if i < len(lines) and lines[i] == ':::':
            i += 1
    return out


def _run_end(lines, i):
    while i + 1 < len(lines) and lines[i + 1].strip() and not _CODE_FENCE_RE.match(lines[i + 1]):
        i += 1
    return i


def _anchor(lines, i):
    """Index of the line the figure text for line i goes after: the end of the
    image's block, or of a "Figure N" caption right below it."""
    end = _run_end(lines, i)
    nxt = end + 1
    while nxt < len(lines) and not lines[nxt].strip():
        nxt += 1
    if nxt < len(lines) and _CAPTION_RE.match(lines[nxt].strip()) and not _line_image_refs(lines[nxt]):
        end = _run_end(lines, nxt)
    return end


def inject_text(text, block_for):
    """Return (text with a figure-text block after each image that has one, block count).

    block_for(ref) returns the block lines for an image reference or None.
    Earlier blocks are replaced, so running this again changes nothing.
    """
    lines = remove_blocks(text.split('\n'))
    inserts = {}
    count = 0
    for i, refs in _image_lines(lines):
        found = [block for block in map(block_for, refs) if block]
        if found:
            inserts.setdefault(_anchor(lines, i), []).extend(found)
            count += len(found)
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        for block in inserts.get(i, ()):
            out.append('')
            out.extend(block)
    return '\n'.join(out), count


def _image_block(temp_dir, path, invalid):
    data, sha256 = _read_image(temp_dir, path)
    if data is None:
        return None
    sidecar_rel = _sidecar_rel(sha256)
    sidecar_path = os.path.join(temp_dir, sidecar_rel)
    if os.path.exists(sidecar_path):
        sidecar, error = load_sidecar(sidecar_path)
        if sidecar:
            return render_block(path, sidecar["blocks"]) if sidecar["kind"] in RENDERED_KINDS else None
        invalid[sidecar_rel] = error
    if image_info(data)[0] == 'svg':
        return render_block(path, svg_text_blocks(data))
    return None


def _update_manifest(temp_dir, changed):
    """Record the new hash of each rewritten chunk. Returns the chunks whose
    entry was already out of date, which are left for merge to report."""
    manifest = load_manifest(temp_dir)
    if manifest is None or not changed:
        return []
    left_alone = []
    for entry in manifest.get("chunks", []):
        hashes = changed.get(entry.get("source_file"))
        if hashes is None:
            continue
        if entry.get("source_hash") == hashes[0]:
            entry["source_hash"] = hashes[1]
        else:
            left_alone.append(entry["source_file"])
    save_manifest(temp_dir, manifest)
    return left_alone


def inject(temp_dir):
    """Insert or refresh the figure-text blocks in every chunk*.md."""
    cache, invalid = {}, {}

    def block_for(ref):
        path = resolve_ref(temp_dir, ref)
        if path is None:
            return None
        if path not in cache:
            cache[path] = _image_block(temp_dir, path, invalid)
        return cache[path]

    changed = {}
    blocks = 0
    for chunk in _chunk_files(temp_dir):
        old_text = chunk.read_text(encoding='utf-8')
        new_text, count = inject_text(old_text, block_for)
        blocks += count
        if new_text != old_text:
            old_hash = file_hash(chunk)
            chunk.write_text(new_text, encoding='utf-8')
            changed[chunk.name] = (old_hash, file_hash(chunk))
    temp_path = Path(temp_dir)
    return {
        "blocks": blocks,
        "updated_chunks": sorted(changed),
        "stale_outputs": [f"output_{name}" for name in sorted(changed) if (temp_path / f"output_{name}").exists()],
        "manifest_left_unchanged": _update_manifest(temp_dir, changed),
        "invalid_sidecars": [{"file": f, "error": e} for f, e in sorted(invalid.items())],
    }


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recognize, check and inject the text inside figures")
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_plan = sub.add_parser('plan', help="List the referenced images that still need recognition")
    p_plan.add_argument('temp_dir')
    p_plan.add_argument('--min-side', type=int, default=DEFAULT_MIN_SIDE,
                        help=f"Skip raster images with a shorter side in pixels (default: {DEFAULT_MIN_SIDE})")
    p_plan.add_argument('--min-area', type=int, default=DEFAULT_MIN_AREA,
                        help=f"Skip raster images with fewer pixels (default: {DEFAULT_MIN_AREA})")

    p_validate = sub.add_parser('validate', help="Check every sidecar under <temp_dir>/figure_text/")
    p_validate.add_argument('temp_dir')

    p_inject = sub.add_parser('inject', help="Insert or refresh the figure-text blocks in chunk*.md")
    p_inject.add_argument('temp_dir')

    args = parser.parse_args(argv)
    if not os.path.isdir(args.temp_dir):
        print(f"Error: Temp directory not found: {args.temp_dir}", file=sys.stderr)
        return 1

    try:
        if args.cmd == 'plan':
            _print_json(plan(args.temp_dir, args.min_side, args.min_area))
        elif args.cmd == 'validate':
            result = validate(args.temp_dir)
            _print_json(result)
            if result["invalid"]:
                return 1
        elif args.cmd == 'inject':
            if shutil.which('pandoc') is None:
                print("Error: pandoc not found. Figure-text blocks are pandoc fenced divs; "
                      "merge_and_build.py's fallback converters would print them as plain text.",
                      file=sys.stderr)
                return 1
            _print_json(inject(args.temp_dir))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
