# Baselines

Checked-in full-pipeline test inputs live under `tests/baselines/<book-id>/`.

Each baseline directory should contain:

- the original input book file (`.epub`, `.pdf`, or `.docx`)
- a short `SOURCE.md` describing the source and why the baseline exists

Generated outputs do not belong here. Put them under `tests/.artifacts/`.

## Tiers

| Tier | Baseline | Chunks (target_size=6000) | Inline images | When to run |
|---|---|---|---|---|
| Smoke | `sleepy-hollow` | ~21 | 0 (cover only) | Default CI / quick iteration |
| Gold | `standard-alice` | ~38 | ~45 | Release-gate / image-path coverage |
| Alias-stress (opt-in) | `diligent-dick` | ~57 | 5 | When changing `merge_meta` or `glossary` |

The smoke baseline is the cheapest way to exercise convert → chunk → glossary → merge → build. It deliberately gives up inline-image coverage; use the gold baseline whenever image handling could be affected. The alias-stress baseline is intentionally larger than the gold one because its value lies in `Dick / Richard / Stuart` co-occurring across ~9 consecutive chunks — a contested-variant cross-batch scenario the smaller baselines cannot reproduce.

## Expected outcome convention

Each `SOURCE.md` documents an `## Expected outcome` section with three tiers:

- **Measured** — a value directly observable in the current `tests/.artifacts/<baseline>_temp/`. Pass/fail today against that artifact.
- **Expected target (unverified)** — structurally deterministic but no run has confirmed it yet. Forward-looking spec, not pass/fail until a run produces evidence.
- **Drift indicator** — LLM-dependent content (e.g. specific translation strings, gender attributions). Recorded for spot-checking; never pass/fail.

A `Measured` assertion that no longer holds against the current artifact is a regression. An `Expected target` that fails on first run means either the target was wrong (update `SOURCE.md`) or the pipeline regressed (fix code) — judgement call. Promote `Expected target` items to `Measured` once a run validates them.

For values that depend on first-run measurement (e.g. exact `manifest.chunk_count`, image counts not pinned by the source file), write the `Expected target` as approximate or as "first run records the exact value; subsequent runs assert equality against the recorded value". Do not pin a precise number until a run produces it. Otherwise an estimation correction on first run will be misread as a pipeline failure.

Field-name vocabulary follows the existing test suite (`target`, `confidence`, `evidence_refs`, `aliases`, `auto_apply`, `consumed_chunk_ids` — see `tests/test_merge_meta.py` and `tests/test_glossary.py`). Schema references: `scripts/glossary.py` (entity shape), `scripts/manifest.py` (manifest), `scripts/meta.py` (per-chunk meta), `scripts/merge_meta.py` (merge-time confidence promotion rule at `:76-91`).

## When adding a new baseline

A baseline that has never been run is not a baseline — it's just a checked-in book. Before merging:

1. Run the full pipeline on it once (`convert.py` → translate via the skill → `merge_and_build.py`).
2. Capture the real chunk count, image count, and any cross-chunk entity / alias evidence in the new `SOURCE.md`. Replace any estimates you used while picking the book.
3. Note coverage gaps explicitly (e.g. "cover only, no inline images" for sleepy-hollow). Future readers should not have to re-derive what this baseline does and does not exercise.
4. Add an `## Expected outcome` section using the three-tier format above. Anything not present in your initial run goes under **Expected target (unverified)** with that label, not **Measured**. Use approximate values (or "first run records exact value") for unmeasured numerical assertions like `chunk_count`.

Numbers in `SOURCE.md` come from a measured run, not from Project Gutenberg metadata or word-count rules of thumb.
