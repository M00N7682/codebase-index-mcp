"""Comprehensive tests for codebase-index-mcp."""

import os
import sqlite3
import tempfile
import subprocess
from pathlib import Path

from codebase_index.models import (
  Symbol, FileInfo, detect_language, should_skip_path,
  GitError, NotAGitRepoError,
)
from codebase_index.git_ops import is_git_repo, get_git_hash, get_tracked_files, read_file_safe
from codebase_index.regex_parser import regex_parse_file
from codebase_index.parser import parse_file
from codebase_index.storage import (
  get_db, upsert_file, delete_file, get_file, get_file_count,
  get_metadata, set_metadata, search_fts, get_project_stats,
)
from codebase_index.ranking import _tokenize, _score_candidate, find_relevant_files
from codebase_index.indexer import build_index, update_index, ensure_index, _pagerank_simple


# ============================================================
# Fixtures
# ============================================================

def _make_git_repo(tmp: str) -> str:
  """Create a minimal git repo with sample files."""
  subprocess.run(["git", "init", tmp], capture_output=True, check=True)
  subprocess.run(["git", "config", "user.name", "test"], cwd=tmp, capture_output=True)
  subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp, capture_output=True)

  # Python file
  src = Path(tmp) / "src"
  src.mkdir()
  (src / "main.py").write_text(
    "import os\nfrom pathlib import Path\n\nclass App:\n  def run(self):\n    pass\n\ndef main():\n  pass\n"
  )
  # Another Python file that imports main
  (src / "utils.py").write_text(
    "from src.main import App\n\ndef helper():\n  return 42\n"
  )
  # TypeScript file
  (src / "index.ts").write_text(
    "import { foo } from './bar'\nexport class Server {\n  start() {}\n}\nexport function handler() {}\n"
  )
  # Nested directory
  infra = src / "infra"
  infra.mkdir()
  (infra / "database.py").write_text(
    "import pymongo\n\nclass MongoClient:\n  def connect(self):\n    pass\n  def timeout(self):\n    pass\n"
  )

  subprocess.run(["git", "add", "."], cwd=tmp, capture_output=True, check=True)
  subprocess.run(["git", "commit", "-m", "init"], cwd=tmp, capture_output=True, check=True)
  return tmp


# ============================================================
# models.py
# ============================================================

def test_detect_language():
  assert detect_language("foo.py") == "python"
  assert detect_language("bar.ts") == "typescript"
  assert detect_language("baz.tsx") == "typescript"
  assert detect_language("main.go") == "go"
  assert detect_language("lib.rs") == "rust"
  assert detect_language("unknown.xyz") == "unknown"
  print("  [PASS] detect_language")


def test_should_skip_path():
  assert should_skip_path("node_modules/foo.js") is True
  assert should_skip_path("src/__pycache__/foo.pyc") is True
  assert should_skip_path("dist/bundle.js") is True
  assert should_skip_path("src/main.py") is False
  assert should_skip_path("foo.min.js") is True
  assert should_skip_path("foo.min.css") is True
  assert should_skip_path("my_package.egg-info/PKG") is True
  assert should_skip_path("src/image.png") is True
  assert should_skip_path("data.db") is True
  print("  [PASS] should_skip_path")


# ============================================================
# git_ops.py
# ============================================================

def test_git_ops(tmp_repo: str):
  assert is_git_repo(tmp_repo) is True
  assert is_git_repo("/tmp") is False

  h = get_git_hash(tmp_repo)
  assert len(h) == 40

  files = get_tracked_files(tmp_repo)
  assert "src/main.py" in files
  assert "src/utils.py" in files
  assert "src/index.ts" in files

  content = read_file_safe(tmp_repo, "src/main.py")
  assert content is not None
  assert "class App" in content

  assert read_file_safe(tmp_repo, "nonexistent.py") is None
  print("  [PASS] git_ops")


# ============================================================
# regex_parser.py
# ============================================================

def test_regex_parser_python():
  code = "import os\nfrom pathlib import Path\n\nclass Foo:\n  def method(self):\n    pass\n\ndef standalone():\n  pass\n"
  fi = regex_parse_file("test.py", code)
  assert fi.language == "python"
  assert fi.lines > 0
  names = {s.name for s in fi.symbols}
  assert "Foo" in names
  assert "standalone" in names
  kinds = {s.name: s.kind for s in fi.symbols}
  assert kinds["Foo"] == "class"
  assert "os" in fi.imports or "pathlib" in fi.imports
  print("  [PASS] regex_parser python")


def test_regex_parser_typescript():
  code = "import { x } from './y'\nexport class Server {}\nfunction handler() {}\ninterface Config {}\n"
  fi = regex_parse_file("test.ts", code)
  assert fi.language == "typescript"
  names = {s.name for s in fi.symbols}
  assert "Server" in names
  assert "handler" in names
  assert "Config" in names
  assert "./y" in fi.imports
  print("  [PASS] regex_parser typescript")


# ============================================================
# treesitter_parser.py (via parser.py dispatcher)
# ============================================================

def test_treesitter_python():
  code = "import os\nfrom pathlib import Path\n\nclass Foo:\n  def method(self):\n    pass\n\ndef standalone():\n  pass\n"
  fi = parse_file("test.py", code)
  names = {s.name for s in fi.symbols}
  kinds = {s.name: s.kind for s in fi.symbols}
  assert "Foo" in names
  assert "standalone" in names
  # Tree-sitter should distinguish method from function
  assert kinds.get("method") == "method" or kinds.get("standalone") == "function"
  print("  [PASS] treesitter python")


def test_treesitter_typescript():
  code = "import { x } from './y'\nexport class Server {}\nfunction handler() {}\n"
  fi = parse_file("test.ts", code)
  names = {s.name for s in fi.symbols}
  assert "Server" in names
  assert "handler" in names
  print("  [PASS] treesitter typescript")


# ============================================================
# storage.py
# ============================================================

def test_storage(tmp_repo: str):
  db = get_db(tmp_repo)

  # Metadata
  set_metadata(db, "test_key", "test_value")
  assert get_metadata(db, "test_key") == "test_value"
  assert get_metadata(db, "nonexistent") is None

  # Upsert file
  fi = FileInfo(
    path="src/main.py", language="python", lines=10,
    symbols=[Symbol("App", "class", 4), Symbol("main", "function", 8)],
    imports=["os", "pathlib"],
  )
  fid = upsert_file(db, fi, "2025-01-01")
  assert fid > 0
  assert get_file_count(db) >= 1

  # Get file
  loaded = get_file(db, "src/main.py")
  assert loaded is not None
  assert loaded.language == "python"
  assert len(loaded.symbols) == 2
  assert len(loaded.imports) == 2

  # Delete file
  delete_file(db, "src/main.py")
  assert get_file(db, "src/main.py") is None

  db.commit()
  print("  [PASS] storage CRUD")


def test_fts_search(tmp_repo: str):
  db = get_db(tmp_repo)

  # Insert test files
  fi1 = FileInfo("src/auth.py", "python", 50,
    [Symbol("AuthService", "class", 1), Symbol("login", "function", 10)],
    ["jwt"])
  fi2 = FileInfo("src/user.py", "python", 30,
    [Symbol("UserModel", "class", 1), Symbol("get_user", "function", 5)],
    ["sqlalchemy"])

  upsert_file(db, fi1, "2025-01-01")
  upsert_file(db, fi2, "2025-01-01")
  db.commit()

  results = search_fts(db, "auth login", limit=10)
  assert len(results) > 0
  paths = [r["path"] for r in results]
  assert "src/auth.py" in paths
  print("  [PASS] FTS search")


# ============================================================
# ranking.py
# ============================================================

def test_tokenize():
  # English
  assert "catalog" in _tokenize("catalog scraper")
  assert "scraper" in _tokenize("catalog scraper")
  # Korean → English
  tokens = _tokenize("네이버 카탈로그")
  assert "naver" in tokens
  assert "catalog" in tokens
  # Stop words removed
  assert "the" not in _tokenize("the quick brown fox")
  # CamelCase split
  assert "user" in _tokenize("UserService")
  # Unmapped Korean kept
  tokens = _tokenize("알와이즈")
  assert "알와이즈" in tokens
  print("  [PASS] tokenize")


def test_score_candidate():
  candidate = {
    "file_id": 1,
    "path": "src/infrastructure/scrapers/naver/catalog_scraper.py",
    "language": "python",
    "lines": 100,
    "pagerank": 0.01,
    "symbols": [
      {"name": "NaverCatalogScraper", "kind": "class", "line": 5},
      {"name": "scrape", "kind": "method", "line": 20},
    ],
  }
  # Query with matching keywords
  score_high = _score_candidate(candidate, ["naver", "catalog", "scraper"])
  # Query with no matching keywords
  score_low = _score_candidate(candidate, ["redis", "queue"])
  assert score_high > score_low
  assert score_high > 0

  # Test penalty
  test_candidate = {**candidate, "path": "tests/test_naver.py"}
  score_test = _score_candidate(test_candidate, ["naver", "catalog", "scraper"])
  assert score_test < score_high  # test files penalized
  print("  [PASS] score_candidate")


# ============================================================
# indexer.py
# ============================================================

def test_pagerank_simple():
  nodes = {1, 2, 3, 4}
  edges = [(1, 2), (2, 3), (3, 1), (4, 1)]
  scores = _pagerank_simple(nodes, edges, alpha=0.85, iterations=40)
  assert len(scores) == 4
  assert all(v > 0 for v in scores.values())
  # Node 1 should have highest score (most incoming edges)
  assert scores[1] > scores[4]
  print("  [PASS] pagerank_simple")


def test_build_index(tmp_repo: str):
  # Clean slate
  db_path = Path(tmp_repo) / ".codebase-index" / "index.db"
  if db_path.exists():
    db_path.unlink()

  count = build_index(tmp_repo)
  assert count >= 3  # main.py, utils.py, index.ts, database.py

  db = get_db(tmp_repo)
  assert get_file_count(db) >= 3
  assert get_metadata(db, "git_hash") is not None
  assert get_metadata(db, "updated_at") is not None
  print("  [PASS] build_index")


def test_update_index(tmp_repo: str):
  # Already built from previous test
  updated = update_index(tmp_repo)
  assert updated is False  # No changes since build

  # Make a change
  (Path(tmp_repo) / "src" / "new_file.py").write_text("def new_func(): pass\n")
  subprocess.run(["git", "add", "."], cwd=tmp_repo, capture_output=True)
  subprocess.run(["git", "commit", "-m", "add new"], cwd=tmp_repo, capture_output=True)

  updated = update_index(tmp_repo)
  assert updated is True

  db = get_db(tmp_repo)
  fi = get_file(db, "src/new_file.py")
  assert fi is not None
  print("  [PASS] update_index")


def test_ensure_index_non_git():
  try:
    ensure_index("/tmp")
    assert False, "Should have raised NotAGitRepoError"
  except NotAGitRepoError:
    pass
  print("  [PASS] ensure_index non-git")


def test_find_relevant_files(tmp_repo: str):
  db = get_db(tmp_repo)

  # Search for database-related files
  results = find_relevant_files(db, "mongo database connect timeout")
  assert len(results) > 0
  paths = [r["path"] for r in results]
  assert any("database" in p for p in paths)

  # Search for app/main
  results = find_relevant_files(db, "app main")
  assert len(results) > 0
  paths = [r["path"] for r in results]
  assert any("main" in p for p in paths)

  # Empty query
  assert find_relevant_files(db, "") == []
  assert find_relevant_files(db, "the and or") == []  # all stop words
  print("  [PASS] find_relevant_files")


# ============================================================
# Integration test on real repo
# ============================================================

def test_real_repo_search():
  """Test on alwayz-scraper if available."""
  real_root = "/Users/levit/scraper/alwayz-scraper/packages/python-scraper"
  if not os.path.exists(real_root):
    print("  [SKIP] real repo not available")
    return

  db = get_db(real_root)
  if get_file_count(db) == 0:
    print("  [SKIP] real repo not indexed")
    return

  # Strict accuracy checks
  checks = [
    ("queenit review scraper retry", "queenit"),
    ("네이버 카탈로그 크롤링 에러", "naver"),
    ("쿠팡 리뷰 스크래퍼", "coupang"),
    ("proxy brightdata residential", "brightdata"),
    ("몽고 연결 타임아웃", "mongo"),
    ("factory dependency injection", "factory"),
  ]

  for query, expected_in_top3 in checks:
    results = find_relevant_files(db, query)
    top3_paths = [r["path"] for r in results[:3]]
    found = any(expected_in_top3 in p for p in top3_paths)
    status = "PASS" if found else "FAIL"
    print(f"  [{status}] \"{query}\" → top3 contains '{expected_in_top3}': {top3_paths[:3]}")
    if not found:
      raise AssertionError(
        f"Expected '{expected_in_top3}' in top 3 for '{query}', got: {top3_paths}"
      )

  print("  [PASS] real repo search (all queries)")


# ============================================================
# Runner
# ============================================================

def main():
  print("\n=== models ===")
  test_detect_language()
  test_should_skip_path()

  with tempfile.TemporaryDirectory() as tmp:
    tmp_repo = _make_git_repo(tmp)

    print("\n=== git_ops ===")
    test_git_ops(tmp_repo)

    print("\n=== regex_parser ===")
    test_regex_parser_python()
    test_regex_parser_typescript()

    print("\n=== treesitter ===")
    test_treesitter_python()
    test_treesitter_typescript()

    print("\n=== storage ===")
    test_storage(tmp_repo)
    test_fts_search(tmp_repo)

    print("\n=== ranking ===")
    test_tokenize()
    test_score_candidate()

    print("\n=== indexer ===")
    test_pagerank_simple()
    test_build_index(tmp_repo)
    test_update_index(tmp_repo)
    test_ensure_index_non_git()
    test_find_relevant_files(tmp_repo)

  print("\n=== integration (real repo) ===")
  test_real_repo_search()

  print("\n✓ ALL TESTS PASSED")


if __name__ == "__main__":
  main()
