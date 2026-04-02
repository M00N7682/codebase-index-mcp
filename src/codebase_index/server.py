"""MCP server — the single interface Claude uses to skip exploration."""

import json
import os

from mcp.server.fastmcp import FastMCP

from .models import NotAGitRepoError
from .indexer import ensure_index, build_index
from .git_ops import get_recent_changes
from .storage import get_db, get_file, get_file_count, get_metadata
from .ranking import find_relevant_files, get_project_summary

mcp = FastMCP(
  "codebase-index",
  instructions=(
    "Codebase Index MCP eliminates exploration overhead. "
    "Call find_files_for_task FIRST before using Glob/Grep/Read. "
    "Call get_project_overview to understand the project structure instantly."
  ),
)


def _resolve_root(project_path: str | None) -> str:
  """Resolve project root path."""
  if project_path:
    return os.path.abspath(os.path.expanduser(project_path))
  return os.getcwd()


def _error_json(msg: str) -> str:
  return json.dumps({"error": msg})


@mcp.tool()
def find_files_for_task(task: str, project_path: str = "") -> str:
  """
  The primary tool. Given a natural language task description,
  returns the most relevant files to read — ranked by BM25 + PageRank.

  Use this BEFORE exploring the codebase with Glob/Grep/Read.
  This single call replaces 10-20 exploration tool calls.

  Args:
    task: Natural language description of what you're trying to do.
          Examples: "add retry logic to queenit scraper",
                    "fix the MongoDB connection timeout",
                    "refactor the catalog pipeline to support batch processing"
    project_path: Absolute path to the project root. Defaults to cwd.
  """
  root = _resolve_root(project_path or None)
  try:
    ensure_index(root)
  except NotAGitRepoError:
    return _error_json(f"Not a git repository: {root}")

  db = get_db(root)
  results = find_relevant_files(db, task)

  if not results:
    return json.dumps({
      "message": "No relevant files found. Try different keywords.",
      "indexed_files": get_file_count(db),
    })

  return json.dumps({
    "task": task,
    "relevant_files": results,
    "total_indexed": get_file_count(db),
    "tip": "Read the top-scored files first. They are most likely to contain what you need.",
  }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_project_overview(project_path: str = "") -> str:
  """
  Get a high-level map of the entire project structure.
  Returns file counts, languages, directory breakdown, and total symbols.

  Use this when starting work on an unfamiliar project to understand
  its structure without reading any files.

  Args:
    project_path: Absolute path to the project root. Defaults to cwd.
  """
  root = _resolve_root(project_path or None)
  try:
    ensure_index(root)
  except NotAGitRepoError:
    return _error_json(f"Not a git repository: {root}")

  db = get_db(root)
  summary = get_project_summary(db)
  return json.dumps(summary, ensure_ascii=False, indent=2)


@mcp.tool()
def get_file_context(file_path: str, project_path: str = "") -> str:
  """
  Get a detailed summary of a specific file WITHOUT reading its full content.
  Returns: language, line count, all symbols (functions/classes/interfaces),
  and import list.

  Use this instead of Read when you just need to know what's IN a file,
  not its full content.

  Args:
    file_path: Relative path to the file within the project.
    project_path: Absolute path to the project root. Defaults to cwd.
  """
  root = _resolve_root(project_path or None)
  try:
    ensure_index(root)
  except NotAGitRepoError:
    return _error_json(f"Not a git repository: {root}")

  db = get_db(root)
  fi = get_file(db, file_path)
  if fi is None:
    return _error_json(f"File '{file_path}' not found in index.")

  return json.dumps({
    "path": fi.path,
    "language": fi.language,
    "lines": fi.lines,
    "symbols": [
      {"name": s.name, "kind": s.kind, "line": s.line}
      for s in fi.symbols
    ],
    "imports": fi.imports,
  }, ensure_ascii=False, indent=2)


@mcp.tool()
def get_recent_changes(project_path: str = "", since: str = "7 days ago") -> str:
  """
  Get recent git changes — commits, affected files, and authors.
  Use this to understand what has been changing in the project recently.

  Args:
    project_path: Absolute path to the project root. Defaults to cwd.
    since: Git time expression. Examples: "3 days ago", "1 week ago", "2025-01-01"
  """
  root = _resolve_root(project_path or None)
  changes = get_recent_changes(root, since)

  if not changes:
    return json.dumps({"message": f"No changes found since {since}."})

  return json.dumps({
    "since": since,
    "commits": changes,
    "total_commits": len(changes),
  }, ensure_ascii=False, indent=2)


@mcp.tool()
def rebuild_index(project_path: str = "") -> str:
  """
  Force a full index rebuild. Use this if the index seems stale or corrupted.

  Args:
    project_path: Absolute path to the project root. Defaults to cwd.
  """
  root = _resolve_root(project_path or None)
  try:
    count = build_index(root)
  except NotAGitRepoError:
    return _error_json(f"Not a git repository: {root}")

  db = get_db(root)
  return json.dumps({
    "message": "Index rebuilt successfully.",
    "files_indexed": count,
    "git_hash": get_metadata(db, "git_hash") or "",
  })


def main():
  mcp.run()


if __name__ == "__main__":
  main()
