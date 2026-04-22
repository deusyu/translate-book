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
