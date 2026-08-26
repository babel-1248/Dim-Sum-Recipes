# Sync FRED Economic Indicators Recipe

A Claude Code and Codex recipe that turns a Markdown indicator checklist into deduplicated FRED economic snapshots in Pachinko.

## Usage

Say **"run"** to execute the full pipeline:

1. Read and validate `FRED_API_KEY` and the optional `FILTER_FILE` checklist
2. Group checked indicators by their internal FRED release IDs
3. Fetch every required Version 2 release with cursor pagination
4. Render one consolidated Markdown snapshot
5. Add a changed snapshot to Pachinko and checkpoint it after success

## Setup

1. Set the required `FRED_API_KEY`
2. Optionally copy and edit `fred-indicators-filter.md`, then select that Markdown file as `FILTER_FILE`
3. Open this project in Claude Code or Codex
4. Say "run"

Without a configured checklist, the recipe includes all 14 bundled indicators and the latest 13 observations for each. This provides broad current coverage and a compact trend view rather than a full-history dump.

## State

Created snapshot IDs are stored under `.feed-state/fred-economic-indicators.json`, scoped by Pachinko destination. Snapshot IDs are derived from the rendered title and body, so unchanged output is not recreated. State advances only after Pachinko returns a note ID.

## Environment Configuration

At the start of every execution, run `.claude/scripts/read_environment.py` exactly once. Parse its JSON output and use it as the only source for these values throughout the execution:

- `QUEUE_FUNCTION_IDS`
- `FEED_ID`
- `SAVE_TO_PROJECT_ID`

The script returns JSON `null` when a variable is not set and an empty string (`""`) when it is set but empty. Do not read these environment variables directly with `echo`, `printenv`, or any other command. Do not use one value for another key.

`FRED_API_KEY` is consumed directly by the bundled configuration and HTTPS client and must never be printed or persisted. `FILTER_FILE` is consumed directly and exactly once by the bundled configuration script.

## Post-Execution: Queue Function

After every execution — including simple responses — always perform the following steps:

1. Read `QUEUE_FUNCTION_IDS` from the JSON captured at startup. If it is `null` or `""`, stop here.
2. Read `FEED_ID` from that same JSON object. Pass only this value as `feed_id` when calling `mcp__pachinko__queue_function`.
3. Parse `QUEUE_FUNCTION_IDS` as a comma-separated list of function IDs.
4. Collect every note newly created during this execution.
5. If no notes were created, do not call `mcp__pachinko__queue_function`.
6. For each function ID, queue all new note IDs in batches of at most 10.
7. Verify that every function received every new note ID. Retry an appropriate transient failure; otherwise report the failed function and note-ID batch.

## Creating Notes

Before creating a note, read `SAVE_TO_PROJECT_ID` from the startup JSON. If it is neither `null` nor `""`, create the note in that project. If that project is not found, use the inbox and use `default` as the state destination. If `SAVE_TO_PROJECT_ID` is `null` or `""`, use the inbox and the `default` state destination.

Never list, edit, replace, or delete older FRED notes. State-based snapshot IDs are authoritative for deduplication.
