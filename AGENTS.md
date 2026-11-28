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

## Prerequisites

- **macOS or Linux.** Native Windows is not supported: every script in `scripts/` that has a command line, and `tests/process_guard.py`, stops at once with a message that points to WSL (Windows Subsystem for Linux). `tests/test_platform.py` and `tests/test_process_guard.py` check this.

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
- `--delete-outputs` deletes only the files a build recorded in `build_outputs.json` (size and SHA-256), and only while they are unchanged; it never deletes by name. Code that writes an output or copy must call `merge_and_build._record_outputs` once the write has succeeded, and not for a file a step skipped; `--delete-outputs` leaves every file that is not recorded
- SKILL.md frontmatter must stay single-line per field (OpenClaw parser requirement)
- Script paths in SKILL.md use `"{baseDir}"` (quoted) not hardcoded paths
- SKILL.md commands hold only fixed text and the placeholders `"{baseDir}"`, `<temp_dir>` and `<NNNN>`; book paths, titles and other external text reach the scripts through files (the Step 2 request file, fixed files in the temp dir). `tests/test_packaging.py` fails on any other command shape and on Markdown outside the plain subset it reads. Fences open with ```` ```bash ````, each line a command, or ```` ```json ````, one object whose fields are in `JSON_FIELDS` and whose strings are in `JSON_STRINGS` or hold no shell syntax; inline code with a space is a command unless it is listed in `PROSE_SPANS`
- Every test module's first statement is `import process_guard` (`tests/process_guard.py`): from then on a test can start a process only through its runners (`run_skill_command`, `run_script`, `run_tool`, `library_processes`), which give the process its own `HOME`, `TMPDIR` and working directory under a temporary root; any other way that Python code or an audit event can take fails the test, and the guard's docstring lists what it cannot see, such as code that turns it off on purpose. `tests/test_process_guard.py` checks this. A test that runs Pandoc or zsh asks for it with `process_guard.needs()` or `process_guard.installed()`, so that it fails instead of skipping where `TRANSLATE_BOOK_REQUIRED_TOOLS` names the tool
- SKILL.md defines `{baseDir}` right under its title (the directory containing SKILL.md); only OpenClaw substitutes it, other hosts rely on that definition
- When the host cannot spawn sub-agents, SKILL.md tells the user and stops; it never falls back to translating chunks sequentially in the main context
- `.claude-plugin/marketplace.json` has no `version` field, so hosts use the commit SHA as the version and releases need no manifest bump
- README host tables mark an agent "Verified" only after it passes the minimal test in the README's Compatibility section
- Subagent instructions in SKILL.md must be platform-neutral (work on Codex, Claude Code, OpenClaw)
- Checked-in baseline inputs live under `tests/baselines/<book-id>/`; generated full-pipeline outputs live under `tests/.artifacts/`
- README changes must be synced to README.md, README.zh-CN.md, and README.ja.md
- The package published to ClawHub contains only `SKILL.md`, `LICENSE` and `scripts/`; new docs, assets and tests must also be added to `.clawhubignore`
- Releases follow `.claude/commands/release.md`; agents that cannot run it as a slash command (Codex, Cursor, OpenClaw) read the file directly and follow it, replacing `$1` with the version number

## Do not

- Do not reintroduce `page*` file support — it was intentionally removed
- Do not hardcode per-runtime skill paths (`~/.agents/skills/`, `~/.claude/skills/`) in SKILL.md — use `{baseDir}`
- Do not put platform-specific tool names (Agent, sessions_spawn) in `allowed-tools` as the only option — keep the whitelist cross-platform
- Do not add mtime-based incremental rebuild for HTML/format generation — the current skip logic is intentionally simple (existence check). Metadata/template changes require manual cleanup. This is documented in the README.

## Cursor Cloud specific instructions

### Environment

- Python 3.12+ is pre-installed; no version manager needed.
- System dependencies (Calibre, Pandoc) and pip packages (pypandoc, beautifulsoup4) are installed by the update script.
- Unit tests depend only on the Python stdlib (no pip packages or external binaries needed; `python3 -m unittest discover` runs as-is). Tests that need Pandoc or zsh skip without them; set `TRANSLATE_BOOK_REQUIRED_TOOLS="pandoc zsh"`, as CI does, to make them fail instead.

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
