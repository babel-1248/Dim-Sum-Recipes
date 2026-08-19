#!/usr/bin/env python3
"""Print Sync US Weather environment configuration as unambiguous JSON."""

import json
import os


VARIABLE_NAMES = (
    "ZIP_CODE",
    "DAYS_AHEAD",
    "QUEUE_FUNCTION_IDS",
    "FEED_ID",
    "SAVE_TO_PROJECT_ID",
)


def main() -> None:
    print(json.dumps({name: os.environ.get(name) for name in VARIABLE_NAMES}))


if __name__ == "__main__":
    main()
