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
