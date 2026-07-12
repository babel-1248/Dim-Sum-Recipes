#!/usr/bin/env python3
"""Mark one RSS article as seen using an atomic state-file update.

Usage: python3 mark_seen.py <state_file> <feed_url> <article_id>
"""

import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


def load_state(state_path: Path) -> dict:
    try:
        with state_path.open() as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return {}

    if not isinstance(state, dict):
        raise ValueError(f"State file must contain a JSON object: {state_path}")
    return state


def save_state_atomically(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = (
        stat.S_IMODE(state_path.stat().st_mode) if state_path.exists() else 0o644
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=state_path.parent,
        prefix=f".{state_path.name}.",
        suffix=".tmp",
    )

    try:
        with os.fdopen(file_descriptor, "w") as temporary_file:
            json.dump(state, temporary_file, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_name, existing_mode)
        os.replace(temporary_name, state_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    if len(sys.argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        raise SystemExit(2)

    state_path = Path(sys.argv[1])
    feed_url = sys.argv[2]
    article_id = sys.argv[3]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")

    # Keep the lock across the complete read-modify-write transaction. Atomic
    # replacement protects readers from partial JSON; this lock additionally
    # prevents concurrent writers from replacing one another's updates.
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        state = load_state(state_path)
        seen = state.setdefault(feed_url, [])

        if not isinstance(seen, list):
            raise ValueError(f"State entry for {feed_url!r} must be a JSON array")
        if article_id not in seen:
            seen.append(article_id)
            save_state_atomically(state_path, state)


if __name__ == "__main__":
    main()
