# standard-alice

Full-pipeline baseline input for this repository.

- File: `standard-alice.epub`
- Source: Standard Ebooks edition of *Alice's Adventures in Wonderland*
- Why this baseline: stable EPUB structure, chaptered prose, recurring entities, and many illustrations, which makes it useful for exercising convert, chunking, translation, merge, and final format generation.

Run the repository baseline test from `tests/.artifacts/` so generated files stay out of the repo root:

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "爱丽丝梦游仙境（Baseline Test）"
```

## Expected outcome

See `tests/baselines/README.md` for the three-tier convention (Measured / Expected target / Drift indicator).

### Measured (from current `tests/.artifacts/standard-alice_temp/`)

The current artifact is a **partial run** — only chunks 4–11 have been translated, and no final-format files have been generated. Anything beyond that lives under *Expected target* below.

`manifest.json`:
- `chunk_count == 38`
- `source_hash == "6b1ea8ca29311ff8c43740b5a87d3fcfe0b906eb69bdcc74585d81a280f40c09"`
- `chunks[]` has 38 entries, sequential `chunk0001`–`chunk0038`, each with a non-empty `source_hash`

Files on disk:
- 38 `chunk0001.md`–`chunk0038.md` exist
- 45 image files in `images/`

`glossary.json` (version 2) entries reflecting the partial run:

| `source` | `confidence` | `evidence_refs` | `aliases` |
|---|---|---|---|
| Alice | high | 5 (chunks 7,8,9,10,11) | [] |
| White Rabbit | high | 4 (chunks 5,6,10,11) | ["the Rabbit", "W. Rabbit"] |
| Dinah | high | 4 (chunks 6,9,10,11) | [] |
| Mouse | high | 4 (chunks 6,7,8,9) | [] |
| Lory | high | 3 (chunks 7,8,9) | [] |
| Duchess | medium | 2 (chunks 5,10) | [] |
| William the Conqueror | medium | 2 (chunks 6,8) | ["William"] |

Note on `Wonderland`, `Cheshire Cat`, `Queen of Hearts`: these carry `confidence: high` with `evidence_refs: []` in the current artifact. This is **not** an anomaly. `merge_meta._confidence_for_evidence_count` (in `scripts/merge_meta.py`) is the merge-time promotion rule, applied only inside `_append_evidence_ref`. `scripts/glossary.py` validates `confidence` against the enum `low/medium/high` only, with no evidence-count requirement. Seeded entries — entries created outside the meta-evidence path — can legitimately carry high confidence with no evidence_refs.

### Expected target (unverified — needs full pipeline rerun)

- All 38 `output_chunk*.md` files exist (today only 8/38 are present)
- All 38 `output_chunk*.meta.json` files emitted by sub-agents
- All 4 final formats generated: `book.html`, `book.docx`, `book.epub`, `book.pdf`
- 45 images preserved end-to-end into the final EPUB

### Drift indicator (record current values, do not pass/fail)

LLM-dependent, may differ across runs/models:
- `Alice.target` currently `"爱丽丝"`
- `White Rabbit.target` currently `"白兔"`
- `Cheshire Cat.target` currently `"柴郡猫"`
- `Queen of Hearts.target` currently `"红心皇后"`
- `Mouse.gender` currently `"male"` (Carroll's narrator uses "his")
- `Dinah.gender` currently `"female"` (Alice refers to her cat as "she")
