import process_guard  # noqa: F401  first, so it guards every process the tests below start
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import meta  # noqa: E402


def minimal_meta():
    return {
        'schema_version': 1,
        'new_entities': [],
        'alias_hypotheses': [],
        'attribute_hypotheses': [],
        'used_term_sources': [],
        'conflicts': [],
    }


def full_meta():
    return {
        'schema_version': 1,
        'new_entities': [
            {'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
             'evidence': 'Taig walked in.'},
        ],
        'alias_hypotheses': [
            {'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
             'evidence': 'Taig nodded — Tai must have heard.'},
        ],
        'attribute_hypotheses': [
            {'entity_source': 'Tai', 'attribute': 'gender', 'value': 'male',
             'confidence': 'high', 'evidence': 'He smiled at Tai.'},
        ],
        'used_term_sources': ['Tai', 'Manhattan'],
        'conflicts': [
            {'entity_source': 'Tai', 'field': 'target', 'injected': '泰',
             'observed_better': '太一', 'evidence': 'Context implies 太一.'},
        ],
    }


class RoundTripTests(unittest.TestCase):
    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'output_chunk0001.meta.json')
            data = full_meta()
            meta.save_meta(path, data)
            loaded = meta.load_meta(path)
            self.assertEqual(loaded, data)


class ValidateMetaTests(unittest.TestCase):
    def test_validate_minimal_meta_with_empty_arrays(self):
        meta.validate_meta(minimal_meta())

    def test_validate_full_meta_with_one_of_each_entity_type(self):
        meta.validate_meta(full_meta())

    def test_rejects_invalid_attribute_confidence(self):
        data = minimal_meta()
        data['attribute_hypotheses'] = [{
            'entity_source': 'Tai', 'attribute': 'gender', 'value': 'male',
            'confidence': 'maybe', 'evidence': '...',
        }]
        with self.assertRaises(ValueError) as ctx:
            meta.validate_meta(data)
        self.assertIn("'confidence'", str(ctx.exception))

    def test_rejects_overlong_evidence_field(self):
        data = minimal_meta()
        data['new_entities'] = [{
            'source': 'X', 'target_proposal': 'x', 'category': 'p',
            'evidence': 'A' * (meta.EVIDENCE_MAX_LEN + 1),
        }]
        with self.assertRaises(ValueError) as ctx:
            meta.validate_meta(data)
        self.assertIn("limit is", str(ctx.exception))

    def test_load_rejects_unknown_top_level_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'output_chunk0001.meta.json')
            data = minimal_meta()
            data['surprise'] = 'oops'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            with self.assertRaises(ValueError) as ctx:
                meta.load_meta(path)
            self.assertIn('surprise', str(ctx.exception))

    def test_load_rejects_chunk_id_in_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'output_chunk0001.meta.json')
            data = minimal_meta()
            data['chunk_id'] = 'chunk0001'
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            with self.assertRaises(ValueError) as ctx:
                meta.load_meta(path)
            msg = str(ctx.exception)
            self.assertIn('chunk_id', msg)
            self.assertIn('filename', msg)

    def test_rejects_wrong_schema_version(self):
        data = minimal_meta()
        data['schema_version'] = 99
        with self.assertRaises(ValueError) as ctx:
            meta.validate_meta(data)
        self.assertIn('schema_version', str(ctx.exception))


class HashTests(unittest.TestCase):
    def test_meta_content_hash_stable_across_key_order(self):
        a = full_meta()
        b = json.loads(json.dumps(a, sort_keys=False))
        self.assertEqual(meta.meta_content_hash(a), meta.meta_content_hash(b))

    def test_meta_content_hash_changes_when_content_changes(self):
        a = minimal_meta()
        b = minimal_meta()
        b['used_term_sources'] = ['Manhattan']
        self.assertNotEqual(meta.meta_content_hash(a), meta.meta_content_hash(b))


class ChunkIdFromPathTests(unittest.TestCase):
    def test_chunk_id_from_meta_path_extracts_basename(self):
        self.assertEqual(meta.chunk_id_from_meta_path('/tmp/output_chunk0042.meta.json'),
                         'chunk0042')
        self.assertEqual(meta.chunk_id_from_meta_path('output_chunk9999.meta.json'),
                         'chunk9999')

    def test_chunk_id_from_meta_path_rejects_unexpected_basename(self):
        with self.assertRaises(ValueError):
            meta.chunk_id_from_meta_path('/tmp/random.json')
        with self.assertRaises(ValueError):
            meta.chunk_id_from_meta_path('/tmp/chunk0001.meta.json')  # missing 'output_'


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_survives_simulated_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'output_chunk0001.meta.json')
            original = minimal_meta()
            original['used_term_sources'] = ['Original']
            meta.save_meta(path, original)

            doomed = minimal_meta()
            doomed['used_term_sources'] = ['Doomed']
            with mock.patch('meta.json.dump', side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    meta.save_meta(path, doomed)

            with open(path, 'r', encoding='utf-8') as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk['used_term_sources'], ['Original'])

            leftovers = [f for f in os.listdir(tmp) if f.startswith('.meta-')]
            self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main()
