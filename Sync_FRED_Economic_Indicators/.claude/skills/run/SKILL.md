---
name: run
description: Run the Sync FRED Economic Indicators recipe. Validate a FRED Version 2 key and optional Markdown checklist, fetch the required releases, render a consolidated economic snapshot, add changed output to Pachinko, and checkpoint successful creation. Use when the user says "run" in this recipe.
---

**Never use the Agent tool. Do not spawn sub-agents or background workers at any point during this skill.**

# Sync FRED Economic Indicators

Run the workflow non-interactively. Work in the session scratchpad (`$SCRATCH`) and invoke scripts through project-relative paths under `./.claude/skills/run/scripts/`. Never print, echo, inspect, or place `FRED_API_KEY` in a command argument or URL. The bundled scripts consume it directly from the environment and redact it from all output.

## Invariants

- Checked checklist items are included; unchecked items are ignored.
- FRED release IDs and series IDs are internal public mappings, never required user input.
- An unset or empty `FILTER_FILE` uses the documented defaults.
- Fetch releases sequentially and never parallelize or run concurrent copies to increase throughput.
- Create at most one consolidated snapshot note per run.
- Never list, edit, replace, or delete earlier FRED notes.
- Never checkpoint before `add_note` returns a non-empty note ID.

## Step 0 — Capture Pachinko routing

Follow **Environment Configuration** in `./CLAUDE.md`: run `./.claude/scripts/read_environment.py` exactly once, parse its JSON, and retain `QUEUE_FUNCTION_IDS`, `FEED_ID`, and `SAVE_TO_PROJECT_ID` for the entire execution.

## Step 1 — Resolve the checklist

Run:

```bash
python3 ./.claude/skills/run/scripts/config.py \
  --out "$SCRATCH/fred-economic-indicators/config.json"
```

The script validates only the API key's required shape; it never writes or prints the key. It reads `FILTER_FILE` exactly once. If the file is unset or empty, it selects all 14 bundled indicators and the last-13-observations history mode. This is the broad but compact default: current data and trend context without a full-history dump.

For a configured checklist, the script includes checked (`- [x]`) indicators, ignores unchecked (`- [ ]`) indicators, and takes the topmost checked option in the **History — pick one** section. If no history option is checked, it uses the default last 13 observations. Stop before any provider request if configuration validation fails. Report any warning about multiple checked history choices.

Echo the resolved indicator labels and history mode without waiting for confirmation. Never reinterpret or silently repair an unknown checked option.

## Step 2 — Fetch selected FRED releases

Run:

```bash
python3 ./.claude/skills/run/scripts/fetch.py \
  --config "$SCRATCH/fred-economic-indicators/config.json" \
  --out "$SCRATCH/fred-economic-indicators/fetched"
```

The fetcher groups indicators by release, uses the Version 2 bearer-token header, follows `next_cursor` pagination, and writes only selected public series data to `manifest.json`. It does not write the API key or complete unselected release payloads. Require the summary's selected-series count to equal the resolved indicator count and stop with state untouched on any missing series, wrong release, pagination error, authentication failure, or incomplete response.

## Step 3 — Render the consolidated snapshot

Run:

```bash
python3 ./.claude/skills/run/scripts/convert.py \
  --manifest "$SCRATCH/fred-economic-indicators/fetched/manifest.json" \
  --out "$SCRATCH/fred-economic-indicators/notes"
```

Require exactly one `index.json` entry with a non-empty `id`, `note_title`, absolute existing `file`, and `source_url`. The entry ID is a hash of the rendered title and body; unchanged output therefore produces the same ID. The note omits missing `.` values from calculations and includes source, release, units, frequency, seasonal adjustment, copyright, and FRED links.

## Step 4 — Resolve destination and pending state

Follow **Creating Notes** in `./CLAUDE.md`. Use the resolved Pachinko project ID as `DESTINATION_TOKEN`; if the project is absent or the inbox/default destination is used, set `DESTINATION_TOKEN=default`.

Run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py pending \
  --destination "$DESTINATION_TOKEN" \
  --index "$SCRATCH/fred-economic-indicators/notes/index.json"
```

If `terminal` is true and `remaining` is zero, report that the selected snapshot is unchanged and skip note creation. Otherwise require exactly one entry in `batch`.

## Step 5 — Create and checkpoint the note

For the pending entry:

1. Call Pachinko `add_note` with its `note_title` and `note_body_file_path` set to its absolute `file`. Use the resolved destination. Never pass the Markdown through `note_body`.
2. Retry a transient failure up to three total attempts. On permanent failure or after the third failure, stop without changing state; the snapshot remains eligible next run.
3. Immediately after success, checkpoint it:

   ```bash
   python3 ./.claude/skills/run/scripts/feedstate.py mark-seen \
     --destination "$DESTINATION_TOKEN" \
     --id "$SNAPSHOT_ID" --note-id "$NEW_NOTE_ID"
   ```

   Retry a transient checkpoint failure up to three total attempts. If it cannot be verified, stop and report the created note ID and snapshot ID so a possible duplicate is explicit.
4. Run `pending` again and require `terminal: true`, `remaining: 0`, and `checkpointed: 1`.
5. Call `set_note_source` with `source_type: "webpage"`. Retry a transient failure up to three total attempts, then report the warning and continue; the note is already checkpointed and contains its FRED links.
6. Collect the new note ID for the queue workflow in `./CLAUDE.md`.

## Step 6 — Report and queue

Report the checklist source (defaults or configured), selected indicator labels, history mode, release/request/page counts, latest observation-through date, snapshot ID, destination mode, and whether a note was created or skipped as unchanged. Report provider, source-metadata, state, and queue warnings explicitly.

Then execute **Post-Execution: Queue Function** from `./CLAUDE.md`. Queue only the note created during this execution and verify delivery to every configured function.

## Provider constraints

The HTTPS client allows only `api.stlouisfed.org`, sends the key only as `Authorization: Bearer …`, reads bounded JSON responses, follows redirects only to that host, and makes requests sequentially at least 0.55 seconds apart. It honors `Retry-After` and backs off after HTTP 429 and transient server/network failures. FRED Version 2 currently permits up to two requests per second.

FRED can split a series across cursor pages. The fetcher merges repeated series metadata and observations and rejects conflicting values. It stops if cursors repeat, a selected series is absent, or the returned release ID/name does not match the internal mapping.

State is stored in `<project>/.feed-state/fred-economic-indicators.json`, keyed by destination. Override the state directory with `FRED_INDICATORS_STATE` or the project root with `FRED_INDICATORS_PROJECT_DIR`.
