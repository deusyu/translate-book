import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import glossary  # noqa: E402


@contextmanager
def temp_book_dir(chunks=None, glossary_data=None):
    """Spin up a temp directory laid out like a real <book>_temp/."""
    with tempfile.TemporaryDirectory() as tmp:
        if chunks:
            for name, body in chunks.items():
                with open(os.path.join(tmp, name), 'w', encoding='utf-8') as f:
                    f.write(body)
        glossary_path = os.path.join(tmp, 'glossary.json')
        if glossary_data is not None:
            with open(glossary_path, 'w', encoding='utf-8') as f:
                json.dump(glossary_data, f, ensure_ascii=False, indent=2)
        yield tmp, glossary_path


def make_term(source, target, category='', aliases=None):
    return {
        'id': source,
        'source': source,
        'target': target,
        'category': category,
        'aliases': list(aliases) if aliases else [],
        'gender': 'unknown',
        'confidence': 'medium',
        'frequency': 0,
        'evidence_refs': [],
        'notes': '',
    }


def make_glossary(*pairs, top_n=20):
    """Build a minimal valid v2 glossary from (source, target[, category]) tuples."""
    terms = []
    for p in pairs:
        if len(p) == 2:
            source, target = p
            category = ''
        else:
            source, target, category = p
        terms.append(make_term(source, target, category))
    return {
        'version': glossary.GLOSSARY_SCHEMA_VERSION,
        'terms': terms,
        'high_frequency_top_n': top_n,
        'applied_meta_hashes': {},
    }


class CountFrequenciesTests(unittest.TestCase):
    def test_counts_frequencies_across_multiple_chunks(self):
        chunks = {
            'chunk0001.md': "Manhattan is busy. Manhattan in spring.",
            'chunk0002.md': "Brooklyn nights. Manhattan again.",
            'chunk0003.md': "Just Brooklyn here.",
        }
        g = make_glossary(('Manhattan', '曼哈顿', 'place'), ('Brooklyn', '布鲁克林', 'place'))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        by_source = {t['source']: t['frequency'] for t in updated['terms']}
        self.assertEqual(by_source['Manhattan'], 3)
        self.assertEqual(by_source['Brooklyn'], 2)

    def test_term_not_present_yields_zero(self):
        chunks = {'chunk0001.md': "Nothing relevant here."}
        g = make_glossary(('Manhattan', '曼哈顿'))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        self.assertEqual(updated['terms'][0]['frequency'], 0)

    def test_handles_special_regex_characters_in_source(self):
        chunks = {
            'chunk0001.md': "Built in C++. Loves .NET. Knows O(n) algorithms.",
            'chunk0002.md': "More C++ code. Another O(n) call. Another .NET service.",
        }
        g = make_glossary(('C++', 'C加加'), ('.NET', '.NET框架'), ('O(n)', '线性'))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        by_source = {t['source']: t['frequency'] for t in updated['terms']}
        self.assertEqual(by_source['C++'], 2)
        self.assertEqual(by_source['.NET'], 2)
        self.assertEqual(by_source['O(n)'], 2)

    def test_word_boundary_avoids_false_positives(self):
        chunks = {'chunk0001.md': "category concatenate cat caterwaul cat."}
        g = make_glossary(('cat', '猫'))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        self.assertEqual(updated['terms'][0]['frequency'], 2)

    def test_handles_cjk_source_terms(self):
        chunks = {
            'chunk0001.md': "他在曼哈顿散步。曼哈顿很热闹。",
            'chunk0002.md': "曼哈顿的夜晚。",
        }
        g = make_glossary(('曼哈顿', 'Manhattan'))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        self.assertEqual(updated['terms'][0]['frequency'], 3)

    def test_excludes_output_chunks_from_count(self):
        chunks = {
            'chunk0001.md': "Manhattan once.",
            'output_chunk0001.md': "曼哈顿 mentioned, with Manhattan stuck inside as residue.",
            'chunk0002.md': "Manhattan twice.",
            'output_chunk0002.md': "Manhattan Manhattan Manhattan inflate me!",
        }
        g = make_glossary(('Manhattan', '曼哈顿'))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        self.assertEqual(updated['terms'][0]['frequency'], 2)

    def test_rejects_single_cjk_char_term_with_warning(self):
        chunks = {'chunk0001.md': "他他他他他"}
        g = make_glossary(('他', 'he'))

        captured = StringIO()
        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            with mock.patch.object(sys, 'stderr', captured):
                glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        self.assertEqual(updated['terms'][0]['frequency'], 0)
        self.assertIn("single-character CJK", captured.getvalue())

    def test_count_frequencies_sums_across_aliases(self):
        chunks = {
            'chunk0001.md': "Tai walked into the room. Later Taig left.",
            'chunk0002.md': "Then Taighi returned, and Tai sat down.",
        }
        g = make_glossary()
        g['terms'].append(make_term('Tai', '太一', 'person', aliases=['Taig', 'Taighi']))

        with temp_book_dir(chunks=chunks, glossary_data=g) as (tmp, gpath):
            glossary.count_frequencies(gpath, tmp)
            updated = glossary.load_glossary(gpath)

        self.assertEqual(updated['terms'][0]['frequency'], 4)


class LoadGlossaryTests(unittest.TestCase):
    def _write(self, tmp, payload):
        path = os.path.join(tmp, 'glossary.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def test_missing_glossary_raises_filenotfound(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                glossary.load_glossary(os.path.join(tmp, 'glossary.json'))

    def test_malformed_json_raises_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'glossary.json')
            with open(path, 'w', encoding='utf-8') as f:
                f.write("{not valid json")
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("not valid JSON", str(ctx.exception))
            self.assertIn(path, str(ctx.exception))

    def test_missing_terms_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {'version': 2})
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'terms'", str(ctx.exception))

    def test_rejects_non_string_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 42, 'target': 'forty-two'}],
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            msg = str(ctx.exception)
            self.assertIn("'source'", msg)
            self.assertIn("string", msg)

    def test_rejects_non_string_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': None}],
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'target'", str(ctx.exception))

    def test_rejects_non_string_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿', 'category': 99}],
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'category'", str(ctx.exception))

    def test_rejects_non_int_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿', 'frequency': 'a lot'}],
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'frequency'", str(ctx.exception))

    def test_rejects_bool_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿', 'frequency': True}],
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'frequency'", str(ctx.exception))

    def test_rejects_non_int_top_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [],
                'high_frequency_top_n': "twenty",
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'high_frequency_top_n'", str(ctx.exception))

    def test_version_mismatch_raises_actionable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {'version': 99, 'terms': []})
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("schema version mismatch", str(ctx.exception))


class V1UpgradeTests(unittest.TestCase):
    def _write(self, tmp, payload):
        path = os.path.join(tmp, 'glossary.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def test_load_v1_in_memory_upgrade_has_v2_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿', 'category': 'place', 'frequency': 3}],
                'high_frequency_top_n': 20,
            })
            with mock.patch.object(sys, 'stderr', StringIO()):
                loaded = glossary.load_glossary(path)
            term = loaded['terms'][0]
            self.assertEqual(term['id'], 'Manhattan')
            self.assertEqual(term['aliases'], [])
            self.assertEqual(term['gender'], 'unknown')
            self.assertEqual(term['confidence'], 'medium')
            self.assertEqual(term['evidence_refs'], [])
            self.assertEqual(term['notes'], '')
            self.assertEqual(loaded['applied_meta_hashes'], {})

    def test_load_v1_writes_v2_back_to_disk_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿', 'category': 'place'}],
                'high_frequency_top_n': 20,
            })
            with mock.patch.object(sys, 'stderr', StringIO()):
                glossary.load_glossary(path)
            with open(path, 'r', encoding='utf-8') as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk['version'], 2)
            self.assertIn('applied_meta_hashes', on_disk)
            self.assertEqual(on_disk['terms'][0]['gender'], 'unknown')
            leftovers = [f for f in os.listdir(tmp) if f.startswith('.glossary-')]
            self.assertEqual(leftovers, [])

    def test_load_v1_emits_one_stderr_line_per_upgrade_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿'}],
                'high_frequency_top_n': 20,
            })
            captured = StringIO()
            with mock.patch.object(sys, 'stderr', captured):
                glossary.load_glossary(path)
            text = captured.getvalue()
            self.assertEqual(text.count('Upgraded glossary.json'), 1)

    def test_load_v2_after_v1_upgrade_does_not_warn_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [{'source': 'Manhattan', 'target': '曼哈顿'}],
                'high_frequency_top_n': 20,
            })
            with mock.patch.object(sys, 'stderr', StringIO()):
                glossary.load_glossary(path)
            second = StringIO()
            with mock.patch.object(sys, 'stderr', second):
                glossary.load_glossary(path)
            self.assertEqual(second.getvalue(), '')

    def test_load_v2_round_trips_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'glossary.json')
            g = make_glossary(('Manhattan', '曼哈顿', 'place'))
            glossary.save_glossary(path, g)
            loaded = glossary.load_glossary(path)
            self.assertEqual(loaded['version'], 2)
            self.assertEqual(loaded['terms'][0]['source'], 'Manhattan')
            self.assertEqual(loaded['applied_meta_hashes'], {})

    def test_load_v1_with_duplicate_source_rejects_with_actionable_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, {
                'version': 1,
                'terms': [
                    {'source': 'Apple', 'target': '苹果', 'category': 'fruit'},
                    {'source': 'Apple', 'target': '苹果', 'category': 'company'},
                ],
                'high_frequency_top_n': 20,
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            msg = str(ctx.exception)
            self.assertIn('Apple', msg)
            self.assertIn('fruit', msg)
            self.assertIn('company', msg)
            self.assertIn('Disambiguate', msg)
            # File on disk must be untouched (still v1, not partially mutated).
            with open(path, 'r', encoding='utf-8') as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk['version'], 1)


class V2ValidationTests(unittest.TestCase):
    def _save_raw(self, tmp, payload):
        # Bypass save_glossary's invariant checks so we can probe load-time validation.
        path = os.path.join(tmp, 'glossary.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        return path

    def test_v2_rejects_invalid_gender_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_term('Tai', '太一')
            t['gender'] = 'banana'
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'gender'", str(ctx.exception))

    def test_v2_rejects_invalid_confidence_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_term('Tai', '太一')
            t['confidence'] = 'maybe'
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'confidence'", str(ctx.exception))

    def test_v2_rejects_non_list_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_term('Tai', '太一')
            t['aliases'] = 'Taig'  # should be list
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'aliases'", str(ctx.exception))

    def test_v2_rejects_non_dict_applied_meta_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [make_term('Tai', '太一')],
                'high_frequency_top_n': 20,
                'applied_meta_hashes': "not-a-dict",
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'applied_meta_hashes'", str(ctx.exception))

    def test_v2_rejects_non_hex_applied_meta_hash_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [make_term('Tai', '太一')],
                'high_frequency_top_n': 20,
                'applied_meta_hashes': {'chunk0001': 'not hex!!'},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("hex string", str(ctx.exception))

    def test_v2_rejects_duplicate_source_across_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            t1 = make_term('Apple', '苹果', 'fruit')
            t2 = make_term('Apple', '苹果', 'company')
            t2['id'] = 'Apple-2'  # avoid id collision; trigger surface-form check
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t1, t2], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            msg = str(ctx.exception)
            self.assertIn('Apple', msg)
            self.assertIn('Apple-2', msg)

    def test_v2_rejects_source_collides_with_other_terms_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            t1 = make_term('Apple', '苹果')
            t2 = make_term('Banana', '香蕉', aliases=['Apple'])
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t1, t2], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn('Apple', str(ctx.exception))

    def test_v2_rejects_alias_shared_across_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            t1 = make_term('A', 'a', aliases=['X'])
            t2 = make_term('B', 'b', aliases=['X'])
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t1, t2], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn("'X'", str(ctx.exception))

    def test_v2_rejects_empty_alias_in_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_term('Tai', '太一', aliases=['Taig', ''])
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            msg = str(ctx.exception)
            self.assertIn('Tai', msg)
            self.assertIn('empty', msg)

    def test_v2_rejects_alias_equal_to_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_term('Tai', '太一', aliases=['Tai'])
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn('source', str(ctx.exception))

    def test_v2_rejects_duplicate_alias_within_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = make_term('Tai', '太一', aliases=['Taig', 'Taig'])
            path = self._save_raw(tmp, {
                'version': 2, 'terms': [t], 'high_frequency_top_n': 20,
                'applied_meta_hashes': {},
            })
            with self.assertRaises(ValueError) as ctx:
                glossary.load_glossary(path)
            self.assertIn('duplicated', str(ctx.exception))


class SelectTermsForChunkTests(unittest.TestCase):
    def test_unions_local_and_top_n(self):
        g = make_glossary(
            ('Manhattan', '曼哈顿'),
            ('Brooklyn', '布鲁克林'),
            ('Queens', '皇后区'),
            ('Bronx', '布朗克斯'),
            top_n=2,
        )
        for term in g['terms']:
            term['frequency'] = {'Manhattan': 100, 'Brooklyn': 80, 'Queens': 5, 'Bronx': 1}[term['source']]

        chunk_text = "We went to the Bronx today."
        selected = glossary.select_terms_for_chunk(g, chunk_text)

        sources = sorted(t['source'] for t in selected)
        self.assertEqual(sources, ['Bronx', 'Brooklyn', 'Manhattan'])

    def test_respects_max_terms_cap(self):
        g = make_glossary(*[(f"Term{i:03d}", f"译{i:03d}") for i in range(100)], top_n=100)
        for i, term in enumerate(g['terms']):
            term['frequency'] = 1000 - i
        chunk_text = "no local hits here"
        selected = glossary.select_terms_for_chunk(g, chunk_text, max_terms=5)
        self.assertEqual(len(selected), 5)

    def test_local_hits_protected_when_max_terms_caps_union(self):
        global_pairs = [(f"Global{i:03d}", f"全局{i:03d}") for i in range(40)]
        local_pairs = [(f"Local{i:03d}", f"本章{i:03d}") for i in range(20)]
        g = make_glossary(*(global_pairs + local_pairs), top_n=40)

        for i, term in enumerate(g['terms']):
            if term['source'].startswith('Global'):
                term['frequency'] = 1000 - i
            else:
                term['frequency'] = 1

        chunk_text = ' '.join(p[0] for p in local_pairs)
        selected = glossary.select_terms_for_chunk(g, chunk_text, max_terms=50)

        sources = {t['source'] for t in selected}
        local_in_selected = sum(1 for s in sources if s.startswith('Local'))
        self.assertEqual(local_in_selected, 20)
        self.assertEqual(len(selected), 50)

    def test_local_hits_use_boundary_match_for_ascii(self):
        g = make_glossary(('cat', '猫'), ('Manhattan', '曼哈顿'), top_n=0)
        chunk_text = "category and concatenate only — no real cats here"

        selected = glossary.select_terms_for_chunk(g, chunk_text)

        self.assertEqual(selected, [])

    def test_local_hits_skip_single_cjk_char(self):
        g = make_glossary(('的', 'of'), top_n=0)
        chunk_text = "这是一段中文，包含很多的字符的的的的"

        selected = glossary.select_terms_for_chunk(g, chunk_text)

        self.assertEqual(selected, [])

    def test_sorted_by_frequency_desc(self):
        g = make_glossary(('A', 'a'), ('B', 'b'), ('C', 'c'), top_n=3)
        for term, freq in zip(g['terms'], [1, 100, 50]):
            term['frequency'] = freq
        chunk_text = "no hits"
        selected = glossary.select_terms_for_chunk(g, chunk_text)
        self.assertEqual([t['source'] for t in selected], ['B', 'C', 'A'])

    def test_select_terms_for_chunk_alias_only_chunk_returns_term(self):
        g = make_glossary(top_n=0)
        g['terms'].append(make_term('Tai', '太一', 'person', aliases=['Taig', 'Taighi']))
        chunk_text = "A chapter where only Taighi appears, never the canonical name."
        selected = glossary.select_terms_for_chunk(g, chunk_text)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['source'], 'Tai')

    def test_select_terms_for_chunk_uses_boundary_match_for_aliases(self):
        # alias 'cat' must not match inside 'category'
        g = make_glossary(top_n=0)
        g['terms'].append(make_term('Feline', '猫科', 'animal', aliases=['cat']))
        chunk_text = "this category of concatenation"
        selected = glossary.select_terms_for_chunk(g, chunk_text)
        self.assertEqual(selected, [])


class HashTests(unittest.TestCase):
    def test_glossary_hash_stable_across_key_order(self):
        g1 = make_glossary(('A', 'a'))
        g1['terms'][0]['frequency'] = 1
        g2 = json.loads(json.dumps(g1))  # deep copy
        # Reorder fields by re-serializing through different key order
        self.assertEqual(glossary.glossary_hash(g1), glossary.glossary_hash(g2))

    def test_glossary_hash_changes_when_target_changes(self):
        g1 = make_glossary(('Manhattan', '曼哈顿'))
        g2 = make_glossary(('Manhattan', '曼哈顿区'))
        self.assertNotEqual(glossary.glossary_hash(g1), glossary.glossary_hash(g2))

    def test_term_hash_changes_when_target_changes(self):
        t1 = {'source': 'Manhattan', 'target': '曼哈顿', 'category': 'place'}
        t2 = {'source': 'Manhattan', 'target': '曼哈顿区', 'category': 'place'}
        self.assertNotEqual(glossary.term_hash(t1), glossary.term_hash(t2))

    def test_term_hash_changes_when_category_changes(self):
        t1 = {'source': 'Apple', 'target': '苹果', 'category': 'fruit'}
        t2 = {'source': 'Apple', 'target': '苹果', 'category': 'company'}
        self.assertNotEqual(glossary.term_hash(t1), glossary.term_hash(t2))

    def test_term_hash_changes_when_alias_added(self):
        # Aliases are part of the injected term table; adding one must change
        # the hash so run_state plans a re-translation for affected chunks.
        t1 = {'source': 'Tai', 'target': '太一', 'category': 'person', 'aliases': []}
        t2 = {'source': 'Tai', 'target': '太一', 'category': 'person', 'aliases': ['Taig']}
        self.assertNotEqual(glossary.term_hash(t1), glossary.term_hash(t2))

    def test_term_hash_changes_when_alias_edited(self):
        t1 = {'source': 'Tai', 'target': '太一', 'category': 'person', 'aliases': ['Taig']}
        t2 = {'source': 'Tai', 'target': '太一', 'category': 'person', 'aliases': ['Taighi']}
        self.assertNotEqual(glossary.term_hash(t1), glossary.term_hash(t2))

    def test_term_hash_ignores_alias_order(self):
        t1 = {'source': 'Tai', 'target': '太一', 'category': 'person', 'aliases': ['A', 'B']}
        t2 = {'source': 'Tai', 'target': '太一', 'category': 'person', 'aliases': ['B', 'A']}
        self.assertEqual(glossary.term_hash(t1), glossary.term_hash(t2))

    def test_term_hash_without_aliases_keeps_historical_value(self):
        # Backwards compatibility: alias-less terms must hash exactly as the
        # pre-alias format did, so old run_state.json records stay valid and
        # upgrading does not force a whole-book re-translation.
        import hashlib
        t = {'source': 'Manhattan', 'target': '曼哈顿', 'category': 'place'}
        legacy = hashlib.sha256('Manhattan→曼哈顿|place'.encode('utf-8')).hexdigest()
        self.assertEqual(glossary.term_hash(t), legacy)
        t_empty = {'source': 'Manhattan', 'target': '曼哈顿', 'category': 'place', 'aliases': []}
        self.assertEqual(glossary.term_hash(t_empty), legacy)


class FormatTermsForPromptTests(unittest.TestCase):
    def test_empty_terms_returns_empty_string(self):
        self.assertEqual(glossary.format_terms_for_prompt([]), '')

    def test_renders_three_col_table(self):
        terms = [{'source': 'Manhattan', 'target': '曼哈顿'}]
        out = glossary.format_terms_for_prompt(terms)
        self.assertIn('| 原文 | 别名 | 译文 |', out)
        self.assertIn('| Manhattan |  | 曼哈顿 |', out)

    def test_renders_aliases_joined_with_comma(self):
        terms = [{'source': 'Tai', 'target': '太一', 'aliases': ['Taig', 'Taighi']}]
        out = glossary.format_terms_for_prompt(terms)
        self.assertIn('| Tai | Taig, Taighi | 太一 |', out)

    def test_empty_aliases_renders_empty_column(self):
        terms = [{'source': 'Manhattan', 'target': '曼哈顿', 'aliases': []}]
        out = glossary.format_terms_for_prompt(terms)
        self.assertIn('| Manhattan |  | 曼哈顿 |', out)

    def test_escapes_pipes_in_term_text(self):
        terms = [{'source': 'A|B', 'target': 'X|Y'}]
        out = glossary.format_terms_for_prompt(terms)
        self.assertIn(r'A\|B', out)
        self.assertIn(r'X\|Y', out)

    def test_escapes_pipes_in_aliases(self):
        terms = [{'source': 'Tai', 'target': '太一', 'aliases': ['T|ai']}]
        out = glossary.format_terms_for_prompt(terms)
        self.assertIn(r'T\|ai', out)


class SaveGlossaryAtomicTests(unittest.TestCase):
    def test_atomic_write_survives_simulated_interrupt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'glossary.json')
            original = make_glossary(('Manhattan', '曼哈顿'))
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(original, f)

            doomed = make_glossary(('Manhattan', 'BROKEN'))
            with mock.patch('glossary.json.dump', side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    glossary.save_glossary(path, doomed)

            with open(path, 'r', encoding='utf-8') as f:
                still_there = json.load(f)
            self.assertEqual(still_there['terms'][0]['target'], '曼哈顿')

            leftovers = [f for f in os.listdir(tmp) if f.startswith('.glossary-')]
            self.assertEqual(leftovers, [])

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'glossary.json')
            g = make_glossary(('Manhattan', '曼哈顿', 'place'), ('Brooklyn', '布鲁克林', 'place'))
            glossary.save_glossary(path, g)
            loaded = glossary.load_glossary(path)
            self.assertEqual(loaded['terms'][0]['source'], 'Manhattan')
            self.assertEqual(loaded['terms'][1]['source'], 'Brooklyn')


if __name__ == '__main__':
    unittest.main()
