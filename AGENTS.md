# AGENTS.md

## Project

translate-book is a Codex Skill that translates books (PDF/DOCX/EPUB) into any language using parallel subagents. Published on ClawHub as `translate-book` and on GitHub as `deusyu/translate-book`.

## Structure

- `SKILL.md` — Skill definition, the orchestration logic that Codex / OpenClaw follows
- `scripts/convert.py` — PDF/DOCX/EPUB → Markdown chunks (via Calibre HTMLZ)
- `scripts/manifest.py` — SHA-256 chunk tracking and merge validation
- `scripts/glossary.py` — Term-consistency glossary; per-chunk term tables injected into sub-agent prompts
- `scripts/chunk_context.py` — Read-only previous/next chunk excerpts injected into sub-agent prompts
- `scripts/meta.py` — Per-chunk sub-agent observation file schema
- `scripts/merge_meta.py` — Batch-boundary merge of sub-agent observations into the canonical glossary
- `scripts/run_state.py` — Selective re-translation planner and run_state.json recorder
- `scripts/merge_and_build.py` — Merge translated chunks → HTML/DOCX/EPUB/PDF
- `scripts/calibre_html_publish.py` — Calibre format conversion wrapper
- `scripts/template.html`, `scripts/template_ebook.html` — HTML templates

## Testing changes

Use a small file for quick checks, or the checked-in baseline book for the repository's full-pipeline test.

Quick smoke test:

```bash
python3 scripts/convert.py /path/to/small.pdf --olang vi
# then run translation via the skill
python3 scripts/merge_and_build.py --temp-dir <name>_temp --title "test"
```

Full baseline test:

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang vi
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
- This fork's default target language is `vi` (Vietnamese), not `zh` — it is the
  default in `convert.py --olang`, `merge_and_build.py`'s `output_lang` fallback,
  `calibre_html_publish.py --lang`, and SKILL.md's `target_lang`. Do not "fix" it
  back to `zh`; other languages including `zh` remain fully supported
- Sub-agents get the English translation prompt for every language except `zh`,
  which keeps the original Chinese prompt. The `vi` path appends a Vietnamese
  style block (proper nouns keep their Latin form, full diacritics, no 书名号)
- README changes must be synced to both README.md and README.zh-CN.md
- Releases follow `.claude/commands/release.md` — three commands in order: `git push origin main`, `git tag vX.Y.Z && git push --tags`, `npx clawhub@latest publish ./ --version X.Y.Z`. Do not skip the git tag; it's the only version anchor in the repo

## Do not

- Do not reintroduce `page*` file support — it was intentionally removed
- Do not hardcode `~/.Codex/skills/` paths in SKILL.md — use `{baseDir}`
- Do not put platform-specific tool names (Agent, sessions_spawn) in `allowed-tools` as the only option — keep the whitelist cross-platform
- Do not add mtime-based incremental rebuild for HTML/format generation — the current skip logic is intentionally simple (existence check). Metadata/template changes require manual cleanup. This is documented in the README.

## Cursor Cloud specific instructions

### Environment

- Python 3.12+ is pre-installed; no version manager needed.
- System dependencies (Calibre, Pandoc) and pip packages (pypandoc, beautifulsoup4) are installed by the update script.
- Unit tests only依赖 Python stdlib（不需要 pip 包或外部二进制，直接 `python3 -m unittest discover` 即可运行）。

### Running tests

- **Unit tests (CI-equivalent):** `python3 -m unittest discover -s tests -p 'test_*.py' -v` — runs from repo root, no setup needed.
- **Compile check:** `python3 -m compileall scripts tests`

### Full pipeline integration test

Run from `tests/.artifacts/` to keep generated files out of the repo root:

```bash
mkdir -p tests/.artifacts && cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang vi
# Create mock output_chunk*.md files (copy source chunks) since actual translation requires LLM subagents
for f in standard-alice_temp/chunk*.md; do cp "$f" "standard-alice_temp/output_$(basename $f)"; done
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

### Known issues

- Ubuntu's Calibre 7.6.0 package has an EPUB generation bug (bytes/str mismatch in `container.py`). DOCX and PDF generation work fine. This is a distro packaging issue, not a codebase bug.
- `pypandoc` installs its CLI script to `~/.local/bin` which may not be on PATH, but the Python library import works regardless.
