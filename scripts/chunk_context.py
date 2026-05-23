#!/usr/bin/env python3
"""
chunk_context.py - Read-only neighbor excerpts for per-chunk translation prompts.

The script never writes state. It only derives a small previous/next excerpt
from adjacent chunk*.md files so the orchestrator can inject context for
pronoun and attribute resolution without giving a sub-agent a full neighboring
chunk to translate.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


CHUNK_RE = re.compile(r'^chunk(\d+)\.md$')


def parse_chunk_name(chunk_name):
    """Return (chunk_id, number, width) for chunkNNNN.md."""
    base = os.path.basename(chunk_name)
    match = CHUNK_RE.match(base)
    if not match:
        raise ValueError(f"Expected chunk filename like chunk0001.md, got {base!r}")
    digits = match.group(1)
    return f"chunk{digits}", int(digits), len(digits)


def _neighbor_path(temp_dir, number, width):
    if number < 1:
        return None
    path = Path(temp_dir) / f"chunk{number:0{width}d}.md"
    return path if path.exists() else None


def _read_excerpt(path, chars, tail=False):
    if path is None:
        return ""
    text = path.read_text(encoding='utf-8')
    excerpt = text[-chars:] if tail else text[:chars]
    return excerpt.strip()


def get_neighbor_context(temp_dir, chunk_name, chars=300):
    """Return neighbor context dict for a chunk.

    `prev_excerpt` is the tail of the previous source chunk; `next_excerpt` is
    the head of the next source chunk. Missing neighbors produce empty strings.
    """
    if chars < 0:
        raise ValueError("chars must be non-negative")

    chunk_id, number, width = parse_chunk_name(chunk_name)
    temp_path = Path(temp_dir)
    source_path = temp_path / os.path.basename(chunk_name)
    if not source_path.exists():
        raise FileNotFoundError(f"Source chunk not found: {source_path}")

    prev_path = _neighbor_path(temp_path, number - 1, width)
    next_path = _neighbor_path(temp_path, number + 1, width)

    return {
        "chunk_id": chunk_id,
        "prev_chunk": prev_path.name if prev_path else None,
        "next_chunk": next_path.name if next_path else None,
        "prev_excerpt": _read_excerpt(prev_path, chars, tail=True),
        "next_excerpt": _read_excerpt(next_path, chars, tail=False),
    }


def format_for_prompt(context):
    """Render a concise prompt block. Empty context renders to empty string."""
    parts = []
    if context.get("prev_excerpt"):
        parts.append(
            f"Previous chunk excerpt ({context['prev_chunk']}, read-only):\n"
            f"```text\n{context['prev_excerpt']}\n```"
        )
    if context.get("next_excerpt"):
        parts.append(
            f"Next chunk excerpt ({context['next_chunk']}, read-only):\n"
            f"```text\n{context['next_excerpt']}\n```"
        )
    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser(
        description="Print read-only neighboring chunk excerpts for a translation prompt"
    )
    parser.add_argument("temp_dir", help="Path to <book>_temp/ directory")
    parser.add_argument("chunk_file", help="Chunk filename such as chunk0001.md")
    parser.add_argument(
        "--chars",
        type=int,
        default=300,
        help="Number of characters to take from each neighbor (default: 300)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a prompt-ready markdown block",
    )
    args = parser.parse_args()

    try:
        context = get_neighbor_context(args.temp_dir, args.chunk_file, args.chars)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
    else:
        block = format_for_prompt(context)
        if block:
            print(block)


if __name__ == "__main__":
    main()
