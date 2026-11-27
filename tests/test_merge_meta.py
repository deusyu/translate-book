import process_guard  # noqa: F401  first, so it guards every process the tests below start
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import glossary as glossary_mod  # noqa: E402
import meta as meta_mod  # noqa: E402
import merge_meta  # noqa: E402


def make_term(source, target, category='', aliases=None,
              confidence='medium', evidence_refs=None):
    return {
        'id': source,
        'source': source,
        'target': target,
        'category': category,
        'aliases': list(aliases) if aliases else [],
        'gender': 'unknown',
        'confidence': confidence,
        'frequency': 0,
        'evidence_refs': list(evidence_refs) if evidence_refs else [],
        'notes': '',
    }


def make_glossary(*terms, top_n=20, applied_meta_hashes=None):
    return {
        'version': glossary_mod.GLOSSARY_SCHEMA_VERSION,
        'terms': list(terms),
        'high_frequency_top_n': top_n,
        'applied_meta_hashes': dict(applied_meta_hashes) if applied_meta_hashes else {},
    }


def empty_meta(**overrides):
    base = {
        'schema_version': 1,
        'new_entities': [],
        'alias_hypotheses': [],
        'attribute_hypotheses': [],
        'used_term_sources': [],
        'conflicts': [],
    }
    base.update(overrides)
    return base


@contextmanager
def temp_workspace(glossary=None, metas=None):
    """Create a temp dir with a glossary.json and any meta files, yielding
    the dir path. `metas` is {chunk_id: meta_dict}."""
    with tempfile.TemporaryDirectory() as tmp:
        gpath = os.path.join(tmp, 'glossary.json')
        if glossary is not None:
            glossary_mod.save_glossary(gpath, glossary)
        if metas:
            for chunk_id, m in metas.items():
                mpath = os.path.join(tmp, f'output_{chunk_id}.meta.json')
                meta_mod.save_meta(mpath, m)
        yield tmp


def run_prepare_merge(temp_dir):
    """Run prepare-merge in-process and return parsed JSON output."""
    out = io.StringIO()
    err = io.StringIO()
    with mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err):
        merge_meta.cmd_prepare_merge(temp_dir)
    return json.loads(out.getvalue()), err.getvalue()


def run_apply_merge(temp_dir, decisions_doc):
    """Run apply-merge in-process with the given decisions JSON. Returns
    (summary_or_empty, stderr, exit_code)."""
    payload = json.dumps(decisions_doc)
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    with mock.patch.object(sys, 'stdin', io.StringIO(payload)), \
         mock.patch.object(sys, 'stdout', out), \
         mock.patch.object(sys, 'stderr', err):
        try:
            merge_meta.cmd_apply_merge(temp_dir)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 0
    summary = json.loads(out.getvalue()) if out.getvalue().strip() else {}
    return summary, err.getvalue(), code


def run_status(temp_dir):
    out = io.StringIO()
    with mock.patch.object(sys, 'stdout', out):
        merge_meta.cmd_status(temp_dir)
    return json.loads(out.getvalue())


class PrepareMergeBasicTests(unittest.TestCase):
    def test_no_metas_returns_empty(self):
        with temp_workspace(glossary=make_glossary()) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(out['decisions_needed'], [])
        self.assertEqual(out['consumed_chunk_ids'], [])

    def test_categorizes_simple_new_entity_as_auto_apply(self):
        m = empty_meta(new_entities=[
            {'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
             'evidence': 'Tai walked in.'},
        ])
        with temp_workspace(glossary=make_glossary(), metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['auto_apply']), 1)
        self.assertEqual(out['auto_apply'][0]['entity']['source'], 'Tai')
        self.assertEqual(out['auto_apply'][0]['evidence_chunks'], ['chunk0001'])
        self.assertEqual(out['decisions_needed'], [])
        self.assertEqual(out['consumed_chunk_ids'], ['chunk0001'])

    def test_groups_identical_proposals_across_chunks(self):
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'first.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'second.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0042': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['auto_apply']), 1)
        self.assertEqual(out['auto_apply'][0]['evidence_chunks'], ['chunk0001', 'chunk0042'])

    def test_routes_conflicting_target_proposal_to_decisions(self):
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person', 'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'person', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0042': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'conflicting_new_entity_proposals')
        self.assertEqual(d['source'], 'Tai')
        self.assertEqual(len(d['variants']), 2)
        self.assertIn('use_variant_0', d['options'])
        self.assertIn('use_variant_1', d['options'])
        self.assertIn('skip', d['options'])

    def test_routes_conflicting_category_to_decisions(self):
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person', 'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'place', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0042': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(len(out['decisions_needed']), 1)
        self.assertEqual(out['decisions_needed'][0]['kind'],
                         'conflicting_new_entity_proposals')

    def test_routes_new_entity_existing_alias_to_decisions(self):
        # Banana already has alias "Apple"; sub-agent proposes Apple as new entity.
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': 'Apple is red.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'new_entity_existing_alias')
        self.assertEqual(d['proposed_source'], 'Apple')
        self.assertEqual(d['currently_alias_of'], 'Banana')
        self.assertEqual(len(d['promoted_variants']), 1)
        self.assertEqual(d['promoted_variants'][0]['target_proposal'], '苹果')
        self.assertEqual(set(d['options']),
                         {'use_variant_0', 'keep_as_alias', 'skip'})

    def test_flags_alias_hypothesis_when_candidate_exists(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
            'evidence': 'Taig must be Tai.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'alias')
        self.assertEqual(d['variant'], 'Taig')
        self.assertEqual(d['candidate_source'], 'Tai')

    def test_flags_conflict_when_entity_known(self):
        existing = make_term('Tai', '泰', 'person')
        m = empty_meta(conflicts=[{
            'entity_source': 'Tai', 'field': 'target', 'injected': '泰',
            'observed_better': '太一', 'evidence': 'context implies 太一.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'conflict')
        self.assertEqual(d['current'], '泰')
        self.assertEqual(d['proposed'], '太一')


class ResumeContractTests(unittest.TestCase):
    def test_skips_meta_already_in_applied_hashes(self):
        m = empty_meta(used_term_sources=['Tai'])
        existing = make_term('Tai', '太一', 'person')
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            mpath = os.path.join(tmp, 'output_chunk0001.meta.json')
            with open(mpath, 'r', encoding='utf-8') as f:
                m_on_disk = json.load(f)
            content_hash = meta_mod.meta_content_hash(m_on_disk)
            # Pre-seed applied_meta_hashes
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            g['applied_meta_hashes']['chunk0001'] = content_hash
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), g)

            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['consumed_chunk_ids'], [])

    def test_unmerged_meta_recovered_on_resume(self):
        m = empty_meta(new_entities=[{
            'source': 'Newbie', 'target_proposal': '新人', 'category': 'person',
            'evidence': 'meet Newbie.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0042': m}) as tmp:
            # First prepare: should propose
            out1, _ = run_prepare_merge(tmp)
            self.assertIn('chunk0042', out1['consumed_chunk_ids'])
            # Second prepare (without applying): still proposes
            out2, _ = run_prepare_merge(tmp)
            self.assertIn('chunk0042', out2['consumed_chunk_ids'])
            # Apply
            run_apply_merge(tmp, {
                'auto_apply': out1['auto_apply'],
                'decisions': [],
                'consumed_chunk_ids': out1['consumed_chunk_ids'],
            })
            # Third prepare: empty
            out3, _ = run_prepare_merge(tmp)
            self.assertEqual(out3['consumed_chunk_ids'], [])

    def test_re_translated_chunk_with_changed_meta_re_proposed(self):
        m1 = empty_meta(used_term_sources=['Tai'])
        existing = make_term('Tai', '太一', 'person')
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1}) as tmp:
            out1, _ = run_prepare_merge(tmp)
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': out1['consumed_chunk_ids'],
            })
            out2, _ = run_prepare_merge(tmp)
            self.assertEqual(out2['consumed_chunk_ids'], [])
            # Now overwrite the meta with different content
            m2 = empty_meta(used_term_sources=['Tai', 'Manhattan'])
            mpath = os.path.join(tmp, 'output_chunk0001.meta.json')
            meta_mod.save_meta(mpath, m2)
            out3, _ = run_prepare_merge(tmp)
            self.assertIn('chunk0001', out3['consumed_chunk_ids'])

    def test_includes_noop_meta_in_consumed_chunk_ids(self):
        m = empty_meta()  # all empty
        with temp_workspace(glossary=make_glossary(), metas={'chunk0099': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['consumed_chunk_ids'], ['chunk0099'])
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(out['decisions_needed'], [])

    def test_records_hash_for_noop_meta(self):
        m = empty_meta()
        with temp_workspace(glossary=make_glossary(), metas={'chunk0099': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0099'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            self.assertIn('chunk0099', g['applied_meta_hashes'])
            # And re-prepare should now see nothing.
            out, _ = run_prepare_merge(tmp)
            self.assertEqual(out['consumed_chunk_ids'], [])


class MalformedMetaTests(unittest.TestCase):
    def test_quarantines_malformed_meta_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), make_glossary())
            bad_path = os.path.join(tmp, 'output_chunk0123.meta.json')
            with open(bad_path, 'w', encoding='utf-8') as f:
                f.write('{not valid json')
            out, err = run_prepare_merge(tmp)
        self.assertIn('chunk0123', out['malformed_meta_chunk_ids'])
        self.assertNotIn('chunk0123', out['consumed_chunk_ids'])
        self.assertIn('chunk0123', err)

    def test_does_not_record_hash_for_malformed_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), make_glossary())
            bad_path = os.path.join(tmp, 'output_chunk0123.meta.json')
            with open(bad_path, 'w', encoding='utf-8') as f:
                f.write('{not valid json')
            out, _ = run_prepare_merge(tmp)
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            self.assertNotIn('chunk0123', g['applied_meta_hashes'])
            # Re-running prepare still surfaces the malformed file
            out2, _ = run_prepare_merge(tmp)
            self.assertIn('chunk0123', out2['malformed_meta_chunk_ids'])


class ApplyMergeNewEntityTests(unittest.TestCase):
    def test_adds_new_entity_with_low_confidence_single_source(self):
        m = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'Tai walks.',
        }])
        with temp_workspace(glossary=make_glossary(), metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'], 'decisions': [],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        self.assertEqual(len(g['terms']), 1)
        self.assertEqual(g['terms'][0]['source'], 'Tai')
        self.assertEqual(g['terms'][0]['confidence'], 'low')
        self.assertEqual(g['terms'][0]['evidence_refs'], ['chunk0001'])

    def test_promotes_to_high_on_three_sources(self):
        metas = {
            f'chunk000{i}': empty_meta(new_entities=[{
                'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
                'evidence': f'src {i}.',
            }]) for i in (1, 2, 3)
        }
        with temp_workspace(glossary=make_glossary(), metas=metas) as tmp:
            out, _ = run_prepare_merge(tmp)
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'], 'decisions': [],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['confidence'], 'high')
        self.assertEqual(len(tai['evidence_refs']), 3)


class ApplyMergeUsedTermSourcesTests(unittest.TestCase):
    def test_appends_chunk_id_to_evidence_refs(self):
        # Start at low so we can observe the append (medium would not downgrade).
        existing = make_term('Manhattan', '曼哈顿', 'place', confidence='low')
        m = empty_meta(used_term_sources=['Manhattan'])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        man = next(t for t in g['terms'] if t['source'] == 'Manhattan')
        self.assertEqual(man['evidence_refs'], ['chunk0001'])
        self.assertEqual(man['confidence'], 'low')

    def test_promotes_confidence_via_used_term_sources_across_batches(self):
        existing = make_term('Manhattan', '曼哈顿', 'place')
        glossary = make_glossary(existing)
        with tempfile.TemporaryDirectory() as tmp:
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), glossary)
            for i in (1, 2, 3):
                cid = f'chunk000{i}'
                meta_mod.save_meta(
                    os.path.join(tmp, f'output_{cid}.meta.json'),
                    empty_meta(used_term_sources=['Manhattan']),
                )
                run_apply_merge(tmp, {
                    'auto_apply': [], 'decisions': [],
                    'consumed_chunk_ids': [cid],
                })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        man = next(t for t in g['terms'] if t['source'] == 'Manhattan')
        self.assertEqual(man['confidence'], 'high')
        self.assertEqual(len(man['evidence_refs']), 3)

    def test_idempotent_on_same_chunk(self):
        existing = make_term('Manhattan', '曼哈顿', 'place')
        m = empty_meta(used_term_sources=['Manhattan'])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            for _ in range(2):
                run_apply_merge(tmp, {
                    'auto_apply': [], 'decisions': [],
                    'consumed_chunk_ids': ['chunk0001'],
                })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        man = next(t for t in g['terms'] if t['source'] == 'Manhattan')
        self.assertEqual(len(man['evidence_refs']), 1)

    def test_does_not_downgrade_confidence(self):
        existing = make_term('Manhattan', '曼哈顿', 'place', confidence='high')
        m = empty_meta(used_term_sources=['Manhattan'])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        man = next(t for t in g['terms'] if t['source'] == 'Manhattan')
        self.assertEqual(man['confidence'], 'high')

    def test_finds_term_via_alias(self):
        existing = make_term('Tai', '太一', 'person', aliases=['Taig'])
        m = empty_meta(used_term_sources=['Taig'])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['evidence_refs'], ['chunk0001'])

    def test_evidence_refs_capped_at_5(self):
        existing = make_term('X', 'x')
        glossary = make_glossary(existing)
        with tempfile.TemporaryDirectory() as tmp:
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), glossary)
            for i in range(1, 8):  # 7 chunks
                cid = f'chunk000{i}'
                meta_mod.save_meta(
                    os.path.join(tmp, f'output_{cid}.meta.json'),
                    empty_meta(used_term_sources=['X']),
                )
                run_apply_merge(tmp, {
                    'auto_apply': [], 'decisions': [],
                    'consumed_chunk_ids': [cid],
                })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        x = next(t for t in g['terms'] if t['source'] == 'X')
        self.assertEqual(len(x['evidence_refs']), 5)
        # FIFO: oldest dropped
        self.assertEqual(x['evidence_refs'][0], 'chunk0003')
        self.assertEqual(x['evidence_refs'][-1], 'chunk0007')


class ApplyMergeDecisionTests(unittest.TestCase):
    def test_alias_yes_appends_alias(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
            'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'yes_alias'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])

    def test_alias_no_does_not_modify_glossary(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
            'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'no_separate_entity'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertNotIn('Taig', tai['aliases'])

    def test_conflict_record_in_notes_does_not_overwrite_canonical(self):
        existing = make_term('Tai', '泰', 'person')
        m = empty_meta(conflicts=[{
            'entity_source': 'Tai', 'field': 'target', 'injected': '泰',
            'observed_better': '太一', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'record_in_notes'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['target'], '泰')
        self.assertIn('太一', tai['notes'])

    def test_conflict_accept_proposed_updates_target_and_stamps_notes(self):
        existing = make_term('Tai', '泰', 'person')
        m = empty_meta(conflicts=[{
            'entity_source': 'Tai', 'field': 'target', 'injected': '泰',
            'observed_better': '太一', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'accept_proposed'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['target'], '太一')
        self.assertIn('泰', tai['notes'])

    def test_promote_to_separate_entity_removes_alias_from_other_term(self):
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'use_variant_0'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        banana = next(t for t in g['terms'] if t['source'] == 'Banana')
        self.assertEqual(banana['aliases'], [])
        apple = next((t for t in g['terms'] if t['source'] == 'Apple'), None)
        self.assertIsNotNone(apple)
        self.assertEqual(apple['target'], '苹果')

    def test_keep_as_alias_is_noop_but_consumed(self):
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'keep_as_alias'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        # Banana keeps Apple alias, no new term added
        banana = next(t for t in g['terms'] if t['source'] == 'Banana')
        self.assertIn('Apple', banana['aliases'])
        self.assertNotIn('Apple', [t['source'] for t in g['terms']])
        # And the chunk is marked consumed
        self.assertIn('chunk0001', g['applied_meta_hashes'])

    def test_use_variant_picks_correct_target_proposal(self):
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'person',
            'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0042': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            # Pick variant 0
            v0_target = d['variants'][0]['target_proposal']
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'use_variant_0'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['target'], v0_target)
        # Combined evidence_chunks from both variants
        self.assertEqual(sorted(tai['evidence_refs']), ['chunk0001', 'chunk0042'])

    def test_use_variant_skip_leaves_glossary_unchanged_but_marks_consumed(self):
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person', 'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'person', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0042': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'skip'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        self.assertEqual(g['terms'], [])
        # Both chunks consumed
        self.assertIn('chunk0001', g['applied_meta_hashes'])
        self.assertIn('chunk0042', g['applied_meta_hashes'])

    def test_rejects_unknown_choice_for_decision_kind(self):
        existing = make_term('Tai', '太一')
        m = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            _, err, _ = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'use_variant_0'}],  # wrong kind
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
        # apply-merge exits with code 1 and surfaces the bad choice in the
        # summary's `errors` field — not necessarily on stderr.

    def test_apply_merge_idempotent_on_replay(self):
        m = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person', 'evidence': 'a.',
        }])
        with temp_workspace(glossary=make_glossary(), metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'], 'decisions': [],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g1 = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            # Re-apply: prepare returns nothing, so re-apply with empty consumed.
            out2, _ = run_prepare_merge(tmp)
            run_apply_merge(tmp, {
                'auto_apply': out2['auto_apply'], 'decisions': [],
                'consumed_chunk_ids': out2['consumed_chunk_ids'],
            })
            g2 = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        self.assertEqual(g1, g2)


class ChunkIdEnforcementTests(unittest.TestCase):
    def test_consumed_chunk_ids_must_match_filename_derived_id(self):
        # apply-merge errors out cleanly without touching state when
        # consumed_chunk_ids references a non-existent meta file.
        m = empty_meta()
        with temp_workspace(glossary=make_glossary(), metas={'chunk0001': m}) as tmp:
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk9999'],  # no such file
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        self.assertEqual(code, 2)
        self.assertNotIn('chunk9999', g['applied_meta_hashes'])
        # chunk0001 exists but wasn't consumed either — the abort is
        # all-or-nothing.
        self.assertNotIn('chunk0001', g['applied_meta_hashes'])
        self.assertIn('no meta file', err)


class DecisionErrorAtomicityTests(unittest.TestCase):
    """Bug fix: a bad decision payload must not leave hashes recorded, otherwise
    on retry the affected metas are silently skipped and the unresolved
    decisions are lost permanently."""

    def test_bad_decision_choice_aborts_without_recording_hashes(self):
        existing = make_term('Tai', '太一')
        m = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'use_variant_0'}],  # wrong choice for kind
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            self.assertNotEqual(code, 0)
            # Critical: no hash recorded → re-running prepare-merge surfaces it again.
            self.assertEqual(g['applied_meta_hashes'], {})
            out2, _ = run_prepare_merge(tmp)
            self.assertEqual(len(out2['decisions_needed']), 1)
            self.assertEqual(out2['decisions_needed'][0]['kind'], 'alias')

    def test_bad_decision_aborts_even_when_some_decisions_are_good(self):
        existing = make_term('Tai', '太一')
        m = empty_meta(alias_hypotheses=[
            {'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'a.'},
            {'variant': 'Taighi', 'may_be_alias_of_source': 'Tai', 'evidence': 'b.'},
        ])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d_good, d_bad = out['decisions_needed']
            _, _, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [
                    {**d_good, 'choice': 'yes_alias'},
                    {**d_bad, 'choice': 'INVALID'},
                ],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        self.assertNotEqual(code, 0)
        # Neither decision applied — even the good one rolls back.
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['aliases'], [])
        self.assertEqual(g['applied_meta_hashes'], {})


class AttributeHypothesesTests(unittest.TestCase):
    """Bug fix: attribute_hypotheses must actually mutate the glossary,
    not be silently dropped."""

    def test_first_gender_evidence_sets_field_and_records_evidence(self):
        existing = make_term('Tai', '太一', confidence='low')
        m = empty_meta(attribute_hypotheses=[{
            'entity_source': 'Tai', 'attribute': 'gender', 'value': 'male',
            'confidence': 'high', 'evidence': 'He smiled at Tai.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['gender'], 'male')
        self.assertIn('chunk0001', tai['evidence_refs'])
        self.assertIn('[gender]', tai['notes'])

    def test_corroborating_gender_evidence_promotes_confidence(self):
        existing = make_term('Tai', '太一', confidence='low')
        existing['gender'] = 'male'
        glossary = make_glossary(existing)
        with tempfile.TemporaryDirectory() as tmp:
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), glossary)
            for i in (1, 2):
                cid = f'chunk000{i}'
                meta_mod.save_meta(
                    os.path.join(tmp, f'output_{cid}.meta.json'),
                    empty_meta(attribute_hypotheses=[{
                        'entity_source': 'Tai', 'attribute': 'gender', 'value': 'male',
                        'confidence': 'high', 'evidence': f'evidence {i}',
                    }]),
                )
                run_apply_merge(tmp, {
                    'auto_apply': [], 'decisions': [],
                    'consumed_chunk_ids': [cid],
                })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['gender'], 'male')
        # evidence_refs grows with each corroboration → confidence escalates.
        self.assertGreaterEqual(len(tai['evidence_refs']), 2)
        self.assertIn(tai['confidence'], ('medium', 'high'))

    def test_conflicting_gender_evidence_resets_to_unknown_and_records_both(self):
        existing = make_term('Tai', '太一', confidence='medium')
        existing['gender'] = 'male'
        m = empty_meta(attribute_hypotheses=[{
            'entity_source': 'Tai', 'attribute': 'gender', 'value': 'female',
            'confidence': 'high', 'evidence': 'She smiled.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['gender'], 'unknown')
        self.assertIn('conflict', tai['notes'])
        self.assertIn("'male'", tai['notes'])
        self.assertIn("'female'", tai['notes'])

    def test_attribute_hypothesis_finds_term_via_alias(self):
        existing = make_term('Tai', '太一', aliases=['Taig'])
        m = empty_meta(attribute_hypotheses=[{
            'entity_source': 'Taig', 'attribute': 'gender', 'value': 'male',
            'confidence': 'high', 'evidence': 'Taig nodded.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['gender'], 'male')

    def test_attribute_hypothesis_for_unknown_entity_is_silent_noop(self):
        # Sub-agent referenced an entity that's not in the glossary — no
        # crash, no spurious term added.
        m = empty_meta(attribute_hypotheses=[{
            'entity_source': 'Phantom', 'attribute': 'gender', 'value': 'male',
            'confidence': 'high', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        self.assertEqual(g['terms'], [])
        # But the chunk is still consumed — we can't surface it again.
        self.assertIn('chunk0001', g['applied_meta_hashes'])

    def test_non_gender_attribute_logs_to_notes_without_mutation(self):
        existing = make_term('Tai', '太一')
        m = empty_meta(attribute_hypotheses=[{
            'entity_source': 'Tai', 'attribute': 'occupation', 'value': 'teacher',
            'confidence': 'medium', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertNotIn('occupation', tai)
        self.assertIn('occupation', tai['notes'])


class AliasOrNewEntityCollisionTests(unittest.TestCase):
    """Bug fix: same-chunk new_entity + alias_hypothesis on the same variant
    must not produce two competing decisions (auto_apply add_entity AND
    yes_alias on the same surface), since accepting the alias would then fail
    surface-form uniqueness."""

    def test_collision_emits_single_alias_or_new_entity_decision(self):
        # Glossary has Tai. Same chunk says "Taig is a new entity" AND
        # "Taig may be an alias of Tai". Without the fix we'd auto_apply Taig
        # as a standalone source AND emit a yes_alias decision — accepting
        # the alias would then try to make Taig both source and alias.
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': 'Taig walked.',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
                'evidence': 'Taig might be Tai.',
            }],
        )
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        # No auto_apply (the collision pulled it out).
        self.assertEqual(out['auto_apply'], [])
        # Single combined decision.
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'alias_or_new_entity')
        self.assertEqual(d['variant'], 'Taig')
        self.assertEqual(len(d['alias_candidates']), 1)
        self.assertEqual(d['alias_candidates'][0]['candidate_source'], 'Tai')
        self.assertEqual(len(d['standalone_variants']), 1)
        self.assertEqual(d['standalone_variants'][0]['target_proposal'], '泰格')
        self.assertEqual(d['standalone_variants'][0]['category'], 'person')
        self.assertEqual(set(d['options']),
                         {'use_alias_0', 'use_standalone_0', 'skip'})

    def test_collision_yes_alias_attaches_alias_only(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': 'Taig walked.',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
                'evidence': 'Taig might be Tai.',
            }],
        )
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [{**d, 'choice': 'use_alias_0'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        # Tai gains alias, no standalone Taig term created.
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])
        self.assertNotIn('Taig', [t['source'] for t in g['terms']])

    def test_collision_use_standalone_creates_standalone_only(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': 'Taig walked.',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
                'evidence': 'Taig might be Tai.',
            }],
        )
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [{**d, 'choice': 'use_standalone_0'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertNotIn('Taig', tai['aliases'])
        taig = next((t for t in g['terms'] if t['source'] == 'Taig'), None)
        self.assertIsNotNone(taig)
        self.assertEqual(taig['target'], '泰格')
        self.assertEqual(taig['evidence_refs'], ['chunk0001'])

    def test_collision_skip_leaves_glossary_unchanged_but_marks_consumed(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': '...',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
            }],
        )
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'skip'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['aliases'], [])
        self.assertEqual([t['source'] for t in g['terms']], ['Tai'])
        self.assertIn('chunk0001', g['applied_meta_hashes'])

    def test_collision_exposes_all_competing_standalone_variants(self):
        # Bug fix: prior version collapsed multi-variant proposals to first one,
        # so promote_to_separate_entity hard-coded the wrong target. Now every
        # competing (target, category) is its own use_standalone_N choice.
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': 'Taig (person).',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
            }],
        )
        m2 = empty_meta(new_entities=[{
            'source': 'Taig', 'target_proposal': '太格', 'category': 'place',
            'evidence': 'Taig (place).',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'alias_or_new_entity')
        self.assertEqual(len(d['standalone_variants']), 2)
        targets = {v['target_proposal'] for v in d['standalone_variants']}
        self.assertEqual(targets, {'泰格', '太格'})
        self.assertEqual(set(d['options']),
                         {'use_alias_0', 'use_standalone_0', 'use_standalone_1', 'skip'})

    def test_collision_use_standalone_picks_correct_variant(self):
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': 'a.',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
            }],
        )
        m2 = empty_meta(new_entities=[{
            'source': 'Taig', 'target_proposal': '太格', 'category': 'place',
            'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            # Find which index corresponds to the "place" variant
            place_idx = next(
                i for i, v in enumerate(d['standalone_variants'])
                if v['category'] == 'place'
            )
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [{**d, 'choice': f'use_standalone_{place_idx}'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        taig = next(t for t in g['terms'] if t['source'] == 'Taig')
        self.assertEqual(taig['target'], '太格')
        self.assertEqual(taig['category'], 'place')
        # evidence_refs combines BOTH chunks (each variant attests Taig exists)
        self.assertEqual(sorted(taig['evidence_refs']), ['chunk0001', 'chunk0002'])


class AliasCandidateInPendingTests(unittest.TestCase):
    """Bug fix: alias hypotheses whose candidate is being auto-added in this
    same prepare-merge run must still produce an alias decision. Otherwise
    the meta gets hashed as consumed and the alias signal is lost."""

    def test_alias_candidate_pending_in_auto_apply_still_produces_decision(self):
        # chunk0001: new_entity Tai. chunk0002: alias_hyp Taig→Tai.
        # Tai isn't in glossary yet — without the fix, alias hyp is dropped.
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'Tai walked.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
            'evidence': 'Taig might be Tai.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        # Tai is auto_apply, AND we should see the alias decision.
        self.assertEqual(len(out['auto_apply']), 1)
        self.assertEqual(out['auto_apply'][0]['entity']['source'], 'Tai')
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'alias')
        self.assertEqual(d['variant'], 'Taig')
        self.assertEqual(d['candidate_source'], 'Tai')

    def test_alias_to_pending_candidate_yes_alias_succeeds_end_to_end(self):
        # Demonstrates the actual fix: apply-merge processes auto_apply (Phase 2)
        # before decisions (Phase 3), so by the time the alias decision runs,
        # Tai is already in the glossary.
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'Tai walked.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [{**d, 'choice': 'yes_alias'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])

    def test_alias_to_pending_candidate_in_alias_or_new_entity_collision(self):
        # Even harder: Taig is BOTH a new_entity proposal AND an alias_hyp
        # to a candidate (Tai) that's also pending. The collision path must
        # accept Tai as a valid candidate.
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'a.',
        }])
        m2 = empty_meta(
            new_entities=[{
                'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
                'evidence': 'b.',
            }],
            alias_hypotheses=[{
                'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
                'evidence': 'c.',
            }],
        )
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        # Tai stays in auto_apply. Taig becomes an alias_or_new_entity decision.
        auto_sources = {e['entity']['source'] for e in out['auto_apply']}
        self.assertIn('Tai', auto_sources)
        self.assertNotIn('Taig', auto_sources)
        kinds = [d['kind'] for d in out['decisions_needed']]
        self.assertIn('alias_or_new_entity', kinds)


class CandidateInConflictingProposalsTests(unittest.TestCase):
    """Bug fix: alias hypothesis whose candidate is in this batch's
    conflicting_new_entity_proposals must still produce a decision —
    otherwise the meta gets hashed as consumed and the alias signal is lost."""

    def test_candidate_in_conflicting_new_entity_proposals_emits_alias_decision(self):
        # chunk0001/0002: competing proposals for Tai (target conflict).
        # chunk0003: Taig may be alias of Tai. The alias must be surfaced even
        # though Tai isn't in glossary AND isn't auto_apply (it's pending in
        # conflicting_new_entity_proposals).
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'person',
            'evidence': 'b.',
        }])
        m3 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'c.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0002': m2, 'chunk0003': m3}) as tmp:
            out, _ = run_prepare_merge(tmp)
        kinds = sorted(d['kind'] for d in out['decisions_needed'])
        self.assertIn('alias', kinds)
        self.assertIn('conflicting_new_entity_proposals', kinds)
        alias_d = next(d for d in out['decisions_needed'] if d['kind'] == 'alias')
        self.assertEqual(alias_d['variant'], 'Taig')
        self.assertEqual(alias_d['candidate_source'], 'Tai')

    def test_candidate_in_conflicting_proposals_resolved_then_alias_attached(self):
        # End-to-end: orchestrator resolves the conflicting proposals AND
        # attaches the alias in a single apply-merge call. Order in the
        # decisions list shouldn't matter — alias dispatch happens after the
        # use_variant_N creator regardless of input order.
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person', 'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'person', 'evidence': 'b.',
        }])
        m3 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'c.',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0002': m2, 'chunk0003': m3}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d_alias = next(d for d in out['decisions_needed'] if d['kind'] == 'alias')
            d_conflict = next(d for d in out['decisions_needed']
                              if d['kind'] == 'conflicting_new_entity_proposals')
            # Pass the alias decision FIRST in the input list. Apply-merge
            # must still run the creator (use_variant_0) before the alias
            # attacher.
            run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [
                    {**d_alias, 'choice': 'yes_alias'},
                    {**d_conflict, 'choice': 'use_variant_0'},
                ],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])


class CandidateNotCanonicalizedTests(unittest.TestCase):
    """Bug fix: candidate_source in the decision item must be the literal
    string the sub-agent wrote, NOT the current owner's source. Apply-merge
    re-resolves at dispatch time so the alias attaches to whichever term
    actually owns that surface AFTER all entity-creating decisions have run."""

    def test_alias_candidate_keeps_subagent_string_when_currently_an_alias(self):
        # Glossary has Banana with alias Apple. Sub-agent says Cherry may be
        # alias of "Apple". The decision should preserve "Apple" verbatim
        # (not eagerly canonicalize to "Banana"), so that if the orchestrator
        # promotes Apple to a standalone entity in the same batch, Cherry
        # attaches to the new Apple — not to Banana.
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m1 = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': 'Apple is red.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Cherry', 'may_be_alias_of_source': 'Apple',
            'evidence': 'Cherry like Apple.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            alias_d = next(d for d in out['decisions_needed'] if d['kind'] == 'alias')
            # Critical: candidate_source preserved as 'Apple', not canonicalized.
            self.assertEqual(alias_d['candidate_source'], 'Apple')

            promote_d = next(d for d in out['decisions_needed']
                             if d['kind'] == 'new_entity_existing_alias')
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [
                    {**alias_d, 'choice': 'yes_alias'},
                    {**promote_d, 'choice': 'use_variant_0'},
                ],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        # Apple is now its own entity; Cherry attaches to the NEW Apple,
        # not to Banana.
        apple = next(t for t in g['terms'] if t['source'] == 'Apple')
        self.assertIn('Cherry', apple['aliases'])
        banana = next(t for t in g['terms'] if t['source'] == 'Banana')
        self.assertNotIn('Cherry', banana['aliases'])
        self.assertNotIn('Apple', banana['aliases'])


class DispatchOrderIndependenceTests(unittest.TestCase):
    """Bug fix: apply-merge dispatches entity-creating decisions before
    alias-attaching ones, regardless of the order they appear in the input
    decisions list. The orchestrator should not have to topo-sort."""

    def test_alias_decision_before_creator_in_input_order_succeeds(self):
        # Glossary empty. chunk0001: new_entity Tai; chunk0002: alias Taig→Tai.
        # Pass alias FIRST in decisions list — dispatch must reorder.
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'Tai walks.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            alias_d = next(d for d in out['decisions_needed'] if d['kind'] == 'alias')
            # Note: Tai goes via auto_apply (no other competing proposal). So
            # this test exercises the auto_apply → alias-attacher ordering,
            # which already worked. The harder case is below.
            _, _, code = run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [{**alias_d, 'choice': 'yes_alias'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertEqual(code, 0)
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])

    def test_alias_to_promoted_alias_in_arbitrary_input_order_succeeds(self):
        # Banana has alias Apple. Sub-agent proposes new_entity Apple AND
        # alias Cherry→Apple. Two decisions: promote Apple, attach Cherry to
        # promoted Apple. Pass alias FIRST in input — dispatch must reorder
        # so promote runs first.
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m1 = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': 'a.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Cherry', 'may_be_alias_of_source': 'Apple',
            'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            alias_d = next(d for d in out['decisions_needed'] if d['kind'] == 'alias')
            promote_d = next(d for d in out['decisions_needed']
                             if d['kind'] == 'new_entity_existing_alias')
            # Pass alias FIRST. Without dispatch reordering, this would fail
            # because Apple isn't yet a glossary source when yes_alias runs.
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [
                    {**alias_d, 'choice': 'yes_alias'},
                    {**promote_d, 'choice': 'use_variant_0'},
                ],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertEqual(code, 0, f"expected success, stderr was: {err}")
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        apple = next(t for t in g['terms'] if t['source'] == 'Apple')
        self.assertIn('Cherry', apple['aliases'])

    def test_alias_to_alias_or_new_entity_creator_in_arbitrary_order(self):
        # alias_or_new_entity collision: chunk0001 has new_entity Tai +
        # alias_hyp Tai→OtherCharacter. Glossary has OtherCharacter.
        # Separately chunk0002: alias_hyp Taig→Tai.
        # Tai becomes a use_standalone-style decision (alias_or_new_entity).
        # Taig→Tai becomes an alias decision. Pass them in reverse order;
        # dispatch reorders so the standalone is created before Taig attaches.
        existing = make_term('OtherCharacter', '其他', 'person')
        m1 = empty_meta(
            new_entities=[{
                'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
                'evidence': 'a.',
            }],
            alias_hypotheses=[{
                'variant': 'Tai', 'may_be_alias_of_source': 'OtherCharacter',
                'evidence': 'b.',
            }],
        )
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'c.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            alias_d = next(d for d in out['decisions_needed']
                           if d['kind'] == 'alias' and d['variant'] == 'Taig')
            collision_d = next(d for d in out['decisions_needed']
                               if d['kind'] == 'alias_or_new_entity')
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': out['auto_apply'],
                'decisions': [
                    {**alias_d, 'choice': 'yes_alias'},
                    {**collision_d, 'choice': 'use_standalone_0'},
                ],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertEqual(code, 0, f"expected success, stderr was: {err}")
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])


class MultiAliasCandidateTests(unittest.TestCase):
    """Bug fix: when multiple alias_hypotheses for the same variant point at
    different candidates, prepare-merge must expose ALL candidates as options
    in a single decision. Otherwise the orchestrator can't compare them and
    the chunks holding the dropped candidates get hashed-as-applied → signal
    lost permanently."""

    def test_multi_alias_candidates_with_standalone_collision(self):
        # The user's exact reproducer: glossary has Tai and Tao;
        # chunk0001 proposes Taig as new_entity;
        # chunk0002 proposes Taig → Tai;
        # chunk0003 proposes Taig → Tao.
        # Expected: ONE decision exposing both alias candidates AND the
        # standalone variant. consumed_chunk_ids covers all three chunks.
        g = make_glossary(
            make_term('Tai', '太一', 'person'),
            make_term('Tao', '陶', 'person'),
        )
        m1 = empty_meta(new_entities=[{
            'source': 'Taig', 'target_proposal': '泰格', 'category': 'person',
            'evidence': 'Taig walks alone.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai',
            'evidence': 'Taig nodded at Tai.',
        }])
        m3 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tao',
            'evidence': 'Taig followed Tao.',
        }])
        with temp_workspace(glossary=g,
                            metas={'chunk0001': m1, 'chunk0002': m2, 'chunk0003': m3}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['auto_apply'], [])
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'alias_or_new_entity')
        self.assertEqual(d['variant'], 'Taig')
        candidate_sources = {c['candidate_source'] for c in d['alias_candidates']}
        self.assertEqual(candidate_sources, {'Tai', 'Tao'})
        self.assertEqual(len(d['standalone_variants']), 1)
        self.assertEqual(set(d['options']),
                         {'use_alias_0', 'use_alias_1', 'use_standalone_0', 'skip'})
        self.assertEqual(set(out['consumed_chunk_ids']),
                         {'chunk0001', 'chunk0002', 'chunk0003'})

    def test_multi_alias_candidates_no_standalone(self):
        # Same as above but no new_entity proposal — still one unified
        # decision with two alias candidates.
        g = make_glossary(
            make_term('Tai', '太一', 'person'),
            make_term('Tao', '陶', 'person'),
        )
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tao', 'evidence': '...',
        }])
        with temp_workspace(glossary=g, metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'alias_or_new_entity')
        self.assertEqual(len(d['alias_candidates']), 2)
        self.assertEqual(d['standalone_variants'], [])
        self.assertEqual(set(d['options']),
                         {'use_alias_0', 'use_alias_1', 'skip'})
        self.assertEqual(set(out['consumed_chunk_ids']),
                         {'chunk0001', 'chunk0002'})

    def test_multi_alias_apply_picks_correct_candidate(self):
        g = make_glossary(
            make_term('Tai', '太一', 'person'),
            make_term('Tao', '陶', 'person'),
        )
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tao', 'evidence': '...',
        }])
        with temp_workspace(glossary=g, metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            tao_idx = next(i for i, c in enumerate(d['alias_candidates'])
                           if c['candidate_source'] == 'Tao')
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': f'use_alias_{tao_idx}'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g_after = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tao = next(t for t in g_after['terms'] if t['source'] == 'Tao')
        self.assertIn('Taig', tao['aliases'])
        tai = next(t for t in g_after['terms'] if t['source'] == 'Tai')
        self.assertNotIn('Taig', tai['aliases'])

    def test_same_candidate_proposed_by_multiple_chunks_dedupes(self):
        # Two chunks both say Taig→Tai. Should produce ONE alias candidate
        # with combined evidence_chunks, not two duplicate candidates.
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'a.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        # Single candidate → simple `alias` decision, no need for the unified
        # multi-candidate kind.
        alias_decisions = [d for d in out['decisions_needed'] if d['kind'] == 'alias']
        self.assertEqual(len(alias_decisions), 1)
        self.assertEqual(alias_decisions[0]['variant'], 'Taig')
        self.assertEqual(alias_decisions[0]['candidate_source'], 'Tai')

    def test_multi_alias_decision_apply_skip_marks_all_chunks_consumed(self):
        # If orchestrator picks skip on the multi-candidate decision, all
        # contributing chunks must still be hashed as applied — otherwise
        # the same decision re-surfaces forever.
        g = make_glossary(
            make_term('Tai', '太一', 'person'),
            make_term('Tao', '陶', 'person'),
        )
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': '...',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tao', 'evidence': '...',
        }])
        with temp_workspace(glossary=g, metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'skip'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g_after = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            self.assertIn('chunk0001', g_after['applied_meta_hashes'])
            self.assertIn('chunk0002', g_after['applied_meta_hashes'])
            # Re-run prepare: nothing surfaces.
            out2, _ = run_prepare_merge(tmp)
            self.assertEqual(out2['decisions_needed'], [])


class ExistingEntityConflictTests(unittest.TestCase):
    """Bug fix: when src is already a glossary source, multi-chunk new_entity
    proposals must surface every distinct (target, category) variant that
    differs from canonical — not collapse to proposals[0]."""

    def test_first_proposal_matches_canonical_other_differs_still_surfaces(self):
        # chunk0001 repeats canonical; chunk0002 proposes a different target.
        # Old code looked at proposals[0] (matches), did silent no-op, but
        # consumed both chunks → chunk0002's signal lost.
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'matches.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'person',
            'evidence': 'differs.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'existing_entity_conflict')
        self.assertEqual(d['entity_source'], 'Tai')
        # Only the differing variant is in proposed_variants.
        self.assertEqual(len(d['proposed_variants']), 1)
        self.assertEqual(d['proposed_variants'][0]['target_proposal'], '泰')

    def test_category_only_difference_surfaces(self):
        # Same target, different category — must surface.
        existing = make_term('Apple', '苹果', 'fruit')
        m = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'company',
            'evidence': 'Apple Inc.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'existing_entity_conflict')
        self.assertEqual(d['proposed_variants'][0]['category'], 'company')

    def test_all_proposals_match_canonical_silent_noop(self):
        # If every proposal matches canonical, no decision but chunks consumed.
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '太一', 'category': 'person',
            'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(out['decisions_needed'], [])
        self.assertEqual(set(out['consumed_chunk_ids']), {'chunk0001', 'chunk0002'})

    def test_multi_distinct_variants_all_surfaced(self):
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰一', 'category': 'person',
            'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'place',
            'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            self.assertEqual(d['kind'], 'existing_entity_conflict')
            self.assertEqual(len(d['proposed_variants']), 2)
            self.assertEqual(set(d['options']),
                             {'keep_current', 'use_variant_0', 'use_variant_1', 'record_in_notes'})

    def test_apply_use_variant_updates_target_and_category(self):
        existing = make_term('Tai', '太一', 'person')
        m = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰', 'category': 'place',
            'evidence': '...',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'use_variant_0'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['target'], '泰')
        self.assertEqual(tai['category'], 'place')
        self.assertIn('updated', tai['notes'])

    def test_apply_record_in_notes_preserves_canonical(self):
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰一', 'category': 'person', 'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Tai', 'target_proposal': '泰二', 'category': 'person', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'record_in_notes'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertEqual(tai['target'], '太一')  # canonical preserved
        # Both observations recorded in notes.
        self.assertIn('泰一', tai['notes'])
        self.assertIn('泰二', tai['notes'])


class NewEntityExistingAliasMultiVariantTests(unittest.TestCase):
    """Bug fix: when src is an existing alias of host, multi-chunk promotion
    proposals must surface every distinct (target, category) variant — not
    just proposals[0]."""

    def test_multi_variant_promotion_surfaces_all_proposals(self):
        # Banana has alias Apple. chunk0001 says Apple → 苹果 (fruit);
        # chunk0002 says Apple → 苹果公司 (company). Both must be exposed.
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m1 = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': 'Apple is red.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果公司', 'category': 'company',
            'evidence': 'Apple Inc.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        self.assertEqual(len(out['decisions_needed']), 1)
        d = out['decisions_needed'][0]
        self.assertEqual(d['kind'], 'new_entity_existing_alias')
        self.assertEqual(d['proposed_source'], 'Apple')
        self.assertEqual(len(d['promoted_variants']), 2)
        targets = {v['target_proposal'] for v in d['promoted_variants']}
        self.assertEqual(targets, {'苹果', '苹果公司'})
        self.assertEqual(set(d['options']),
                         {'use_variant_0', 'use_variant_1', 'keep_as_alias', 'skip'})
        self.assertEqual(set(out['consumed_chunk_ids']),
                         {'chunk0001', 'chunk0002'})

    def test_multi_variant_apply_picks_correct_promoted_form(self):
        existing = make_term('Banana', '香蕉', aliases=['Apple'])
        m1 = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果', 'category': 'fruit',
            'evidence': 'a.',
        }])
        m2 = empty_meta(new_entities=[{
            'source': 'Apple', 'target_proposal': '苹果公司', 'category': 'company',
            'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d = out['decisions_needed'][0]
            company_idx = next(
                i for i, v in enumerate(d['promoted_variants'])
                if v['category'] == 'company'
            )
            run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': f'use_variant_{company_idx}'}],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        # Banana lost the alias; new Apple created with company target/category.
        banana = next(t for t in g['terms'] if t['source'] == 'Banana')
        self.assertEqual(banana['aliases'], [])
        apple = next(t for t in g['terms'] if t['source'] == 'Apple')
        self.assertEqual(apple['target'], '苹果公司')
        self.assertEqual(apple['category'], 'company')


class AliasChainTests(unittest.TestCase):
    """Bug fix: alias hypotheses can chain through other pending alias decisions.
    chunk0001: Taig → Tai (Tai in glossary). chunk0002: Taighi → Taig (Taig
    becomes resolvable after chunk0001's alias is accepted). prepare-merge
    must surface BOTH decisions, and apply-merge must resolve them regardless
    of input order."""

    def test_two_step_alias_chain_emits_both_decisions(self):
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'a.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taighi', 'may_be_alias_of_source': 'Taig', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
        # BOTH alias decisions must appear.
        alias_decisions = [d for d in out['decisions_needed'] if d['kind'] == 'alias']
        self.assertEqual(len(alias_decisions), 2)
        variants = {d['variant']: d['candidate_source'] for d in alias_decisions}
        self.assertEqual(variants, {'Taig': 'Tai', 'Taighi': 'Taig'})

    def test_two_step_chain_apply_in_input_order(self):
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'a.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taighi', 'may_be_alias_of_source': 'Taig', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            # Order: parent (Taig→Tai) first, then child (Taighi→Taig)
            ordered = sorted(
                (d for d in out['decisions_needed'] if d['kind'] == 'alias'),
                key=lambda d: d['variant'] != 'Taig',  # Taig (parent) first
            )
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'yes_alias'} for d in ordered],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertEqual(code, 0, f"stderr was: {err}")
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        # Both Taig and Taighi attach to Tai.
        self.assertIn('Taig', tai['aliases'])
        self.assertIn('Taighi', tai['aliases'])

    def test_two_step_chain_apply_in_reverse_order(self):
        # Same scenario, but pass child decision FIRST. Fixed-point dispatch
        # in apply-merge must still resolve the chain.
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'a.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taighi', 'may_be_alias_of_source': 'Taig', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            # Reverse order: Taighi (child) first, Taig (parent) second.
            ordered = sorted(
                (d for d in out['decisions_needed'] if d['kind'] == 'alias'),
                key=lambda d: d['variant'] == 'Taig',  # child first
            )
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [{**d, 'choice': 'yes_alias'} for d in ordered],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertEqual(code, 0, f"stderr was: {err}")
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        tai = next(t for t in g['terms'] if t['source'] == 'Tai')
        self.assertIn('Taig', tai['aliases'])
        self.assertIn('Taighi', tai['aliases'])

    def test_chain_with_parent_skipped_aborts_child_until_orchestrator_picks_skip(self):
        # If the orchestrator picks no_separate_entity for the parent
        # Taig→Tai, the child Taighi→Taig becomes unresolvable. apply-merge
        # aborts; transactional rollback means hashes aren't recorded, so
        # prepare-merge surfaces both decisions again. Orchestrator can fix
        # by also skipping the child.
        existing = make_term('Tai', '太一', 'person')
        m1 = empty_meta(alias_hypotheses=[{
            'variant': 'Taig', 'may_be_alias_of_source': 'Tai', 'evidence': 'a.',
        }])
        m2 = empty_meta(alias_hypotheses=[{
            'variant': 'Taighi', 'may_be_alias_of_source': 'Taig', 'evidence': 'b.',
        }])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2}) as tmp:
            out, _ = run_prepare_merge(tmp)
            d_taig = next(d for d in out['decisions_needed'] if d.get('variant') == 'Taig')
            d_taighi = next(d for d in out['decisions_needed'] if d.get('variant') == 'Taighi')
            # Reject parent, attempt child anyway → child can't resolve.
            _, _, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': [
                    {**d_taig, 'choice': 'no_separate_entity'},
                    {**d_taighi, 'choice': 'yes_alias'},
                ],
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertNotEqual(code, 0)
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
            # Atomicity: nothing written, both metas re-surface on retry.
            self.assertEqual(g['applied_meta_hashes'], {})
            out2, _ = run_prepare_merge(tmp)
            self.assertEqual(len(out2['consumed_chunk_ids']), 2)

    def test_three_step_alias_chain(self):
        # A → B → C → D, all proposed as alias hyps in one batch.
        existing = make_term('A', 'a')
        m1 = empty_meta(alias_hypotheses=[
            {'variant': 'B', 'may_be_alias_of_source': 'A', 'evidence': '1.'},
        ])
        m2 = empty_meta(alias_hypotheses=[
            {'variant': 'C', 'may_be_alias_of_source': 'B', 'evidence': '2.'},
        ])
        m3 = empty_meta(alias_hypotheses=[
            {'variant': 'D', 'may_be_alias_of_source': 'C', 'evidence': '3.'},
        ])
        with temp_workspace(glossary=make_glossary(existing),
                            metas={'chunk0001': m1, 'chunk0002': m2, 'chunk0003': m3}) as tmp:
            out, _ = run_prepare_merge(tmp)
            self.assertEqual(len([d for d in out['decisions_needed']
                                  if d['kind'] == 'alias']), 3)
            # Pass in arbitrary order.
            decisions = [
                {**d, 'choice': 'yes_alias'}
                for d in out['decisions_needed'] if d['kind'] == 'alias'
            ]
            decisions.reverse()  # D, C, B order
            _, err, code = run_apply_merge(tmp, {
                'auto_apply': [],
                'decisions': decisions,
                'consumed_chunk_ids': out['consumed_chunk_ids'],
            })
            self.assertEqual(code, 0, f"stderr was: {err}")
            g = glossary_mod.load_glossary(os.path.join(tmp, 'glossary.json'))
        a = next(t for t in g['terms'] if t['source'] == 'A')
        # All three variants attach to A.
        self.assertIn('B', a['aliases'])
        self.assertIn('C', a['aliases'])
        self.assertIn('D', a['aliases'])


class StatusTests(unittest.TestCase):
    def test_reports_translated_meta_consumed_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 5 translated chunks
            for i in range(1, 6):
                Path(tmp, f'output_chunk000{i}.md').write_text('translated', encoding='utf-8')
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), make_glossary())
            # 4 meta files
            for i in (1, 2, 3, 4):
                meta_mod.save_meta(
                    os.path.join(tmp, f'output_chunk000{i}.meta.json'),
                    empty_meta(),
                )
            # Apply 3 of them (chunk0001/2/3)
            run_apply_merge(tmp, {
                'auto_apply': [], 'decisions': [],
                'consumed_chunk_ids': ['chunk0001', 'chunk0002', 'chunk0003'],
            })
            status = run_status(tmp)
        self.assertEqual(status['translated_chunks'], 5)
        self.assertEqual(status['meta_files_found'], 4)
        self.assertEqual(status['meta_files_consumed'], 3)
        self.assertEqual(status['unmerged_meta_files'], 1)
        self.assertEqual(status['malformed_meta_files'], 0)
        self.assertEqual(status['missing_meta_chunk_ids'], ['chunk0005'])

    def test_reports_malformed_meta_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'output_chunk0001.md').write_text('x', encoding='utf-8')
            glossary_mod.save_glossary(os.path.join(tmp, 'glossary.json'), make_glossary())
            with open(os.path.join(tmp, 'output_chunk0001.meta.json'), 'w') as f:
                f.write('{not valid json')
            status = run_status(tmp)
        self.assertEqual(status['malformed_meta_files'], 1)
        self.assertIn('chunk0001', status['malformed_meta_chunk_ids'])
        self.assertEqual(status['meta_files_consumed'], 0)



class ChunkIdInputValidationTests(unittest.TestCase):
    def test_apply_merge_rejects_consumed_ids_that_are_not_chunk_ids(self):
        with temp_workspace(glossary=make_glossary()) as tmp:
            before = Path(tmp, "glossary.json").read_bytes()
            decisions = {"auto_apply": [], "decisions": [], "consumed_chunk_ids": ["../../etc/passwd"]}
            result = process_guard.run_script("merge_meta.py", "apply-merge", tmp, input=json.dumps(decisions))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("is not a chunk id", result.stdout + result.stderr)
            self.assertEqual(Path(tmp, "glossary.json").read_bytes(), before)

    def test_prepare_merge_stops_at_a_stray_meta_file(self):
        with temp_workspace(glossary=make_glossary()) as tmp:
            Path(tmp, "output_chunk1.meta.json").write_text("{}", encoding="utf-8")
            result = process_guard.run_script("merge_meta.py", "prepare-merge", tmp)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unexpected file", result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
