#!/usr/bin/env python3
"""
run_state.py - Selective re-translation state for translate-book.

The state file records what glossary and source/output hashes were used for
each translated chunk. Future runs can then decide which chunks need actual
re-translation after glossary or source changes, and which existing outputs
only need their state recorded.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import glossary as glossary_mod
from manifest import file_hash, load_manifest


RUN_STATE_VERSION = 1
RUN_STATE_FILE = "run_state.json"


def _run_state_path(temp_dir):
    return os.path.join(temp_dir, RUN_STATE_FILE)


def _empty_state():
    return {"version": RUN_STATE_VERSION, "chunks": {}}


def load_run_state(temp_dir):
    path = _run_state_path(temp_dir)
    if not os.path.exists(path):
        return _empty_state()
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if data.get("version") != RUN_STATE_VERSION:
        raise ValueError(
            f"run_state.json version mismatch: expected {RUN_STATE_VERSION}, "
            f"got {data.get('version')!r}"
        )
    chunks = data.get("chunks")
    if not isinstance(chunks, dict):
        raise ValueError("run_state.json field 'chunks' must be an object")
    return data


def save_run_state(temp_dir, state):
    path = _run_state_path(temp_dir)
    os.makedirs(temp_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.run_state.', suffix='.json', dir=temp_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _chunk_entries_from_manifest(temp_dir):
    manifest = load_manifest(temp_dir)
    if manifest:
        entries = []
        for chunk in sorted(manifest.get("chunks", []), key=lambda c: c.get("order", 0)):
            entries.append({
                "id": chunk["id"],
                "source_file": chunk["source_file"],
                "output_file": chunk["output_file"],
                "manifest_source_hash": chunk.get("source_hash", ""),
                "order": chunk.get("order", 0),
            })
        return entries

    temp_path = Path(temp_dir)
    source_files = sorted(
        p for p in temp_path.glob('chunk*.md')
        if not p.name.startswith('output_')
    )
    return [
        {
            "id": p.stem,
            "source_file": p.name,
            "output_file": f"output_{p.name}",
            "manifest_source_hash": "",
            "order": i,
        }
        for i, p in enumerate(source_files, 1)
    ]


def _load_glossary(temp_dir):
    path = os.path.join(temp_dir, 'glossary.json')
    if not os.path.exists(path):
        return None, "", [], {}
    glossary = glossary_mod.load_glossary(path)
    glossary_hash = glossary_mod.glossary_hash(glossary)
    terms = glossary.get('terms', [])
    term_by_id = {t.get('id', t.get('source')): t for t in terms}
    return glossary, glossary_hash, terms, term_by_id


def _selected_terms_for_chunk(glossary, source_path):
    if glossary is None or not os.path.exists(source_path):
        return []
    text = Path(source_path).read_text(encoding='utf-8')
    return glossary_mod.select_terms_for_chunk(glossary, text)


def _term_ids_and_hashes(terms):
    ids = []
    hashes = {}
    for term in terms:
        term_id = term.get('id', term.get('source'))
        ids.append(term_id)
        hashes[term_id] = glossary_mod.term_hash(term)
    return ids, hashes


def _now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_chunk_record(temp_dir, chunk_id):
    entries = {entry["id"]: entry for entry in _chunk_entries_from_manifest(temp_dir)}
    if chunk_id not in entries:
        raise ValueError(f"Unknown chunk id {chunk_id!r}")

    entry = entries[chunk_id]
    source_path = os.path.join(temp_dir, entry["source_file"])
    output_path = os.path.join(temp_dir, entry["output_file"])
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"Source chunk not found: {source_path}")
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Output chunk not found: {output_path}")
    if os.path.getsize(output_path) == 0:
        raise ValueError(f"Output chunk is empty: {output_path}")

    glossary, glossary_hash, _, _ = _load_glossary(temp_dir)
    selected_terms = _selected_terms_for_chunk(glossary, source_path)
    entity_ids, entity_hashes = _term_ids_and_hashes(selected_terms)

    return {
        "source_file": entry["source_file"],
        "output_file": entry["output_file"],
        "source_hash": file_hash(source_path),
        "output_hash": file_hash(output_path),
        "glossary_version_used": glossary_hash,
        "entity_ids_used": entity_ids,
        "entity_hashes_used": entity_hashes,
        "updated_at": _now_utc(),
    }


def record_chunks(temp_dir, chunk_ids):
    state = load_run_state(temp_dir)
    recorded = []
    for chunk_id in chunk_ids:
        state["chunks"][chunk_id] = build_chunk_record(temp_dir, chunk_id)
        recorded.append(chunk_id)
    save_run_state(temp_dir, state)
    return recorded


def _reason(item, code, detail=None):
    if detail is None:
        item["reasons"].append(code)
    else:
        item["reasons"].append({"code": code, "detail": detail})


def plan(temp_dir, retranslate_untracked=False):
    state = load_run_state(temp_dir)
    glossary, glossary_hash, _, _ = _load_glossary(temp_dir)

    result = {
        "temp_dir": temp_dir,
        "glossary_hash": glossary_hash,
        "translation_chunk_ids": [],
        "record_only_chunk_ids": [],
        "unchanged_chunk_ids": [],
        "chunks": [],
        "decision_rules": [
            "missing_output_or_empty_output",
            "manifest_source_hash_changed",
            "untracked_existing_output",
            "source_hash_changed_since_record",
            "glossary_term_selection_or_term_hash_changed",
        ],
        "record_update_rules": [
            "output_hash_changed_since_record",
        ],
    }

    entries = _chunk_entries_from_manifest(temp_dir)
    records = state.get("chunks", {})

    for entry in entries:
        chunk_id = entry["id"]
        source_path = os.path.join(temp_dir, entry["source_file"])
        output_path = os.path.join(temp_dir, entry["output_file"])
        item = {
            "chunk_id": chunk_id,
            "source_file": entry["source_file"],
            "output_file": entry["output_file"],
            "action": "unchanged",
            "reasons": [],
        }

        if not os.path.exists(output_path):
            item["action"] = "translate"
            _reason(item, "missing_output")
        elif os.path.getsize(output_path) == 0:
            item["action"] = "translate"
            _reason(item, "empty_output")

        current_source_hash = file_hash(source_path) if os.path.exists(source_path) else ""
        manifest_source_hash = entry.get("manifest_source_hash", "")
        if item["action"] == "unchanged" and manifest_source_hash:
            if current_source_hash != manifest_source_hash:
                item["action"] = "translate"
                _reason(item, "manifest_source_hash_changed")

        record = records.get(chunk_id)
        if item["action"] == "unchanged" and record is None:
            if retranslate_untracked:
                item["action"] = "translate"
                _reason(item, "untracked_existing_output")
            else:
                item["action"] = "record"
                _reason(item, "untracked_existing_output")

        if item["action"] == "unchanged" and record is not None:
            if record.get("source_hash") != current_source_hash:
                item["action"] = "translate"
                _reason(item, "source_hash_changed_since_record")

        if item["action"] == "unchanged" and record is not None:
            selected_terms = _selected_terms_for_chunk(glossary, source_path)
            current_entity_ids, current_entity_hashes = _term_ids_and_hashes(selected_terms)
            recorded_entity_ids = record.get("entity_ids_used", [])
            recorded_entity_hashes = record.get("entity_hashes_used", {})

            if current_entity_ids != recorded_entity_ids:
                item["action"] = "translate"
                _reason(item, "glossary_term_selection_changed")
            else:
                changed_ids = [
                    term_id for term_id in current_entity_ids
                    if current_entity_hashes.get(term_id) != recorded_entity_hashes.get(term_id)
                ]
                if changed_ids:
                    item["action"] = "translate"
                    _reason(item, "glossary_term_hash_changed", changed_ids)

        if item["action"] == "unchanged" and record is not None:
            current_output_hash = file_hash(output_path) if os.path.exists(output_path) else ""
            if record.get("output_hash") != current_output_hash:
                item["action"] = "record"
                _reason(item, "output_hash_changed_since_record")

        if item["action"] == "translate":
            result["translation_chunk_ids"].append(chunk_id)
        elif item["action"] == "record":
            result["record_only_chunk_ids"].append(chunk_id)
        else:
            result["unchanged_chunk_ids"].append(chunk_id)
        result["chunks"].append(item)

    return result


def status(temp_dir):
    state = load_run_state(temp_dir)
    entries = _chunk_entries_from_manifest(temp_dir)
    planned = plan(temp_dir)
    return {
        "temp_dir": temp_dir,
        "tracked_chunks": len(state.get("chunks", {})),
        "source_chunks": len(entries),
        "translation_needed": len(planned["translation_chunk_ids"]),
        "record_only_needed": len(planned["record_only_chunk_ids"]),
        "unchanged": len(planned["unchanged_chunk_ids"]),
    }


def _print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="Track selective re-translation state")
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_plan = sub.add_parser('plan', help="Decide which chunks need translation or state recording")
    p_plan.add_argument('temp_dir')
    p_plan.add_argument(
        '--retranslate-untracked',
        action='store_true',
        help="Treat existing outputs without run_state records as needing translation",
    )

    p_record = sub.add_parser('record', help="Record one or more completed output chunks")
    p_record.add_argument('temp_dir')
    p_record.add_argument('chunk_ids', nargs='+')

    p_record_all = sub.add_parser('record-all', help="Record every complete output chunk")
    p_record_all.add_argument('temp_dir')

    p_status = sub.add_parser('status', help="Show run_state progress summary")
    p_status.add_argument('temp_dir')

    args = parser.parse_args()

    try:
        if args.cmd == 'plan':
            _print_json(plan(args.temp_dir, retranslate_untracked=args.retranslate_untracked))
        elif args.cmd == 'record':
            _print_json({"recorded_chunk_ids": record_chunks(args.temp_dir, args.chunk_ids)})
        elif args.cmd == 'record-all':
            plan_data = plan(args.temp_dir)
            eligible = [
                item["chunk_id"] for item in plan_data["chunks"]
                if item["action"] in ("record", "unchanged")
            ]
            _print_json({"recorded_chunk_ids": record_chunks(args.temp_dir, eligible)})
        elif args.cmd == 'status':
            _print_json(status(args.temp_dir))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
