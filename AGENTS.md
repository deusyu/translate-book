# AGENTS.md

## Project

translate-book is a Codex Skill that translates books (PDF/DOCX/EPUB) into any language using parallel subagents. Published on ClawHub as `translate-book` and on GitHub as `deusyu/translate-book`.

## Structure

- `SKILL.md` — Skill definition, the orchestration logic that Codex / OpenClaw follows
- `scripts/convert.py` — PDF/DOCX/EPUB → Markdown chunks (via Calibre HTMLZ)
- `scripts/manifest.py` — SHA-256 chunk tracking and merge validation
- `scripts/glossary.py` — Term-consistency glossary; per-chunk term tables injected into sub-agent prompts
- `scripts/meta.py` — Per-chunk sub-agent observation file schema
- `scripts/merge_meta.py` — Batch-boundary merge of sub-agent observations into the canonical glossary
- `scripts/merge_and_build.py` — Merge translated chunks → HTML/DOCX/EPUB/PDF
- `scripts/calibre_html_publish.py` — Calibre format conversion wrapper
- `scripts/template.html`, `scripts/template_ebook.html` — HTML templates

## Testing changes

Use a small file for quick checks, or the checked-in baseline book for the repository's full-pipeline test.

Quick smoke test:

```bash
python3 scripts/convert.py /path/to/small.pdf --olang zh
# then run translation via the skill
python3 scripts/merge_and_build.py --temp-dir <name>_temp --title "test"
```

Full baseline test:

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

Verify: all output_chunk*.md files exist, manifest validation passes, output formats generate.

## Conventions

- Only `chunk*.md` naming — no `page*` legacy support
- SKILL.md frontmatter must stay single-line per field (OpenClaw parser requirement)
- Script paths in SKILL.md use `{baseDir}` not hardcoded paths
- Subagent instructions in SKILL.md must be platform-neutral (work on Codex, OpenClaw, Codex)
- Checked-in baseline inputs live under `tests/baselines/<book-id>/`; generated full-pipeline outputs live under `tests/.artifacts/`
- README changes must be synced to both README.md and README.zh-CN.md
- Releases follow `.claude/commands/release.md` — three commands in order: `git push origin main`, `git tag vX.Y.Z && git push --tags`, `clawhub publish ./ --version X.Y.Z`. Do not skip the git tag; it's the only version anchor in the repo

## Do not

- Do not reintroduce `page*` file support — it was intentionally removed
- Do not hardcode `~/.Codex/skills/` paths in SKILL.md — use `{baseDir}`
- Do not put platform-specific tool names (Agent, sessions_spawn) in `allowed-tools` as the only option — keep the whitelist cross-platform
- Do not add mtime-based incremental rebuild for HTML/format generation — the current skip logic is intentionally simple (existence check). Metadata/template changes require manual cleanup. This is documented in the README.
