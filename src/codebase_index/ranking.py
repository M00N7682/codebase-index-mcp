"""BM25 + PageRank combined ranking — replaces keyword-only search."""

import re
import sqlite3
from pathlib import Path

from .storage import search_fts, get_file, get_project_stats, get_metadata

# Korean → English mappings for codebases with English file/symbol names
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
  text = text.lower()
  tokens = re.findall(r"[a-z0-9\uac00-\ud7a3_]+", text)
  expanded = []
  for t in tokens:
    if any("\uac00" <= c <= "\ud7a3" for c in t):
      mapped = _KO_EN_MAP.get(t, [])
      if mapped:
        expanded.extend(mapped)
      else:
        expanded.append(t)  # Keep unmapped Korean tokens
      continue
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).lower().split()
    for p in parts:
      expanded.extend(p.split("_"))
  return [t for t in expanded if t and t not in _STOP_WORDS and len(t) > 1]


def find_relevant_files(
  db: sqlite3.Connection,
  query: str,
  limit: int = 15,
) -> list[dict]:
  """
  Find relevant files using BM25 full-text search + PageRank.
  Combined score = normalized_bm25 * 0.7 + normalized_pagerank * 0.3
  """
  keywords = _tokenize(query)
  if not keywords:
    return []

  fts_query = " ".join(keywords)
  candidates = search_fts(db, fts_query, limit)

  if not candidates:
    return []

  # Normalize BM25 scores (they're negative, more negative = more relevant)
  bm25_scores = [c["bm25_score"] for c in candidates]
  bm25_min = min(bm25_scores) if bm25_scores else 0
  bm25_max = max(bm25_scores) if bm25_scores else 0
  bm25_range = bm25_max - bm25_min if bm25_max != bm25_min else 1.0

  # Normalize PageRank scores
  pr_scores = [c["pagerank"] for c in candidates]
  pr_max = max(pr_scores) if pr_scores else 0
  pr_max = pr_max if pr_max > 0 else 1.0

  results = []
  for c in candidates:
    # BM25: more negative = better, so invert
    norm_bm25 = (bm25_max - c["bm25_score"]) / bm25_range if bm25_range else 1.0
    norm_pr = c["pagerank"] / pr_max

    combined = norm_bm25 * 0.85 + norm_pr * 0.15

    # Penalize test/spec files
    path_lower = c["path"].lower()
    if "test" in path_lower or "spec" in path_lower:
      combined *= 0.7

    top_symbols = [
      f"{s['kind']} {s['name']} (L{s['line']})"
      for s in c["symbols"][:10]
    ]

    results.append({
      "path": c["path"],
      "score": round(combined * 100, 1),
      "language": c["language"],
      "lines": c["lines"],
      "symbols": top_symbols,
      "pagerank": round(c["pagerank"], 4),
    })

  results.sort(key=lambda x: x["score"], reverse=True)
  return results[:limit]


def get_project_summary(db: sqlite3.Connection) -> dict:
  """Generate a high-level project overview from SQLite."""
  stats = get_project_stats(db)
  stats["git_hash"] = get_metadata(db, "git_hash") or ""
  stats["updated_at"] = get_metadata(db, "updated_at") or ""
  return stats
