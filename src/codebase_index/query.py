"""Search and ranking logic — find the right files for a given task."""

import re
from collections import Counter
from pathlib import Path

from .indexer import ProjectIndex
from .parsers import FileInfo

# Common stop words to filter out of queries
_STOP_WORDS = {
  "the", "a", "an", "is", "are", "was", "were", "be", "been",
  "do", "does", "did", "will", "would", "could", "should",
  "have", "has", "had", "this", "that", "these", "those",
  "in", "on", "at", "to", "for", "of", "with", "by", "from",
  "and", "or", "not", "no", "but", "if", "then", "else",
  "it", "its", "i", "we", "you", "he", "she", "they",
  "add", "fix", "update", "change", "make", "create", "implement",
  "need", "want", "로직", "추가", "수정", "변경", "구현", "만들기",
  "기능", "하는", "하기", "에서", "으로", "것",
}

# Korean → English keyword mappings for codebases with English file/symbol names
_KO_EN_MAP: dict[str, list[str]] = {
  "네이버": ["naver"],
  "카탈로그": ["catalog", "catalogue"],
  "크롤링": ["crawl", "scrape", "scraper"],
  "크롤러": ["crawler", "scraper"],
  "스크래퍼": ["scraper"],
  "스크래핑": ["scraping", "scrape"],
  "에러": ["error", "exception", "fail"],
  "오류": ["error", "exception", "fail"],
  "프록시": ["proxy"],
  "리뷰": ["review"],
  "상품": ["product", "item", "catalog"],
  "카테고리": ["category"],
  "가격": ["price"],
  "배치": ["batch"],
  "파이프라인": ["pipeline"],
  "몽고": ["mongo", "mongodb"],
  "데이터베이스": ["database", "db", "mongo"],
  "세션": ["session"],
  "쿠키": ["cookie"],
  "쿠팡": ["coupang"],
  "옥션": ["auction"],
  "지마켓": ["gmarket"],
  "다나와": ["danawa"],
  "저장": ["save", "persist", "repository"],
  "조회": ["find", "query", "get", "fetch"],
  "삭제": ["delete", "remove"],
  "설정": ["config", "setting"],
  "로그": ["log", "logging"],
  "인증": ["auth", "authentication"],
  "테스트": ["test"],
  "응답": ["response"],
  "요청": ["request"],
  "재시도": ["retry"],
  "타임아웃": ["timeout"],
  "연결": ["connection", "connect"],
}


def _tokenize(text: str) -> list[str]:
  """Extract meaningful keywords from text, with Korean→English expansion."""
  text = text.lower()
  # Split on non-alphanumeric (keep Korean chars)
  tokens = re.findall(r"[a-z0-9\uac00-\ud7a3_]+", text)
  # Also split camelCase / snake_case
  expanded = []
  for t in tokens:
    # Korean → English expansion
    if any("\uac00" <= c <= "\ud7a3" for c in t):
      mapped = _KO_EN_MAP.get(t, [])
      if mapped:
        expanded.extend(mapped)
      continue
    # camelCase split
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).lower().split()
    # snake_case split
    for p in parts:
      expanded.extend(p.split("_"))
  return [t for t in expanded if t and t not in _STOP_WORDS and len(t) > 1]


def _score_file(fi: FileInfo, keywords: list[str], import_counts: Counter) -> float:
  """Score a file's relevance to the given keywords."""
  score = 0.0
  path_lower = fi.path.lower()
  path_parts = set(Path(fi.path).parts)
  path_stem = Path(fi.path).stem.lower()

  symbol_names = {s.name.lower() for s in fi.symbols}
  symbol_names_joined = " ".join(symbol_names)

  for kw in keywords:
    # Filename exact match (strongest signal)
    if kw in path_stem:
      score += 10.0

    # Directory name match
    for part in path_parts:
      if kw in part.lower():
        score += 3.0
        break

    # Symbol name match
    for sym in symbol_names:
      if kw == sym:
        score += 8.0
        break
      elif kw in sym:
        score += 4.0
        break

    # Import path match
    for imp in fi.imports:
      if kw in imp.lower():
        score += 2.0
        break

  # Centrality bonus: files imported by many others are more important
  centrality = import_counts.get(fi.path, 0)
  score += min(centrality * 0.5, 5.0)

  # Penalize test files slightly (less likely to be the target)
  if "test" in path_lower or "spec" in path_lower:
    score *= 0.7

  # Penalize very large files slightly
  if fi.lines > 500:
    score *= 0.9

  return score


def _build_import_graph(index: ProjectIndex) -> Counter:
  """Count how many files import each file (rough centrality)."""
  counts: Counter = Counter()
  all_paths = set(index.files.keys())

  for fi in index.files.values():
    for imp in fi.imports:
      # Try to resolve import to a file path
      imp_clean = imp.replace(".", "/")
      for path in all_paths:
        if imp_clean in path:
          counts[path] += 1
          break
  return counts


def find_relevant_files(
  index: ProjectIndex,
  query: str,
  limit: int = 15,
) -> list[dict]:
  """
  Given a natural language task description, return the most relevant files.
  Ranked by keyword match against filenames, symbols, and import graph centrality.
  """
  keywords = _tokenize(query)
  if not keywords:
    return []

  import_counts = _build_import_graph(index)
  scored: list[tuple[float, str, FileInfo]] = []

  for path, fi in index.files.items():
    s = _score_file(fi, keywords, import_counts)
    if s > 0:
      scored.append((s, path, fi))

  scored.sort(key=lambda x: x[0], reverse=True)

  results = []
  for score, path, fi in scored[:limit]:
    top_symbols = [
      f"{s.kind} {s.name} (L{s.line})"
      for s in fi.symbols[:10]
    ]
    results.append({
      "path": path,
      "score": round(score, 1),
      "language": fi.language,
      "lines": fi.lines,
      "symbols": top_symbols,
      "imports_count": len(fi.imports),
    })

  return results


def build_directory_tree(index: ProjectIndex) -> dict:
  """Build a nested directory tree with file counts and language stats."""
  tree: dict = {}

  for path, fi in index.files.items():
    parts = Path(path).parts
    node = tree
    for part in parts[:-1]:
      if part not in node:
        node[part] = {"_files": 0, "_languages": Counter()}
      node[part]["_files"] += 1
      node[part]["_languages"][fi.language] += 1
      node = node[part]

  return tree


def get_project_summary(index: ProjectIndex) -> dict:
  """Generate a high-level project overview."""
  lang_counts: Counter = Counter()
  total_lines = 0
  total_symbols = 0

  for fi in index.files.values():
    lang_counts[fi.language] += 1
    total_lines += fi.lines
    total_symbols += len(fi.symbols)

  # Top-level directory breakdown
  dir_breakdown: dict[str, dict] = {}
  for path, fi in index.files.items():
    parts = Path(path).parts
    top_dir = parts[0] if len(parts) > 1 else "."
    if top_dir not in dir_breakdown:
      dir_breakdown[top_dir] = {"files": 0, "lines": 0, "languages": Counter()}
    dir_breakdown[top_dir]["files"] += 1
    dir_breakdown[top_dir]["lines"] += fi.lines
    dir_breakdown[top_dir]["languages"][fi.language] += 1

  # Convert Counters to dicts for serialization
  for d in dir_breakdown.values():
    d["languages"] = dict(d["languages"])

  return {
    "total_files": len(index.files),
    "total_lines": total_lines,
    "total_symbols": total_symbols,
    "languages": dict(lang_counts),
    "directories": dir_breakdown,
    "git_hash": index.git_hash,
    "updated_at": index.updated_at,
  }
