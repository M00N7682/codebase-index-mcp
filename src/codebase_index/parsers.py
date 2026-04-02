"""Language-specific symbol extraction via regex patterns."""

import re
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
  ".codebase-index", "vendor", "egg-info",
}

SKIP_EXTENSIONS = {
  ".pyc", ".pyo", ".so", ".dll", ".dylib", ".class",
  ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
  ".woff", ".woff2", ".ttf", ".eot",
  ".zip", ".tar", ".gz", ".bz2",
  ".pdf", ".doc", ".docx", ".xls", ".xlsx",
  ".lock", ".map", ".min.js", ".min.css",
  ".db", ".sqlite", ".sqlite3",
}


@dataclass
class Symbol:
  name: str
  kind: str  # function, class, method, interface, struct, export
  line: int


@dataclass
class FileInfo:
  path: str
  language: str
  lines: int
  symbols: list[Symbol] = field(default_factory=list)
  imports: list[str] = field(default_factory=list)


# --- Python ---
_PY_FUNC = re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
_PY_CLASS = re.compile(r"^class\s+(\w+)", re.MULTILINE)
_PY_IMPORT_FROM = re.compile(r"^from\s+([\w.]+)\s+import", re.MULTILINE)
_PY_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)


def _parse_python(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  symbols = []
  for m in _PY_CLASS.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "class", ln))
  for m in _PY_FUNC.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "function", ln))

  imports = []
  for m in _PY_IMPORT_FROM.finditer(content):
    imports.append(m.group(1))
  for m in _PY_IMPORT.finditer(content):
    imports.append(m.group(1))
  return symbols, imports


# --- TypeScript / JavaScript ---
_TS_FUNC = re.compile(
  r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE
)
_TS_CLASS = re.compile(r"(?:export\s+)?class\s+(\w+)", re.MULTILINE)
_TS_INTERFACE = re.compile(r"(?:export\s+)?interface\s+(\w+)", re.MULTILINE)
_TS_ARROW = re.compile(
  r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?[\(<]",
  re.MULTILINE,
)
_TS_IMPORT = re.compile(r"import\s+.*?from\s+['\"](.+?)['\"]", re.MULTILINE)
_TS_TYPE = re.compile(r"(?:export\s+)?type\s+(\w+)\s*[=<]", re.MULTILINE)


def _parse_typescript(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  symbols = []
  for m in _TS_CLASS.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "class", ln))
  for m in _TS_INTERFACE.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "interface", ln))
  for m in _TS_FUNC.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "function", ln))
  for m in _TS_ARROW.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "function", ln))
  for m in _TS_TYPE.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "type", ln))

  imports = [m.group(1) for m in _TS_IMPORT.finditer(content)]
  return symbols, imports


# --- Go ---
_GO_FUNC = re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.MULTILINE)
_GO_STRUCT = re.compile(r"^type\s+(\w+)\s+struct", re.MULTILINE)
_GO_INTERFACE = re.compile(r"^type\s+(\w+)\s+interface", re.MULTILINE)
_GO_IMPORT = re.compile(r'"([\w./\-]+)"')


def _parse_go(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  symbols = []
  for m in _GO_STRUCT.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "struct", ln))
  for m in _GO_INTERFACE.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "interface", ln))
  for m in _GO_FUNC.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "function", ln))

  imports = [m.group(1) for m in _GO_IMPORT.finditer(content)]
  return symbols, imports


# --- Rust ---
_RS_FN = re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.MULTILINE)
_RS_STRUCT = re.compile(r"^(?:pub\s+)?struct\s+(\w+)", re.MULTILINE)
_RS_ENUM = re.compile(r"^(?:pub\s+)?enum\s+(\w+)", re.MULTILINE)
_RS_TRAIT = re.compile(r"^(?:pub\s+)?trait\s+(\w+)", re.MULTILINE)
_RS_USE = re.compile(r"^use\s+([\w:]+)", re.MULTILINE)


def _parse_rust(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  symbols = []
  for m in _RS_STRUCT.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "struct", ln))
  for m in _RS_ENUM.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "enum", ln))
  for m in _RS_TRAIT.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "trait", ln))
  for m in _RS_FN.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "function", ln))

  imports = [m.group(1) for m in _RS_USE.finditer(content)]
  return symbols, imports


# --- Java / Kotlin / C# (similar enough) ---
_JAVA_CLASS = re.compile(
  r"(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)",
  re.MULTILINE,
)
_JAVA_INTERFACE = re.compile(
  r"(?:public\s+)?interface\s+(\w+)", re.MULTILINE
)
_JAVA_METHOD = re.compile(
  r"(?:public|private|protected)\s+(?:static\s+)?(?:async\s+)?"
  r"(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(",
  re.MULTILINE,
)
_JAVA_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)


def _parse_java(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  symbols = []
  for m in _JAVA_CLASS.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "class", ln))
  for m in _JAVA_INTERFACE.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "interface", ln))
  for m in _JAVA_METHOD.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "method", ln))

  imports = [m.group(1) for m in _JAVA_IMPORT.finditer(content)]
  return symbols, imports


# --- Ruby ---
_RB_CLASS = re.compile(r"^class\s+(\w+)", re.MULTILINE)
_RB_MODULE = re.compile(r"^module\s+(\w+)", re.MULTILINE)
_RB_DEF = re.compile(r"^\s*def\s+(\w+)", re.MULTILINE)
_RB_REQUIRE = re.compile(r"^require\s+['\"](.+?)['\"]", re.MULTILINE)


def _parse_ruby(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  symbols = []
  for m in _RB_CLASS.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "class", ln))
  for m in _RB_MODULE.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "module", ln))
  for m in _RB_DEF.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "function", ln))

  imports = [m.group(1) for m in _RB_REQUIRE.finditer(content)]
  return symbols, imports


# --- Generic fallback ---
def _parse_generic(content: str, lines: list[str]) -> tuple[list[Symbol], list[str]]:
  return [], []


# --- Dispatcher ---
_PARSERS = {
  "python": _parse_python,
  "typescript": _parse_typescript,
  "javascript": _parse_typescript,  # close enough
  "go": _parse_go,
  "rust": _parse_rust,
  "java": _parse_java,
  "kotlin": _parse_java,  # similar syntax
  "csharp": _parse_java,
  "ruby": _parse_ruby,
  "vue": _parse_typescript,  # script section
  "svelte": _parse_typescript,
}


def detect_language(path: str) -> str:
  """Detect language from file extension."""
  suffix = Path(path).suffix.lower()
  return LANGUAGE_MAP.get(suffix, "unknown")


def should_skip_path(path: str) -> bool:
  """Check if a path should be skipped during indexing."""
  parts = Path(path).parts
  for part in parts:
    if part in SKIP_DIRS:
      return True
  suffix = Path(path).suffix.lower()
  if suffix in SKIP_EXTENSIONS:
    return True
  return False


def parse_file(path: str, content: str) -> FileInfo:
  """Parse a file and extract symbols + imports."""
  language = detect_language(path)
  file_lines = content.split("\n")
  line_count = len(file_lines)

  parser = _PARSERS.get(language, _parse_generic)
  symbols, imports = parser(content, file_lines)

  return FileInfo(
    path=path,
    language=language,
    lines=line_count,
    symbols=symbols,
    imports=imports,
  )
