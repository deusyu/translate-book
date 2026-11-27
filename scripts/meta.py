#!/usr/bin/env python3
"""
meta.py - Per-chunk sub-agent observation file.

Each translation sub-agent emits an `output_chunk<NNNN>.meta.json` alongside
its translated chunk. The main agent reads these after each batch and merges
them into the canonical glossary (see merge_meta.py).

The schema (v1):

    {
      "schema_version": 1,
      "new_entities":         [{"source": "...", "target_proposal": "...",
                                "category": "...", "evidence": "..."}],
      "alias_hypotheses":     [{"variant": "...", "may_be_alias_of_source": "...",
                                "evidence": "..."}],
      "attribute_hypotheses": [{"entity_source": "...", "attribute": "...",
                                "value": "...", "confidence": "...",
                                "evidence": "..."}],
      "used_term_sources":    ["..."],
      "conflicts":            [{"entity_source": "...", "field": "...",
                                "injected": "...", "observed_better": "...",
                                "evidence": "..."}]
    }

`chunk_id` is intentionally NOT a field — chunk identity is derived from the
filename (`output_chunk<NNNN>.meta.json` → `chunk<NNNN>`). Letting the
sub-agent fill it would create a hallucination hole.
"""

import hashlib
import json
import os
import tempfile

from manifest import chunk_id_from_file_name


META_SCHEMA_VERSION = 1
EVIDENCE_MAX_LEN = 200

VALID_TOP_LEVEL_KEYS = frozenset({
    'schema_version',
    'new_entities',
    'alias_hypotheses',
    'attribute_hypotheses',
    'used_term_sources',
    'conflicts',
})

VALID_ATTRIBUTE_CONFIDENCES = ('low', 'medium', 'high')


def chunk_id_from_meta_path(path):
    """Extract the chunk identifier from a meta filename.

    Filename is the authoritative source of chunk identity — never trust the
    payload to provide it.
    """
    basename = os.path.basename(path)
    try:
        return chunk_id_from_file_name(basename, 'meta')
    except ValueError:
        raise ValueError(
            f"Meta filename {basename!r} does not match expected pattern "
            f"'output_chunk<NNNN>.meta.json'. Cannot derive chunk_id."
        ) from None


def _canonical_json(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def meta_content_hash(data):
    """SHA-256 of canonical JSON. Used by merge_meta.py to detect whether a
    given meta has already been applied to the glossary."""
    return hashlib.sha256(_canonical_json(data).encode('utf-8')).hexdigest()


def _check_evidence(value, where, path):
    if not isinstance(value, str):
        raise ValueError(
            f"Meta at {path}: 'evidence' in {where} must be a string, "
            f"got {type(value).__name__}"
        )
    if len(value) > EVIDENCE_MAX_LEN:
        raise ValueError(
            f"Meta at {path}: 'evidence' in {where} is {len(value)} chars; "
            f"limit is {EVIDENCE_MAX_LEN}. Quote a shorter excerpt."
        )


def _require_str(value, field, where, path):
    if not isinstance(value, str):
        raise ValueError(
            f"Meta at {path}: {where} field {field!r} must be a string, "
            f"got {type(value).__name__}"
        )


def _validate_array(data, key, path):
    val = data.get(key, [])
    if not isinstance(val, list):
        raise ValueError(
            f"Meta at {path}: {key!r} must be a list, got {type(val).__name__}"
        )
    return val


def validate_meta(data, path='<meta>'):
    """Strict v1 validation. Raises ValueError with actionable messages."""
    if not isinstance(data, dict):
        raise ValueError(
            f"Meta at {path} must be a JSON object, got {type(data).__name__}"
        )

    schema_version = data.get('schema_version')
    if schema_version != META_SCHEMA_VERSION:
        raise ValueError(
            f"Meta at {path}: schema_version mismatch — expected "
            f"{META_SCHEMA_VERSION}, got {schema_version!r}."
        )

    extras = set(data.keys()) - VALID_TOP_LEVEL_KEYS
    if extras:
        # 'chunk_id' is the most likely offender; call it out specifically so
        # the fix is obvious.
        if 'chunk_id' in extras:
            raise ValueError(
                f"Meta at {path}: 'chunk_id' field is not allowed in the meta "
                f"payload — chunk identity is derived from the filename. Remove it."
            )
        raise ValueError(
            f"Meta at {path}: unknown top-level key(s) {sorted(extras)!r}. "
            f"Allowed keys: {sorted(VALID_TOP_LEVEL_KEYS)!r}."
        )

    for entity in _validate_array(data, 'new_entities', path):
        if not isinstance(entity, dict):
            raise ValueError(
                f"Meta at {path}: each new_entities entry must be an object, "
                f"got {type(entity).__name__}"
            )
        for required in ('source', 'target_proposal', 'evidence'):
            if required not in entity:
                raise ValueError(
                    f"Meta at {path}: new_entities entry missing required field "
                    f"{required!r}"
                )
        _require_str(entity['source'], 'source', 'new_entities', path)
        _require_str(entity['target_proposal'], 'target_proposal', 'new_entities', path)
        if 'category' in entity:
            _require_str(entity['category'], 'category', 'new_entities', path)
        _check_evidence(entity['evidence'], 'new_entities', path)

    for alias in _validate_array(data, 'alias_hypotheses', path):
        if not isinstance(alias, dict):
            raise ValueError(
                f"Meta at {path}: each alias_hypotheses entry must be an object"
            )
        for required in ('variant', 'may_be_alias_of_source', 'evidence'):
            if required not in alias:
                raise ValueError(
                    f"Meta at {path}: alias_hypotheses entry missing field {required!r}"
                )
        _require_str(alias['variant'], 'variant', 'alias_hypotheses', path)
        _require_str(alias['may_be_alias_of_source'], 'may_be_alias_of_source',
                     'alias_hypotheses', path)
        _check_evidence(alias['evidence'], 'alias_hypotheses', path)

    for attr in _validate_array(data, 'attribute_hypotheses', path):
        if not isinstance(attr, dict):
            raise ValueError(
                f"Meta at {path}: each attribute_hypotheses entry must be an object"
            )
        for required in ('entity_source', 'attribute', 'value', 'confidence', 'evidence'):
            if required not in attr:
                raise ValueError(
                    f"Meta at {path}: attribute_hypotheses entry missing field {required!r}"
                )
        _require_str(attr['entity_source'], 'entity_source', 'attribute_hypotheses', path)
        _require_str(attr['attribute'], 'attribute', 'attribute_hypotheses', path)
        _require_str(attr['value'], 'value', 'attribute_hypotheses', path)
        if attr['confidence'] not in VALID_ATTRIBUTE_CONFIDENCES:
            raise ValueError(
                f"Meta at {path}: attribute_hypotheses 'confidence' must be one of "
                f"{list(VALID_ATTRIBUTE_CONFIDENCES)}, got {attr['confidence']!r}"
            )
        _check_evidence(attr['evidence'], 'attribute_hypotheses', path)

    for s_idx, src in enumerate(_validate_array(data, 'used_term_sources', path)):
        if not isinstance(src, str):
            raise ValueError(
                f"Meta at {path}: used_term_sources #{s_idx} must be a string, "
                f"got {type(src).__name__}"
            )

    for conflict in _validate_array(data, 'conflicts', path):
        if not isinstance(conflict, dict):
            raise ValueError(
                f"Meta at {path}: each conflicts entry must be an object"
            )
        for required in ('entity_source', 'field', 'injected', 'observed_better', 'evidence'):
            if required not in conflict:
                raise ValueError(
                    f"Meta at {path}: conflicts entry missing field {required!r}"
                )
        _require_str(conflict['entity_source'], 'entity_source', 'conflicts', path)
        _require_str(conflict['field'], 'field', 'conflicts', path)
        _require_str(conflict['injected'], 'injected', 'conflicts', path)
        _require_str(conflict['observed_better'], 'observed_better', 'conflicts', path)
        _check_evidence(conflict['evidence'], 'conflicts', path)


def load_meta(path):
    """Load and strictly validate a meta file. Raises ValueError on any
    problem. Callers that want non-blocking behavior should wrap this in a
    quarantine wrapper (see merge_meta.py)."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Meta not found: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Meta at {path} is not valid JSON: {e}") from e
    validate_meta(data, path)
    return data


def save_meta(path, data):
    """Atomically write a meta file. Validates before writing."""
    validate_meta(data, path)

    dirname = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix='.meta-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
