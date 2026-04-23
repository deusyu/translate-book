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
