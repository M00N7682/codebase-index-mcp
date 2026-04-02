"""Parser dispatcher — tree-sitter first, regex fallback."""

from .models import FileInfo, detect_language


def parse_file(path: str, content: str) -> FileInfo:
  """Parse a file for symbols and imports. Tries tree-sitter, falls back to regex."""
  try:
    from .treesitter_parser import treesitter_parse_file, UnsupportedLanguageError
    try:
      return treesitter_parse_file(path, content)
    except UnsupportedLanguageError:
      pass
  except ImportError:
    pass

  from .regex_parser import regex_parse_file
  return regex_parse_file(path, content)
