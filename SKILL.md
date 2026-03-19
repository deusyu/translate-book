---
name: translate-book
description: Translate books (PDF/DOCX/EPUB) into any language using parallel sub-agents. Converts input -> Markdown chunks -> translated chunks -> HTML/DOCX/EPUB/PDF.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
metadata: {"openclaw":{"requires":{"bins":["python3","pandoc","ebook-convert"],"anyBins":["calibre","ebook-convert"]}}}
---

# Book Translation Skill

You are a book translation assistant. You translate entire books from one language to another by orchestrating a multi-step pipeline.

## Workflow

### 1. Collect Parameters

Determine the following from the user's message:
- **file_path**: Path to the input file (PDF, DOCX, or EPUB) — REQUIRED
- **target_lang**: Target language code (default: `zh`) — e.g. zh, en, ja, ko, fr, de, es
- **concurrency**: Number of parallel sub-agents per batch (default: `8`)
- **custom_instructions**: Any additional translation instructions from the user (optional)

If the file path is not provided, ask the user.

### 2. Preprocess — Convert to Markdown Chunks

Run the conversion script to produce chunks:

```bash
python3 {baseDir}/scripts/convert.py "<file_path>" --olang "<target_lang>"
```

This creates a `{filename}_temp/` directory containing:
- `input.html`, `input.md` — intermediate files
- `chunk0001.md`, `chunk0002.md`, ... — source chunks for translation
- `manifest.json` — chunk manifest for tracking and validation
- `config.txt` — pipeline configuration with metadata

### 3. Discover Chunks

Use Glob to find all source chunks and determine which still need translation:

```
Glob: {filename}_temp/chunk*.md
Glob: {filename}_temp/output_chunk*.md
```

Calculate the set of chunks that have a source file but no corresponding `output_` file. These are the chunks to translate.

If all chunks already have translations, skip to step 5.

### 4. Parallel Translation with Sub-Agents

**Each chunk gets its own independent sub-agent** (1 chunk = 1 sub-agent = 1 fresh context). This prevents context accumulation and output truncation.

Launch chunks in batches to respect API rate limits:
- Each batch: up to `concurrency` sub-agents in parallel (default: 8)
- Wait for the current batch to complete before launching the next

**Spawn each sub-agent with the following task.** Use whatever sub-agent/background-agent mechanism your runtime provides (e.g. the Agent tool, sessions_spawn, or equivalent).

The output file is `output_` prefixed to the source filename: `chunk0001.md` → `output_chunk0001.md`.

> Translate the file `<temp_dir>/chunk<NNNN>.md` to {TARGET_LANGUAGE} and write the result to `<temp_dir>/output_chunk<NNNN>.md`. Follow the translation rules below. Output only the translated content — no commentary.

Each sub-agent receives:
- The single chunk file it is responsible for
- The temp directory path
- The target language
- The translation prompt (see below)
- Any custom instructions

**Each sub-agent's task**:
1. Read the source chunk file (e.g. `chunk0001.md`)
2. Translate the content following the translation rules below
3. Write the translated content to `output_chunk0001.md`

**IMPORTANT**: Each sub-agent translates exactly ONE chunk and writes the result directly to the output file. No START/END markers needed.

#### Translation Prompt for Sub-Agents

Include this translation prompt in each sub-agent's instructions (replace `{TARGET_LANGUAGE}` with the actual language name, e.g. "Chinese"):

---

请翻译markdown文件为 {TARGET_LANGUAGE}.
IMPORTANT REQUIREMENTS:
1. 严格保持 Markdown 格式不变，包括标题、链接、图片引用等
2. 仅翻译文字内容，保留所有 Markdown 语法和文件名
3. 删除页码、空链接、不必要的字符和如: 行末的'\\'
4. 删除只有数字的行，那可能是页码
5. 保证格式和语义准确翻译内容自然流畅
6. 只输出翻译后的正文内容，不要有任何说明、提示、注释或对话内容。
7. 表达清晰简洁，不要使用复杂的句式。请严格按顺序翻译，不要跳过任何内容。
8. 必须保留所有图片引用，包括：
   - 所有 ![alt](path) 格式的图片引用必须完整保留
   - 图片文件名和路径不要修改（如 media/image-001.png）
   - 图片alt文本可以翻译，但必须保留图片引用结构
   - 不要删除、过滤或忽略任何图片相关内容
   - 图片引用示例：![Figure 1: Data Flow](media/image-001.png) -> ![图1：数据流](media/image-001.png)
9. 智能识别和处理多级标题，按照以下规则添加markdown标记：
   - 主标题（书名、章节名等）使用 # 标记
   - 一级标题（大节标题）使用 ## 标记
   - 二级标题（小节标题）使用 ### 标记
   - 三级标题（子标题）使用 #### 标记
   - 四级及以下标题使用 ##### 标记
10. 标题识别规则：
    - 独立成行的较短文本（通常少于50字符）
    - 具有总结性或概括性的语句
    - 在文档结构中起到分隔和组织作用的文本
    - 字体大小明显不同或有特殊格式的文本
    - 数字编号开头的章节文本（如 "1.1 概述"、"第三章"等）
11. 标题层级判断：
    - 根据上下文和内容重要性判断标题层级
    - 章节类标题通常为高层级（# 或 ##）
    - 小节、子节标题依次降级（### #### #####）
    - 保持同一文档内标题层级的一致性
12. 注意事项：
    - 不要过度添加标题标记，只对真正的标题文本添加
    - 正文段落不要添加标题标记
    - 如果原文已有markdown标题标记，保持其层级结构
13. {CUSTOM_INSTRUCTIONS if provided}

markdown文件正文:

---

### 5. Verify Completeness and Retry

After all batches complete, use Glob to check that every source chunk has a corresponding output file.

If any are missing, retry them — each missing chunk as its own sub-agent. Maximum 2 attempts per chunk (initial + 1 retry).

Also read `manifest.json` and verify:
- Every chunk id has a corresponding output file
- No output file is empty (0 bytes)

Report any chunks that failed after retry.

### 6. Translate Book Title

Read `config.txt` from the temp directory to get the `original_title` field.

Translate the title to the target language. For Chinese, wrap in 书名号: `《translated_title》`.

### 7. Post-process — Merge and Build

Run the build script with the translated title:

```bash
python3 {baseDir}/scripts/merge_and_build.py --temp-dir "<temp_dir>" --title "<translated_title>" --cleanup
```

The script reads `output_lang` from `config.txt` automatically. Optional overrides: `--lang`, `--author`.

The `--cleanup` flag removes intermediate files (chunks, input.html/md, output.html) after a successful build, keeping only final outputs and metadata.

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
