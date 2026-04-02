"""Generate a compact project map for context injection via hooks."""

import sqlite3
from pathlib import Path
from collections import Counter

from .storage import get_db, get_metadata, get_file_count
from .indexer import ensure_index
from .models import INDEX_DIR, DB_FILE
from .git_ops import is_git_repo


def _find_git_root(start: str) -> str | None:
  """Walk up from start directory to find the git root."""
  current = Path(start).resolve()
  while current != current.parent:
    if (current / ".git").exists():
      return str(current)
    current = current.parent
  return None


def generate_compact_map(root: str, max_core_files: int = 15) -> str:
  """
  Generate a compact project map (~300-500 tokens) from the index.
  Designed to be injected into Claude's context via hooks.
  """
  db = get_db(root)

  # Basic stats
  total_files = get_file_count(db)
  if total_files == 0:
    return ""

  total_lines = db.execute(
    "SELECT COALESCE(SUM(lines), 0) FROM code_files"
  ).fetchone()[0]
  total_symbols = db.execute("SELECT COUNT(*) FROM code_symbols").fetchone()[0]

  # Language breakdown
  lang_rows = db.execute(
    "SELECT language, COUNT(*) FROM code_files GROUP BY language ORDER BY COUNT(*) DESC LIMIT 5"
  ).fetchall()
  lang_str = ", ".join(f"{lang} ({cnt})" for lang, cnt in lang_rows)

  # Directory structure with file counts (depth 2)
  dir_rows = db.execute("""
    SELECT
      CASE
        WHEN INSTR(path, '/') > 0 AND INSTR(SUBSTR(path, INSTR(path, '/') + 1), '/') > 0
        THEN SUBSTR(path, 1,
          INSTR(path, '/') + INSTR(SUBSTR(path, INSTR(path, '/') + 1), '/') - 1)
        WHEN INSTR(path, '/') > 0
        THEN SUBSTR(path, 1, INSTR(path, '/') - 1)
        ELSE '.'
      END as dir,
      COUNT(*) as cnt
    FROM code_files
    GROUP BY dir
    HAVING cnt >= 3
    ORDER BY cnt DESC
    LIMIT 20
  """).fetchall()

  # Core files by PageRank
  core_rows = db.execute("""
    SELECT f.path, f.lines, f.pagerank,
      GROUP_CONCAT(s.kind || ' ' || s.name, ', ') as symbols
    FROM code_files f
    LEFT JOIN (
      SELECT file_id, name, kind,
        ROW_NUMBER() OVER (PARTITION BY file_id ORDER BY line) as rn
      FROM code_symbols
    ) s ON s.file_id = f.id AND s.rn <= 5
    GROUP BY f.id
    ORDER BY f.pagerank DESC
    LIMIT ?
  """, (max_core_files,)).fetchall()

  # Build the map
  lines = []
  lines.append(f"## Project Map (auto-generated)")
  lines.append(f"{total_files} files | {total_lines:,} lines | {total_symbols:,} symbols")
  lines.append(f"Languages: {lang_str}")
  lines.append("")

  lines.append("### Structure")
  for dir_path, cnt in dir_rows:
    lines.append(f"  {dir_path}/ ({cnt} files)")
  lines.append("")

  lines.append("### Core Files (most referenced)")
  for path, file_lines, pr, symbols in core_rows:
    sym_str = symbols if symbols else ""
    # Truncate symbol list if too long
    if len(sym_str) > 80:
      sym_str = sym_str[:77] + "..."
    lines.append(f"  {path} ({file_lines}L) — {sym_str}")

  git_hash = get_metadata(db, "git_hash") or ""
  if git_hash:
    lines.append("")
    lines.append(f"Index: {git_hash[:8]}")

  return "\n".join(lines)


def ensure_and_generate(cwd: str | None = None) -> str:
  """Ensure index exists and generate compact map. Returns empty string on failure."""
  import os
  cwd = cwd or os.getcwd()

  root = _find_git_root(cwd)
  if not root:
    return ""

  try:
    ensure_index(root)
  except Exception:
    return ""

  try:
    return generate_compact_map(root)
  except Exception:
    return ""
