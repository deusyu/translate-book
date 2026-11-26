#!/usr/bin/env python3
"""
manifest.py - Manifest management for chunk tracking and merge validation.
"""

import os
import json
import hashlib
import re
import stat


# Agents put chunk numbers into shell commands, so a chunk id is `chunk` and at
# least four ASCII digits, and nothing else counts as a chunk anywhere.
CHUNK_ID_RE = re.compile(r'chunk[0-9]{4,}')
_CHUNK_FILE_KINDS = {
    'source': ('', '.md'),
    'output': ('output_', '.md'),
    'meta': ('output_', '.meta.json'),
}
_MANIFEST_ENTRY_KEYS = {"id", "order", "source_file", "source_hash", "output_file"}


def is_chunk_id(value):
    return isinstance(value, str) and CHUNK_ID_RE.fullmatch(value) is not None


def check_chunk_id(value):
    """Return value if it is a chunk id such as chunk0001; raise ValueError otherwise."""
    if not is_chunk_id(value):
        raise ValueError(f"{value!r} is not a chunk id such as chunk0001")
    return value


def nth_chunk_id(n):
    """The id convert.py gives the n-th chunk it splits off: chunk0001 to chunk9999, then chunk10000."""
    return f"chunk{n:04d}"


def chunk_file_name(chunk_id, kind='source'):
    """chunk0001 -> chunk0001.md ('source'), output_chunk0001.md ('output'),
    output_chunk0001.meta.json ('meta')."""
    prefix, suffix = _CHUNK_FILE_KINDS[kind]
    return f"{prefix}{check_chunk_id(chunk_id)}{suffix}"


def chunk_id_from_file_name(name, kind='source'):
    """Inverse of chunk_file_name(); raises ValueError for any other name."""
    prefix, suffix = _CHUNK_FILE_KINDS[kind]
    if name.startswith(prefix + 'chunk') and name.endswith(suffix):
        chunk_id = name[len(prefix):len(name) - len(suffix)]
        if is_chunk_id(chunk_id):
            return chunk_id
    raise ValueError(f"{name!r} is not a chunk file name such as {chunk_file_name('chunk0001', kind)}")


def list_chunk_files(temp_dir, kind='source'):
    """Return the names of temp_dir's chunk files of one kind, in chunk order.

    Every name the glob for that kind would match (chunk*.md, output_chunk*.md
    or output_chunk*.meta.json) must be a chunk file name, a regular file and
    not a symlink, and no two may share a number. Raises ValueError otherwise,
    so a stray name never reaches a plan or a prompt.
    """
    prefix, suffix = _CHUNK_FILE_KINDS[kind]
    numbers = {}
    for name in os.listdir(temp_dir):
        if not (name.startswith(prefix + 'chunk') and name.endswith(suffix)):
            continue
        if kind == 'output' and name.endswith('.meta.json'):
            continue
        try:
            chunk_id = chunk_id_from_file_name(name, kind)
        except ValueError:
            raise ValueError(
                f"unexpected file {name!r} in {temp_dir}: chunk files are named like "
                f"{chunk_file_name('chunk0001', kind)}. Move or rename it outside a shell "
                f"command, since its name may hold shell syntax, and run again."
            ) from None
        mode = os.lstat(os.path.join(temp_dir, name)).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError(f"{name!r} in {temp_dir} is not a regular file"
                             + (" but a symlink" if stat.S_ISLNK(mode) else ""))
        number = int(chunk_id[len('chunk'):])
        if number in numbers:
            raise ValueError(f"{numbers[number]!r} and {name!r} in {temp_dir} have the same chunk number")
        numbers[number] = name
    return [numbers[n] for n in sorted(numbers)]


def file_hash(filepath):
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for block in iter(lambda: f.read(8192), b''):
            h.update(block)
    return h.hexdigest()


def read_output_text(filepath):
    """Read a translated output chunk as UTF-8 text.

    Returns None when the file cannot be read or decoded. Callers treat None
    the same as blank content: the chunk has no usable translation.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def create_manifest(temp_dir, chunk_files, source_md_path):
    """Create manifest.json after splitting.

    Args:
        temp_dir: temp directory path
        chunk_files: list of chunk filenames (e.g. ['chunk0001.md', ...])
        source_md_path: path to the source input.md
    """
    source_hash = file_hash(source_md_path) if os.path.exists(source_md_path) else ""

    chunks = []
    for order, filename in enumerate(chunk_files, 1):
        filepath = os.path.join(temp_dir, filename)
        chunk_id = chunk_id_from_file_name(filename)
        output_filename = chunk_file_name(chunk_id, 'output')

        chunks.append({
            "id": chunk_id,
            "order": order,
            "source_file": filename,
            "source_hash": file_hash(filepath) if os.path.exists(filepath) else "",
            "output_file": output_filename,
        })

    manifest = {
        "chunk_count": len(chunks),
        "source_hash": source_hash,
        "chunks": chunks,
    }
    save_manifest(temp_dir, manifest)

    print(f"Created manifest.json ({len(chunks)} chunks)")
    return manifest


def save_manifest(temp_dir, manifest):
    """Write manifest.json into temp_dir."""
    manifest_path = os.path.join(temp_dir, "manifest.json")
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def load_manifest(temp_dir):
    """Load manifest.json from temp_dir. Returns None if not found.

    Raises ValueError unless every entry names a chunk id and that chunk's own
    files, so neither a plan nor a merge can be pointed at another file, and
    has exactly the fields create_manifest() writes, with their types: a
    record missing its source_hash would skip the check that the chunk is
    unchanged.
    """
    manifest_path = os.path.join(temp_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return None
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    chunks = manifest.get("chunks") if isinstance(manifest, dict) else None
    if not isinstance(chunks, list):
        raise ValueError("manifest.json: 'chunks' is not a list")
    ids, orders = set(), set()
    for chunk in chunks:
        chunk_id = chunk.get("id") if isinstance(chunk, dict) else None
        if not is_chunk_id(chunk_id):
            raise ValueError(f"manifest.json: {chunk_id!r} is not a chunk id such as chunk0001")
        if (chunk.get("source_file") != chunk_file_name(chunk_id)
                or chunk.get("output_file") != chunk_file_name(chunk_id, 'output')):
            raise ValueError(f"manifest.json: the files of {chunk_id} must be "
                             f"{chunk_file_name(chunk_id)} and {chunk_file_name(chunk_id, 'output')}")
        if set(chunk) != _MANIFEST_ENTRY_KEYS:
            raise ValueError(f"manifest.json: the entry of {chunk_id} has the fields "
                             f"{', '.join(sorted(chunk))}, not {', '.join(sorted(_MANIFEST_ENTRY_KEYS))}")
        order, source_hash = chunk["order"], chunk["source_hash"]
        if type(order) is not int or order < 1 or not isinstance(source_hash, str) \
                or not re.fullmatch(r'(?:[0-9a-f]{64})?', source_hash):
            raise ValueError(f"manifest.json: {chunk_id} needs an order of 1 or more and a "
                             f"source_hash of 64 lowercase hex digits (or empty)")
        if chunk_id in ids or order in orders:
            raise ValueError(f"manifest.json: {chunk_id} or its order {order} is listed twice")
        ids.add(chunk_id)
        orders.add(order)
    return manifest


def validate_for_merge(temp_dir):
    """Validate that all chunks have been translated before merging.

    Returns (ok, ordered_output_files, warnings) where:
        ok: True if merge can proceed
        ordered_output_files: list of output file paths in order
        warnings: list of warning strings
    """
    manifest = load_manifest(temp_dir)
    if manifest is None:
        # No manifest — fall back to legacy glob-based merge
        return True, None, ["No manifest.json found, using legacy merge"]

    errors = []
    warnings = []
    ordered_output_files = []

    for chunk in sorted(manifest["chunks"], key=lambda c: c["order"]):
        output_path = os.path.join(temp_dir, chunk["output_file"])
        source_path = os.path.join(temp_dir, chunk["source_file"])

        # Check source file exists — reject outputs without source chunks
        if not os.path.exists(source_path):
            errors.append(
                f"Missing source: {chunk['source_file']} (chunk {chunk['id']}) — "
                f"cannot verify output integrity without source chunk"
            )
            continue

        # Check source hash matches — detect stale outputs from changed sources
        if chunk.get("source_hash"):
            current_hash = file_hash(source_path)
            if current_hash != chunk["source_hash"]:
                errors.append(
                    f"Source changed since splitting: {chunk['source_file']} "
                    f"(chunk {chunk['id']}). "
                    f"Expected hash {chunk['source_hash'][:12]}..., "
                    f"got {current_hash[:12]}... — "
                    f"delete output and re-translate, or re-run convert.py to re-split"
                )
                continue

        # Check output exists
        if not os.path.exists(output_path):
            errors.append(f"Missing output: {chunk['output_file']} (chunk {chunk['id']})")
            continue

        # Check non-empty. Whitespace-only files have bytes on disk but merge
        # to nothing after strip(), silently dropping the chunk's content —
        # treat them exactly like empty files.
        output_size = os.path.getsize(output_path)
        if output_size == 0:
            errors.append(f"Empty output: {chunk['output_file']} (chunk {chunk['id']})")
            continue
        output_text = read_output_text(output_path)
        if output_text is None:
            errors.append(
                f"Unreadable output: {chunk['output_file']} (chunk {chunk['id']}) — "
                f"not valid UTF-8 text"
            )
            continue
        if not output_text.strip():
            errors.append(
                f"Blank output: {chunk['output_file']} (chunk {chunk['id']}) — "
                f"whitespace-only content would be silently dropped on merge"
            )
            continue

        # Check abnormally short
        if os.path.exists(source_path):
            source_size = os.path.getsize(source_path)
            if source_size > 0 and output_size < source_size * 0.1:
                warnings.append(
                    f"Suspiciously short: {chunk['output_file']} "
                    f"({output_size} bytes vs source {source_size} bytes)"
                )

        ordered_output_files.append(output_path)

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return False, None, warnings

    for w in warnings:
        print(f"WARNING: {w}")

    return True, ordered_output_files, warnings
