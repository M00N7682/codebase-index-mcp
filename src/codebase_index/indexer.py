"""Core indexing logic — build, persist, and incrementally update the codebase index."""

import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from .parsers import FileInfo, Symbol, parse_file, should_skip_path, detect_language

INDEX_DIR = ".codebase-index"
INDEX_FILE = "index.json"


@dataclass
class ProjectIndex:
  root: str
  git_hash: str
  files: dict[str, FileInfo]
  updated_at: str

  def to_dict(self) -> dict:
    return {
      "root": self.root,
      "git_hash": self.git_hash,
      "updated_at": self.updated_at,
      "file_count": len(self.files),
      "files": {
        path: {
          "language": fi.language,
          "lines": fi.lines,
          "symbols": [
            {"name": s.name, "kind": s.kind, "line": s.line}
            for s in fi.symbols
          ],
          "imports": fi.imports,
        }
        for path, fi in self.files.items()
      },
    }

  @classmethod
  def from_dict(cls, data: dict) -> "ProjectIndex":
    files = {}
    for path, fd in data.get("files", {}).items():
      symbols = [
        Symbol(s["name"], s["kind"], s["line"])
        for s in fd.get("symbols", [])
      ]
      files[path] = FileInfo(
        path=path,
        language=fd["language"],
        lines=fd["lines"],
        symbols=symbols,
        imports=fd.get("imports", []),
      )
    return cls(
      root=data["root"],
      git_hash=data["git_hash"],
      files=files,
      updated_at=data["updated_at"],
    )


def _git(root: str, *args: str) -> str:
  """Run a git command and return stdout."""
  result = subprocess.run(
    ["git", *args],
    cwd=root,
    capture_output=True,
    text=True,
    timeout=30,
  )
  return result.stdout.strip()


def get_git_hash(root: str) -> str:
  return _git(root, "rev-parse", "HEAD")


def get_tracked_files(root: str) -> list[str]:
  """Get all git-tracked files."""
  output = _git(root, "ls-files")
  if not output:
    return []
  return [f for f in output.split("\n") if f]


def get_changed_files(root: str, since_hash: str) -> list[str]:
  """Get files changed between since_hash and HEAD."""
  output = _git(root, "diff", "--name-only", since_hash, "HEAD")
  if not output:
    return []
  return [f for f in output.split("\n") if f]


def get_recent_git_changes(root: str, since: str = "7 days ago") -> list[dict]:
  """Get recent commits with changed files."""
  fmt = "%H|%an|%s|%ai"
  output = _git(root, "log", f"--since={since}", f"--pretty=format:{fmt}", "--name-only")
  if not output:
    return []

  commits = []
  current: dict | None = None
  for line in output.split("\n"):
    if "|" in line and line.count("|") >= 3:
      parts = line.split("|", 3)
      if len(parts) == 4:
        if current:
          commits.append(current)
        current = {
          "hash": parts[0][:8],
          "author": parts[1],
          "message": parts[2],
          "date": parts[3],
          "files": [],
        }
    elif line.strip() and current is not None:
      current["files"].append(line.strip())

  if current:
    commits.append(current)
  return commits


def _read_file_safe(root: str, path: str) -> str | None:
  """Read file content, return None on failure."""
  try:
    full = Path(root) / path
    if full.stat().st_size > 500_000:  # skip files > 500KB
      return None
    return full.read_text(encoding="utf-8", errors="ignore")
  except (OSError, UnicodeDecodeError):
    return None


def build_index(root: str) -> ProjectIndex:
  """Build a full index from scratch."""
  root = str(Path(root).resolve())
  git_hash = get_git_hash(root)
  tracked = get_tracked_files(root)
  files: dict[str, FileInfo] = {}

  for path in tracked:
    if should_skip_path(path):
      continue
    lang = detect_language(path)
    if lang == "unknown":
      continue

    content = _read_file_safe(root, path)
    if content is None:
      continue

    fi = parse_file(path, content)
    files[path] = fi

  index = ProjectIndex(
    root=root,
    git_hash=git_hash,
    files=files,
    updated_at=datetime.now(timezone.utc).isoformat(),
  )
  save_index(index)
  return index


def update_index(index: ProjectIndex) -> ProjectIndex:
  """Incrementally update an existing index."""
  root = index.root
  new_hash = get_git_hash(root)

  if new_hash == index.git_hash:
    return index

  changed = get_changed_files(root, index.git_hash)
  tracked_set = set(get_tracked_files(root))

  for path in changed:
    # file deleted
    if path not in tracked_set:
      index.files.pop(path, None)
      continue
    if should_skip_path(path):
      continue
    lang = detect_language(path)
    if lang == "unknown":
      continue

    content = _read_file_safe(root, path)
    if content is None:
      index.files.pop(path, None)
      continue

    fi = parse_file(path, content)
    index.files[path] = fi

  index.git_hash = new_hash
  index.updated_at = datetime.now(timezone.utc).isoformat()
  save_index(index)
  return index


def save_index(index: ProjectIndex) -> None:
  """Persist index to disk."""
  idx_dir = Path(index.root) / INDEX_DIR
  idx_dir.mkdir(exist_ok=True)
  idx_path = idx_dir / INDEX_FILE
  idx_path.write_text(
    json.dumps(index.to_dict(), ensure_ascii=False, indent=1),
    encoding="utf-8",
  )


def load_index(root: str) -> ProjectIndex | None:
  """Load index from disk, return None if not found."""
  root = str(Path(root).resolve())
  idx_path = Path(root) / INDEX_DIR / INDEX_FILE
  if not idx_path.exists():
    return None
  try:
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    return ProjectIndex.from_dict(data)
  except (json.JSONDecodeError, KeyError):
    return None


def ensure_index(root: str) -> ProjectIndex:
  """Load existing index and update, or build from scratch."""
  existing = load_index(root)
  if existing is None:
    return build_index(root)
  return update_index(existing)
