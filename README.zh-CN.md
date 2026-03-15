# Rainman Translate Book

[English](README.md) | 中文

Claude Code Skill，使用并行 subagent 将整本书（PDF/DOCX/EPUB）翻译成任意语言。

> 本项目受 [claude_translater](https://github.com/wizlijun/claude_translater) 启发。原项目以 shell 脚本为入口，配合 Claude CLI 和多个步骤脚本完成分块翻译；本项目则将流程重构为 Claude Code Skill，使用 subagent 按 chunk 并行翻译，并引入 manifest 驱动的完整性校验，将续跑和多格式输出整合为更统一的流水线。由于项目结构和实现方式均与原项目不同，本项目为独立实现，而非 fork。

---

## 工作原理

```
输入文件 (PDF/DOCX/EPUB)
  │
  ▼
Calibre ebook-convert → HTMLZ → HTML → Markdown
  │
  ▼
拆分为 chunk（chunk0001.md, chunk0002.md, ...）
  │  manifest.json 记录每个 chunk 的 SHA-256 hash
  ▼
并行 subagent 翻译（默认 8 路并发）
  │  每个 subagent：读取 1 个 chunk → 翻译 → 写入 output_chunk*.md
  │  分批执行，控制 API 速率
  ▼
校验（manifest hash 比对，源文件↔输出文件 1:1 匹配）
  │
  ▼
合并 → Pandoc → HTML（含目录）→ Calibre → DOCX / EPUB / PDF
```

每个 chunk 由独立的 subagent 翻译，拥有全新的上下文窗口。这避免了单次会话翻译整本书时的上下文堆积和输出截断问题。

## 功能特性

- **并行 subagent** — 每批 8 个并发翻译器，各自独立上下文
- **可续跑** — chunk 级续跑，重新运行时自动跳过已翻译的 chunk（元数据或模板变更建议全新运行）
- **Manifest 校验** — SHA-256 hash 追踪，防止过时或损坏的输出被合并
- **多格式输出** — HTML（含浮动目录）、DOCX、EPUB、PDF
- **多语言** — zh、en、ja、ko、fr、de、es（可扩展）
- **多格式输入** — PDF/DOCX/EPUB，Calibre 负责格式转换

## 前置要求

- **Claude Code CLI** — 已安装并完成认证
- **Calibre** — `ebook-convert` 命令可用（[下载](https://calibre-ebook.com/)）
- **Pandoc** — 用于 HTML↔Markdown 转换（[下载](https://pandoc.org/)）
- **Python 3**，需要：
  - `pypandoc` — 必需（`pip install pypandoc`）
  - `beautifulsoup4` — 可选，用于更好的目录生成（`pip install beautifulsoup4`）

## 快速开始

### 1. 安装 Skill

**方式 A：npx（推荐）**

```bash
npx skills add deusyu/translate-book -a claude-code -g
```

**方式 B：ClawHub**

```bash
clawhub install translate-book
```

**方式 C：Git 克隆**

```bash
git clone https://github.com/deusyu/translate-book.git ~/.claude/skills/translate-book
```


### 2. 翻译一本书

在 Claude Code 中直接说：

```
translate /path/to/book.pdf to Chinese
```

或使用斜杠命令：

```
/translate-book translate /path/to/book.pdf to Japanese
```

Skill 自动处理完整流程 — 转换、拆分、并行翻译、校验、合并、生成所有输出格式。

### 3. 查看输出

所有文件在 `{book_name}_temp/` 目录下：

| 文件 | 说明 |
|------|------|
| `output.md` | 合并后的翻译 Markdown |
| `book.html` | 网页版，含浮动目录 |
| `book.docx` | Word 文档 |
| `book.epub` | 电子书 |
| `book.pdf` | 可打印 PDF |

## 流程详解

### 第一步：转换

```bash
python3 scripts/convert.py /path/to/book.pdf --olang zh
```

Calibre 将输入文件转为 HTMLZ，解压后转为 Markdown，再拆分为 chunk（每个约 6000 字符）。`manifest.json` 记录每个源 chunk 的 SHA-256 hash，用于后续校验。

### 第二步：翻译（并行 subagent）

Skill 分批启动 subagent（默认 8 路并发）。每个 subagent：

1. 读取一个源 chunk（如 `chunk0042.md`）
2. 翻译为目标语言
3. 将结果写入 `output_chunk0042.md`

如果运行中断，重新运行会跳过已有合法输出的 chunk。翻译失败的 chunk 会自动重试一次。

### 第三步：合并与构建

```bash
python3 scripts/merge_and_build.py --temp-dir book_temp --title "《译后书名》"
```

合并前校验：
- 每个源 chunk 都有对应的输出文件（1:1 匹配）
- 源 chunk hash 与 manifest 一致（无过时输出）
- 输出文件不为空

校验通过后：合并 → Pandoc 生成 HTML → 注入目录 → Calibre 生成 DOCX、EPUB、PDF。

**注意：** `{book_name}_temp/` 是单次翻译运行的工作目录。如果修改了标题、作者、输出语言、模板或图片资源，建议使用新的 temp 目录，或先删除已有的最终产物（`output.md`、`book*.html`、`book.docx`、`book.epub`、`book.pdf`）再重跑。

## 项目结构

| 文件 | 用途 |
|------|------|
| `SKILL.md` | Claude Code Skill 定义 — 编排完整流程 |
| `scripts/convert.py` | PDF/DOCX/EPUB → Markdown chunks（经 Calibre HTMLZ） |
| `scripts/manifest.py` | Chunk manifest：SHA-256 追踪与合并校验 |
| `scripts/merge_and_build.py` | 合并 chunks → HTML → DOCX/EPUB/PDF |
| `scripts/calibre_html_publish.py` | Calibre 格式转换封装 |
| `scripts/template.html` | 网页 HTML 模板，含浮动目录 |
| `scripts/template_ebook.html` | 电子书 HTML 模板 |

## 常见问题

| 问题 | 解决方案 |
|------|----------|
| `Calibre ebook-convert not found` | 安装 Calibre，确保 `ebook-convert` 在 PATH 中 |
| `Manifest validation failed` | 源 chunk 在拆分后被修改 — 重新运行 `convert.py` |
| `Missing source chunk` | 源文件被删除 — 重新运行 `convert.py` 重新生成 |
| 翻译不完整 | 重新运行 Skill，会从中断处继续 |
| 修改标题、模板或图片后输出未更新 | 删除 temp 目录中的 `output.md`、`book*.html`、`book.docx`、`book.epub`、`book.pdf`，然后重跑 `merge_and_build.py` |
| `output.md exists but manifest invalid` | 旧输出已过时 — 脚本会自动删除并重新合并 |
| PDF 生成失败 | 确认 Calibre 已安装且支持 PDF 输出 |

## License

[MIT](LICENSE)
