---
name: run
description: Run the Sync US Weather recipe. Validate ZIP_CODE and optional DAYS_AHEAD, derive a date window from the last successfully created note, fetch hourly weather and US AQI, create a new Pachinko note, record success, and queue the note for configured post-processing. Use when the user says "run" in this recipe.
---

**Never use the Agent tool. Do not spawn sub-agents or background workers at any point during this skill.**

# Sync US Weather

Run the workflow non-interactively. Work in `$SCRATCH` and invoke scripts through
project-relative paths under `./.claude/skills/run/`. Never parallelize provider
requests.

This feed is append-only. Create one new note on every run. Never list, search,
edit, replace, or delete an existing weather note. Existing notes do not affect
the workflow; only the recorded last-successful sync date affects the window.

## Step 0 — Resolve configuration

Use the JSON captured by `./.claude/scripts/read_environment.py` at startup.
Read `ZIP_CODE` only from that object. It is required and must match exactly
five ASCII digits; preserve leading zeroes. If it is `null`, empty, or invalid,
report the configuration error and stop before making network or Pachinko calls.

Read `DAYS_AHEAD` only from the same captured object. If it is `null` or empty,
use `1`; this is the documented default and means the note includes tomorrow.
Otherwise require an ASCII integer from `0` through `6`, inclusive. `0` ends
the window on today. Report an invalid value and stop before network or Pachinko
calls. Preserve the validated integer as `DAYS_AHEAD` for the entire run.

Follow **Creating Notes** in `./CLAUDE.md` to resolve the destination from the
captured `SAVE_TO_PROJECT_ID`. Use the resolved project ID as the destination
state token, or `default` when Pachinko's default destination is used. Never
choose or hardcode a destination inside this skill.

Use Fahrenheit, mph, inches, US AQI, and PM2.5.

## Step 1 — Derive the date window

Run:

```bash
python3 ./.claude/skills/run/scripts/syncstate.py window \
  --destination "$DESTINATION_TOKEN" --zip "$ZIP_CODE" \
  --days-ahead "$DAYS_AHEAD"
```

Capture its JSON as the authoritative `TODAY`, `START_DATE`, and `END_DATE`.
On a first run, `START_DATE` is seven days before `TODAY`. On later runs it is
the last successfully recorded sync date, but never earlier than seven days
before `TODAY`. `END_DATE` is `DAYS_AHEAD` days after `TODAY`. All dates are
inclusive, so the default first run contains nine calendar dates: seven days
back, today, and tomorrow. Never calculate or widen the window independently.

This command is read-only and must not advance the successful-run state.

## Step 2 — Fetch and render

Run:

```bash
python3 ./.claude/skills/run/scripts/fetch.py "$ZIP_CODE" \
  --start "$START_DATE" --end "$END_DATE" --out "$SCRATCH/us-weather"
```

Capture the printed JSON and read the generated `index.json` and
`manifest.json`. Require exactly one index entry whose `zip` equals the
configured ZIP, whose `file` exists, whose `window_start` and `window_end`
equal the requested dates, and whose `note_title` is non-empty. Stop without
changing success state if fetching, geocoding, rendering, or validation fails.

The generated title always contains the inclusive range:

`US Weather — ZIP (City, ST) — YYYY-MM-DD to YYYY-MM-DD`

The note body contains one hourly row for the complete window and includes
source attribution. Never paste the table into a tool parameter; use its file
path.

## Step 3 — Create the new note, then record success

1. Call Pachinko `add_note` with the index entry's `note_title` and
   `note_body_file_path` set to its `file`. Use the resolved destination. Retry
   transient failures up to three total attempts.
2. If `add_note` does not return a non-empty new note ID, stop. Do not call
   `record-success`, do not advance the date, and do not queue a note.
3. Only after successful note creation, record that success:

   ```bash
   python3 ./.claude/skills/run/scripts/syncstate.py record-success \
     --destination "$DESTINATION_TOKEN" --zip "$ZIP_CODE" \
     --sync-date "$TODAY" --note-id "$NEW_NOTE_ID"
   ```

   Retry a transient state-write failure up to three total attempts. The command
   is idempotent for the same values. Verify with `syncstate.py show` that the
   destination-and-ZIP entry contains the returned note ID and `TODAY`. If
   recording cannot be verified, report the created note ID and
   state failure; the prior successful date remains authoritative for the next
   run.
4. Call `set_note_source` for the new note with `source_type: "webpage"`.
   Retry transient failures up to three total attempts, then report the metadata
   warning and continue; the note was still successfully created.
5. Collect the new note ID for the post-execution queue workflow in
   `./CLAUDE.md`.

Never record success before `add_note` returns the new note ID. Never inspect or
remove older weather notes. If execution stops after note creation but before
the state write, the next run intentionally reuses the prior successful date.

## Step 4 — Report

Report the ZIP, resolved city/state, generated hour count, inclusive date
window, configured days ahead, destination mode, new note ID, and whether this
was the first recorded run. Surface hours with AQI above 150 and any fetch,
state, source metadata, or queue warnings.

## State commands

State is stored in `<project>/.feed-state/weather.json`, keyed by destination
and ZIP. Mutations use an exclusive `flock`, an atomic file swap, and an fsync.

```bash
python3 ./.claude/skills/run/scripts/syncstate.py show
python3 ./.claude/skills/run/scripts/syncstate.py window \
  --destination "$DESTINATION_TOKEN" --zip "$ZIP_CODE" --days-ahead 1
python3 ./.claude/skills/run/scripts/syncstate.py reset
```

The state contains only successful creation records.

ZIP coordinates are cached in `<project>/.weather-cache/zip_geo.json`. Override
the state directory with `RESEARCH_FEED_STATE`, the project root with
`RESEARCH_PROJECT_DIR`, or the geocode cache with `WEATHER_FEED_CACHE`.

## Provider constraints

The Python client allows only HTTPS requests to the two Open-Meteo hosts and
Zippopotam.us, follows redirects only to those hosts, issues requests
sequentially, waits at least 0.11 seconds between them, honors `Retry-After`, and
backs off after transient errors. Never parallelize calls or run concurrent
weather syncs to increase throughput.

Each uncached refresh makes one Zippopotam request and two Open-Meteo requests;
later refreshes make only the two Open-Meteo requests. Open-Meteo's free tier is
non-commercial and currently limited to fewer than 600 calls/minute, 5,000/hour,
10,000/day, and 300,000/month. Zippopotam.us does not publish a numeric limit;
cache results and keep traffic minimal.
