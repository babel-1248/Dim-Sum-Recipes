# Sync US Weather Recipe

A Claude Code and Codex recipe that creates a new Pachinko note with hourly weather and US air-quality data for a configured US ZIP code.

## Usage

Say **"run"** to execute the full pipeline:

1. Load and validate the required `ZIP_CODE`
2. Derive the window from the last successful sync through the configured number of days ahead
3. Render one complete hourly Markdown table
4. Create a new note and then record its successful sync date
5. Queue the newly created note for configured post-processing functions

## Setup

1. Set `ZIP_CODE` to one five-digit US ZIP code
2. Optionally set `DAYS_AHEAD` from 0 through 6; when omitted or empty it defaults to 1 and includes tomorrow
3. Open this project in Claude Code or Codex
4. Say "run" to start

## Skills included

| Skill | Description |
| --- | --- |
| `run` | Fetch hourly weather and AQI, create a new note, and record its successful sync date |

## State

Successful-run state is tracked under `.feed-state/weather.json`, keyed by destination and ZIP. The recipe writes only the sync date, returned note ID, and record timestamp after Pachinko creates the note. Fetching and rendering do not advance state. If note creation fails—or execution stops before success is recorded—the prior successful date remains the basis for the next run.

ZIP geocoding is cached under `.weather-cache/zip_geo.json`. The cache avoids repeatedly sending the same ZIP to the geocoding provider.

## Environment Configuration

At the start of every execution, run `.claude/scripts/read_environment.py` exactly once. Parse its JSON output and use it as the only source for these values throughout the execution:

- `ZIP_CODE`
- `DAYS_AHEAD`
- `QUEUE_FUNCTION_IDS`
- `FEED_ID`
- `SAVE_TO_PROJECT_ID`

The script returns JSON `null` when a variable is not set and an empty string (`""`) when it is set but empty. Do not read these environment variables directly with `echo`, `printenv`, or any other command. Do not use one value for another key.

## Post-Execution: Queue Function

After every execution — including simple responses — always perform the following steps:

1. Read `QUEUE_FUNCTION_IDS` from the JSON captured at startup. If it is `null` or `""`, stop here.
2. Read `FEED_ID` from that same JSON object. Pass only this value as the `feed_id` parameter when calling `mcp__pachinko__queue_function`.
3. Parse `QUEUE_FUNCTION_IDS` as a comma-separated list of function IDs.
4. Collect every note successfully created during this execution.
5. If no notes qualify, stop here. Never queue an empty note list.
6. For each function ID, queue the complete note-ID list. Use batches of 10 when there are more than 10 notes.
7. Verify that each function ID received every note ID. Retry appropriate transient failures and report any unresolved function and batch.

## Creating Notes

Read `SAVE_TO_PROJECT_ID` from the startup JSON before creating a weather note. If it is neither `null` nor `""`, use that project consistently. If it is missing, empty, or not found, use Pachinko's default destination without substituting a hardcoded project name. Never list, edit, replace, or delete older weather notes.
