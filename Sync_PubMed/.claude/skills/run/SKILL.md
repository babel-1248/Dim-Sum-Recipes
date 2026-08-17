---
name: run
description: Run the Sync PubMed recipe. Load optional plain-text instructions from FILTER_FILE, incrementally fetch PubMed citations, apply Entrez and semantic filtering, convert matching records to Markdown, add one Pachinko note per paper in verified batches, and update resumable deduplication state. Use when the user says "run" in this recipe.
---

**Never use the Agent tool. Do not spawn sub-agents or background workers at any point during this skill.**

# Sync PubMed

Run the entire workflow non-interactively. Never pause for confirmation. Apply
the defaults and precedence rules below, then name assumptions in the final
report.

Invoke scripts through project-relative paths under
`./.claude/skills/run/scripts/` so project-local rules apply. Work in the
session scratchpad (`$SCRATCH`).

## Safe defaults

- Start seven days back when neither the filter nor stored state supplies a
  lower bound.
- Create at most 20 notes per run unless the filter sets another positive limit
  or explicitly requests no limit.
- Use PubMed entered dates (`edat`) and full text when available.
- Use `all[sb]` when no query constraint exists. Never pass a bare date window.
- Never delete a note. Report a suspected duplicate and leave it in place.

## Step 0 — Load and resolve `FILTER_FILE`

Load the filter exactly once before fetching:

```bash
python3 ./.claude/skills/run/load_filter.py
```

Capture the output. If it is empty, use `null` filter instructions. If the
script exits with an error, report it and stop; do not silently run unfiltered.
Do not read `FILTER_FILE` with any other command.

Resolve the instructions into the following settings and echo them before
fetching without waiting for confirmation:

1. **Window.** Parse an inclusive lower and optional upper `YYYY-MM-DD` date.
2. **Layer 1 query.** Push safe Entrez-native conditions into terms, MeSH
   headings, journals, publication types, and/or a raw query fragment.
3. **Layer 2 relevance.** Retain criteria requiring judgement about a paper's
   meaning, quality, sample size, findings, or applicability.
4. **Date basis.** Default to `edat`; use `pdat` or `mdat` only when explicit.
5. **Full text.** Default to PubMed Central full text with abstract fallback;
   select `--abstract-only` only when explicitly requested.
6. **Note limit.** Default to 20. A positive integer overrides it. Explicit
   "no limit" or "unlimited" removes it. Ambiguous or non-positive limit
   wording leaves the default unchanged.

If the file uses checkbox sections, interpret checked (`- [x]`) values and
ignore unchecked (`- [ ]`) values. In a section labeled "pick one", take the
topmost checked value if several are checked and report that choice. Map common
sections as follows:

| Filter section | Resolved setting |
| --- | --- |
| Search terms | one `--term` per value |
| Which field the terms search | `--term-field`, such as `Title/Abstract` |
| MeSH headings | one `--mesh` per value |
| Publication types | one `--pub-type` per checked value |
| Journals | one `--journal` per value |
| Raw query | `--query` |
| Date basis | `--datetype edat`, `pdat`, or `mdat` |
| Full text | `--abstract-only` when selected |
| Caps / note limit | numeric limit or no limit |
| Extra instructions | layer-2 relevance criterion |

Treat an empty fenced block as no constraint. Ignore example lines beginning
with `#` inside fenced blocks. Nothing selected means all PubMed, not zero
records; resolve layer 1 to `--query 'all[sb]'` and report the unfiltered run.

### Split the filter safely

Inspect the verified Entrez vocabulary before forming layer 1:

```bash
python3 ./.claude/skills/run/scripts/fetch.py --list-syntax
```

Repeatable flags are ORed within a group and groups are ANDed. Prefer Entrez
conditions for literal concepts it supports, such as `Review[pt]`, journals,
authors, affiliations, and languages. Keep judgements such as study quality or
whether a result is positive in layer 2.

MeSH auto-explodes to narrower headings but indexing may lag publication by
weeks or months. For a recent window, pair a MeSH constraint with text terms so
new, not-yet-indexed papers are not silently lost. Never edit `fetch.py` to add
a condition; use `--query` for valid Entrez syntax not covered by a shorthand.

## Step 1 — Determine the window

Use this precedence:

1. An explicit lower bound in the filter overrides stored state.
2. Otherwise use the stored `next_since`.
3. Otherwise start seven days before the current local date.

When no explicit lower bound exists, run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py since pubmed \
  --default "$(date -v-7d +%F)"
```

On GNU systems, use `date -d '7 days ago' +%F` for the default. Use the current
local date as the upper bound unless the filter supplies one.

The boundary day is deliberately re-scanned because records are indexed
throughout a day. `seen_ids` prevents duplicates. If the current filter is wider
than an earlier run, resume normally and report that newly included topics are
covered only from this run's lower bound unless the filter requested a backfill.

## Step 2 — Fetch metadata

Build the command only from resolved settings:

```bash
python3 ./.claude/skills/run/scripts/fetch.py \
  --since "$SINCE" --until "$UNTIL" --out "$SCRATCH/pubmed" \
  [--term "base editing" --term "prime editing"] \
  [--term-field "Title/Abstract"] [--mesh "Gene Editing"] \
  [--journal Nature] [--pub-type Review] \
  [--query 'free full text[sb]'] [--datetype pdat]
```

When no layer-1 conditions exist, include `--query 'all[sb]'`. Do not use
`fetch.py --limit` as the note limit: truncating before semantic filtering can
hide eligible records. Step 5 applies an overflow-safe cap.

Check the printed `term:` line against the resolved intent. This stage fetches
complete PubMed citation metadata—title, abstract, authors, journal, MeSH,
keywords, publication types, DOI and PMC ID—but not PMC full text.

The fetcher adds a safe `window_date` for state and capping. It uses exact
entered or publication dates when available. For imprecise publication dates
and all `mdat` runs, it conservatively uses the window start so a capped or
failed run re-scans rather than advancing from a different XML date field.

## Step 3 — Drop previously saved papers

Write every manifest `pmid`, one per line, to
`$SCRATCH/pubmed/all_ids.txt`, then run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py filter-seen pubmed \
  --ids-file "$SCRATCH/pubmed/all_ids.txt" > "$SCRATCH/pubmed/fresh.txt"
```

Use state-based deduplication even if Pachinko's note listing appears to contain
all titles; listing is not guaranteed to return every note.

## Step 4 — Apply layer-2 relevance

Read `manifest.json` and judge every fresh paper using `title`, `abstract`,
`mesh`, `keywords`, `pub_types`, and `journal`. Write matching PMIDs, one per
line, to `$SCRATCH/pubmed/keep.txt`.

If there is no layer-2 criterion, copy the fresh IDs into `keep.txt`; every
later step consumes a concrete keep file. Records without abstracts must be
judged from their available fields rather than silently dropped. For more than
about 150 fresh records, judge in batches. Never sample.

Track fetched → fresh → kept counts and record representative exclusions with
their reasons.

## Step 5 — Apply the note limit

For the default or a numeric override, run:

```bash
python3 ./.claude/skills/run/scripts/cap_keep.py \
  --manifest "$SCRATCH/pubmed/manifest.json" \
  --keep "$SCRATCH/pubmed/keep.txt" \
  --selected "$SCRATCH/pubmed/selected.txt" \
  --deferred "$SCRATCH/pubmed/deferred.txt" \
  --limit "$RESOLVED_LIMIT"
```

For an explicit unlimited instruction, replace `--limit ...` with `--no-limit`.
Capture the JSON summary. The helper selects the oldest eligible records by
`window_date` and leaves overflow in `deferred.txt`. Never record deferred IDs.

## Step 6 — Convert selected papers

```bash
python3 ./.claude/skills/run/scripts/convert.py \
  --manifest "$SCRATCH/pubmed/manifest.json" \
  --out "$SCRATCH/pubmed/notes" \
  --keep "$SCRATCH/pubmed/selected.txt" \
  --max 0 [--abstract-only]
```

Passing `--max 0` disables the converter's legacy internal cap because Step 5
already produced explicit selected and deferred lists.

For full-text mode, the converter requests PMC JATS XML only for selected
records with a PMC ID and converts it to Markdown. A PMC ID does not guarantee
an accessible body, and many PubMed records have no PMC ID; all such cases still
produce metadata-and-abstract notes. Check and report warnings rather than
treating abstract fallback as a failed paper.

## Step 7 — Create Pachinko notes

Before listing or creating notes, follow **Creating Notes** in `./CLAUDE.md` to
resolve the destination from the `SAVE_TO_PROJECT_ID` value captured at startup.
Use that destination consistently for listing and every `add_note` call.

### Completion invariant

Treat the converted index as a durable work queue. Process it in batches of five
and re-read checkpoint state after every batch. Do not infer completion from tool
count, elapsed time, context compaction, or a partial success message. Do not
enter Step 8 or send the final response while `remaining` is greater than zero.

Request the next batch:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py pending pubmed \
  --index "$SCRATCH/pubmed/notes/index.json" --batch-size 5 \
  [--exclude-id "$FAILED_PMID"]...
```

The command prints `total`, `checkpointed`, `failed`, `remaining`, `terminal`,
and at most five entries under `batch`. Keep IDs that exhaust the retry rule in
memory and pass each back as `--exclude-id` on every later status call.

For every entry returned in `batch`, in order:

1. Call `add_note` with its `note_title` and `note_body_file_path` set to its
   `file`. Never pass the rendered paper in `note_body`.
2. If `add_note` fails transiently, make at most three total attempts. On a
   permanent failure or after the third failed attempt, add the PMID to the
   failed-ID list and continue with later entries.
3. Immediately after `add_note` succeeds, checkpoint it before doing anything
   else:

   ```bash
   python3 ./.claude/skills/run/scripts/feedstate.py mark-seen pubmed \
     --id "$PMID"
   ```

   Checkpoint after every note, not after the loop. It updates only `seen_ids`,
   not the watermark. If it fails, retry before creating another note. If it
   still fails, stop and report the created note ID and PMID.
4. Call `set_note_source` with `source_type: "webpage"`. Retry a transient
   failure up to two more times, then report it and continue; the note remains
   checkpointed and its PubMed, DOI and PMC URLs are already in the body.
5. Collect the new note ID for the post-execution queue workflow in
   `./CLAUDE.md`.

After each batch, run `pending` again with all failed-ID exclusions. Continue
until all three conditions hold:

- `terminal` is `true`
- `remaining` is `0`
- `checkpointed + failed == total`

If counts do not balance, keep processing or correct the failure list. If a
tool or execution limit interrupts the turn, continue the same loop on the next
automatic continuation; checkpoints make the next `pending` call authoritative.

After each status call, send a concise intermediate update such as
`PubMed notes: 10/20 checkpointed, 0 failed, 10 remaining`. Never treat that
update as completion.

Do not set a due date. Do not delete suspected duplicates. Append each
successfully checkpointed PMID to `$SCRATCH/pubmed/written.txt`. Keep failed
PMIDs outstanding for the continuation calculation.

## Step 8 — Record state

Record state only after the Step 7 completion invariant passes, including on an
empty window. Never use the end-of-run record to paper over a nonzero
`remaining` count.

```bash
python3 ./.claude/skills/run/scripts/feedstate.py record pubmed \
  --watermark "$MAX_WRITTEN_WINDOW_DATE" \
  --until "$CONTINUATION_DATE" \
  --ids-file "$SCRATCH/pubmed/written.txt" \
  --fetched "$FETCHED" --kept "$KEPT" --written "$WRITTEN"
```

Set `CONTINUATION_DATE` to the earliest `window_date` among deferred records and
selected records whose `add_note` call failed. If an outstanding record lacks a
date, use the window start. If nothing is outstanding, use the requested window
end. On an empty window, also use the window end. Omit `--watermark` when no
paper was successfully written.

This re-scans from the oldest outstanding date while per-note `seen_ids`
checkpoints remove successfully created notes after a partial run. Passing the
same IDs again to `record` is intentionally idempotent. Never record deferred or
failed IDs.

## Step 9 — Report

Report the window, Entrez query, layer-2 criterion, date basis, full-text mode,
resolved note limit, and fetched → fresh → kept → selected → written → deferred
counts. Include full-text versus abstract-only counts, missing abstracts,
conversion warnings, note failures, representative exclusions, and next
since-date.

Name defaults used: initial seven-day lookback, `edat`, full-text preference,
default 20-note cap, and an unfiltered `all[sb]` run.

## State files

State is stored in `<project>/.feed-state/pubmed.json`. Mutations use an
exclusive `flock` and atomic replacement.

```bash
python3 ./.claude/skills/run/scripts/feedstate.py show pubmed
python3 ./.claude/skills/run/scripts/feedstate.py mark-seen pubmed --id 12345678
python3 ./.claude/skills/run/scripts/feedstate.py reset pubmed
```

Override the state directory with `RESEARCH_FEED_STATE`, or the project root
with `RESEARCH_PROJECT_DIR`.

## NCBI access constraints

The scripts issue sequential E-utilities requests. NCBI permits three requests
per second without an API key and ten with one. Never parallelize requests or
run concurrent syncs to evade the limit. Use `NCBI_API_KEY` and optionally
`NCBI_EMAIL`; NCBI separately requires manual registration of tool/email values
for formal compliance.

Schedule genuinely large jobs for weekends or 9 PM–5 AM Eastern when practical.
Both scripts honor retry responses and back off. If NCBI remains unavailable,
report the failure and stop; state has not advanced, so a later run is safe.

Additional facts:

- The fetcher uses the Entrez history server to avoid the 9,999-ID `esearch`
  response cap and applies a 50,000-record safety backstop.
- PubMed book/chapter containers lack the fields used by this workflow; the
  fetcher counts and reports them.
- MeSH indexing lags publication, often leaving recent records without headings.
- Publication dates may be a year or season rather than an exact date; the
  explicit `window_date` fallback prevents unsafe state advancement.
- Figures are not embedded; captions remain in Markdown.
- `jats2md.py` targets PMC JATS XML, not arbitrary XML or HTML.
