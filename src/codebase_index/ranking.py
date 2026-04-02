"""Multi-signal ranking: BM25 for recall, custom scorer for precision."""

import re
import sqlite3
from pathlib import Path

from .storage import get_file_count, get_project_stats, get_metadata

_KO_EN_MAP: dict[str, list[str]] = {
  "네이버": ["naver"], "카탈로그": ["catalog", "catalogue"],
  "크롤링": ["crawl", "scraper"], "크롤러": ["crawler", "scraper"],
  "스크래퍼": ["scraper"], "스크래핑": ["scraping"],
  "에러": ["error"], "오류": ["error"],
  "프록시": ["proxy"], "리뷰": ["review"],
  "상품": ["product", "item", "catalog"], "카테고리": ["category"],
  "가격": ["price"], "배치": ["batch"], "파이프라인": ["pipeline"],
  "몽고": ["mongo", "mongodb"], "데이터베이스": ["database", "db", "mongo"],
  "세션": ["session"], "쿠키": ["cookie"],
  "쿠팡": ["coupang"], "옥션": ["auction"], "지마켓": ["gmarket"],
  "다나와": ["danawa"], "저장": ["save", "persist", "repository"],
  "조회": ["find", "query", "get", "fetch"], "삭제": ["delete", "remove"],
  "설정": ["config", "setting"], "로그": ["log", "logging"],
  "인증": ["auth", "authentication"], "테스트": ["test"],
  "응답": ["response"], "요청": ["request"],
  "재시도": ["retry"], "타임아웃": ["timeout"], "연결": ["connection", "connect"],
  "컴포넌트": ["component"], "라우터": ["router", "routing"],
  "미들웨어": ["middleware"], "핸들러": ["handler"],
  "스케줄러": ["scheduler", "cron"], "워커": ["worker"],
  "큐": ["queue"], "캐시": ["cache"], "인덱스": ["index"],
}

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


def _tokenize(text: str) -> list[str]:
  """Extract keywords with Korean→English expansion. Unmapped Korean kept as-is."""
  # Split camelCase BEFORE lowering
  text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
  text = text.lower()
  tokens = re.findall(r"[a-z0-9\uac00-\ud7a3_]+", text)
  expanded = []
  for t in tokens:
    if any("\uac00" <= c <= "\ud7a3" for c in t):
      mapped = _KO_EN_MAP.get(t, [])
      if mapped:
        expanded.extend(mapped)
      else:
        expanded.append(t)
      continue
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).lower().split()
    for p in parts:
      expanded.extend(p.split("_"))
  return [t for t in expanded if t and t not in _STOP_WORDS and len(t) > 1]


def _retrieve_candidates(db: sqlite3.Connection, keywords: list[str], limit: int) -> list[dict]:
  """
  Stage 1: Candidate retrieval from two sources:
  - FTS5 BM25 for symbol/import matching
  - Direct SQL LIKE for path matching (FTS tokenizer may miss path components)
  """
  seen_ids: set[int] = set()
  rows: list[tuple] = []

  # Source A: FTS5 full-text search
  fts_query = " OR ".join(f'"{t}"' for t in keywords)
  if fts_query:
    try:
      fts_rows = db.execute(
        """
        SELECT f.id, f.path, f.language, f.lines, f.pagerank
        FROM search_index
        JOIN code_files f ON search_index.rowid = f.id
        WHERE search_index MATCH ?
        LIMIT ?
        """,
        (fts_query, limit * 5),
      ).fetchall()
      for r in fts_rows:
        if r[0] not in seen_ids:
          seen_ids.add(r[0])
          rows.append(r)
    except sqlite3.OperationalError:
      pass

  # Source B: Direct path LIKE search — catches files FTS misses
  for kw in keywords:
    if len(kw) < 3:
      continue
    like_rows = db.execute(
      """
      SELECT id, path, language, lines, pagerank
      FROM code_files
      WHERE path LIKE ?
      LIMIT ?
      """,
      (f"%{kw}%", limit * 2),
    ).fetchall()
    for r in like_rows:
      if r[0] not in seen_ids:
        seen_ids.add(r[0])
        rows.append(r)

  candidates = []
  for file_id, path, lang, lines, pagerank in rows:
    symbols = db.execute(
      "SELECT name, kind, line FROM code_symbols WHERE file_id=? ORDER BY line LIMIT 15",
      (file_id,),
    ).fetchall()
    candidates.append({
      "file_id": file_id,
      "path": path,
      "language": lang,
      "lines": lines,
      "pagerank": pagerank,
      "symbols": [{"name": r[0], "kind": r[1], "line": r[2]} for r in symbols],
    })
  return candidates


def _score_candidate(candidate: dict, keywords: list[str]) -> float:
  """
  Stage 2: Multi-signal scoring.

  Signals (in order of importance):
  1. Path coverage  — how many keywords appear in the file path (strongest)
  2. Path stem hit  — keyword matches the filename stem (very strong)
  3. Symbol match   — keyword matches a function/class name (strong)
  4. PageRank       — file's importance in the import graph (moderate)
  """
  path = candidate["path"]
  path_lower = path.lower()
  path_parts = path_lower.replace("/", " ").replace("_", " ").replace(".", " ").split()
  path_stem = Path(path).stem.lower()
  stem_parts = set(re.split(r"[_\-.]", path_stem))
  symbol_names = {s["name"].lower() for s in candidate["symbols"]}

  score = 0.0
  matched_keywords = set()

  for kw in keywords:
    kw_matched = False

    # Signal 1: path stem exact word match (strongest)
    if kw in stem_parts:
      score += 15.0
      kw_matched = True
    # Signal 2: path directory match
    elif kw in path_parts:
      score += 8.0
      kw_matched = True
    # Signal 3: path substring (weaker, requires 3+ char to avoid noise)
    elif len(kw) >= 3 and kw in path_lower:
      score += 5.0
      kw_matched = True

    # Signal 4: symbol exact match
    if kw in symbol_names:
      score += 6.0
      kw_matched = True
    elif any(kw in sym for sym in symbol_names if len(kw) >= 3):
      score += 3.0
      kw_matched = True

    if kw_matched:
      matched_keywords.add(kw)

  # Coverage bonus: reward files matching MORE distinct keywords
  if keywords:
    coverage = len(matched_keywords) / len(set(keywords))
    score *= (0.5 + coverage * 0.5)  # 50% base + 50% from coverage

  # PageRank bonus (capped to avoid domination by hub files)
  score += min(candidate["pagerank"] * 200, 5.0)

  # Penalties
  if "test" in path_lower or "spec" in path_lower or "__test" in path_lower:
    score *= 0.6
  if "_legacy" in path_lower or "deprecated" in path_lower:
    score *= 0.7
  if candidate["lines"] > 800:
    score *= 0.9

  return score


def find_relevant_files(
  db: sqlite3.Connection,
  query: str,
  limit: int = 15,
) -> list[dict]:
  """
  Two-stage ranking:
  1. BM25 retrieves broad candidates from FTS5
  2. Multi-signal scorer re-ranks by path, symbols, and PageRank
  """
  keywords = _tokenize(query)
  if not keywords:
    return []

  candidates = _retrieve_candidates(db, keywords, limit)
  if not candidates:
    return []

  # Score and sort
  scored = []
  for c in candidates:
    s = _score_candidate(c, keywords)
    if s > 0:
      scored.append((s, c))

  scored.sort(key=lambda x: x[0], reverse=True)

  results = []
  for score, c in scored[:limit]:
    top_symbols = [
      f"{s['kind']} {s['name']} (L{s['line']})"
      for s in c["symbols"][:10]
    ]
    results.append({
      "path": c["path"],
      "score": round(score, 1),
      "language": c["language"],
      "lines": c["lines"],
      "symbols": top_symbols,
      "pagerank": round(c["pagerank"], 4),
    })
  return results


def get_project_summary(db: sqlite3.Connection) -> dict:
  """Generate a high-level project overview from SQLite."""
  stats = get_project_stats(db)
  stats["git_hash"] = get_metadata(db, "git_hash") or ""
  stats["updated_at"] = get_metadata(db, "updated_at") or ""
  return stats
