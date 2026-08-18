---
name: run
description: Run the Sync GitHub Trending recipe. Validate the configured rolling date range, programming language, and spoken language; fetch repository cards from github.com/trending; add fresh repositories to Pachinko as checkpointed Markdown notes; and update per-filter deduplication state. Use when the user says "run" in this recipe.
---

**Never use the Agent tool. Do not spawn sub-agents or background workers at any point during this skill.**

# Sync GitHub Trending

Run the entire workflow non-interactively. Apply the defaults below, echo the
resolved configuration, and continue without asking for confirmation. Work in
the session scratchpad (`$SCRATCH`). Invoke every script through its
project-relative path under `./.claude/skills/run/scripts/` so project-local
rules apply.

This recipe supports Trending repositories only. Never request, follow, parse,
or create notes from `/trending/developers`.

## Safe defaults and invariants

- `DATE_RANGE` defaults to `daily`; the only valid values are `daily`,
  `weekly`, and `monthly`.
- An unset, empty, `any`, `all`, or `*` value for `LANGUAGE` or
  `SPOKEN_LANGUAGE` means no filter.
- Process every repository card returned by the configured page. There is no
  hidden note limit.
- Preserve GitHub's ranking order.
- Deduplicate repositories by case-insensitive `owner/repository` within the
  exact canonical filter URL. Changing filters creates an independent state
  scope.
- Never delete a note. Report a suspected duplicate and leave it in place.
- Never advance state after an invalid configuration, unexpected page, parser
  failure, or incomplete note queue.

## Step 0 — Load recipe parameters exactly once

Run:

```bash
python3 ./.claude/skills/run/scripts/config.py
```

Capture and parse the JSON output. Treat it as the only source for
`DATE_RANGE`, `LANGUAGE`, and `SPOKEN_LANGUAGE` throughout the run. Do not read
those variables directly with `echo`, `printenv`, or another command.

If configuration validation fails, report the error and stop before making a
network request or changing state.

## Step 1 — Fetch GitHub Trending repositories

Build the command from the captured configuration:

```bash
python3 ./.claude/skills/run/scripts/fetch.py \
  --date-range "$DATE_RANGE" \
  [--language "$LANGUAGE"] \
  [--spoken-language "$SPOKEN_LANGUAGE"] \
  --out "$SCRATCH/github-trending"
```

Omit an optional argument when its JSON value is `null`. Capture the JSON
summary, especially `source`, which is the canonical state scope and the exact
Trending repository page represented by the notes.

The fetcher first reads GitHub's current repository filter menus. It accepts a
programming-language display name or path slug and a spoken-language display
name or code. It then requests the resolved URL when different from the
discovery page. Requests are sequential and retry transient failures with
backoff.

The parser verifies all of the following before writing its manifest:

- the repositories tab, not developers, is selected;
- repository IDs are unique;
- every card has an `owner/repository` link;
- each card's rolling star label matches the requested date range; and
- a zero-card result includes GitHub's explicit empty-state message.

If any check fails, report it and stop. Do not reinterpret a failed or changed
page manually and do not edit the script during a run.

## Step 2 — Remove previously saved repositories

Use the canonical `source` URL from Step 1 as `SCOPE`:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py filter-seen \
  --scope "$SCOPE" \
  --ids-file "$SCRATCH/github-trending/all_ids.txt" \
  --out "$SCRATCH/github-trending/fresh.txt"
```

Capture the fetched, fresh, and already-seen counts. State-based deduplication
is authoritative; Pachinko note listings are not guaranteed to contain every
note.

## Step 3 — Convert fresh repository cards

Run:

```bash
python3 ./.claude/skills/run/scripts/convert.py \
  --manifest "$SCRATCH/github-trending/manifest.json" \
  --keep "$SCRATCH/github-trending/fresh.txt" \
  --out "$SCRATCH/github-trending/notes"
```

The converter creates one Markdown file per fresh repository and an
`index.json` work queue. Each note contains only the point-in-time Trending card
information: rank, configured filters, description, primary language, total
stars, forks, rolling-window stars, contributors, snapshot time, and source
links. It does not crawl README files or other repository content.

## Step 4 — Create Pachinko notes

Before creating notes, follow **Creating Notes** in `./CLAUDE.md` to resolve the
destination from the `SAVE_TO_PROJECT_ID` value captured at startup. Use that
destination for every `add_note` call.

Treat `index.json` as a durable work queue. Process it in batches of five. Keep
terminal add-note failures in
`$SCRATCH/github-trending/failed.txt` through the bundled `mark-failed` command;
do not create or edit that file another way.

Request status and the next batch:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py pending \
  --scope "$SCOPE" \
  --index "$SCRATCH/github-trending/notes/index.json" \
  --batch-size 5 \
  [--exclude-file "$SCRATCH/github-trending/failed.txt"]
```

Omit `--exclude-file` until the file exists. For every returned entry, in
order:

1. Call `add_note` with `note_title` from the entry and
   `note_body_file_path` set to its absolute `file`. Never pass the rendered
   Markdown through `note_body`.
2. Retry a transient `add_note` failure up to three total attempts. After a
   permanent failure or the third failed attempt, run:

   ```bash
   python3 ./.claude/skills/run/scripts/feedstate.py mark-failed \
     --file "$SCRATCH/github-trending/failed.txt" --id "$REPOSITORY_ID"
   ```

   Continue with later entries.
3. Immediately after `add_note` succeeds, checkpoint the repository before any
   other operation:

   ```bash
   python3 ./.claude/skills/run/scripts/feedstate.py mark-seen \
     --scope "$SCOPE" --id "$REPOSITORY_ID"
   ```

   Retry a failed checkpoint before creating another note. If it still fails,
   stop and report both the Pachinko note ID and repository ID so the possible
   duplicate is explicit.
4. Call `set_note_source` with `source_type: "webpage"`. Retry a transient
   failure up to three total attempts, then report it and continue. The note is
   already checkpointed and includes its source links.
5. Collect the new note ID for the post-execution queue workflow in
   `./CLAUDE.md` and increment the written count.

After every batch, call `pending` again with the failed file when present and
send a concise progress update such as
`GitHub Trending notes: 10/24 checkpointed, 0 failed, 14 remaining`.

Do not enter Step 5 or send the final response until all three conditions hold:

- `terminal` is `true`;
- `remaining` is `0`; and
- `checkpointed + failed == total`.

If counts do not balance, keep processing or repair the failed-ID file. Do not
infer completion from elapsed time, context compaction, or a partial tool
success. If execution continues in another turn, the next `pending` response is
authoritative because successful notes were checkpointed individually.

Do not set a due date and do not run two copies of the same filter scope
concurrently.

## Step 5 — Record the completed run

Only after the Step 4 completion invariant passes, including for an empty
index, run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py record \
  --scope "$SCOPE" --fetched "$FETCHED" --written "$WRITTEN"
```

Per-note checkpoints already contain every successful repository ID. Failed
IDs are deliberately absent and therefore remain eligible on the next run.

## Step 6 — Report and queue functions

Report:

- the canonical Trending URL;
- resolved date range, programming language, and spoken language;
- fetched → already seen → fresh → written → failed counts;
- repository IDs for any failures; and
- whether the default daily/any-language values were used.

Then execute **Post-Execution: Queue Function** from `./CLAUDE.md`. Queue only
notes created during this execution, in batches of at most 10 for every
configured function ID. Verify complete delivery before finishing.

## State location

State is stored in a hashed per-scope file under `<project>/.feed-state/`.
Mutations use an exclusive `flock` and atomic replacement. Override the state
directory with `GITHUB_TRENDING_STATE`, or the project root with
`GITHUB_TRENDING_PROJECT_DIR`.

Inspect a known scope with:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py show --scope "$SCOPE"
```

GitHub Trending is an HTML interface rather than a documented API. If the live
markup no longer passes the bundled invariants, report the parser failure and
stop with state untouched.
