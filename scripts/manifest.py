#!/usr/bin/env python3
"""
manifest.py - Manifest management for chunk tracking and merge validation.
"""

import os
import json
import hashlib


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
        # Derive output filename: chunk0001.md -> output_chunk0001.md
        output_filename = f"output_{filename}"
        chunk_id = os.path.splitext(filename)[0]  # e.g. "chunk0001"

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

    manifest_path = os.path.join(temp_dir, "manifest.json")
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"Created manifest.json ({len(chunks)} chunks)")
    return manifest


def load_manifest(temp_dir):
    """Load manifest.json from temp_dir. Returns None if not found."""
    manifest_path = os.path.join(temp_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return None
    with open(manifest_path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
