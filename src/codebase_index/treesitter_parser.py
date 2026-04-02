"""Tree-sitter AST-based symbol extraction via tree walking."""

from .models import Symbol, FileInfo, detect_language

# Cache for parser instances
_parser_cache: dict[str, object] = {}

# Languages we have tree-sitter support for
_SUPPORTED = {
  "python", "typescript", "javascript", "go", "rust",
  "java", "kotlin", "ruby", "c", "cpp", "csharp", "swift", "php",
}

# Map our language names to tree-sitter-language-pack names
_TS_LANG_MAP = {
  "csharp": "c_sharp",
  "cpp": "cpp",
}


class UnsupportedLanguageError(Exception):
  """Tree-sitter does not support this language."""


def _get_parser(language: str):
  """Get or create a cached parser for a language."""
  if language in _parser_cache:
    return _parser_cache[language]

  if language not in _SUPPORTED:
    raise UnsupportedLanguageError(f"No tree-sitter support for: {language}")

  try:
    from tree_sitter_language_pack import get_parser
  except ImportError:
    raise UnsupportedLanguageError("tree-sitter-language-pack not installed")

  ts_name = _TS_LANG_MAP.get(language, language)
  try:
    parser = get_parser(ts_name)
  except Exception:
    raise UnsupportedLanguageError(f"Grammar not available: {ts_name}")

  _parser_cache[language] = parser
  return parser


# --- Node type → symbol kind mapping per language ---

_DEFINITION_NODES: dict[str, dict[str, str]] = {
  "python": {
    "class_definition": "class",
    "function_definition": "function",
  },
  "typescript": {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "function_declaration": "function",
    "method_definition": "method",
    "type_alias_declaration": "type",
  },
  "javascript": {
    "class_declaration": "class",
    "function_declaration": "function",
    "method_definition": "method",
  },
  "go": {
    "function_declaration": "function",
    "method_declaration": "method",
  },
  "rust": {
    "struct_item": "struct",
    "enum_item": "enum",
    "trait_item": "trait",
    "function_item": "function",
    "impl_item": "impl",
  },
  "java": {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "method_declaration": "method",
    "enum_declaration": "enum",
  },
  "kotlin": {
    "class_declaration": "class",
    "object_declaration": "class",
    "function_declaration": "function",
  },
  "ruby": {
    "class": "class",
    "module": "module",
    "method": "function",
  },
  "c": {
    "function_definition": "function",
    "struct_specifier": "struct",
    "enum_specifier": "enum",
  },
  "cpp": {
    "function_definition": "function",
    "class_specifier": "class",
    "struct_specifier": "struct",
    "enum_specifier": "enum",
  },
  "csharp": {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "method_declaration": "method",
    "enum_declaration": "enum",
  },
  "swift": {
    "class_declaration": "class",
    "protocol_declaration": "interface",
    "function_declaration": "function",
    "enum_declaration": "enum",
  },
  "php": {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "function_definition": "function",
    "method_declaration": "method",
  },
}

_IMPORT_NODES: dict[str, set[str]] = {
  "python": {"import_statement", "import_from_statement"},
  "typescript": {"import_statement"},
  "javascript": {"import_statement"},
  "go": {"import_spec"},
  "rust": {"use_declaration"},
  "java": {"import_declaration"},
  "kotlin": {"import_header"},
  "ruby": {"call"},  # require/require_relative
  "c": {"preproc_include"},
  "cpp": {"preproc_include"},
  "csharp": {"using_directive"},
  "swift": {"import_declaration"},
  "php": {"namespace_use_declaration"},
}


def _get_name(node, language: str) -> str | None:
  """Extract the name from a definition node."""
  # Try common field names
  for field in ("name", "type"):
    child = node.child_by_field_name(field)
    if child and child.text:
      return child.text.decode("utf-8")

  # Fallback: find first named identifier child
  for child in node.children:
    if child.type in ("identifier", "type_identifier", "constant",
                       "property_identifier", "field_identifier"):
      if child.text:
        return child.text.decode("utf-8")
  return None


def _extract_import(node, language: str) -> str | None:
  """Extract import path from an import node."""
  if language == "python":
    # from X import Y → get X; import X → get X
    module = node.child_by_field_name("module_name")
    if module and module.text:
      return module.text.decode("utf-8")
    name = node.child_by_field_name("name")
    if name and name.text:
      return name.text.decode("utf-8")
    # Fallback: find dotted_name
    for child in node.children:
      if child.type == "dotted_name" and child.text:
        return child.text.decode("utf-8")

  elif language in ("typescript", "javascript"):
    source = node.child_by_field_name("source")
    if source and source.text:
      return source.text.decode("utf-8").strip("'\"")

  elif language == "go":
    path = node.child_by_field_name("path")
    if path and path.text:
      return path.text.decode("utf-8").strip("'\"")

  elif language == "rust":
    arg = node.child_by_field_name("argument")
    if arg and arg.text:
      return arg.text.decode("utf-8")

  elif language == "ruby":
    # require("path") — check method name first
    method = node.child_by_field_name("method")
    if method and method.text and method.text.decode("utf-8") in ("require", "require_relative"):
      args = node.child_by_field_name("arguments")
      if args:
        for child in args.children:
          if child.type == "string" and child.text:
            return child.text.decode("utf-8").strip("'\"")

  # Generic: try to find a string or identifier in the node
  if node.text:
    text = node.text.decode("utf-8")
    # Clean up common patterns
    for prefix in ("import ", "from ", "use ", "require ", "#include ", "using "):
      if text.startswith(prefix):
        text = text[len(prefix):]
    return text.strip().strip("'\";<>")

  return None


def _is_inside_class(node, language: str) -> bool:
  """Check if a node is inside a class body."""
  parent = node.parent
  while parent:
    if parent.type in (
      "class_definition", "class_declaration", "class_body",
      "class_specifier", "impl_item",
    ):
      return True
    parent = parent.parent
  return False


def treesitter_parse_file(path: str, content: str) -> FileInfo:
  """Parse a file using tree-sitter AST walking."""
  language = detect_language(path)
  parser = _get_parser(language)

  tree = parser.parse(content.encode("utf-8"))
  def_nodes = _DEFINITION_NODES.get(language, {})
  imp_node_types = _IMPORT_NODES.get(language, set())

  symbols: list[Symbol] = []
  imports: list[str] = []
  seen: set[tuple[str, int]] = set()

  def walk(node):
    node_type = node.type

    # Check for definition
    if node_type in def_nodes:
      kind = def_nodes[node_type]
      name = _get_name(node, language)
      if name:
        line = node.start_point[0] + 1
        if (name, line) not in seen:
          seen.add((name, line))
          # Upgrade function to method if inside a class
          if kind == "function" and _is_inside_class(node, language):
            kind = "method"
          symbols.append(Symbol(name=name, kind=kind, line=line))

    # Check for import
    if node_type in imp_node_types:
      imp = _extract_import(node, language)
      if imp:
        imports.append(imp)

    # Special: Go struct/interface inside type_declaration
    if language == "go" and node_type == "type_declaration":
      for child in node.children:
        if child.type == "type_spec":
          name_node = child.child_by_field_name("name")
          type_node = child.child_by_field_name("type")
          if name_node and type_node and name_node.text:
            name = name_node.text.decode("utf-8")
            line = child.start_point[0] + 1
            if type_node.type == "struct_type":
              symbols.append(Symbol(name, "struct", line))
            elif type_node.type == "interface_type":
              symbols.append(Symbol(name, "interface", line))
            seen.add((name, line))

    # Recurse into children
    for child in node.children:
      walk(child)

  walk(tree.root_node)

  line_count = content.count("\n") + 1
  return FileInfo(
    path=path, language=language, lines=line_count,
    symbols=symbols, imports=imports,
  )
