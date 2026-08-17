# Sync PubMed Recipe

A Claude Code and Codex recipe that syncs new PubMed citations into Pachinko notes using optional filter instructions.

## Usage

Say **"run"** to execute the full pipeline:

1. Load the optional filter instructions from `FILTER_FILE`
2. Fetch PubMed metadata for the resolved date window and Entrez query
3. Apply relevance filtering and the resolved note limit
4. Convert matching records to Markdown and add every selected paper to Pachinko in checkpointed batches
5. Update state so previously saved PMIDs are not duplicated

## Setup

1. Optionally set `FILTER_FILE` to a plain-text file containing PubMed search and filtering instructions
2. Optionally set `NCBI_API_KEY` and `NCBI_EMAIL` for identified, higher-rate NCBI requests
3. Open this project in Claude Code or Codex
4. Say "run" to start

## Skills included

| Skill | Description |
| --- | --- |
| `run` | Full pipeline: load filter → fetch → filter → limit → convert → add to Pachinko |

## State

The feed watermark and saved PMIDs are tracked under `.feed-state/` in the project root. Each successfully created note is checkpointed immediately, so restarting after a partial note-creation run skips notes already saved. When the filter supplies no time range, the recipe resumes from its stored watermark; the first run starts seven days before the run date.

The recipe creates at most 20 notes per run after time-range, seen-state, and relevance filtering by default. `FILTER_FILE` may explicitly set another positive limit or request no limit. Records beyond a numeric limit remain eligible for the next run.

## Environment Configuration

At the start of every execution, run `.claude/scripts/read_environment.py` exactly once. Parse its JSON output and use it as the only source for these values throughout the execution:

- `QUEUE_FUNCTION_IDS`
- `FEED_ID`
- `SAVE_TO_PROJECT_ID`

The script returns JSON `null` when a variable is not set and an empty string (`""`) when it is set but empty. Do not read these environment variables directly with `echo`, `printenv`, or any other command. Do not use one value for another key.

`NCBI_API_KEY` and `NCBI_EMAIL` are consumed directly by the bundled PubMed scripts and are not part of the wrapper values above.

## Post-Execution: Queue Function

After every execution — including simple responses — always perform the following steps:

1. Read `QUEUE_FUNCTION_IDS` from the JSON captured at the start of the execution. If it is `null` or `""`, stop here.
2. Read `FEED_ID` from that same JSON object. Pass only this value as the `feed_id` parameter when calling `mcp__pachinko__queue_function`.
3. Parse `QUEUE_FUNCTION_IDS` as a comma-separated list of function IDs (e.g. `function-a,function-b`).
4. Collect every note that was newly created during this execution.
5. If no notes were newly created during this execution, stop here. Do not call `mcp__pachinko__queue_function` with an empty note list.
6. For each function ID in the list, call `mcp__pachinko__queue_function` with that function ID, the `feed_id` value, and the list of new note IDs.
   - If there are 10 or fewer new notes, queue all of them in a single call per function.
   - If there are more than 10 new notes, queue them in batches of 10 per call — each function receives one call per batch until all notes are queued.
7. Before finishing, verify that each function ID received the complete set of newly created note IDs. Retry a failed queue call when appropriate; otherwise report the specific failed function and note-ID batch. Never silently stop after the first queue batch.

## Creating Notes

Before creating any notes, read `SAVE_TO_PROJECT_ID` from the JSON captured at the start of the execution. If it is neither `null` nor `""`, create notes in that project. If that project is not found, revert back to inbox. If it is `null` or `""`, create notes in the inbox by default.
