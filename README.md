# codebase-index-mcp

Claude Code (or any MCP client) that **skips codebase exploration entirely**.

Instead of Glob/Grep/Read 10-20 times to figure out where things are, ask once — get the right files instantly.

## The Problem

Every time Claude starts a new session, it re-explores the codebase from scratch:

```
Session start → Glob → Grep → Read → Glob → Read → Read → ...finally starts working
                └── 8-15 tool calls, thousands of tokens, 30-60 seconds ──┘
```

**This is the single biggest waste of tokens in AI-assisted coding.**

The codebase structure barely changes between commits, yet Claude re-discovers it every time.

## The Solution

```
Session start → find_files_for_task("add retry to review scraper") → starts working
                └── 1 call, ~500 tokens, <2ms ──┘
```

codebase-index-mcp pre-indexes your codebase using tree-sitter AST parsing, stores it in SQLite, and ranks files using a combination of full-text search, path matching, and PageRank on the import graph.

## How It Works

```
git commit
    ↓
tree-sitter parses all files → extracts functions, classes, methods, imports
    ↓
SQLite + FTS5 stores the index (incremental — only changed files re-parsed)
    ↓
PageRank computed on the import graph (which files are most central?)
    ↓
Claude asks "find files for task X"
    ↓
Two-stage ranking:
  1. FTS5 BM25 + path LIKE → broad candidate retrieval
  2. Multi-signal scorer → re-ranks by path coverage, symbol match, PageRank
    ↓
Top files returned with symbols and scores
```

## Tools

### `find_files_for_task`

The primary tool. Natural language in, ranked files out.

```
Input:  "add retry logic to the queenit scraper"
Output: [
  { path: "src/scrapers/queenit/review_scraper.py", score: 41.2 },
  { path: "src/use_cases/queenit/batch_crawl.py",   score: 34.2 },
  ...
]
```

Supports Korean queries with automatic translation:

```
Input:  "네이버 카탈로그 크롤링 에러"
Output: [
  { path: "src/use_cases/batch_crawl_naver_catalog_review.py", score: 40.7 },
  { path: "src/scrapers/naver/catalog_scraper.py",             score: 35.4 },
  ...
]
```

### `get_project_overview`

Instant project structure map — file counts, languages, directory breakdown, total symbols. No file reading required.

### `get_file_context`

Get a file's symbols (functions, classes, methods) and imports without reading the full content. Useful when you need to know *what's in* a file, not its full source.

### `get_recent_changes`

Recent git history — commits, affected files, authors. Customizable time range.

### `rebuild_index`

Force a full re-index if something seems stale.

## Install

### Prerequisites

- Python 3.11+
- git

### Setup

```bash
git clone https://github.com/M00N7682/codebase-index-mcp.git
cd codebase-index-mcp
uv venv && uv pip install -e .
```

### Connect to Claude Code

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "codebase-index": {
      "type": "stdio",
      "command": "/path/to/codebase-index-mcp/.venv/bin/python",
      "args": ["-m", "codebase_index"]
    }
  }
}
```

Enable in `~/.claude/settings.json`:

```json
{
  "enabledMcpjsonServers": ["codebase-index"]
}
```

Restart Claude Code. The `find_files_for_task` tool is now available.

## Supported Languages

Tree-sitter AST parsing (accurate symbol extraction):

Python, TypeScript, JavaScript, Go, Rust, Java, Kotlin, Ruby, C, C++, C#, Swift, PHP

Regex fallback for everything else.

## Architecture

```
src/codebase_index/
├── server.py              # MCP interface (5 tools)
├── models.py              # Symbol, FileInfo, language detection
├── git_ops.py             # Safe git operations with error handling
├── treesitter_parser.py   # AST-based symbol extraction (primary)
├── regex_parser.py        # Regex-based extraction (fallback)
├── parser.py              # Dispatcher: tree-sitter → regex
├── storage.py             # SQLite + FTS5 persistence
├── indexer.py             # Build / update / ensure index + PageRank
└── ranking.py             # Two-stage retrieval + multi-signal scoring
```

### Ranking Signals (in order of weight)

1. **Path stem match** — keyword matches the filename (+15)
2. **Directory match** — keyword matches a directory name (+8)
3. **Path substring** — keyword appears anywhere in path (+5)
4. **Symbol exact match** — keyword matches a function/class name (+6)
5. **Symbol substring** — keyword appears within a symbol name (+3)
6. **Coverage bonus** — files matching more distinct keywords ranked higher
7. **PageRank** — files imported by many others get a boost (capped)
8. **Penalties** — test files, legacy code, oversized files ranked lower

## Performance

Tested on a 569-file Python project (206k lines):

| Metric | Value |
|--------|-------|
| Full index build | 1.1s |
| Incremental update | <0.1s |
| Search latency | 1-2ms |
| DB size | ~2MB |
| Dependencies | ~5MB (tree-sitter only) |

## Tests

```bash
.venv/bin/python tests/test_all.py
```

18 tests covering models, git ops, parsers, storage, ranking, indexing, and real-repo integration.

## License

MIT
