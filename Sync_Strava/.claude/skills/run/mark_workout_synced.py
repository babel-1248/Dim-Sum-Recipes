#!/usr/bin/env python3
"""Atomically record a successfully saved Strava workout.

Usage: mark_workout_synced.py <activity_id> <pachinko_note_id>
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


STATE_PATH = Path("strava_sync_state.json")


def load_state() -> dict:
    try:
        with STATE_PATH.open() as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return {"version": 1, "synced_workouts": {}}

    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("state must be a version 1 JSON object")
    synced_workouts = state.get("synced_workouts")
    if not isinstance(synced_workouts, dict):
        raise ValueError("synced_workouts must be a JSON object")
    for activity_id, record in synced_workouts.items():
        if not isinstance(activity_id, str) or not activity_id.isdigit():
            raise ValueError("every synced_workouts key must be a numeric activity ID")
        if not isinstance(record, dict):
            raise ValueError(f"record for activity {activity_id} must be a JSON object")
        if not isinstance(record.get("pachinko_note_id"), str) or not record["pachinko_note_id"]:
            raise ValueError(f"record for activity {activity_id} must contain a Pachinko note ID")
        if not isinstance(record.get("synced_at"), str) or not record["synced_at"]:
            raise ValueError(f"record for activity {activity_id} must contain a sync timestamp")
    return state


def save_state_atomically(state: dict) -> None:
    existing_mode = stat.S_IMODE(STATE_PATH.stat().st_mode) if STATE_PATH.exists() else 0o644
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=STATE_PATH.parent,
        prefix=f".{STATE_PATH.name}.",
        suffix=".tmp",
    )

    try:
        with os.fdopen(file_descriptor, "w") as temporary_file:
            json.dump(state, temporary_file, indent=2, sort_keys=True)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_name, existing_mode)
        os.replace(temporary_name, STATE_PATH)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        raise SystemExit(2)

    activity_id = sys.argv[1]
    pachinko_note_id = sys.argv[2]
    if not activity_id.isdigit():
        print("activity_id must contain only digits", file=sys.stderr)
        raise SystemExit(2)
    if not pachinko_note_id:
        print("pachinko_note_id must not be empty", file=sys.stderr)
        raise SystemExit(2)

    try:
        state = load_state()
        existing_record = state["synced_workouts"].get(activity_id)
        if existing_record is not None:
            if existing_record["pachinko_note_id"] != pachinko_note_id:
                raise ValueError(
                    f"activity {activity_id} is already linked to Pachinko note "
                    f"{existing_record['pachinko_note_id']}"
                )
            return
        state["synced_workouts"][activity_id] = {
            "pachinko_note_id": pachinko_note_id,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        save_state_atomically(state)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Unable to update {STATE_PATH}: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
