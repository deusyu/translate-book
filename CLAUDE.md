# CLAUDE.md

## Project

translate-book is an Agent Skill for Claude Code, Codex, Cursor, OpenClaw, and other Agent Skills–compatible agents that translates books (PDF/DOCX/EPUB) into any language using parallel subagents. Published on ClawHub as `translate-book` and on GitHub as `deusyu/translate-book`.

## Structure

- `SKILL.md` — Skill definition, the orchestration logic that the host agent follows
- `.claude-plugin/marketplace.json` — Plugin-marketplace manifest for Claude Code / GitHub Copilot CLI / Cursor (single plugin, source `./`)
- `scripts/convert.py` — PDF/DOCX/EPUB → Markdown chunks (via Calibre HTMLZ); Markdown input (e.g. from MinerU/Marker) skips Calibre
- `scripts/manifest.py` — SHA-256 chunk tracking and merge validation
- `scripts/glossary.py` — Term-consistency glossary; per-chunk term tables injected into sub-agent prompts
- `scripts/chunk_context.py` — Read-only previous/next chunk excerpts injected into sub-agent prompts
- `scripts/meta.py` — Per-chunk sub-agent observation file schema
- `scripts/merge_meta.py` — Batch-boundary merge of sub-agent observations into the canonical glossary
- `scripts/run_state.py` — Selective re-translation planner and run_state.json recorder
- `scripts/figure_text.py` — Text inside figures: lists images awaiting recognition, validates the recognition sidecars, injects their text into chunks so it is translated with the body
- `scripts/merge_and_build.py` — Merge translated chunks → HTML/DOCX/EPUB/PDF
- `scripts/calibre_html_publish.py` — Calibre format conversion wrapper
- `scripts/template.html`, `scripts/template_ebook.html` — HTML templates

## Prerequisites

- **Python 3.12+** — the version CI runs.
- **Unit tests need nothing else.** Every third-party import in `scripts/` is guarded
  (`try/except ImportError` or function-local), so the suite runs on a bare stdlib.
- **The full pipeline additionally needs** Calibre (`ebook-convert`) and Pandoc binaries,
  plus the `beautifulsoup4` and `markdown` packages (`pypandoc` is optional: `convert.py` calls the `pandoc` binary without it). Missing pieces degrade
  individual output formats rather than failing the run.

## Testing changes

Unit tests (what CI runs — fast, no setup):

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m compileall scripts tests
```

Then test with a small PDF to verify the full pipeline:

```bash
python3 scripts/convert.py /path/to/small.pdf --olang zh
# then run translation via the skill
python3 scripts/merge_and_build.py --temp-dir <name>_temp --title "test"
```

Verify: all output_chunk*.md files exist, manifest validation passes, output formats generate.

## Conventions

- Only `chunk*.md` naming — no `page*` legacy support
- Pipeline output artifacts use the canonical names `book.html`, `book_doc.html`, `book.docx`, `book.epub`, `book.pdf`. Internal scripts and skip/cache logic depend on these names; if title-based filenames are added later they must be optional aliases/copies, not silent replacements
- SKILL.md frontmatter must stay single-line per field (OpenClaw parser requirement)
- Script paths in SKILL.md use `{baseDir}` not hardcoded paths
- SKILL.md defines `{baseDir}` right under its title (the directory containing SKILL.md); only OpenClaw substitutes it, other hosts rely on that definition
- When the host cannot spawn sub-agents, SKILL.md tells the user and stops; it never falls back to translating chunks sequentially in the main context
- `.claude-plugin/marketplace.json` has no `version` field, so hosts use the commit SHA as the version and releases need no manifest bump
- README host tables mark an agent "Verified" only after it passes the minimal test in the README's Compatibility section
- Subagent instructions in SKILL.md must be platform-neutral (work on Codex, Claude Code, OpenClaw)
- Checked-in baseline inputs live under `tests/baselines/<book-id>/`; generated full-pipeline outputs live under `tests/.artifacts/`
- README changes must be synced to README.md, README.zh-CN.md, and README.ja.md
- The package published to ClawHub contains only `SKILL.md`, `LICENSE` and `scripts/`; new docs, assets and tests must also be added to `.clawhubignore`
- Releases follow `.claude/commands/release.md` — check that no open issue is labeled `release-blocker`, then run four commands in order: `git push origin main`, `git tag vX.Y.Z && git push origin vX.Y.Z`, `npx clawhub@latest publish ./ --slug translate-book --name translate-book --version X.Y.Z`, `gh release create vX.Y.Z --verify-tag --generate-notes`. Do not skip the git tag; it's the only version anchor in the repo

## Do not

- Do not reintroduce `page*` file support — it was intentionally removed
- Do not hardcode `~/.claude/skills/` paths in SKILL.md — use `{baseDir}`
- Do not put platform-specific tool names (Agent, sessions_spawn) in `allowed-tools` as the only option — keep the whitelist cross-platform
- Do not add mtime-based incremental rebuild for HTML/format generation — the current skip logic is intentionally simple (existence check). Metadata/template changes require manual cleanup. This is documented in the README.
