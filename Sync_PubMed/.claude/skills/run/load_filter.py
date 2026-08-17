#!/usr/bin/env python3
"""Read PubMed feed instructions from $FILTER_FILE and print them to stdout.

Exit 0 and print the file contents when FILTER_FILE points to a non-empty file.
Exit 0 and print nothing when FILTER_FILE is unset or empty.
Exit 1 with a readable error when the configured file cannot be read.
"""

import os
import sys


path = os.environ.get("FILTER_FILE", "").strip()
if not path:
    sys.exit(0)

try:
    with open(path, encoding="utf-8") as filter_file:
        contents = filter_file.read().strip()
except OSError as exc:
    print(f"Error reading FILTER_FILE '{path}': {exc}", file=sys.stderr)
    sys.exit(1)

if contents:
    print(contents)
