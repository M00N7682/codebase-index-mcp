"""Hook entry point — outputs compact project map to stdout for Claude Code injection."""

import os
import sys


def main():
  """
  Called by Claude Code's UserPromptSubmit hook.
  Stdout is injected into Claude's conversation context.
  """
  # Silence all warnings/logs — only the map should go to stdout
  import warnings
  warnings.filterwarnings("ignore")
  import logging
  logging.disable(logging.CRITICAL)

  from .compact_map import ensure_and_generate

  cwd = os.environ.get("PROJECT_DIR") or os.getcwd()
  result = ensure_and_generate(cwd)
  if result:
    print(result)


if __name__ == "__main__":
  main()
