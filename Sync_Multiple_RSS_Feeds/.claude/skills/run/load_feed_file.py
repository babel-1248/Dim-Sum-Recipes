#!/usr/bin/env python3
"""Read a feed source file without interpreting its contents.

Usage:
  python3 load_feed_file.py          # Read the path in $FEED_FILE
  python3 load_feed_file.py <path>   # Read a file referenced by instructions

Prints one JSON object containing the resolved source_path and the file's raw
text contents. Feed-list interpretation is intentionally left to the LLM.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import NoReturn


def fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if len(sys.argv) > 2:
        fail(__doc__.strip())

    configured_path = sys.argv[1] if len(sys.argv) == 2 else os.environ.get("FEED_FILE", "")
    configured_path = configured_path.strip()
    if not configured_path:
        fail("FEED_FILE environment variable is not set.")

    path = Path(configured_path).expanduser()
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except UnicodeError as error:
        fail(f"Feed file is not valid UTF-8 text '{path}': {error}")
    except OSError as error:
        fail(f"Unable to read feed file '{path}': {error}")

    if not contents.strip():
        fail(f"Feed file is empty: {path}")

    print(
        json.dumps(
            {"source_path": str(path.resolve()), "contents": contents},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
