#!/usr/bin/env python3
"""Print the Custom Prompt environment configuration as unambiguous JSON."""

import json
import os


VARIABLE_NAMES = (
    "QUEUE_FUNCTION_IDS",
    "FEED_ID",
    "SAVE_TO_PROJECT_ID",
)


def main() -> None:
    values = {name: os.environ.get(name) for name in VARIABLE_NAMES}
    print(json.dumps(values))


if __name__ == "__main__":
    main()
