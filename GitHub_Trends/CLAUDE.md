# GitHub Trends Recipe

A Claude Code and Codex recipe that syncs repository cards from GitHub Trending into Pachinko notes.

## Usage

Say **"run"** to execute the full pipeline:

1. Validate `DATE_RANGE`, `LANGUAGE`, and `SPOKEN_LANGUAGE`
2. Fetch the matching GitHub Trending repositories page
3. Remove repositories already saved for this exact filter combination
4. Convert each fresh repository card to a Markdown note
5. Add every note to Pachinko in checkpointed batches

The recipe never processes GitHub Trending developers.

## Setup

1. Optionally set `DATE_RANGE` to `daily`, `weekly`, or `monthly`; it defaults to `daily`
2. Optionally set `LANGUAGE` to a programming-language name such as `Python` or `C++`
3. Optionally set `SPOKEN_LANGUAGE` to a spoken-language name such as `English`
4. Open this project in Claude Code or Codex
5. Say "run"

## State

Saved repositories are tracked under `.feed-state/`, independently for each canonical combination of date range, programming language, and spoken language. Each successful note is checkpointed immediately, so a partial run can resume without recreating completed notes.

## Environment Configuration

At the start of every execution, run `.claude/scripts/read_environment.py` exactly once. Parse its JSON output and use it as the only source for these values throughout the execution:

- `QUEUE_FUNCTION_IDS`
- `FEED_ID`
- `SAVE_TO_PROJECT_ID`

The script returns JSON `null` when a variable is not set and an empty string (`""`) when it is set but empty. Do not read these environment variables directly with `echo`, `printenv`, or any other command. Do not use one value for another key.

The recipe parameters `DATE_RANGE`, `LANGUAGE`, and `SPOKEN_LANGUAGE` are read exactly once by the bundled configuration script.

## Post-Execution: Queue Function

After every execution — including simple responses — always perform the following steps:

1. Read `QUEUE_FUNCTION_IDS` from the JSON captured at startup. If it is `null` or `""`, stop here.
2. Read `FEED_ID` from the same JSON object. Pass only this value as `feed_id` when calling `mcp__pachinko__queue_function`.
3. Parse `QUEUE_FUNCTION_IDS` as a comma-separated list of function IDs.
4. Collect every note newly created during this execution.
5. If no notes were created, do not call `mcp__pachinko__queue_function`.
6. For each function ID, queue all new note IDs in batches of at most 10.
7. Verify that every function received every new note ID. Retry an appropriate transient failure; otherwise report the failed function and note-ID batch.

## Creating Notes

Before creating notes, read `SAVE_TO_PROJECT_ID` from the JSON captured at startup. If it is neither `null` nor `""`, create notes in that project. If that project is not found, use the inbox. If it is `null` or `""`, use the inbox.
