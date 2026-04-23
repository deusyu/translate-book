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
