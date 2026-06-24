# Custom Prompt Recipe

A Claude Code recipe that runs a custom prompt with the Pachinko MCP server enabled.

## Post-Execution: Queue Function

After every execution — including simple responses — always perform the following steps:

**Important:** Run these commands using `echo "$VAR"` exactly as written. Do not substitute `printenv` or other alternatives.

1. Run `echo "$QUEUE_FUNCTION_IDS"` in bash to read the environment variable value.
2. If the output is empty, stop here.
3. Run `echo "$FEED_ID"` in bash to read the environment variable value. Pass this value as the `feed_id` parameter when calling `mcp__pachinko__queue_function`.
4. Parse the output as a comma-separated list of function IDs (e.g. `function-a,function-b`).
5. Collect every note that was newly created during this execution.
6. If no notes were newly created during this execution, stop here. Do not call `mcp__pachinko__queue_function` with an empty note list.
7. For each function ID in the list, call `mcp__pachinko__queue_function` with that function ID, the `feed_id` value, and the list of new note IDs.
   - If there are 10 or fewer new notes, queue all of them in a single call per function.
   - If there are more than 10 new notes, queue them in batches of 10 per call — each function receives one call per batch until all notes are queued.

## Creating Notes

Before creating any notes, run `echo "$SAVE_TO_PROJECT_ID"` in bash to read the environment variable value. If it is set, create notes in that project. If that project is not found, revert back to inbox. If it is not set, create notes in the inbox by default.

**Important:** Use `echo "$SAVE_TO_PROJECT_ID"` exactly as written. Do not substitute `printenv` or other alternatives.
