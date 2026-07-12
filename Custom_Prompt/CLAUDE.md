# Custom Prompt Recipe

A Claude Code recipe that runs a custom prompt with the Pachinko MCP server enabled.

## Environment Configuration

At the start of every execution, run `.claude/scripts/read_environment.py` exactly once. Parse its JSON output and use it as the only source for these values throughout the execution:

- `QUEUE_FUNCTION_IDS`
- `FEED_ID`
- `SAVE_TO_PROJECT_ID`

The script returns JSON `null` when a variable is not set and an empty string (`""`) when it is set but empty. Do not read these environment variables directly with `echo`, `printenv`, or any other command. Do not use one value for another key.

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

## Creating Notes

Before creating any notes, read `SAVE_TO_PROJECT_ID` from the JSON captured at the start of the execution. If it is neither `null` nor `""`, create notes in that project. If that project is not found, revert back to inbox. If it is `null` or `""`, create notes in the inbox by default.
