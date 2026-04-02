"""Safe git operations with proper error handling."""

import subprocess
from pathlib import Path

from .models import GitError, NotAGitRepoError

_SEP = "\x00"  # Null byte separator — cannot appear in commit messages


def _git(root: str, *args: str) -> str:
  """Run a git command, check returncode, return stdout."""
  try:
    result = subprocess.run(
      ["git", *args],
      cwd=root,
      capture_output=True,
      text=True,
      timeout=30,
    )
  except FileNotFoundError:
    raise GitError("git is not installed or not in PATH")
  except subprocess.TimeoutExpired:
    raise GitError(f"git {args[0]} timed out after 30s")

  if result.returncode != 0:
    stderr = result.stderr.strip()
    if "not a git repository" in stderr.lower():
      raise NotAGitRepoError(f"Not a git repository: {root}")
    raise GitError(f"git {' '.join(args)} failed: {stderr}")

  return result.stdout.strip()


def is_git_repo(root: str) -> bool:
  """Check if directory is a git repository."""
  try:
    _git(root, "rev-parse", "--git-dir")
    return True
  except (GitError, OSError):
    return False


def get_git_hash(root: str) -> str:
  """Get current HEAD commit hash."""
  return _git(root, "rev-parse", "HEAD")


def get_tracked_files(root: str) -> list[str]:
  """Get all git-tracked files."""
  output = _git(root, "ls-files")
  if not output:
    return []
  return [f for f in output.split("\n") if f]


def get_changed_files(root: str, since_hash: str) -> list[str]:
  """Get files changed between since_hash and HEAD. Safe on missing hashes."""
  try:
    output = _git(root, "diff", "--name-only", since_hash, "HEAD")
  except GitError:
    # Hash no longer exists (rebase, gc, force push) — return all files
    return get_tracked_files(root)
  if not output:
    return []
  return [f for f in output.split("\n") if f]


def get_recent_changes(root: str, since: str = "7 days ago") -> list[dict]:
  """Get recent commits with changed files. Uses null-byte separator."""
  fmt = f"%H{_SEP}%an{_SEP}%s{_SEP}%ai"
  try:
    output = _git(
      root, "log", f"--since={since}",
      f"--pretty=format:{fmt}", "--name-only",
    )
  except GitError:
    return []

  if not output:
    return []

  commits: list[dict] = []
  current: dict | None = None

  for line in output.split("\n"):
    if _SEP in line:
      parts = line.split(_SEP, 3)
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


def read_file_safe(root: str, path: str) -> str | None:
  """Read file content, return None on failure or oversized files."""
  try:
    full = Path(root) / path
    if full.stat().st_size > 500_000:
      return None
    return full.read_text(encoding="utf-8", errors="ignore")
  except (OSError, UnicodeDecodeError):
    return None
