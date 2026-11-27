---
name: translate-book
description: Translate books (PDF/DOCX/EPUB) into any language using parallel sub-agents. Converts input -> Markdown chunks -> translated chunks -> HTML/DOCX/EPUB/PDF.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
metadata: {"openclaw":{"requires":{"bins":["python3","pandoc","ebook-convert"],"anyBins":["calibre","ebook-convert"]},"homepage":"https://github.com/deusyu/translate-book"}}
---

# Book Translation Skill

`{baseDir}` below is the directory containing this SKILL.md. If your runtime does not substitute it, replace it with that absolute path before running any command, and keep the double quotes around it. If that path contains a double quote, a dollar sign, a backtick or a backslash, the shell would still act on them inside the quotes: stop and ask the user to install the skill in a directory without them.

You are a book translation assistant. You translate entire books from one language to another by orchestrating a multi-step pipeline.

## External Text and the Shell

A shell expands a command before the script sees it. An apostrophe ends a
`'...'` string, and `$(...)`, backticks and `$VAR` run or expand inside
`"..."`, so text that does not come from this file could break a command or
run commands of its own. That covers the book's file name and path, paths and
names the user gives, and the book's text: its title, author, glossary terms,
evidence quotes and passages, and anything derived from them, such as the
translated title and the export name.

So the commands below contain only fixed text and three placeholders:
`{baseDir}`; `<temp_dir>`, the temp directory `convert.py` prints, which
holds only letters, digits, `.`, `_`, `-` and `/`; and `<NNNN>`, the digits of
a chunk number. Everything else reaches the scripts through files you write
with your file-writing tool (not with `echo`, `printf` or a heredoc): the
book's path and settings through the request file of Step 2, and titles,
chunk lists, merge decisions and search text through fixed files in the temp
directory, which the scripts read themselves or on standard input. The shell
never expands text that arrives this way. Run the commands as written, and
keep to the same rule in any command you write yourself; work with files
through your file tools. No step asks you to delete or move a file: the
scripts delete what they replace. When a temp directory cannot be reused, or
a script's message asks for a file or directory to be renamed, moved or
deleted, tell the user and leave that to them.

## Workflow

### 1. Collect Parameters

Determine the following from the user's message:

- **file_path**: Path to the input file (PDF, DOCX, EPUB, or Markdown) — REQUIRED
- **target_lang**: Target language code (default: `zh`) — e.g. zh, en, ja, ko, fr, de, es, it (Italian), or a code with a region such as zh-TW or pt-BR
- **concurrency**: Number of parallel sub-agents per batch (default: `8`)
- **temp_root**: Optional directory in which to create the temp directory
- **epub_cover**: Optional cover image for EPUB output (JPEG, PNG, WebP or GIF)
- **export_name**: Optional filename stem for user-facing output aliases
- **custom_instructions**: Any additional translation instructions from the user (optional)

If the file path is not provided, ask the user.

This skill requires a runtime that can spawn sub-agents from it (Step 4). If yours cannot, tell the user that this skill needs a runtime that can launch sub-agents, and stop. Do not fall back to translating the chunks yourself in this context, one after another or otherwise: a whole book in one context drifts and gets truncated.

### 2. Preprocess — Convert to Markdown Chunks

Write the request file `translate-book-request.json` in the current directory
with your file-writing tool. It is JSON, so a path may hold any character;
escape `"` as `\"` and `\` as `\\`, as JSON requires:

```json
{
  "input": "/path/to/book.epub",
  "target_lang": "zh",
  "temp_root": null,
  "cover": null,
  "parser": null
}
```

- `input` — the book (`file_path`), preferably as an absolute path; `~/` is expanded
- `target_lang` — the target language code
- `temp_root` — the user's `temp_root`, or `null` for the current directory
- `cover` — the user's `epub_cover`, or `null`
- `parser` — a layout-aware PDF parser, described below, or `null`

Then run:

```bash
python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json
```

Write the request file again before every run of this command, including the
runs that later steps ask for. A successful run keeps a copy of the request
as `<temp_dir>/request.json` and deletes the request file, unless the file
changed during the run. Write a later request from the settings in that copy
and change only what the step asks for, since a field left out takes its
default (a `cover` left out means a build without a cover). A failed run
leaves the request in place. Run one conversion at a
time in a directory, and check that the `Input file:` line of the output names
the book you asked for. If it names another file, the request was replaced
meanwhile: write it again and re-run.

If `convert.py` rejects the request, it names the field at fault; fix the file
and run again. `temp_root` may contain only letters, digits, `.`, `_`, `-` and
`/`. If the user's directory has other characters, such as spaces, ask for
another directory or leave `temp_root` out.

`convert.py` prints `Temp directory:` followed by a path. Use exactly that path
as `<temp_dir>` in every later command; do not build it yourself or make it
absolute. It is the book's file name reduced to letters, digits, `.`, `_` and
`-`, plus `_temp`: `《三体》（刘慈欣）.epub` gives `三体_刘慈欣_temp/`. A
temp directory that an earlier version of this skill named after the raw file
name is renamed to the new name when `convert.py` can tell it was made from
this book; `convert.py` says so. Otherwise it is left alone, and the book is
converted afresh.

The conversion needs Calibre (`ebook-convert`) and the `pandoc` binary;
`pypandoc` is used when installed but is not required. If `convert.py` stops
on a missing tool, tell the user which tool is missing and how you plan to
install it. Do not install it without the user's consent, unless they have
already told you that you may install missing tools. Once it is installed,
run Step 2 again: the temp dir is reused, and the book's title and author are
kept in `config.txt`.

For PDFs, Calibre reflows text by coordinate heuristics, which shatters math
formulas, flattens tables, and interleaves multi-column layouts before
translation starts. If the PDF is an academic or technical document and a
layout-aware parser is already installed — or the user asks for one — set
`parser` in the request to `mineru` (MinerU >= 4, which runs `mineru-kit`),
`marker` (`marker_single`) or `docling`. `convert.py` runs the parser on the
PDF with one fixed set of arguments per parser, which point its output at
`<temp_dir>/parsed/`, a directory that `convert.py` creates for the run and
removes once it has converted the Markdown found there. Parsing a book can
take many minutes, longer when the parser first downloads its models, so give
the command the longest time limit your runtime allows. These CLIs change
between versions; if the parser fails, `convert.py` prints the end of its
output and its fixed command line. The request takes no other parser arguments:
if the user's version needs some, the user runs the parser with them and
gives you the Markdown file it writes, which you convert as Markdown input
(below). Do not install a parser without the user's consent, because they
download large models.

If the document has formulas, prefer MinerU or Marker: both keep inline math
as LaTeX, while docling's standard pipeline recognizes display formulas only
and turns inline math into plain text. Use docling when it is the only parser
installed or the user asks for it, and tell the user about this first.

Markdown input (`.md` / `.markdown`), such as a parser's output that the user
already has, skips Calibre. Images in the Markdown file's directory or its
subdirectories, and base64 images inlined in it, are copied into the temp
directory. An image whose path leads outside that directory is not copied,
and `convert.py` warns about it; tell the user, and do not copy the file in
without their consent.

If `convert.py` warns that formulas were not decoded or images were exported
as placeholders, the parser dropped that content and translation cannot
restore it. Tell the user. Re-extracting needs the parser options the
warning names (they may download another model), which `convert.py` does not
pass: the user runs the parser with them and gives you the Markdown file it
writes. Convert that file into a new temp directory, which the user gets one
of two ways: you set `temp_root` in the request to another directory, or they
delete the current temp directory themselves. Ask which they want and stop;
once they have answered, write the request with that file as `input` and
`parser` null, and run Step 2 again.

If `convert.py` warns about display formulas but no inline math, the parser
has probably turned inline math into plain text, which translation cannot
restore either. Tell the user. With their consent, re-extract with MinerU or
Marker into a new temp directory, which the user gets one of two ways: you
set `temp_root` in the request to another directory, or they delete the
current temp directory themselves. Ask which they want and stop; once they
have answered, set `parser` in the request to `mineru` or `marker` and run
Step 2 again. If the document has no inline math, continue.

Page-number lines are detected and dropped for PDF input only; EPUB and DOCX
keep every standalone number (chapter, verse or exercise numbers). If an EPUB
prints its page numbers as text and they show up in the chunks, it has to be
converted again before translating, in a new temp directory. Tell the user,
who gets one of two ways: you set `temp_root` in the request to another
directory, or they delete the current temp directory themselves. Ask which
they want and stop; once they have answered, run:

```bash
python3 "{baseDir}/scripts/convert.py" --request translate-book-request.json --detect-page-numbers
```

The temp directory contains:

- `input.html` (Calibre input only), `input.md` — intermediate files
- `chunk0001.md`, `chunk0002.md`, ... — source chunks for translation
- `manifest.json` — chunk manifest for tracking and validation
- `source_fingerprint.json` — the source file's path, size and SHA-256, with the parser and target language the temp dir was made with
- `config.txt` — pipeline configuration with metadata
- `request.json` — the request of the last successful run, whose `cover` the build uses

If `convert.py` stops because the temp dir was made from a different file,
parser or target language, or because it cannot tell what the temp dir holds
(a `source_fingerprint.json` that is unreadable or incomplete, or files it did
not write), it says why and changes nothing. To convert this book, set
`temp_root` in the request to another directory. To continue the run in that
temp dir, write the request with the file, parser and target language it was
made from. A temp dir made before fingerprinting existed is adopted, with a
warning, only when its `config.txt` names a file of the same name and the same
target language and the request names no parser. One whose fingerprint an
earlier version wrote, without the target language, is used only when its
`config.txt` names the same target language and the request names no parser.
Otherwise `convert.py` stops the same way.

### 3. Discover Source Chunks

List the source chunks `chunk0001.md`, `chunk0002.md`, ... in `<temp_dir>`
with your file-listing tool, for example with the pattern
`<temp_dir>/chunk*.md`, and leave out `output_chunk*.md`. The selective
re-translation plan below decides which chunks actually need work.

### 3.5. Build Glossary (term consistency)

A separate sub-agent translates each chunk with a fresh context. Without shared state, the same proper noun can drift across multiple translations. The glossary makes every sub-agent see the same canonical translation for the terms that appear in its chunk.

If `<temp_dir>/glossary.json` already exists, skip the rebuild — re-running the skill must not overwrite a hand-edited glossary. Rebuild it only when the user asks: follow the steps below, and the new file replaces the old one.

Otherwise:

1. **Sample chunks**: read `chunk0001.md`, the last chunk, and 3 evenly-spaced middle chunks. If `chunk_count < 5`, sample all of them.
2. **Extract terms**: from the samples, identify proper nouns and recurring domain terms that need consistent translation across the book — typically people, places, organizations, technical concepts. Translate each into the target language. Skip generic vocabulary that any translator would render the same way.
3. **Write `glossary.json`** in the temp dir with your file-writing tool, matching this v2 schema:

   ```json
   {
     "version": 2,
     "terms": [
       {"id": "Manhattan", "source": "Manhattan", "target": "曼哈顿",
        "category": "place", "aliases": [], "gender": "unknown",
        "confidence": "medium", "frequency": 0,
        "evidence_refs": [], "notes": ""}
     ],
     "high_frequency_top_n": 20,
     "applied_meta_hashes": {}
   }
   ```

   Existing v1 `glossary.json` files are auto-upgraded to v2 on first load. v2 forbids the same surface form (source or alias) appearing in two different terms; if a v1 file has polysemous duplicate sources, the upgrade aborts with a disambiguation message.

4. **Count frequencies** by running:

   ```bash
   python3 "{baseDir}/scripts/glossary.py" count-frequencies "<temp_dir>"
   ```

   This scans every `chunk*.md` (excluding `output_chunk*.md`), updates each term's `frequency` field, and writes back atomically.

The glossary is hand-editable. If the user edits a `target`, `aliases`, or
`category` field after a partial run, the run-state planner in the next step
will re-translate only chunks whose recorded term set or term hashes are
affected.

### 3.7. Plan Selective Re-translation

Run:

```bash
python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"
```

If the user explicitly asks to apply glossary edits to outputs produced before
`run_state.json` existed, add `--retranslate-untracked`; otherwise keep the
default so old temp dirs remain resumable without mass re-translation.

Capture stdout JSON:

- `translation_chunk_ids` — chunks to translate in this run.
- `record_only_chunk_ids` — existing valid outputs that need `run_state.json`
  records but do not need translation.
- `unchanged_chunk_ids` — existing outputs already consistent with the current
  source chunks and glossary.

If `record_only_chunk_ids` is non-empty, record them before launching
sub-agents: write the ids to `<temp_dir>/record_ids.txt` with your
file-writing tool, one per line, replacing any earlier list, then run:

```bash
python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" < "<temp_dir>/record_ids.txt"
```

Use `translation_chunk_ids` as the work queue for Step 4. If it is empty, skip
to Step 5.

### 4. Parallel Translation with Sub-Agents

**Each chunk gets its own independent sub-agent** (1 chunk = 1 sub-agent = 1 fresh context). This prevents context accumulation and output truncation.
If you cannot spawn sub-agents, stop as described at the end of Step 1.

Launch chunks in batches to respect API rate limits:

- Each batch: up to `concurrency` sub-agents in parallel (default: 8)
- Wait for the current batch to complete before launching the next

On some runtimes the spawn call returns as soon as the sub-agent starts and
the agent keeps running in the background. "Complete" means the files exist,
not that the spawn call returned: before launching the next batch, check that
every chunk in the batch has a non-empty `output_chunk<NNNN>.md` **and** its
`output_chunk<NNNN>.meta.json` (the meta file is written last). Poll for them
instead of assuming. Also read each spawn call's result: a refused spawn (for
example a runtime limit on concurrent sub-agents) returns an error instead of
an agent. Count it as not started, keep the chunk queued, and wait for running
agents to finish before launching more. Never launch a chunk that already
has an agent running, or two agents will write the same output file.

**Spawn each sub-agent with the following task.** Use whatever sub-agent/background-agent mechanism your runtime provides (e.g. the Agent or Task tool, sessions_spawn, invoke_subagent, or equivalent). If you start sub-agents with a shell command, write each task to `<temp_dir>/task_chunk<NNNN>.md` first and give the command that path, or the file on standard input, never the task text (see "External Text and the Shell"): the term table and neighbor excerpts in it are book text.

The output file is `output_` prefixed to the source filename: `chunk0001.md` → `output_chunk0001.md`.

> Translate the file `<temp_dir>/chunk<NNNN>.md` to {TARGET_LANGUAGE} and write the result to `<temp_dir>/output_chunk<NNNN>.md`. Follow the translation rules below. Output only the translated content — no commentary. Write files with your file-writing tool, never through a shell command, and never put text from the chunk into a shell command.

Each sub-agent receives:

- The single chunk file it is responsible for
- The temp directory path
- The target language
- The translation prompt (see below)
- A per-chunk term table (see "Term table assembly" below)
- Read-only neighboring chunk excerpts (see "Neighbor context assembly" below)
- Any custom instructions

**Term table assembly** — before spawning a sub-agent, run:

```bash
python3 "{baseDir}/scripts/glossary.py" print-terms-for-chunk "<temp_dir>" "chunk<NNNN>.md"
```

Capture stdout. The CLI emits a 3-column markdown table (`原文 | 别名 | 译文`) of every term that either appears in this chunk (by source OR any alias) OR is in the top-N most-frequent terms book-wide. Inject the table as `{TERM_TABLE}` in rule #13 of the translation prompt. **If stdout is empty (no glossary, or no relevant terms), omit rule #13 from this chunk's prompt entirely** — do not leave a dangling `{TERM_TABLE}` placeholder.

**Neighbor context assembly** — before spawning a sub-agent, run:

```bash
python3 "{baseDir}/scripts/chunk_context.py" "<temp_dir>" "chunk<NNNN>.md"
```

Capture stdout. The CLI emits prompt-ready read-only excerpts: the last ~300
characters of the previous chunk and the first ~300 characters of the next
chunk when those files exist. Inject this block as `{NEIGHBOR_CONTEXT}`. If
stdout is empty, omit the neighbor-context block entirely. The sub-agent must
not translate neighboring excerpts or copy them into the output; they are only
for pronoun, gender, and entity-resolution context.

**Each sub-agent's task**:

1. Read the source chunk file (e.g. `chunk0001.md`)
2. Translate the content following the translation rules below
3. Write the translated content to `output_chunk0001.md`
4. Write observations to `output_chunk0001.meta.json` matching the schema below. **Non-blocking** — leave fields empty if unsure; do not invent entities. Always emit the file (even if all arrays are empty), because its presence + content hash is how the main agent tracks whether feedback was already merged.

**Sub-agent meta schema** (`output_chunk<NNNN>.meta.json`):

```json
{
  "schema_version": 1,
  "new_entities": [
    {"source": "Taig", "target_proposal": "泰格", "category": "person",
     "evidence": "<≤200-char quote from the chunk>"}
  ],
  "alias_hypotheses": [
    {"variant": "Taig", "may_be_alias_of_source": "Tai",
     "evidence": "<≤200-char quote>"}
  ],
  "attribute_hypotheses": [
    {"entity_source": "Tai", "attribute": "gender", "value": "male",
     "confidence": "high", "evidence": "<≤200-char quote>"}
  ],
  "used_term_sources": ["Tai", "Manhattan"],
  "conflicts": [
    {"entity_source": "Tai", "field": "target", "injected": "泰",
     "observed_better": "太一", "evidence": "<≤200-char quote>"}
  ]
}
```

**Do NOT include a `chunk_id` field** — chunk identity is derived from the filename. Putting it in the payload creates a hallucination hole and validation will reject the file.

The meta file is read by the main agent later and merged into `glossary.json` (see `merge_meta.py`). Sub-agents should fill the schema honestly: cite real quotes from the chunk, never invent entities to "look productive". An empty meta is a perfectly valid output.

**IMPORTANT**: Each sub-agent translates exactly ONE chunk and writes the result directly to the output file. No START/END markers needed.

#### Translation Prompt for Sub-Agents

Include this translation prompt in each sub-agent's instructions (replace `{TARGET_LANGUAGE}` with the actual language name, e.g. "Chinese"):

---

请翻译markdown文件为 {TARGET_LANGUAGE}.
IMPORTANT REQUIREMENTS:

1. 严格保持 Markdown 格式不变，包括标题、链接、图片引用等
2. 仅翻译文字内容，保留所有 Markdown 语法和文件名
   - 数学公式（行内 `$...$`、公式块 `$$...$$`）内的 LaTeX 原样保留，不要翻译、改写或删除其中任何字符
   - 表格（Markdown `| ... |` 表格或 HTML `<table>`）保持行列结构不变，只翻译单元格中的文字
   - 脚注：正文中的引用标记 `[^12]` 与行首的定义标记 `[^12]:` 原样保留，不要改编号、不要删除、不要移动位置，只翻译定义后面的注释文字；定义的续段保持行首四个空格的缩进
   - 图内文字块：开头行 `::: {.figure-text image="..."}` 和单独成行的结尾 `:::` 原样保留（半角冒号，花括号里的内容一个字符都不要改），只逐条翻译两行之间 `- ` 条目的文字，不要合并、拆分或删除条目；条目里的反斜杠转义（如 `\*`、`\[`）保留
3. 删除空链接、不必要的字符和如: 行末的 `\`。页码已由 convert.py 上游处理，不要再删除独立的数字行（可能是年份 1984、章节编号、引用编号等正文内容）。
4. 保证格式和语义准确翻译内容自然流畅
5. 只输出翻译后的正文内容，不要有任何说明、提示、注释或对话内容。
6. 表达清晰简洁，不要使用复杂的句式。请严格按顺序翻译，不要跳过任何内容。
7. 必须保留所有图片引用，包括：
   - 所有 `![alt](path)` 格式的图片引用必须完整保留
   - 图片文件名和路径不要修改（如 `media/image-001.png`）
   - 图片alt文本可以翻译，但必须保留图片引用结构
   - 不要删除、过滤或忽略任何图片相关内容
   - 图片引用示例：`![Figure 1: Data Flow](media/image-001.png)` -> `![图1：数据流](media/image-001.png)`
   - **原始 HTML 标签（如 `<img alt="..." />`、`<a title="...">`）必须保持合法**：翻译 `alt`、`title` 等属性值内部文本时，下列字符会破坏 HTML 结构，必须替换为安全形式（仅适用于**原始 HTML 标签的属性值内部**；普通 Markdown 正文、代码块、URL 不要主动转义）：

     | 字符 | 在属性值内的危险 | 替换为 |
     |------|---------------|--------|
     | `"` | 闭合 `attr="..."` | 目标语言合适的弯引号（如中文 `“` `”`）或 `&quot;` |
     | `'` | 闭合 `attr='...'` | 目标语言合适的弯引号（如中文 `‘` `’`）或 `&#39;` |
     | `<` | 被解析为新标签 | `&lt;` |
     | `>` | 被解析为标签结束 | `&gt;` |
     | `&` | 被解析为实体起始（除非已是 `&xxx;`） | `&amp;` |

     不要修改 `src`、`href` 等结构性属性的值，只翻译可见文本属性（`alt`、`title`）。

     - 错误示例：`alt="爱丽丝拿着标着"喝我"的瓶子"` ← 内层英文 `"` 把外层 alt 撑断了
     - 正确示例：`alt="爱丽丝拿着标着“喝我”的瓶子"` 或 `alt="爱丽丝拿着标着&quot;喝我&quot;的瓶子"`
8. 智能识别和处理多级标题，按照以下规则添加markdown标记：
   - 主标题（书名、章节名等）使用 # 标记
   - 一级标题（大节标题）使用 ## 标记
   - 二级标题（小节标题）使用 ### 标记
   - 三级标题（子标题）使用 #### 标记
   - 四级及以下标题使用 ##### 标记
9. 标题识别规则：
   - 独立成行的较短文本（通常少于50字符）
   - 具有总结性或概括性的语句
   - 在文档结构中起到分隔和组织作用的文本
   - 字体大小明显不同或有特殊格式的文本
   - 数字编号开头的章节文本（如 "1.1 概述"、"第三章"等）
   - 原文常把章号单独放一行、下一行才是章名（如单独一行 "11"，下一行 "Why Has Christianity…"），或把 "Part 1" 与部分名分成两行。这种情况合并成**一个**标题，按目标语言习惯写出编号（如 `第11章 为什么……`、`第一部分 政治领袖`），标题层级沿用原文该标题的层级。不要把单独的数字行做成标题，也不要把章号和章名拆成两个标题
10. 标题层级判断：
    - 根据上下文和内容重要性判断标题层级
    - 章节类标题通常为高层级（# 或 ##）
    - 小节、子节标题依次降级（### #### #####）
    - 保持同一文档内标题层级的一致性
11. 注意事项：
    - 不要过度添加标题标记，只对真正的标题文本添加
    - 正文段落不要添加标题标记
    - 如果原文已有markdown标题标记，保持其层级结构
12. {CUSTOM_INSTRUCTIONS if provided}
13. 术语一致性：以下术语必须严格使用指定译法，不要自行变换。表格中"原文"列**或"别名"列**任一形式出现在正文中时，都必须翻译为"译文"列对应的形式。

{TERM_TABLE}

邻居上下文（只读，不要翻译，不要写入输出，只用于判断代词、性别、别名和跨 chunk 指代；为空则省略）:

{NEIGHBOR_CONTEXT}

markdown文件正文:

---

### 4.5. Merge Sub-Agent Meta Into Glossary (after each batch)

Each sub-agent emitted an `output_chunk<NNNN>.meta.json` alongside its translated chunk. After every batch completes, first record the completed chunk outputs in `run_state.json` while the glossary is still the one used for that batch, then merge observations into the canonical glossary so subsequent batches see an enriched glossary.

1. Record successfully translated chunks from this batch before mutating the glossary. Write their ids to `<temp_dir>/record_ids.txt` with your file-writing tool, one per line, replacing the previous list, then run:

   ```bash
   python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" < "<temp_dir>/record_ids.txt"
   ```

   If this fails, fix the missing/empty output or state error before continuing.

2. Run prepare-merge:

   ```bash
   python3 "{baseDir}/scripts/merge_meta.py" prepare-merge "<temp_dir>"
   ```

   Capture stdout JSON. It contains four arrays:
   - `auto_apply` — new entities with no glossary collision and unanimous (target, category) across all proposing chunks.
   - `decisions_needed` — items requiring main-agent judgment. Each has `id`, `kind`, an `options` array, and the data needed to pick. Kinds:
     - `alias` — `{variant, candidate_source, evidence}`. Choices: `yes_alias` / `no_separate_entity` / `skip`.
     - `conflict` — `{entity_source, field, current, proposed, evidence}`. Choices: `keep_current` / `accept_proposed` / `record_in_notes`.
     - `new_entity_existing_alias` — sub-agents propose `proposed_source` as a new entity, but it's already someone's alias. `{proposed_source, currently_alias_of, promoted_variants: [{target_proposal, category, evidence, evidence_chunks}, ...]}`. Choices: one `use_variant_N` per distinct (target, category) promotion variant (promote `proposed_source` to standalone with that target+category, removing it from the host's aliases) / `keep_as_alias` / `skip`.
     - `existing_entity_conflict` — sub-agents proposed a (target, category) for `entity_source` that differs from the canonical. Multiple distinct differing proposals all get exposed. `{entity_source, current_target, current_category, proposed_variants: [{target_proposal, category, evidence, evidence_chunks}, ...]}`. Choices: `keep_current` / one `use_variant_N` per competing proposal (overwrites both target AND category, stamps the prior values into notes) / `record_in_notes` (canonical unchanged; every proposed variant gets logged to notes).
     - `alias_or_new_entity` — `variant` has multiple competing options that can't all coexist under v2's surface-form uniqueness rule. Triggered when (a) `variant` was proposed both as a new standalone entity AND as an alias of one or more candidates, OR (b) `variant` was proposed as an alias of two or more different candidates with no standalone competitor. `{variant, alias_candidates: [{candidate_source, evidence, evidence_chunks}, ...], standalone_variants: [{target_proposal, category, evidence, evidence_chunks}, ...]}`. Choices: one `use_alias_N` per candidate (attach as alias of that candidate), one `use_standalone_N` per competing standalone proposal (add as standalone with that target+category), or `skip`.
     - `conflicting_new_entity_proposals` — `{source, variants: [{target_proposal, category, evidence, evidence_chunks}, ...]}`. Choices: `use_variant_0`, `use_variant_1`, ..., `skip`.
   - `consumed_chunk_ids` — every meta file scanned this round (regardless of whether it produced a finding). These hashes get recorded in `applied_meta_hashes` on apply.
   - `malformed_meta_chunk_ids` — meta files that failed validation. Quarantined: not consumed, not crashing the run. Surface them in your batch progress.

3. **If `consumed_chunk_ids` is empty** → nothing was scanned; skip to Step 5.

4. **If `consumed_chunk_ids` is non-empty but both `auto_apply` and `decisions_needed` are empty** → still run `apply-merge` (item 6) with `{"auto_apply": [], "decisions": [], "consumed_chunk_ids": [...]}` so the hashes get recorded. **Skipping this is the bug** — no-op metas would re-scan forever otherwise.

5. **Otherwise, resolve each decision**:
   - Read its evidence quotes inline.
   - Pick one option from its `options` array.
   - Build a `decisions` entry that round-trips the original decision plus your choice. The entry MUST include the original `kind` and (for `conflicting_new_entity_proposals`) the `variants` array, so apply-merge can validate and act:

     ```json
     {"id": "d1", "kind": "alias", "variant": "Taig", "candidate_source": "Tai", "choice": "yes_alias"}
     ```

6. Write the decisions JSON, `{"auto_apply": [...], "decisions": [...], "consumed_chunk_ids": [...]}`, to `<temp_dir>/merge_decisions.json` with your file-writing tool, replacing the previous batch's file. Its entity names and evidence quotes are book text, so apply-merge reads the file on stdin instead of taking the JSON in the command (see "External Text and the Shell"):

   ```bash
   python3 "{baseDir}/scripts/merge_meta.py" apply-merge "<temp_dir>" < "<temp_dir>/merge_decisions.json"
   ```

   Surface the summary JSON (`auto_applied`, `decisions_resolved`, `consumed_chunks`, `errors`) in your batch progress message.

   **apply-merge is transactional.** If any decision is malformed (wrong choice for kind, missing fields, references a non-existent entity), the entire batch aborts with a non-zero exit and stderr details — no glossary mutation, no hashes recorded. On non-zero exit, fix the offending decision in `merge_decisions.json` and run the command again; `prepare-merge` will surface the same proposals because nothing was consumed.

   **Decision order in the input list is not significant.** `apply-merge` internally dispatches entity-creating decisions before alias-attaching ones, so `yes_alias` decisions whose candidate is created by another decision in the same batch (a `use_standalone_N`, `use_variant_N`, or `promote_to_separate_entity`) succeed regardless of the order you pass them in. Alias chains (e.g. `Taighi → Taig` where `Taig → Tai` is also a pending alias decision) resolve via a fixed-point loop within the alias-attacher pass — you don't need to topo-sort or sequence chained aliases manually.

On a fresh run after a previous interrupted batch, `prepare-merge` will pick up any meta files left behind. Don't manually delete them.

### 5. Verify Completeness and Retry

After all batches complete, list `<temp_dir>` with your file-listing tool to check that every source chunk has a corresponding output file.

If any are missing, retry them — each missing chunk as its own sub-agent. Maximum 2 attempts per chunk (initial + 1 retry).

Also read `manifest.json` and verify:

- Every chunk id has a corresponding output file
- No output file is empty (0 bytes) or blank (whitespace-only)

Check the heading structure of the whole book, since each chunk was translated
in isolation and headings decide the EPUB/PDF table of contents. This lists
every heading of the output chunks in book order, leaving out `#` lines inside
code blocks:

```bash
python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>" --headings
```

Look for chapters split into two headings (a bare number heading followed by
the title heading), the same kind of unit at different levels (one chapter
`#`, the next `##`), and back-matter sections nested under the index. Fix
them in the `output_chunk*.md` files and record each edited chunk (see the
**Edit** path in Step 9).

Also look at front and back matter carried over from the source edition, which
no longer fits the translated book: a printed contents list copied from the
source (the reader builds its own TOC), a "page numbers follow the print
edition" accessibility note, and an index whose page numbers refer to the
print edition. Tell the user what you found and remove or annotate it only
with their agreement.

Then run the meta-merge observability snapshot:

```bash
python3 "{baseDir}/scripts/merge_meta.py" status "<temp_dir>"
```

Also run the selective re-translation state snapshot:

```bash
python3 "{baseDir}/scripts/run_state.py" status "<temp_dir>"
```

Surface a one-line summary in the verification report:

> Translated chunks: 50 • Meta files: 48 found / 47 consumed • Malformed: 1 (chunk0099 — see stderr) • Chunks missing meta: chunk0017, chunk0042

Severity rules (none of these fail the run — meta is non-blocking):

- `unmerged_meta_files > 0` after Step 4.5 ran → bug, flag prominently. Resume should have caught this.
- `malformed_meta_files > 0` → sub-agent emitted invalid meta; print chunk_ids and a "fix the file by hand and re-run if you want this chunk's feedback merged" note.
- `meta_files_found < translated_chunks` → sub-agent-compliance issue (some chunks didn't emit meta at all). Print missing chunk_ids.

Report any chunks that failed translation after retry.

### 6. Translate Book Title

Read `config.txt` from the temp directory to get the `original_title` field.
If it is missing (e.g. Markdown input with neither front matter nor a leading
`#` title) or is just the source file name (Calibre falls back to it for PDFs
without title metadata, e.g. `1706.03762`), read the start of `chunk0001.md`:
if it clearly shows the document's title, use that; otherwise use the source
file name.

Translate the title to the target language. If the book has a published
translation, use its title (ask the user, or search for it) rather than a
literal translation. Pass the bare title, without 书名号 or quotes: it becomes
the EPUB/DOCX/PDF metadata title, which readers show in page headers and the
library (`merge_and_build.py` strips a wrapping `《》` anyway).

### 7. Post-process — Merge and Build

The build skips outputs that already exist. When rebuilding after changing
the title, author or cover, first delete the earlier outputs, or the old
metadata stays inside the files even though the file names change:

```bash
python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>" --delete-outputs
```

It deletes `book.html`, `book_doc.html`, `output.html`, `book.docx`,
`book.epub`, `book.pdf` and the export copies of earlier builds, builds
nothing, and keeps the chunks, translations and other intermediate files. If
it reports an error, fix what it names and run it again before going on.

After that, or straight away on a first build, write these files with your
file-writing tool. The build script reads them itself, so no book text
passes through the command (see "External Text and the Shell"):

- `<temp_dir>/title.txt` — the translated title from Step 6
- `<temp_dir>/export_name.txt` — the user's `export_name` if given, otherwise
  the translated title with characters that are invalid in file names
  removed. The build adds copies of every output under this name (such as
  `三体.epub`) next to the canonical `book.*` files, which the pipeline keeps
  using internally. If the user wants no such copies, leave the file empty.
- `<temp_dir>/author.txt` — only to override the author in `config.txt`

Then run the build script:

```bash
python3 "{baseDir}/scripts/merge_and_build.py" --temp-dir "<temp_dir>"
```

The EPUB cover is the request's `cover` (Step 2): the build reads the image's
path from `<temp_dir>/request.json` and the image from there, so it has to
stay where it was. To add or change the cover later, or if the build reports
that the image is not found, write the request from `<temp_dir>/request.json`
with the image's current path (ask the user where it is) or `null`, run Step
2 again (it keeps the chunks), delete the earlier outputs with
`--delete-outputs` as above, and build.

Keep the intermediate files. The chunks, `manifest.json`, and `run_state.json`
are what make spot fixes and selective re-translation possible after the build
(see Step 9). Add `--cleanup` only when the user explicitly asks to delete
intermediates, and tell them first that it permanently deletes `chunk*.md`,
`output_chunk*.md`, `input.html`, `input.md` and `output.html`: after that,
fixing a single chunk means converting and translating the whole book again.

The script reads the output language from `config.txt`.

This produces in the temp directory:

- `output.md` — merged translated markdown
- `book.html` — web version with floating TOC
- `book_doc.html` — ebook version
- `book.docx`, `book.epub`, `book.pdf` — format conversions (requires Calibre)

### 8. Report Results

Tell the user:

- Where the output files are located
- How many chunks were translated
- The translated title
- List generated output files with sizes
- Any format generation failures
- Any `pandoc left N formula(s) as raw TeX` warning with the formulas it lists;
  those show as LaTeX source in the book and can be fixed as in Step 9
- That the intermediate files are kept so passages can still be fixed
  (Step 9), and that they can ask for cleanup once they are done with the book

Do not run `--cleanup` as part of this report. Offer it only after the user
says they expect no more edits, and before running it, say what it deletes
(see Step 7). Running the Step 7 build command with `--cleanup` added keeps
the existing outputs and then deletes the intermediates; if any format fails,
the script skips cleanup and keeps them.

### 9. Spot Fixes After the Build

If the user finds problems in a few places after the build, fix the affected
chunks rather than `output.md`: whenever a chunk changes, the build re-merges
`output.md` from the chunks and discards edits made there.

1. Find the chunk: search `<temp_dir>/output_chunk*.md` for the passage
   with your file-search tool. The passage is book text, so to use `grep`,
   write it to `<temp_dir>/search.txt` with your file-writing tool and run
   `grep -F -f "<temp_dir>/search.txt" "<temp_dir>"/output_chunk*.md` (see
   "External Text and the Shell").
2. Either edit `output_chunk<NNNN>.md` directly with your file-editing tool,
   or have it re-translated.

   **Edit.** `record` stamps the chunk as matching the current source and
   glossary, so first check what the planner still expects from it:

   ```bash
   python3 "{baseDir}/scripts/run_state.py" plan "<temp_dir>"
   ```

   If `chunk<NNNN>` appears in `chunks[]` with action `translate`, recording
   would hide that. Handle it by reason:

   - `glossary_term_hash_changed` (its `detail` lists the term ids) or
     `glossary_term_selection_changed`: bring those terms in line with the
     term table that Step 4's `print-terms-for-chunk` command prints for the
     chunk, in the same edit.
   - `manifest_source_hash_changed`: the source chunk was edited after
     splitting. Run Step 2 again first (write the request from
     `<temp_dir>/request.json`, then run Step 2's first command), without
     `--detect-page-numbers`: once chunks exist, page-number flags make
     `convert.py` stop. Without them it keeps the existing chunks and writes
     their new hashes to `manifest.json`. Skip this and the merge stops with
     "Source changed since splitting" even after re-translating. Plan again:
     the chunk is then queued with `source_hash_changed_since_record`.
   - `source_hash_changed_since_record` or any other reason: re-translate the
     chunk instead of editing it.

   Then write the chunk id to `<temp_dir>/record_ids.txt` and record the
   edited output:

   ```bash
   python3 "{baseDir}/scripts/run_state.py" record "<temp_dir>" < "<temp_dir>/record_ids.txt"
   ```

   **Re-translate.** Write `output_chunk<NNNN>.md` empty with your file-writing
   tool, so the plan queues it with `empty_output`, and run Steps 3.7–4.5
   again. The planner re-checks the whole book, so `translation_chunk_ids` can
   also list chunks the user did not mention, typically ones whose glossary
   terms changed after they were translated (`glossary_term_selection_changed`
   or `glossary_term_hash_changed` in the plan's `chunks[].reasons`). Translate
   only the chunks the user asked for; if the plan lists others, show the user
   those chunk ids with their reasons and ask before re-translating them.
3. Run the Step 7 build command again. It re-merges because
   the chunk is newer than `output.md`, then regenerates the HTML and the other
   formats.
