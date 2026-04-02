"""Index orchestration — build, update, and ensure freshness."""

from datetime import datetime, timezone
from pathlib import Path

from .models import (
  detect_language, should_skip_path, NotAGitRepoError, INDEX_DIR,
)
from .git_ops import (
  is_git_repo, get_git_hash, get_tracked_files,
  get_changed_files, read_file_safe,
)
from .parser import parse_file
from .storage import (
  get_db, get_metadata, set_metadata, upsert_file, delete_file,
  get_file_count, get_all_file_ids, get_all_imports,
  clear_import_edges, insert_import_edges, update_pagerank_scores,
  get_import_edge_list, migrate_from_json,
)


def _resolve_imports(db) -> None:
  """Resolve raw import strings to actual file paths, populate import_edges."""
  file_ids = get_all_file_ids(db)  # path → id
  all_imports = get_all_imports(db)  # [(file_id, raw_import), ...]

  # Build suffix lookup: "models/entities" → file_id for "src/domain/models/entities.py"
  suffix_map: dict[str, int] = {}
  for path, fid in file_ids.items():
    # Generate multiple suffix keys for matching
    stem = Path(path).with_suffix("").as_posix()  # "src/domain/models/entities"
    parts = stem.split("/")
    for i in range(len(parts)):
      suffix = "/".join(parts[i:])
      # Only store if not ambiguous (first wins)
      if suffix not in suffix_map:
        suffix_map[suffix] = fid

  edges: list[tuple[int, int]] = []
  for source_id, raw_import in all_imports:
    # Normalize import: dots → slashes, strip quotes
    imp = raw_import.strip("'\"").replace(".", "/").replace("::", "/")

    # Try exact suffix match
    target_id = suffix_map.get(imp)
    if target_id and target_id != source_id:
      edges.append((source_id, target_id))
      continue

    # Try with common file extensions stripped
    for suffix in [imp, imp.split("/")[-1]]:
      if suffix in suffix_map:
        tid = suffix_map[suffix]
        if tid != source_id:
          edges.append((source_id, tid))
          break

  clear_import_edges(db)
  if edges:
    insert_import_edges(db, edges)


def _pagerank_simple(
  nodes: set[int],
  edges: list[tuple[int, int]],
  alpha: float = 0.85,
  iterations: int = 40,
) -> dict[int, float]:
  """Pure Python PageRank — no numpy/scipy dependency."""
  n = len(nodes)
  if n == 0:
    return {}

  score = {nid: 1.0 / n for nid in nodes}
  out_degree: dict[int, int] = {nid: 0 for nid in nodes}
  in_edges: dict[int, list[int]] = {nid: [] for nid in nodes}

  for src, tgt in edges:
    out_degree[src] += 1
    in_edges[tgt].append(src)

  base = (1.0 - alpha) / n
  for _ in range(iterations):
    new_score = {}
    for nid in nodes:
      rank = base
      for src in in_edges[nid]:
        if out_degree[src] > 0:
          rank += alpha * score[src] / out_degree[src]
      new_score[nid] = rank
    score = new_score

  return score


def _compute_pagerank(db) -> None:
  """Compute PageRank on import graph and store scores."""
  edges = get_import_edge_list(db)
  if not edges:
    return

  file_ids = get_all_file_ids(db)
  all_ids = set(file_ids.values())

  # Pure Python PageRank — no numpy/scipy needed
  scores = _pagerank_simple(all_ids, edges, alpha=0.85, iterations=40)

  update_pagerank_scores(db, scores)


def _now_iso() -> str:
  return datetime.now(timezone.utc).isoformat()


def build_index(root: str) -> int:
  """Build a full index from scratch. Returns number of files indexed."""
  root = str(Path(root).resolve())

  if not is_git_repo(root):
    raise NotAGitRepoError(f"Not a git repository: {root}")

  db = get_db(root)
  git_hash = get_git_hash(root)
  tracked = get_tracked_files(root)
  now = _now_iso()
  count = 0

  # Clear existing data — drop and recreate FTS table (contentless can't DELETE)
  db.execute("DROP TABLE IF EXISTS search_index")
  db.execute("DELETE FROM code_files")
  db.executescript("""
    CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
      path, symbol_names, import_names, content='', tokenize='unicode61'
    );
  """)
  db.commit()

  for path in tracked:
    if should_skip_path(path):
      continue
    lang = detect_language(path)
    if lang == "unknown":
      continue
    content = read_file_safe(root, path)
    if content is None:
      continue

    fi = parse_file(path, content)
    upsert_file(db, fi, now)
    count += 1

  set_metadata(db, "git_hash", git_hash)
  set_metadata(db, "updated_at", now)
  db.commit()

  # Build import graph + PageRank
  _resolve_imports(db)
  _compute_pagerank(db)
  db.commit()

  return count


def update_index(root: str) -> bool:
  """Incrementally update index. Returns True if updated, False if already fresh."""
  root = str(Path(root).resolve())
  db = get_db(root)

  stored_hash = get_metadata(db, "git_hash")
  current_hash = get_git_hash(root)

  if stored_hash == current_hash:
    return False

  changed = get_changed_files(root, stored_hash or "")
  if not changed and stored_hash:
    set_metadata(db, "git_hash", current_hash)
    db.commit()
    return False

  tracked_set = set(get_tracked_files(root))
  now = _now_iso()

  for path in changed:
    if path not in tracked_set:
      delete_file(db, path)
      continue
    if should_skip_path(path):
      continue
    lang = detect_language(path)
    if lang == "unknown":
      continue
    content = read_file_safe(root, path)
    if content is None:
      delete_file(db, path)
      continue

    fi = parse_file(path, content)
    upsert_file(db, fi, now)

  set_metadata(db, "git_hash", current_hash)
  set_metadata(db, "updated_at", now)
  db.commit()

  # Rebuild import graph + PageRank after changes
  _resolve_imports(db)
  _compute_pagerank(db)
  db.commit()

  return True


def ensure_index(root: str) -> None:
  """Ensure the index exists and is fresh. Auto-migrates from JSON if needed."""
  root = str(Path(root).resolve())

  if not is_git_repo(root):
    raise NotAGitRepoError(f"Not a git repository: {root}")

  db = get_db(root)

  # Auto-migrate from old JSON format
  migrate_from_json(root, db)

  if get_file_count(db) == 0:
    build_index(root)
  else:
    update_index(root)
