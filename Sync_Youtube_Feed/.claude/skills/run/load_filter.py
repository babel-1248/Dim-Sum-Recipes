#!/usr/bin/env python3
"""Read filter instructions from $FILTER_FILE and print them to stdout.

Exits 0 and prints the file contents if FILTER_FILE is set and non-empty.
Exits 0 and prints nothing if FILTER_FILE is unset or empty.
Exits 1 with an error message on stderr if the file cannot be read.
"""
import os
import sys

path = os.environ.get("FILTER_FILE", "").strip()
if not path:
    sys.exit(0)

try:
    contents = open(path).read().strip()
except OSError as e:
    print(f"Error reading FILTER_FILE '{path}': {e}", file=sys.stderr)
    sys.exit(1)

if contents:
    print(contents)
