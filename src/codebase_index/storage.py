"""SQLite + FTS5 storage layer — replaces JSON persistence."""

import json
import sqlite3
from pathlib import Path

from .models import Symbol, FileInfo, INDEX_DIR, DB_FILE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_files (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  language TEXT NOT NULL,
  lines INTEGER NOT NULL,
  pagerank REAL DEFAULT 0.0,
  indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_symbols (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  line INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
  raw_import TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_edges (
  source_id INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
  target_id INTEGER NOT NULL REFERENCES code_files(id) ON DELETE CASCADE,
  PRIMARY KEY (source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_symbols_file ON code_symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(name);
CREATE INDEX IF NOT EXISTS idx_imports_file ON imports(file_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON code_files(path);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
  path,
  symbol_names,
  import_names,
  content='',
  tokenize='unicode61'
);
"""

# Module-level connection cache — one per project root
_db_cache: dict[str, sqlite3.Connection] = {}


def get_db(root: str) -> sqlite3.Connection:
  """Get or create a cached database connection for a project root."""
  root = str(Path(root).resolve())
  if root in _db_cache:
    try:
      _db_cache[root].execute("SELECT 1")
      return _db_cache[root]
    except sqlite3.ProgrammingError:
      del _db_cache[root]

  db_dir = Path(root) / INDEX_DIR
  db_dir.mkdir(exist_ok=True)
  db_path = db_dir / DB_FILE

  conn = sqlite3.connect(str(db_path), timeout=10)
  conn.execute("PRAGMA journal_mode=WAL")
  conn.execute("PRAGMA foreign_keys=ON")
  conn.execute("PRAGMA synchronous=NORMAL")
  conn.executescript(_SCHEMA)
  conn.executescript(_FTS_SCHEMA)
  conn.commit()

  _db_cache[root] = conn
  return conn


def get_metadata(db: sqlite3.Connection, key: str) -> str | None:
  """Get a metadata value."""
  row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
  return row[0] if row else None


def set_metadata(db: sqlite3.Connection, key: str, value: str) -> None:
  """Set a metadata value."""
  db.execute(
    "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
    (key, value),
  )


def upsert_file(db: sqlite3.Connection, fi: FileInfo, indexed_at: str) -> int:
  """Insert or update a file and its symbols/imports. Returns file_id."""
  existing = db.execute(
    "SELECT id FROM code_files WHERE path=?", (fi.path,)
  ).fetchone()

  if existing:
    file_id = existing[0]
    db.execute(
      "UPDATE code_files SET language=?, lines=?, indexed_at=? WHERE id=?",
      (fi.language, fi.lines, indexed_at, file_id),
    )
    db.execute("DELETE FROM code_symbols WHERE file_id=?", (file_id,))
    db.execute("DELETE FROM imports WHERE file_id=?", (file_id,))
    # Remove old FTS entry
    db.execute(
      "INSERT INTO search_index(search_index, rowid, path, symbol_names, import_names) "
      "VALUES('delete', ?, ?, ?, ?)",
      (file_id, "", "", ""),
    )
  else:
    cur = db.execute(
      "INSERT INTO code_files(path, language, lines, indexed_at) VALUES(?, ?, ?, ?)",
      (fi.path, fi.language, fi.lines, indexed_at),
    )
    file_id = cur.lastrowid

  # Insert symbols
  if fi.symbols:
    db.executemany(
      "INSERT INTO code_symbols(file_id, name, kind, line) VALUES(?, ?, ?, ?)",
      [(file_id, s.name, s.kind, s.line) for s in fi.symbols],
    )

  # Insert imports
  if fi.imports:
    db.executemany(
      "INSERT INTO imports(file_id, raw_import) VALUES(?, ?)",
      [(file_id, imp) for imp in fi.imports],
    )

  # Update FTS index — tokenize path components for better matching
  path_tokens = fi.path.replace("/", " ").replace("_", " ").replace(".", " ")
  symbol_names = " ".join(s.name for s in fi.symbols)
  import_names = " ".join(fi.imports)
  db.execute(
    "INSERT INTO search_index(rowid, path, symbol_names, import_names) VALUES(?, ?, ?, ?)",
    (file_id, path_tokens, symbol_names, import_names),
  )

  return file_id


def delete_file(db: sqlite3.Connection, path: str) -> None:
  """Delete a file and cascade to symbols/imports."""
  row = db.execute("SELECT id FROM code_files WHERE path=?", (path,)).fetchone()
  if row:
    file_id = row[0]
    db.execute(
      "INSERT INTO search_index(search_index, rowid, path, symbol_names, import_names) "
      "VALUES('delete', ?, ?, ?, ?)",
      (file_id, "", "", ""),
    )
    db.execute("DELETE FROM code_files WHERE id=?", (file_id,))


def get_file(db: sqlite3.Connection, path: str) -> FileInfo | None:
  """Get a single file's info."""
  row = db.execute(
    "SELECT id, path, language, lines FROM code_files WHERE path=?", (path,)
  ).fetchone()
  if not row:
    return None
  file_id, fpath, lang, lines = row

  symbols = [
    Symbol(r[0], r[1], r[2])
    for r in db.execute(
      "SELECT name, kind, line FROM code_symbols WHERE file_id=? ORDER BY line",
      (file_id,),
    )
  ]
  imports = [
    r[0] for r in db.execute(
      "SELECT raw_import FROM imports WHERE file_id=?", (file_id,),
    )
  ]
  return FileInfo(path=fpath, language=lang, lines=lines, symbols=symbols, imports=imports)


def get_file_count(db: sqlite3.Connection) -> int:
  """Get total number of indexed files."""
  return db.execute("SELECT COUNT(*) FROM code_files").fetchone()[0]


def search_fts(db: sqlite3.Connection, query: str, limit: int = 15) -> list[dict]:
  """Search using FTS5 BM25 ranking."""
  if not query.strip():
    return []

  # Build FTS5 query using OR for recall, then boost files matching more terms
  terms = [t for t in query.strip().split() if t]
  if not terms:
    return []

  fts_query = " OR ".join(f'"{t}"' for t in terms)

  try:
    rows = db.execute(
      """
      SELECT
        f.id, f.path, f.language, f.lines, f.pagerank,
        bm25(search_index, 10.0, 8.0, 2.0) as bm25_score
      FROM search_index
      JOIN code_files f ON search_index.rowid = f.id
      WHERE search_index MATCH ?
      ORDER BY bm25_score ASC
      LIMIT ?
      """,
      (fts_query, limit * 3),
    ).fetchall()
  except sqlite3.OperationalError:
    return []

  # Boost files that match more search terms (breadth bonus)
  term_set = set(terms)
  boosted_rows = []
  for row in rows:
    file_id, path, lang, lines, pagerank, bm25 = row
    # Count how many terms appear in the path
    path_lower = path.lower().replace("/", " ").replace("_", " ").replace(".", " ")
    term_hits = sum(1 for t in term_set if t in path_lower)
    # Breadth bonus: multiply BM25 score (negative) by coverage factor
    coverage = 1.0 + (term_hits / len(term_set)) * 0.5 if term_set else 1.0
    boosted_bm25 = bm25 * coverage  # More negative = better
    boosted_rows.append((file_id, path, lang, lines, pagerank, boosted_bm25))

  boosted_rows.sort(key=lambda r: r[5])  # Sort by boosted BM25 (ascending = better)
  rows = boosted_rows

  results = []
  for row in rows:
    file_id, path, lang, lines, pagerank, bm25 = row
    symbols = [
      {"name": r[0], "kind": r[1], "line": r[2]}
      for r in db.execute(
        "SELECT name, kind, line FROM code_symbols WHERE file_id=? ORDER BY line LIMIT 10",
        (file_id,),
      )
    ]
    results.append({
      "file_id": file_id,
      "path": path,
      "language": lang,
      "lines": lines,
      "pagerank": pagerank,
      "bm25_score": bm25,
      "symbols": symbols,
    })

  return results


def update_pagerank_scores(db: sqlite3.Connection, scores: dict[int, float]) -> None:
  """Batch update PageRank scores for files."""
  db.executemany(
    "UPDATE code_files SET pagerank=? WHERE id=?",
    [(score, fid) for fid, score in scores.items()],
  )


def get_all_file_ids(db: sqlite3.Connection) -> dict[str, int]:
  """Get path→id mapping for all files."""
  return dict(db.execute("SELECT path, id FROM code_files").fetchall())


def get_all_imports(db: sqlite3.Connection) -> list[tuple[int, str]]:
  """Get all (file_id, raw_import) pairs."""
  return db.execute("SELECT file_id, raw_import FROM imports").fetchall()


def clear_import_edges(db: sqlite3.Connection) -> None:
  """Clear all import edges before rebuilding."""
  db.execute("DELETE FROM import_edges")


def insert_import_edges(db: sqlite3.Connection, edges: list[tuple[int, int]]) -> None:
  """Batch insert import edges."""
  db.executemany(
    "INSERT OR IGNORE INTO import_edges(source_id, target_id) VALUES(?, ?)",
    edges,
  )


def get_import_edge_list(db: sqlite3.Connection) -> list[tuple[int, int]]:
  """Get all import edges as (source_id, target_id) pairs."""
  return db.execute("SELECT source_id, target_id FROM import_edges").fetchall()


def get_project_stats(db: sqlite3.Connection) -> dict:
  """Get aggregated project statistics."""
  total_files = db.execute("SELECT COUNT(*) FROM code_files").fetchone()[0]
  total_lines = db.execute("SELECT COALESCE(SUM(lines), 0) FROM code_files").fetchone()[0]
  total_symbols = db.execute("SELECT COUNT(*) FROM code_symbols").fetchone()[0]

  lang_rows = db.execute(
    "SELECT language, COUNT(*) FROM code_files GROUP BY language ORDER BY COUNT(*) DESC"
  ).fetchall()
  languages = dict(lang_rows)

  dir_rows = db.execute("""
    SELECT
      CASE WHEN INSTR(path, '/') > 0
           THEN SUBSTR(path, 1, INSTR(path, '/') - 1)
           ELSE '.'
      END as dir,
      COUNT(*) as cnt,
      SUM(lines) as total_lines
    FROM code_files
    GROUP BY dir
    ORDER BY cnt DESC
  """).fetchall()

  directories = {}
  for d, cnt, tl in dir_rows:
    directories[d] = {"files": cnt, "lines": tl}

  return {
    "total_files": total_files,
    "total_lines": total_lines,
    "total_symbols": total_symbols,
    "languages": languages,
    "directories": directories,
  }


def migrate_from_json(root: str, db: sqlite3.Connection) -> bool:
  """Migrate from old index.json to SQLite. Returns True if migrated."""
  json_path = Path(root) / INDEX_DIR / "index.json"
  if not json_path.exists():
    return False

  if get_file_count(db) > 0:
    return False

  try:
    data = json.loads(json_path.read_text(encoding="utf-8"))
  except (json.JSONDecodeError, OSError):
    return False

  from datetime import datetime, timezone
  now = datetime.now(timezone.utc).isoformat()

  for path, fd in data.get("files", {}).items():
    symbols = [
      Symbol(s["name"], s["kind"], s["line"])
      for s in fd.get("symbols", [])
    ]
    fi = FileInfo(
      path=path,
      language=fd["language"],
      lines=fd["lines"],
      symbols=symbols,
      imports=fd.get("imports", []),
    )
    upsert_file(db, fi, now)

  if "git_hash" in data:
    set_metadata(db, "git_hash", data["git_hash"])
  set_metadata(db, "updated_at", now)
  db.commit()

  # Rename old file
  backup = json_path.with_suffix(".json.bak")
  json_path.rename(backup)
  return True
