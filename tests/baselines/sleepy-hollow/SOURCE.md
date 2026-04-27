# sleepy-hollow

Smoke baseline — cheapest full-pipeline input that still exercises convert, chunking, glossary, merge, and final format generation.

- File: `sleepy-hollow.epub`
- Source: Project Gutenberg #41 *The Legend of Sleepy Hollow* (`epub3.images`)
- Why this baseline: at `target_size=6000` the input splits into ~21 chunks (about 13 story chunks + 8 PG boilerplate chunks), roughly 55% of the cost of `standard-alice`. The `Headless Horseman / Galloping Hessian / spectre / goblin` alias chain spans ~9 chunks, so cross-chunk entity tracking still gets exercised.
- Coverage gap: only 1 image (the cover) — does **not** exercise inline-image preservation. Use `standard-alice` for image-path coverage.

Run from `tests/.artifacts/` so generated files stay out of the repo root:

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/sleepy-hollow/sleepy-hollow.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir sleepy-hollow_temp --title "睡谷传奇（Smoke Baseline）"
```

## Expected outcome

See `tests/baselines/README.md` for the three-tier convention.

*Forward-looking targets, not yet validated. The first full pipeline run after this section was added is the validation event — promote items to **Measured** as a run confirms them, or correct the targets if the run reveals the spec was wrong.*

### Measured

None — no `tests/.artifacts/sleepy-hollow_temp/` exists yet.

### Expected target (unverified — needs first full pipeline run)

- `manifest.chunk_count` is approximately 21 (this `SOURCE.md` says `~21` above). First run records the exact value; subsequent runs assert equality against the recorded value, not against `21`. A first-run result of 20 or 22 is an estimation correction, not a regression.
- All `chunk*.md` and matching `output_chunk*.md` exist; count matches `manifest.chunk_count`
- 1 image (cover) preserved end-to-end into the final EPUB
- All 4 final formats generated: `book.html`, `book.docx`, `book.epub`, `book.pdf`
- Glossary entries for `Ichabod Crane`, `Headless Horseman`, `Brom Bones`, `Katrina Van Tassel` reach `confidence == "high"` (≥3 evidence_refs each, per `scripts/merge_meta.py:76-91`)
- **Primary assertion** (the reason this baseline exists beyond smoke coverage): `Headless Horseman` and `Galloping Hessian` resolve to a *single* glossary entity, with both surface forms present in `aliases`. If they split into two entities, the cross-chunk alias coverage failed.

### Drift indicator (record current values, do not pass/fail)

- `Ichabod Crane.target`: a single Chinese form across all chunks (consistency only, not a specific value)
- `Brom Bones`'s alias chain (`Abraham`, `Brom Van Brunt`) collapses into one entity
