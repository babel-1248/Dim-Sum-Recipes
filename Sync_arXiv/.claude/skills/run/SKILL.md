---
name: run
description: Run the Sync arXiv recipe. Load optional plain-text instructions from FILTER_FILE, then incrementally fetch arXiv preprints, apply server-side and semantic filtering, convert matching papers to Markdown, add one Pachinko note per paper, and update deduplication state. Use when the user says "run" in this recipe.
---

**Never use the Agent tool. Do not spawn sub-agents or background workers at any point during this skill.**

# Sync arXiv

Run the entire workflow non-interactively. Never pause for confirmation. Apply
the defaults and precedence rules below, then name the assumptions in the final
report.

Scripts live under `./.claude/skills/run/scripts/`. Invoke them with their
project-relative paths so the project-local rules apply. Work in the session
scratchpad (`$SCRATCH`).

## Safe defaults

- Start seven days back when neither the filter nor stored state supplies a
  lower bound.
- Create at most 20 notes per run unless the filter sets another positive limit
  or explicitly requests no limit.
- Use new-paper submission dates (`submittedDate`), ascending order, and full
  text when available.
- Never delete a note. Report a suspected duplicate and leave it in place.

## Step 0 — Load and resolve `FILTER_FILE`

Load the filter exactly once before fetching:

```bash
python3 ./.claude/skills/run/load_filter.py
```

Capture the output. If it is empty, use `null` filter instructions. If the
script exits with an error, report it and stop; do not silently run unfiltered.
Do not read `FILTER_FILE` with any other command.

Resolve the instructions into the following settings. Echo the resolved values
before fetching so the user can see how the file was interpreted, but continue
without waiting for confirmation.

1. **Window.** Parse an inclusive lower and optional upper `YYYY-MM-DD` date.
2. **Layer 1 query.** Push safe arXiv-native conditions into categories, terms,
   authors, and/or a raw query fragment.
3. **Layer 2 relevance.** Retain criteria that require judging a paper's
   meaning, such as relevance, survey status, code availability, venue quality,
   or author affiliation.
4. **Date basis.** Default to `submittedDate`; use `lastUpdatedDate` only when
   the filter explicitly asks for revisions or updated papers.
5. **Order.** Default to ascending; use descending only when explicitly asked.
6. **Full text.** Default to full text with abstract fallback; select
   `--abstract-only` only when explicitly requested.
7. **Note limit.** Default to 20. A positive integer overrides it. Explicit
   "no limit" or "unlimited" removes it. Ambiguous or non-positive limit
   wording leaves the default unchanged.

If the file uses checkbox sections, interpret checked (`- [x]`) values and
ignore unchecked (`- [ ]`) values. In a section labeled "pick one", take the
topmost checked value if several are checked and report that choice. Map common
sections as follows:

| Filter section | Resolved setting |
| --- | --- |
| Categories | one `--category` per checked value |
| Search terms | one `--term` per value |
| Which field the terms search | `--term-field` (`all`, `ti`, `abs`, `co`, or `jr`) |
| Authors | one `--author` per value |
| Raw query | `--query` |
| Date basis | `--date-field submittedDate` or `lastUpdatedDate` |
| Order within the window | `--sort-order ascending` or `descending` |
| Full text | `--abstract-only` when selected |
| Caps / note limit | numeric limit or no limit |
| Extra instructions | layer-2 relevance criterion |

Treat an empty fenced block as no constraint. Ignore example lines beginning
with `#` inside fenced blocks. No selected category means all arXiv categories,
not zero categories.

### Split the filter safely

Before forming layer 1, inspect the verified arXiv query vocabulary:

```bash
python3 ./.claude/skills/run/scripts/fetch.py --list-syntax
```

Repeatable flags are ORed within a group and groups are ANDed: two categories
and two terms become `(category A OR category B) AND (term A OR term B)`.

Use a concrete phrase such as "in-context learning" as a server-side term.
Keep vague criteria such as "practical" or "promising" in layer 2 because
requiring those literal words would silently discard relevant papers. Raw arXiv
syntax belongs in `--query`; never edit `fetch.py` to add a new condition.

## Step 1 — Determine the window

Use this precedence:

1. An explicit lower bound in the filter overrides stored state.
2. Otherwise use the stored `next_since`.
3. Otherwise start seven days before the current local date.

When no explicit lower bound exists, run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py since arxiv --default "$(date -v-7d +%F)"
```

On GNU systems, use `date -d '7 days ago' +%F` for the default. Use the current
local date as the upper bound unless the filter supplies one.

The boundary day is deliberately re-scanned. Papers arrive throughout the day;
`seen_ids` prevents duplicates. If the current filter is wider than an earlier
run, resume normally and report that newly included categories are covered only
from this run's lower bound unless the filter explicitly requested a backfill.

## Step 2 — Fetch metadata

Build the command only from resolved settings:

```bash
python3 ./.claude/skills/run/scripts/fetch.py \
  --since "$SINCE" --until "$UNTIL" --out "$SCRATCH/arxiv" \
  [--category cs.LG --category cs.CL] \
  [--term "in-context learning" --term-field abs] \
  [--author Hinton] \
  [--query 'abs:"world model" ANDNOT cat:math.*'] \
  [--date-field lastUpdatedDate] [--sort-order descending]
```

Do not use `fetch.py --limit` as the note limit. It would truncate the result
before semantic filtering and could hide eligible papers. The dedicated cap in
Step 5 preserves overflow correctly.

Check the `search_query:` line printed by the script against the resolved
intent. This stage downloads metadata only; never move full-text downloads into
it.

## Step 3 — Drop previously saved papers

Write every manifest `arxiv_id`, one per line, to
`$SCRATCH/arxiv/all_ids.txt`, then run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py filter-seen arxiv \
  --ids-file "$SCRATCH/arxiv/all_ids.txt" > "$SCRATCH/arxiv/fresh.txt"
```

Use this state-based deduplication even if Pachinko's note listing appears to
contain all titles. Listing is not guaranteed to return every note.

## Step 4 — Apply layer-2 relevance

Read `manifest.json` and judge every fresh paper using its `title`, `abstract`,
`categories`, and `comment`. Write matching arXiv IDs, one per line, to
`$SCRATCH/arxiv/keep.txt`.

If there is no layer-2 criterion, copy the fresh IDs into `keep.txt`; every
later step always consumes a concrete keep file. For more than about 150 fresh
papers, judge in batches. Never sample: every fresh paper must be judged or the
reported coverage is false.

Track fetched → fresh → kept counts and record a few representative exclusions
with their reasons.

## Step 5 — Apply the note limit

For the default or a numeric override, run:

```bash
python3 ./.claude/skills/run/scripts/cap_keep.py \
  --manifest "$SCRATCH/arxiv/manifest.json" \
  --keep "$SCRATCH/arxiv/keep.txt" \
  --selected "$SCRATCH/arxiv/selected.txt" \
  --deferred "$SCRATCH/arxiv/deferred.txt" \
  --limit "$RESOLVED_LIMIT"
```

For an explicit unlimited instruction, replace `--limit ...` with `--no-limit`.
Capture the JSON summary. The helper selects the oldest eligible papers first
and leaves overflow in `deferred.txt`. Do not record deferred IDs as written.

## Step 6 — Convert selected papers

```bash
python3 ./.claude/skills/run/scripts/convert.py \
  --manifest "$SCRATCH/arxiv/manifest.json" \
  --out "$SCRATCH/arxiv/notes" \
  --keep "$SCRATCH/arxiv/selected.txt" \
  --max 0 [--abstract-only]
```

Passing `--max 0` disables the converter's legacy internal cap because Step 5
has already applied the resolved recipe limit and produced an explicit deferred
list.

For full-text mode, the converter downloads each selected paper's
`arxiv.org/html/<id>` sequentially, waits 15 seconds between requests, and
converts LaTeXML HTML to Markdown. A paper without HTML still produces an
abstract-only note. Check and report warnings rather than treating that fallback
as a failed paper.

## Step 7 — Create Pachinko notes

Before listing or creating notes, follow **Creating Notes** in `./CLAUDE.md` to
resolve the destination from the `SAVE_TO_PROJECT_ID` value captured at startup.
Use that destination consistently for listing and every `add_note` call.

### Completion invariant

Treat the converted index as a durable work queue. Process it in batches of five
and re-read checkpoint state after every batch. Do not infer completion from how
many tool calls have already been made, elapsed time, context compaction, or a
partial success message. Do not enter Step 8 or send the final response while
`remaining` is greater than zero.

Request the next batch:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py pending arxiv \
  --index "$SCRATCH/arxiv/notes/index.json" --batch-size 5 \
  [--exclude-id "$FAILED_ARXIV_ID"]...
```

The command prints JSON containing `total`, `checkpointed`, `failed`,
`remaining`, `terminal`, and at most five entries under `batch`. Keep an
in-memory list of IDs that exhaust the retry rule below and pass each one back
as `--exclude-id` on every later status call in this execution.

For every entry returned in `batch`, in order:

1. Call `add_note` with `note_title` from the entry and
   `note_body_file_path` set to the entry's `file`. Never pass the rendered paper
   in `note_body`.
2. If `add_note` fails transiently, make at most three total attempts. On a
   permanent failure or after the third failed attempt, add its arXiv ID to the
   failed-ID list and continue with the rest of the batch. Never let one bad
   paper abandon later entries.
3. Immediately after `add_note` succeeds, checkpoint that paper before doing
   anything else:

   ```bash
   python3 ./.claude/skills/run/scripts/feedstate.py mark-seen arxiv \
     --id "$ARXIV_ID"
   ```

   Do this after every successful note, not in a batch after the loop. The
   checkpoint updates only `seen_ids`; it does not advance the watermark or
   continuation date. If the checkpoint command fails, retry it before creating
   another note. If it still fails, stop and report the created note ID and arXiv
   ID rather than widening the uncheckpointed interval.
4. Call `set_note_source` with `source_type: "webpage"`. This sets an enum; the
   arXiv, PDF, and HTML URLs are already in the note body. Retry a transient
   source-setting failure up to two more times, then report it and continue; the
   successfully created note remains checkpointed.
5. Collect the new note ID for the post-execution queue workflow in
   `./CLAUDE.md`.

After processing every entry returned in `batch`, run `pending` again with the
same index and all failed-ID exclusions. Continue requesting and processing
batches until it returns all three conditions:

- `terminal` is `true`
- `remaining` is `0`
- `checkpointed + failed == total`

If the counts do not balance, keep processing or correct the failure list; do
not report success. If a tool or execution limit interrupts the turn, continue
the same loop on the next automatic continuation. The per-note checkpoints make
the next `pending` call authoritative.

After each status call, send a concise progress update such as
`arXiv notes: 10/20 checkpointed, 0 failed, 10 remaining`. Treat it only as an
intermediate update; it is never a reason to stop the run.

Do not set a due date. Do not delete suspected duplicates.

Also append each successfully checkpointed `arxiv_id` to
`$SCRATCH/arxiv/written.txt` for the end-of-run summary record. Keep failed
`add_note` calls outstanding for the continuation calculation. The paper's
**window date** is
`published_date` for `submittedDate` runs and the date portion of `updated` for
`lastUpdatedDate` runs.

## Step 8 — Record state

Record state immediately after the Step 7 completion invariant passes,
including on an empty window. Never use the end-of-run record to paper over a
nonzero `remaining` count.
Write successful arXiv IDs to `$SCRATCH/arxiv/written.txt` and run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py record arxiv \
  --watermark "$MAX_WRITTEN_WINDOW_DATE" \
  --until "$CONTINUATION_DATE" \
  --ids-file "$SCRATCH/arxiv/written.txt" \
  --fetched "$FETCHED" --kept "$KEPT" --written "$WRITTEN"
```

Set `CONTINUATION_DATE` to the earliest window date among deferred papers and
selected papers whose `add_note` call failed. Use `published_date` for a
`submittedDate` run and the date portion of `updated` for a `lastUpdatedDate`
run. If an outstanding paper has no date, use the window start. If nothing is
outstanding, use the requested window end. On an empty window, also use the
window end so state advances. Omit `--watermark` when no paper was successfully
written.

This re-scans from the oldest outstanding date next time while the per-note
`seen_ids` checkpoints remove successfully created notes even if the previous
process stopped before Step 8. The IDs passed to `record` are intentionally
idempotent with those checkpoints. Never record a deferred or failed ID.

## Step 9 — Report

Report the window, resolved layer-1 query, layer-2 criterion, date basis,
full-text mode, resolved note limit, and fetched → fresh → kept → selected →
written → deferred counts. Include full-text versus abstract-only counts,
conversion warnings, note-creation failures, representative semantic
exclusions, and the next since-date.

Name every default used: initial seven-day lookback, submitted-date basis,
ascending order, full-text preference, default 20-note cap, and an unfiltered or
all-category run.

## State files

State is stored in `<project>/.feed-state/arxiv.json`. Mutations use an exclusive
`flock` and atomic replacement, so concurrent writers cannot clobber each other.

```bash
python3 ./.claude/skills/run/scripts/feedstate.py show arxiv
python3 ./.claude/skills/run/scripts/feedstate.py mark-seen arxiv --id 2608.00001v1
python3 ./.claude/skills/run/scripts/feedstate.py reset arxiv
```

Override the state directory with `RESEARCH_FEED_STATE`, or the project root
with `RESEARCH_PROJECT_DIR`.

## arXiv access constraints

`fetch.py` uses `https://export.arxiv.org/api/query`, one connection at a time,
with at least three seconds between paged requests. `convert.py` uses
`https://arxiv.org/html/<id>`, one connection at a time, with the site's
15-second crawl delay. Never parallelize either script or run several syncs to
evade the limits.

Both scripts honor retry responses and back off. If arXiv remains unavailable,
report the failure and stop. State has not advanced, so a later run is safe.

Additional facts:

- Use HTTPS; the HTTP API endpoint can redirect with an empty body.
- The arXiv search index commonly lags by about a day, and weekends may be quiet.
- API paging stops at 30,000 results. Narrow the query or window if that ceiling
  is reached.
- Versioned IDs are distinct. `lastUpdatedDate` may therefore create a later
  note for a revised paper, while the default `submittedDate` sees first
  submissions.
- Figures are not embedded; figure captions remain in the Markdown.
- `latexml2md.py` is specific to arXiv's LaTeXML HTML, not a general HTML
  converter.
