"""Shared data models for the codebase index."""

from dataclasses import dataclass, field
from pathlib import Path

LANGUAGE_MAP: dict[str, str] = {
  ".py": "python",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".js": "javascript",
  ".jsx": "javascript",
  ".go": "go",
  ".rs": "rust",
  ".java": "java",
  ".kt": "kotlin",
  ".swift": "swift",
  ".rb": "ruby",
  ".php": "php",
  ".cs": "csharp",
  ".cpp": "cpp",
  ".c": "c",
  ".h": "c",
  ".vue": "vue",
  ".svelte": "svelte",
}

SKIP_DIRS = {
  "node_modules", ".git", "__pycache__", ".venv", "venv",
  "dist", "build", ".next", ".nuxt", "target", ".tox",
  ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage",
  ".codebase-index", "vendor",
}

SKIP_EXTENSIONS = {
  ".pyc", ".pyo", ".so", ".dll", ".dylib", ".class",
  ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
  ".woff", ".woff2", ".ttf", ".eot",
  ".zip", ".tar", ".gz", ".bz2",
  ".pdf", ".doc", ".docx", ".xls", ".xlsx",
  ".lock", ".map",
  ".db", ".sqlite", ".sqlite3",
}

INDEX_DIR = ".codebase-index"
DB_FILE = "index.db"


@dataclass
class Symbol:
  name: str
  kind: str  # function, class, method, interface, struct, enum, trait, module, type
  line: int


@dataclass
class FileInfo:
  path: str
  language: str
  lines: int
  symbols: list[Symbol] = field(default_factory=list)
  imports: list[str] = field(default_factory=list)


class GitError(Exception):
  """Raised when a git operation fails."""


class NotAGitRepoError(GitError):
  """Raised when the target directory is not a git repository."""


def detect_language(path: str) -> str:
  """Detect language from file extension."""
  suffix = Path(path).suffix.lower()
  return LANGUAGE_MAP.get(suffix, "unknown")


def should_skip_path(path: str) -> bool:
  """Check if a path should be skipped during indexing."""
  parts = Path(path).parts
  for part in parts:
    if part in SKIP_DIRS or part.endswith(".egg-info"):
      return True
  suffix = Path(path).suffix.lower()
  # Handle compound extensions like .min.js
  name = Path(path).name.lower()
  if name.endswith(".min.js") or name.endswith(".min.css"):
    return True
  return suffix in SKIP_EXTENSIONS
