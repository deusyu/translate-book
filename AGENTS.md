# AGENTS.md

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
- Releases follow `.claude/commands/release.md` — check that no open issue is labeled `release-blocker`, then run three commands in order: `git push origin main`, `git tag vX.Y.Z && git push origin vX.Y.Z`, `npx clawhub@latest publish ./ --version X.Y.Z`. Do not skip the git tag; it's the only version anchor in the repo

## Do not

- Do not reintroduce `page*` file support — it was intentionally removed
- Do not hardcode per-runtime skill paths (`~/.agents/skills/`, `~/.claude/skills/`) in SKILL.md — use `{baseDir}`
- Do not put platform-specific tool names (Agent, sessions_spawn) in `allowed-tools` as the only option — keep the whitelist cross-platform
- Do not add mtime-based incremental rebuild for HTML/format generation — the current skip logic is intentionally simple (existence check). Metadata/template changes require manual cleanup. This is documented in the README.

## Cursor Cloud specific instructions

### Environment

- Python 3.12+ is pre-installed; no version manager needed.
- System dependencies (Calibre, Pandoc) and pip packages (pypandoc, beautifulsoup4) are installed by the update script.
- Unit tests depend only on the Python stdlib (no pip packages or external binaries needed; `python3 -m unittest discover` runs as-is).

### Running tests

- **Unit tests (CI-equivalent):** `python3 -m unittest discover -s tests -p 'test_*.py' -v` — runs from repo root, no setup needed.
- **Compile check:** `python3 -m compileall scripts tests`

### Full pipeline integration test

Run from `tests/.artifacts/` to keep generated files out of the repo root:

```bash
mkdir -p tests/.artifacts && cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# Create mock output_chunk*.md files (copy source chunks) since actual translation requires LLM subagents
for f in standard-alice_temp/chunk*.md; do cp "$f" "standard-alice_temp/output_$(basename $f)"; done
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

### Known issues

- Ubuntu's Calibre 7.6.0 package has an EPUB generation bug (bytes/str mismatch in `container.py`). DOCX and PDF generation work fine. This is a distro packaging issue, not a codebase bug.
- `pypandoc` installs its CLI script to `~/.local/bin` which may not be on PATH, but the Python library import works regardless.
