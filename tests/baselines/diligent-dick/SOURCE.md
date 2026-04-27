# diligent-dick

Opt-in alias-stress baseline — exercises the `merge_meta` contested-variant and alias-chain paths under realistic cross-chunk pressure. **Not part of default CI.** Run when changing `scripts/merge_meta.py` or `scripts/glossary.py`.

- File: `diligent-dick.epub`
- Source: Project Gutenberg #71493 *Diligent Dick; or, The Young Farmer* (`epub3.images`)
- Why this baseline: the protagonist appears as `Dick` / `Richard` / `Stuart` (with `Mr. Stuart` ambiguating father vs. son) and all three variants co-occur across ~9 consecutive chunks (chunks 30/33/36/38/40/42/48/50/53/54 in a fresh convert). Single-chunk Brom-style aliases — which Sleepy Hollow only provides intra-chunk — appear here cross-batch, which is exactly what the recent `merge_meta` "Surface ALL competing proposals" / "Surface ALL alias candidates" fixes are meant to protect.
- Cost note: ~57 chunks (≈1.5× `standard-alice`), and ~30 of those are tiny heading- or image-only chunks created by the chunker's heading-boundary preference on this book's many short Sunday-school chapters. Real story content is ~27 chunks. Treat the high chunk count as expected, not a bug.
- Image coverage: 5 inline images (`image001`–`image005`); modest, do not rely on this baseline for image-path coverage.

Run from `tests/.artifacts/` so generated files stay out of the repo root:

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/diligent-dick/diligent-dick.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir diligent-dick_temp --title "勤奋的迪克（Alias-Stress Baseline）"
```

## Expected outcome

See `tests/baselines/README.md` for the three-tier convention.

*Forward-looking targets, not yet validated. The first full pipeline run after this section was added is the validation event.*

### Measured

None — no `tests/.artifacts/diligent-dick_temp/` exists yet.

### Expected target (unverified — needs first full pipeline run)

- `manifest.chunk_count` is approximately 57 (this `SOURCE.md` says `~57 chunks (≈1.5× standard-alice)` above, with the heading-boundary explanation for why so many are tiny). First run records the exact value; subsequent runs assert equality against the recorded value. Variance of a few chunks is estimation drift, not regression.
- All `chunk*.md` and matching `output_chunk*.md` exist; count matches `manifest.chunk_count`
- 5 inline images preserved (`image001`–`image005`, source-file property — exact)
- All 4 final formats generated: `book.html`, `book.docx`, `book.epub`, `book.pdf`
- **Primary alias assertion**: `Dick` and `Richard` resolve to a *single* glossary entity, both forms present in `aliases`. (Unambiguous nickname relationship.)
- **Critical ambiguity flag** — `Stuart` / `Mr. Stuart` (father vs. son): the disambiguation must surface in `merge_meta prepare-merge` output as **any** decision item (any `kind` — `alias`, `alias_or_new_entity`, `conflicting_new_entity_proposals`, `new_entity_existing_alias`, or `existing_entity_conflict` — whichever fires depends on run state). The non-silent criterion is "main agent gets to judge", regardless of decision kind. It must **NOT** be silently merged into `Dick`; silent merge = loss of evidence = regression.
  - Do **NOT** assert it must appear in the per-chunk meta `conflicts` array; that array is specifically for "injected canonical field vs observed-better value" comparisons (see `scripts/meta.py:21-23`), not general entity ambiguity.
- Once the protagonist has been seen in the first two co-occurrence chunks (chunks 30 and 33 per the documented evidence above — *not* "batch 2", which lands on chunks 9–16 at default concurrency and contains no protagonist), the entity must reach `confidence != "low"`. The recent `merge_meta` "Surface ALL competing proposals" / "Surface ALL alias candidates" fixes (commits `73b5f82`, `bbee9c9`) exist to drive convergence here without waiting for many additional chunks.

### Drift indicator (record current values, do not pass/fail)

- `Dick`/`Richard` final translation: a single Chinese form (not `迪克` and `理查德` mixed in the same output)
- `Mr. Stuart`'s rendering matches whichever resolution wins (consistency only, no specific target value)
