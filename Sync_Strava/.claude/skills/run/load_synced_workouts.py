#!/usr/bin/env python3
"""Print validated Strava sync state from the project-local state file."""

import json
from pathlib import Path
import sys


STATE_PATH = Path("strava_sync_state.json")
EMPTY_STATE = {"version": 1, "synced_workouts": {}}


def load_state() -> dict:
    try:
        with STATE_PATH.open() as state_file:
            state = json.load(state_file)
    except FileNotFoundError:
        return EMPTY_STATE

    if not isinstance(state, dict):
        raise ValueError("state must be a JSON object")
    if state.get("version") != 1:
        raise ValueError("state version must be 1")

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


def main() -> None:
    try:
        print(json.dumps(load_state(), sort_keys=True))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Invalid {STATE_PATH}: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
