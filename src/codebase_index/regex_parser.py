"""Regex-based symbol extraction — fallback for languages without tree-sitter support."""

import re

from .models import Symbol, FileInfo, detect_language

# --- Python ---
_PY_FUNC = re.compile(r"^(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
_PY_CLASS = re.compile(r"^class\s+(\w+)", re.MULTILINE)
_PY_METHOD = re.compile(r"^  (?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
_PY_IMPORT_FROM = re.compile(r"^from\s+([\w.]+)\s+import", re.MULTILINE)
_PY_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)


def _parse_python(content: str) -> tuple[list[Symbol], list[str]]:
  symbols = []
  class_ranges: list[tuple[int, int]] = []
  for m in _PY_CLASS.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "class", ln))
    class_ranges.append((m.start(), m.end()))

  for m in _PY_METHOD.finditer(content):
    ln = content[: m.start()].count("\n") + 1
    symbols.append(Symbol(m.group(1), "method", ln))

  for m in _PY_FUNC.finditer(content):
    pos = m.start()
    # Skip if this is actually a method (indented under a class)
    line_start = content.rfind("\n", 0, pos) + 1
    indent = pos - line_start
    if indent > 0:
      continue
    ln = content[: pos].count("\n") + 1
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


def _parse_typescript(content: str) -> tuple[list[Symbol], list[str]]:
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


def _parse_go(content: str) -> tuple[list[Symbol], list[str]]:
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


def _parse_rust(content: str) -> tuple[list[Symbol], list[str]]:
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


# --- Java / Kotlin / C# ---
_JAVA_CLASS = re.compile(
  r"(?:public|private|protected)?\s*(?:abstract\s+)?class\s+(\w+)",
  re.MULTILINE,
)
_JAVA_INTERFACE = re.compile(r"(?:public\s+)?interface\s+(\w+)", re.MULTILINE)
_JAVA_METHOD = re.compile(
  r"(?:public|private|protected)\s+(?:static\s+)?(?:async\s+)?"
  r"(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(",
  re.MULTILINE,
)
_JAVA_IMPORT = re.compile(r"^import\s+([\w.]+)", re.MULTILINE)


def _parse_java(content: str) -> tuple[list[Symbol], list[str]]:
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


def _parse_ruby(content: str) -> tuple[list[Symbol], list[str]]:
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


def _parse_generic(content: str) -> tuple[list[Symbol], list[str]]:
  return [], []


_PARSERS = {
  "python": _parse_python,
  "typescript": _parse_typescript,
  "javascript": _parse_typescript,
  "go": _parse_go,
  "rust": _parse_rust,
  "java": _parse_java,
  "kotlin": _parse_java,
  "csharp": _parse_java,
  "ruby": _parse_ruby,
  "vue": _parse_typescript,
  "svelte": _parse_typescript,
}


def regex_parse_file(path: str, content: str) -> FileInfo:
  """Parse a file using regex patterns. Fallback for unsupported tree-sitter languages."""
  language = detect_language(path)
  line_count = content.count("\n") + 1
  parser = _PARSERS.get(language, _parse_generic)
  symbols, imports = parser(content)
  return FileInfo(
    path=path, language=language, lines=line_count,
    symbols=symbols, imports=imports,
  )
