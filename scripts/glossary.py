#!/usr/bin/env python3
"""
glossary.py - Term-consistency glossary for parallel-subagent translation.

A separate sub-agent translates each chunk with a fresh context. Without
shared state, the same proper noun can get translated three different ways
across a 100-chunk book. This module manages a hand-editable glossary.json
that the main agent injects into each sub-agent's prompt as a per-chunk term
table — so every sub-agent sees the same canonical translations for the
terms that matter to its chunk.

The schema (v2):

    {
      "version": 2,
      "terms": [
        {"id": "Manhattan", "source": "Manhattan", "target": "曼哈顿",
         "category": "place", "aliases": [], "gender": "unknown",
         "confidence": "medium", "frequency": 12,
         "evidence_refs": [], "notes": ""}
      ],
      "high_frequency_top_n": 20,
      "applied_meta_hashes": {}
    }

Lives at <temp_dir>/glossary.json and is meant to be hand-edited.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


GLOSSARY_SCHEMA_VERSION = 2
DEFAULT_TOP_N = 20
DEFAULT_MAX_TERMS = 50

VALID_GENDERS = ('male', 'female', 'nonbinary', 'unknown')
VALID_CONFIDENCES = ('low', 'medium', 'high')

_HEX_RE = re.compile(r'^[0-9a-f]+$')

_CJK_RANGES = (
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3040, 0x309F),   # Hiragana
    (0x30A0, 0x30FF),   # Katakana
    (0xAC00, 0xD7AF),   # Hangul Syllables
)


def _contains_cjk(s):
    for c in s:
        cp = ord(c)
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def _canonical_json(data):
    """Stable JSON for hashing — sorted keys, no whitespace, unicode preserved."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def glossary_hash(glossary):
    """SHA-256 of the canonical glossary. Input order / whitespace insensitive."""
    return hashlib.sha256(_canonical_json(glossary).encode('utf-8')).hexdigest()


def term_hash(term):
    """SHA-256 of a single term's prompt-affecting fields.

    Aliases participate because they are injected into the per-chunk term
    table (别名 column): adding or editing an alias changes the prompt
    contract for chunks that already selected this term, so run_state must
    see a different hash and plan a re-translation. The alias segment is
    appended only when aliases exist, so alias-less terms keep their
    historical hash and existing run_state.json records stay valid.
    """
    payload = f"{term.get('source', '')}→{term.get('target', '')}|{term.get('category', '')}"
    aliases = sorted(term.get('aliases', []) or [])
    if aliases:
        payload += '|aliases=' + json.dumps(aliases, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _v2_term_defaults(term):
    """Apply v2 defaults to a term in place. Caller must have already
    validated v1-shape required fields."""
    term.setdefault('id', term.get('source', ''))
    term.setdefault('aliases', [])
    term.setdefault('gender', 'unknown')
    term.setdefault('confidence', 'medium')
    term.setdefault('evidence_refs', [])
    term.setdefault('notes', '')
    term.setdefault('frequency', 0)


def _validate_term_strict(term, idx, path):
    """Strict v2 validation for a single term. Raises ValueError mentioning
    the file `path` for context."""
    if not isinstance(term, dict):
        raise ValueError(f"Glossary term #{idx} in {path} must be an object")

    for required in ('source', 'target'):
        if required not in term:
            raise ValueError(
                f"Glossary term #{idx} in {path} missing required field '{required}'"
            )
        if not isinstance(term[required], str):
            raise ValueError(
                f"Glossary term #{idx} in {path}: field '{required}' must be a string, "
                f"got {type(term[required]).__name__}"
            )

    if 'category' in term and not isinstance(term['category'], str):
        raise ValueError(
            f"Glossary term #{idx} in {path}: field 'category' must be a string, "
            f"got {type(term['category']).__name__}"
        )

    if 'frequency' in term:
        if isinstance(term['frequency'], bool) or not isinstance(term['frequency'], int):
            raise ValueError(
                f"Glossary term #{idx} in {path}: field 'frequency' must be an integer, "
                f"got {type(term['frequency']).__name__}"
            )

    if 'id' not in term:
        raise ValueError(f"Glossary term #{idx} in {path} missing required field 'id'")
    if not isinstance(term['id'], str) or not term['id']:
        raise ValueError(
            f"Glossary term #{idx} in {path}: 'id' must be a non-empty string"
        )

    aliases = term.get('aliases', [])
    if not isinstance(aliases, list):
        raise ValueError(
            f"Glossary term #{idx} (id={term['id']!r}) in {path}: 'aliases' must be a list, "
            f"got {type(aliases).__name__}"
        )
    for a_idx, alias in enumerate(aliases):
        if not isinstance(alias, str):
            raise ValueError(
                f"Glossary term #{idx} (id={term['id']!r}) in {path}: alias #{a_idx} "
                f"must be a string, got {type(alias).__name__}"
            )
        if alias == '':
            raise ValueError(
                f"Glossary term id={term['id']!r} in {path}: alias #{a_idx} is the empty "
                f"string. Empty aliases match nothing — remove it."
            )
        if alias == term['source']:
            raise ValueError(
                f"Glossary term id={term['id']!r} in {path}: alias #{a_idx} ({alias!r}) "
                f"equals the term's own source. Drop the redundant alias."
            )
    if len(set(aliases)) != len(aliases):
        seen = set()
        dup = next((a for a in aliases if a in seen or seen.add(a)), None)
        raise ValueError(
            f"Glossary term id={term['id']!r} in {path}: alias {dup!r} is duplicated "
            f"within the term's aliases list. Each alias must be unique per term."
        )

    gender = term.get('gender')
    if gender not in VALID_GENDERS:
        raise ValueError(
            f"Glossary term id={term['id']!r} in {path}: 'gender' must be one of "
            f"{list(VALID_GENDERS)}, got {gender!r}"
        )

    confidence = term.get('confidence')
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(
            f"Glossary term id={term['id']!r} in {path}: 'confidence' must be one of "
            f"{list(VALID_CONFIDENCES)}, got {confidence!r}"
        )

    evidence_refs = term.get('evidence_refs', [])
    if not isinstance(evidence_refs, list):
        raise ValueError(
            f"Glossary term id={term['id']!r} in {path}: 'evidence_refs' must be a list, "
            f"got {type(evidence_refs).__name__}"
        )
    for e_idx, ref in enumerate(evidence_refs):
        if not isinstance(ref, str):
            raise ValueError(
                f"Glossary term id={term['id']!r} in {path}: evidence_refs #{e_idx} "
                f"must be a string, got {type(ref).__name__}"
            )

    notes = term.get('notes', '')
    if not isinstance(notes, str):
        raise ValueError(
            f"Glossary term id={term['id']!r} in {path}: 'notes' must be a string, "
            f"got {type(notes).__name__}"
        )


def _validate_cross_term_invariants(data, path):
    """Term id uniqueness + surface-form uniqueness across terms +
    high_frequency_top_n shape + applied_meta_hashes shape. Run after every
    term has passed individual validation."""
    terms = data['terms']

    seen_ids = {}
    for idx, t in enumerate(terms):
        if t['id'] in seen_ids:
            raise ValueError(
                f"Glossary at {path}: duplicate term id {t['id']!r} "
                f"(terms #{seen_ids[t['id']]} and #{idx}). Each term must have a unique id."
            )
        seen_ids[t['id']] = idx

    surface_owner = {}  # surface -> (term_id, "source"|"alias")
    for t in terms:
        surfaces = [(t['source'], 'source')] + [(a, 'alias') for a in t.get('aliases', [])]
        for surface, role in surfaces:
            if surface in surface_owner:
                other_id, other_role = surface_owner[surface]
                raise ValueError(
                    f"Glossary at {path}: surface form {surface!r} appears as "
                    f"{other_role} of term id={other_id!r} AND as {role} of term id={t['id']!r}. "
                    f"v2 forbids the same surface form mapping to two different terms — "
                    f"the term table can only list one translation per word. "
                    f"Disambiguate by renaming one source, dropping one alias, or merging "
                    f"the two terms into a single entity."
                )
            surface_owner[surface] = (t['id'], role)

    if 'high_frequency_top_n' in data:
        top_n_val = data['high_frequency_top_n']
        if isinstance(top_n_val, bool) or not isinstance(top_n_val, int):
            raise ValueError(
                f"Glossary at {path}: 'high_frequency_top_n' must be an integer, "
                f"got {type(top_n_val).__name__}"
            )

    if 'applied_meta_hashes' in data:
        amh = data['applied_meta_hashes']
        if not isinstance(amh, dict):
            raise ValueError(
                f"Glossary at {path}: 'applied_meta_hashes' must be an object, "
                f"got {type(amh).__name__}"
            )
        for k, v in amh.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"Glossary at {path}: 'applied_meta_hashes' key {k!r} must be a string"
                )
            if not isinstance(v, str) or not _HEX_RE.match(v):
                raise ValueError(
                    f"Glossary at {path}: 'applied_meta_hashes[{k!r}]' must be a hex string, "
                    f"got {v!r}"
                )


def _validate_v1_shape(terms, data, path):
    """Pre-upgrade validation for v1 inputs — catches bad source/target/etc.
    before defaults paper over them."""
    for i, t in enumerate(terms):
        if not isinstance(t, dict):
            raise ValueError(f"Glossary term #{i} in {path} must be an object")
        for required in ('source', 'target'):
            if required not in t:
                raise ValueError(
                    f"Glossary term #{i} in {path} missing required field '{required}'"
                )
            if not isinstance(t[required], str):
                raise ValueError(
                    f"Glossary term #{i} in {path}: field '{required}' must be a string, "
                    f"got {type(t[required]).__name__}"
                )
        if 'category' in t and not isinstance(t['category'], str):
            raise ValueError(
                f"Glossary term #{i} in {path}: field 'category' must be a string, "
                f"got {type(t['category']).__name__}"
            )
        if 'frequency' in t:
            if isinstance(t['frequency'], bool) or not isinstance(t['frequency'], int):
                raise ValueError(
                    f"Glossary term #{i} in {path}: field 'frequency' must be an integer, "
                    f"got {type(t['frequency']).__name__}"
                )
    if 'high_frequency_top_n' in data:
        top_n_val = data['high_frequency_top_n']
        if isinstance(top_n_val, bool) or not isinstance(top_n_val, int):
            raise ValueError(
                f"Glossary at {path}: 'high_frequency_top_n' must be an integer, "
                f"got {type(top_n_val).__name__}"
            )


def load_glossary(path):
    """Load and validate a glossary file. v1 files are auto-upgraded to v2
    on disk (atomic write-back) and a one-line stderr notice is emitted.
    Raises actionable errors on bad input."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Glossary not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Glossary at {path} is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Glossary at {path} must be a JSON object, got {type(data).__name__}")

    version = data.get('version')
    if version not in (1, 2):
        raise ValueError(
            f"Glossary schema version mismatch in {path}: "
            f"expected 1 or 2, got {version!r}. "
            f"Delete the file to rebuild, or migrate it by hand."
        )

    terms = data.get('terms')
    if not isinstance(terms, list):
        raise ValueError(f"Glossary at {path} must have a 'terms' array")

    if version == 1:
        _validate_v1_shape(terms, data, path)

        # v1 legitimately allows two terms sharing a source (differentiated by
        # category). v2 forbids it because the term table can only list one
        # translation per surface form. Reject the upgrade with disambiguation
        # guidance BEFORE writing anything to disk.
        src_groups = {}
        for idx, t in enumerate(terms):
            src_groups.setdefault(t['source'], []).append(idx)
        for src, idxs in src_groups.items():
            if len(idxs) > 1:
                cats = [terms[i].get('category', '') for i in idxs]
                raise ValueError(
                    f"v1→v2 upgrade aborted for {path}: {len(idxs)} terms share source "
                    f"{src!r} (categories: {cats}). v2 forbids polysemous surface forms because "
                    f"the term table can only list one translation per word. Disambiguate by "
                    f"hand (e.g., change one source to {src!r} + a qualifier such as "
                    f"'{src} (Inc.)') and re-load. The file on disk has NOT been modified."
                )

        # Apply defaults, then run strict v2 validation. If v2 validation fails
        # here, the file is NOT rewritten — the user gets the original v1 back.
        data['version'] = GLOSSARY_SCHEMA_VERSION
        for term in terms:
            _v2_term_defaults(term)
        data.setdefault('applied_meta_hashes', {})

        for i, t in enumerate(terms):
            _validate_term_strict(t, i, path)
        _validate_cross_term_invariants(data, path)

        save_glossary(path, data)
        sys.stderr.write(f"Upgraded glossary.json at {path} from v1 to v2.\n")
    else:
        for i, t in enumerate(terms):
            _validate_term_strict(t, i, path)
        _validate_cross_term_invariants(data, path)

    return data


def save_glossary(path, glossary):
    """Atomically write the glossary as v2. Re-asserts the v2 invariants
    (per-term shape, surface-form uniqueness, etc.) before writing."""
    glossary['version'] = GLOSSARY_SCHEMA_VERSION
    glossary.setdefault('applied_meta_hashes', {})
    for term in glossary.get('terms', []):
        _v2_term_defaults(term)

    for i, t in enumerate(glossary.get('terms', [])):
        _validate_term_strict(t, i, path)
    _validate_cross_term_invariants(glossary, path)

    dirname = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix='.glossary-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(glossary, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _count_in_text(source, text):
    """Count occurrences of source in text. CJK uses substring; ASCII uses
    boundary-aware regex so 'C++' and '.NET' work and 'cat' doesn't match
    'category'."""
    if not source:
        return 0
    if _contains_cjk(source):
        if len(source) <= 1:
            return 0
        return text.count(source)
    if source.isascii():
        escaped = re.escape(source)
        pattern = rf'(?<!\w){escaped}(?!\w)'
        return len(re.findall(pattern, text))
    return text.count(source)


def _appears_in_text(source, text):
    """Boundary-aware presence check. Single-CJK-char sources return False
    (would over-match)."""
    return _count_in_text(source, text) > 0


def _term_appears_in_text(term, text):
    """True if the term's source OR any alias appears in text. Each surface
    form is checked via the same boundary-aware matcher."""
    if _appears_in_text(term.get('source', ''), text):
        return True
    for alias in term.get('aliases', []):
        if _appears_in_text(alias, text):
            return True
    return False


def count_frequencies(glossary_path, chunks_dir):
    """Scan all source chunks and write per-term frequency back into the glossary.

    Frequency = sum of source-occurrences plus every alias-occurrence. Source
    chunk discovery excludes `output_chunk*.md` files so re-runs don't
    double-count translated text.
    """
    glossary = load_glossary(glossary_path)
    chunks_dir_path = Path(chunks_dir)

    chunk_paths = sorted(
        p for p in chunks_dir_path.glob('chunk*.md')
        if not p.name.startswith('output_')
    )

    if not chunk_paths:
        print(f"Warning: no chunk*.md files found under {chunks_dir}", file=sys.stderr)

    all_text_parts = []
    for p in chunk_paths:
        try:
            all_text_parts.append(p.read_text(encoding='utf-8'))
        except OSError as e:
            print(f"Warning: could not read {p}: {e}", file=sys.stderr)
    all_text = '\n'.join(all_text_parts)

    for term in glossary['terms']:
        total = 0
        for surface in [term.get('source', '')] + list(term.get('aliases', [])):
            if not surface:
                continue
            if _contains_cjk(surface) and len(surface) <= 1:
                print(
                    f"Warning: skipping frequency count for single-character CJK surface "
                    f"{surface!r} in term id={term.get('id')!r} (would over-match as substring)",
                    file=sys.stderr,
                )
                continue
            total += _count_in_text(surface, all_text)
        term['frequency'] = total

    save_glossary(glossary_path, glossary)


def select_terms_for_chunk(glossary, chunk_text, top_n=None, max_terms=DEFAULT_MAX_TERMS):
    """Return terms relevant to a single chunk: union of (terms whose source
    or any alias appears in chunk_text) and (top-N most-frequent terms across
    the whole book).

    Local hits are protected — if the union exceeds max_terms, local hits keep
    their slots and global top-N is what gets truncated.
    """
    if top_n is None:
        top_n = glossary.get('high_frequency_top_n', DEFAULT_TOP_N)

    terms = glossary.get('terms', [])
    if not terms:
        return []

    sort_key = lambda t: (-t.get('frequency', 0), t.get('source', ''))

    local_terms = [t for t in terms if t.get('source') and _term_appears_in_text(t, chunk_text)]
    local_terms.sort(key=sort_key)

    local_ids = {t.get('id', t.get('source')) for t in local_terms}
    top_pool = sorted(
        (t for t in terms if t.get('id', t.get('source')) not in local_ids),
        key=sort_key,
    )
    top_terms = top_pool[:max(0, top_n)]

    if max_terms <= 0:
        return []
    if len(local_terms) >= max_terms:
        return local_terms[:max_terms]
    remaining = max_terms - len(local_terms)
    return local_terms + top_terms[:remaining]


def format_terms_for_prompt(terms):
    """Render a 3-col markdown table: 原文 | 别名 | 译文.

    Empty input yields empty string so the caller can omit the rule line
    entirely. The 别名 column is empty for terms without aliases. Pipes in
    any field escape to \\|.
    """
    if not terms:
        return ''
    rows = ['| 原文 | 别名 | 译文 |', '|------|------|------|']
    for t in terms:
        source = t.get('source', '').replace('|', '\\|')
        aliases = t.get('aliases', []) or []
        alias_str = ', '.join(a.replace('|', '\\|') for a in aliases)
        target = t.get('target', '').replace('|', '\\|')
        rows.append(f"| {source} | {alias_str} | {target} |")
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser(description="Glossary management for translate-book")
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_count = sub.add_parser('count-frequencies', help="Update frequencies in glossary.json")
    p_count.add_argument('temp_dir', help="Path to <book>_temp/ directory")

    p_print = sub.add_parser('print-terms-for-chunk', help="Print markdown table of terms for a chunk")
    p_print.add_argument('temp_dir')
    p_print.add_argument('chunk_filename', help="e.g. chunk0001.md")
    p_print.add_argument('--top-n', type=int, default=None,
                         help="Override high_frequency_top_n from glossary.json")
    p_print.add_argument('--max-terms', type=int, default=DEFAULT_MAX_TERMS,
                         help=f"Cap on terms in the table (default: {DEFAULT_MAX_TERMS})")

    p_hash = sub.add_parser('compute-hash', help="Print glossary_hash to stdout")
    p_hash.add_argument('temp_dir')

    args = parser.parse_args()
    glossary_path = os.path.join(args.temp_dir, 'glossary.json')

    if args.cmd == 'count-frequencies':
        count_frequencies(glossary_path, args.temp_dir)
    elif args.cmd == 'print-terms-for-chunk':
        glossary = load_glossary(glossary_path)
        chunk_path = os.path.join(args.temp_dir, args.chunk_filename)
        with open(chunk_path, 'r', encoding='utf-8') as f:
            chunk_text = f.read()
        terms = select_terms_for_chunk(
            glossary, chunk_text, top_n=args.top_n, max_terms=args.max_terms
        )
        table = format_terms_for_prompt(terms)
        if table:
            print(table)
    elif args.cmd == 'compute-hash':
        glossary = load_glossary(glossary_path)
        print(glossary_hash(glossary))


if __name__ == '__main__':
    main()
