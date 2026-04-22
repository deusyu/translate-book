# Rainman Translate Book

English | [中文](README.zh-CN.md)

Claude Code skill that translates entire books (PDF/DOCX/EPUB) into any language using parallel subagents.

> Inspired by [claude_translater](https://github.com/wizlijun/claude_translater). The original project uses shell scripts as its entry point, coordinating the Claude CLI with multiple step scripts to perform chunked translation. This project restructures the workflow as a Claude Code Skill, using subagents to translate chunks in parallel, with manifest-driven integrity checks, resumable runs, and multi-format output unified into a single pipeline. As the project structure and implementation differ significantly from the original, this is an independent project rather than a fork.

---

## How It Works

```
Input (PDF/DOCX/EPUB)
  │
  ▼
Calibre ebook-convert → HTMLZ → HTML → Markdown
  │
  ▼
Split into chunks (chunk0001.md, chunk0002.md, ...)
  │  manifest.json tracks chunk hashes
  ▼
Parallel subagents (8 concurrent by default)
  │  each subagent: read 1 chunk → translate → write output_chunk*.md
  │  batched to respect API rate limits
  ▼
Validate (manifest hash check, 1:1 source↔output match)
  │
  ▼
Merge → Pandoc → HTML (with TOC) → Calibre → DOCX / EPUB / PDF
```

Each chunk gets its own independent subagent with a fresh context window. This prevents context accumulation and output truncation that happen when translating a full book in a single session.

## Features

- **Parallel subagents** — 8 concurrent translators per batch, each with isolated context
- **Resumable** — chunk-level resume; already-translated chunks are skipped on re-run (for metadata/template changes, use a fresh run)
- **Manifest validation** — SHA-256 hash tracking prevents stale or corrupt outputs from being merged
- **Multi-format output** — HTML (with floating TOC), DOCX, EPUB, PDF
- **Multi-language** — zh, en, ja, ko, fr, de, es (extensible)
- **PDF/DOCX/EPUB input** — Calibre handles the conversion heavy lifting

## Prerequisites

- **Claude Code CLI** — installed and authenticated
- **Calibre** — `ebook-convert` command must be available ([download](https://calibre-ebook.com/))
- **Pandoc** — for HTML↔Markdown conversion ([download](https://pandoc.org/))
- **Python 3** with:
  - `pypandoc` — required (`pip install pypandoc`)
  - `beautifulsoup4` — optional, for better TOC generation (`pip install beautifulsoup4`)

## Quick Start

### 1. Install the skill

**Option A: npx (recommended)**

```bash
npx skills add deusyu/translate-book -a claude-code -g
```

**Option B: ClawHub**

```bash
clawhub install translate-book
```

**Option C: Git clone**

```bash
git clone https://github.com/deusyu/translate-book.git ~/.claude/skills/translate-book
```


### 2. Translate a book

In Claude Code, say:

```
translate /path/to/book.pdf to Chinese
```

Or use the slash command:

```
/translate-book translate /path/to/book.pdf to Japanese
```

The skill handles the full pipeline automatically — convert, chunk, translate in parallel, validate, merge, and build all output formats.

### 3. Find your outputs

All files are in `{book_name}_temp/`:

| File | Description |
|------|-------------|
| `output.md` | Merged translated Markdown |
| `book.html` | Web version with floating TOC |
| `book.docx` | Word document |
| `book.epub` | E-book |
| `book.pdf` | Print-ready PDF |

## Repository Test Assets

- Checked-in baseline inputs live under `tests/baselines/<book-id>/`.
- Generated full-pipeline outputs live under `tests/.artifacts/` and should not be committed.
- Because `scripts/convert.py` writes `{book_name}_temp/` under the current working directory, run repository baseline tests from inside `tests/.artifacts/` to keep generated files out of the repo root.

### Full-Pipeline Baseline Example

```bash
mkdir -p tests/.artifacts
cd tests/.artifacts
python3 ../../scripts/convert.py ../baselines/standard-alice/standard-alice.epub --olang zh
# then run translation via the skill
python3 ../../scripts/merge_and_build.py --temp-dir standard-alice_temp --title "test"
```

## Pipeline Details

### Step 1: Convert

```bash
python3 scripts/convert.py /path/to/book.pdf --olang zh
```

Calibre converts the input to HTMLZ, which is extracted and converted to Markdown, then split into chunks (~6000 chars each). A `manifest.json` records the SHA-256 hash of each source chunk for later validation.

### Step 1.5: Glossary (term consistency across chunks)

Each chunk is translated by a fresh-context sub-agent, which means the same proper noun can drift across multiple translations on a 100-chunk book. To fix this, the skill builds a glossary before translation:

1. Sample 5 chunks (first, last, 3 evenly-spaced middle).
2. Extract proper nouns and recurring domain terms; pick canonical translations.
3. Write `<temp_dir>/glossary.json` (hand-editable schema below).
4. Run `python3 scripts/glossary.py count-frequencies <temp_dir>` to populate per-term frequencies (ASCII terms use word-boundary regex so `cat` doesn't match `category`; CJK terms use substring; single-CJK-char terms are rejected; aliases count toward the term they belong to).
5. For each chunk, the orchestrator calls `python3 scripts/glossary.py print-terms-for-chunk <temp_dir> chunkNNNN.md` and injects the resulting 3-column (`原文 | 别名 | 译文`) markdown table into that chunk's prompt as a hard constraint. Term selection = (terms whose source OR any alias appears in this chunk) ∪ (top-N most-frequent book-wide).

```json
{
  "version": 2,
  "terms": [
    {"id": "Manhattan", "source": "Manhattan", "target": "曼哈顿",
     "category": "place", "aliases": [], "gender": "unknown",
     "confidence": "medium", "frequency": 12,
     "evidence_refs": [], "notes": ""}
  ],
  "high_frequency_top_n": 20,
  "applied_meta_hashes": {}
}
```

Existing v1 `glossary.json` files are auto-upgraded to v2 on first load. v2 forbids the same surface form (source or alias) appearing in two different terms; if a v1 file has polysemous duplicate sources, the upgrade aborts with a disambiguation message — fix the file by hand and reload.

Edit `glossary.json` between runs to fix translations; existing `glossary.json` is never overwritten — delete it to rebuild from scratch.

> **Note on partial reruns**: in the current release, editing `glossary.json` after some chunks have been translated does NOT auto-invalidate those chunks — they keep their old translations. Precise glossary-driven re-translation is planned for the next commit. For now, delete the affected `output_chunk*.md` files (or the whole temp dir) to apply glossary edits.

### Step 2: Translate (parallel subagents)

The skill launches subagents in batches (default: 8 concurrent). Each subagent:

1. Reads one source chunk (e.g. `chunk0042.md`)
2. Translates to the target language
3. Writes the result to `output_chunk0042.md`

If a run is interrupted, re-running skips chunks that already have valid output files. Failed chunks are retried once automatically.

### Step 3: Merge & Build

```bash
python3 scripts/merge_and_build.py --temp-dir book_temp --title "《translated title》"
```

Before merging, the script validates:
- Every source chunk has a corresponding output file (1:1 match)
- Source chunk hashes match the manifest (no stale outputs)
- No output files are empty

Then: merge → Pandoc HTML → inject TOC → Calibre generates DOCX, EPUB, PDF.

**Note:** `{book_name}_temp/` is a working directory for a single translation run. If you change the title, author, output language, template, or image assets, either use a fresh temp directory or delete the existing final artifacts (`output.md`, `book*.html`, `book.docx`, `book.epub`, `book.pdf`) before re-running.

## Project Structure

| File | Purpose |
|------|---------|
| `SKILL.md` | Claude Code skill definition — orchestrates the full pipeline |
| `scripts/convert.py` | PDF/DOCX/EPUB → Markdown chunks via Calibre HTMLZ |
| `scripts/manifest.py` | Chunk manifest: SHA-256 tracking and merge validation |
| `scripts/glossary.py` | Glossary management: per-chunk term tables for consistent terminology |
| `scripts/meta.py` | Per-chunk sub-agent observation file schema (`output_chunkNNNN.meta.json`) |
| `scripts/merge_meta.py` | Batch-boundary merge: sub-agent observations → canonical glossary |
| `scripts/merge_and_build.py` | Merge chunks → HTML → DOCX/EPUB/PDF |
| `scripts/calibre_html_publish.py` | Calibre wrapper for format conversion |
| `scripts/template.html` | Web HTML template with floating TOC |
| `scripts/template_ebook.html` | Ebook HTML template |
| `tests/baselines/` | Checked-in baseline book inputs for full-pipeline testing |
| `tests/.artifacts/` | Ignored full-pipeline test outputs |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Calibre ebook-convert not found` | Install Calibre and ensure `ebook-convert` is in PATH |
| `Manifest validation failed` | Source chunks changed since splitting — re-run `convert.py` |
| `Missing source chunk` | Source file deleted — re-run `convert.py` to regenerate |
| Incomplete translation | Re-run the skill — it resumes from where it stopped |
| Changed title/template/assets but output didn't update | Delete existing `output.md`, `book*.html`, `book.docx`, `book.epub`, `book.pdf` from the temp dir, then re-run `merge_and_build.py` |
| Want page-number footers stripped from PDF output | By default, monotonic page-number sequences (e.g. `1, 2, 3, ...`) are auto-detected and dropped while outliers like years (`1984`), chapter numbers, and citation indices stay preserved. If detection misses your case, pass `--strip-page-numbers` to `convert.py` to aggressively delete every standalone-digit line. The flag aborts if a cached `input.md` or `chunk*.md` already exists — delete them first so the flag actually takes effect. |
| `output.md exists but manifest invalid` | Stale output — the script auto-deletes and re-merges |
| `Glossary upgrade rejected: duplicate source` | v2 disallows two terms sharing a source/alias surface form. Edit `glossary.json` to disambiguate (e.g., rename one source from `Apple` to `Apple (Inc.)`) and reload. |
| PDF generation fails | Ensure Calibre is installed with PDF output support |

## Roadmap

Tracking [issue #7](https://github.com/deusyu/translate-book/issues/7) — name/term inconsistency and pronoun/gender errors across chunks. Today's glossary covers high-frequency main entities; secondary characters, spelling variants, and pronoun resolution are not yet addressed. The plan is four independently shippable phases.

### Design principles

- **Scripts do bookkeeping; LLMs do semantic merge.** State, schemas, dedup, hashing, IO are deterministic Python. Naming, gender attribution, alias judgment, conflict resolution are LLM calls.
- **Single writer for shared state.** Only the main agent writes `glossary.json` and `run_state.json`; sub-agents write per-chunk meta files. No locking needed.
- **Conservative merge.** New entities require evidence; alias merges need LLM judgment, not just string similarity; gender starts at `unknown` and only moves up under explicit evidence; canonical values aren't silently overwritten on conflict.
- **Three-layer state, three separate files.** `glossary.json` (canonical, sub-agents read), `output_chunkNNNN.meta.json` (raw per-chunk observations), `run_state.json` (orchestration).

### Phase 1 — Sub-agent feedback + glossary merge (shipped)

Closes the read+write loop. Glossary v2 adds `id`, `aliases`, `gender`, `confidence`, `evidence_refs`, `notes` (v1 files auto-upgrade on first load; the term table is now 3-col and aliases participate in selection). Sub-agents emit `output_chunkNNNN.meta.json` alongside each translated chunk. `scripts/merge_meta.py` (`prepare-merge` / `apply-merge` / `status`) merges per-batch with conservative rules: surface-form uniqueness enforced, malformed metas quarantined (warn + skip + count), confidence escalation via both `evidence_chunks` and `used_term_sources`, FIFO-cap at 5. See SKILL.md Step 4 / Step 4.5 / Step 5.

### Phase 2 — Neighbor context for pronouns (not started, independent of Phase 1)

Inject `prev_excerpt` (last ~300 chars of previous chunk) and `next_excerpt` (first ~300 chars of next chunk) into each sub-agent prompt as read-only context. No new state files. Pure prompt-assembly change.

### Phase 3 — Selective re-translation (not started, depends on Phase 1)

Phase 1's batch feedback only improves *forward*. Selective rerun closes the *backward* loop: new `scripts/run_state.py` + `run_state.json` schema; per-chunk tracking of `glossary_version_used`, `entity_ids_used`, `output_hash`; five decision rules for deciding which chunks need re-translation this run.

### Phase 4 — Bootstrap warm-up (experimental, gated on Phase 1 data)

Phase 1 grows the glossary batch-by-batch, so the first batch sees the smallest glossary and has the highest drift risk. Possible approaches: sequential bootstrap, variable concurrency, or skip entirely. Decision belongs to whoever has run the system on real books.

> The specific schemas and file layouts in each phase are illustrative — they may shift as Phase 1 hits real data. Phase 4 is gated on data; Phase 3 may be re-scoped or dropped if Phase 1 alone proves "good enough".

## Star History

If you find this project helpful, please consider giving it a Star ⭐!

[![Star History Chart](https://api.star-history.com/svg?repos=deusyu/translate-book&type=Date)](https://star-history.com/#deusyu/translate-book&Date)

## Sponsor

If this project saves you time, consider sponsoring to keep it maintained and improved.

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-pink?logo=github)](https://github.com/sponsors/deusyu)

## License

[MIT](LICENSE)
