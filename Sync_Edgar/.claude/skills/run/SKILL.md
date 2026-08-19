---
name: run
description: Run the Sync EDGAR recipe. Load optional instructions from FILTER_FILE, incrementally fetch SEC EDGAR filings, filter and convert filing documents and exhibits, parse Forms 13F-HR and 13F-HR/A into complete institutional holdings tables, add one Pachinko note per selected filing in verified batches, and update resumable accession state. Use when the user says "run" in this recipe.
---

**Never use the Agent tool. Do not spawn sub-agents or background workers at any point during this skill.**

# Sync EDGAR

Run the entire workflow non-interactively. Apply the defaults and precedence
rules below, then name assumptions in the final report.

Invoke scripts through project-relative paths under
`./.claude/skills/run/scripts/` so project-local rules apply. Work in the
session scratchpad (`$SCRATCH`). Never parallelize SEC requests.

`SEC_CONTACT_EMAIL` is required by the recipe and consumed directly by the
Python client. If it is unexpectedly missing or empty, stop and report the
configuration error; never send EDGAR requests with a generic User-Agent.

## Safe defaults

- Start seven days back when neither the filter nor stored state supplies a
  lower bound.
- Create at most 20 notes per run unless the filter sets another positive limit
  or explicitly requests no limit.
- With no watchlist, CIK, form, or query, run a market-wide `8-K` search. A bare
  date window is never sent to EDGAR.
- Include amendments, fetch `EX-99` exhibits, and convert complete filing
  documents unless the filter explicitly says otherwise.
- Never delete a note. Report a suspected duplicate and leave it in place.

## Step 0 — Load and resolve `FILTER_FILE`

Load the filter exactly once before fetching:

```bash
python3 ./.claude/skills/run/load_filter.py
```

Capture the output. If it is empty, use `null` filter instructions. If the
script exits with an error, report it and stop; do not silently run with the
market-wide default. Do not read `FILTER_FILE` with another command.

Resolve the instructions into these settings and echo them before fetching:

1. **Window.** Parse an inclusive lower and optional upper `YYYY-MM-DD` filing
   date.
2. **Companies.** Resolve tickers and CIKs. Tickers are case-insensitive.
3. **Forms.** Resolve included and excluded form types and whether amendments
   are included.
4. **Full-text query.** Preserve a valid EDGAR query phrase or expression.
5. **Layer-2 relevance.** Retain criteria that can be judged from manifest
   metadata: form, decoded 8-K items, material-item flags, company, ticker,
   filing date, and report date.
6. **Report sections.** Resolve comma-separated 10-K, 10-Q, or 20-F Item keys,
   such as `1A,7`.
7. **Exhibits.** Default to `EX-99`; use an empty exhibit value only when the
   filter explicitly disables exhibits.
8. **Note limit.** Default to 20. A positive integer overrides it. Explicit
   "no limit" or "unlimited" removes it. Ambiguous or non-positive wording
   leaves the default unchanged.

If the file uses checkbox sections, interpret checked (`- [x]`) values and
ignore unchecked (`- [ ]`) values. In a section labeled "pick one", take the
topmost checked value if several are checked and report that choice.

| Filter section | Resolved setting |
| --- | --- |
| Tickers | one `--ticker` per checked value |
| CIKs | one `--cik` per checked value |
| Form types | one `--form` per checked value |
| Forms to ignore | one `--exclude-form` per checked value |
| Include amendments | omit `--no-amendments` when selected |
| Full-text phrase or query | `--query` |
| Filing-date window | `--since` and optional `--until` |
| 10-K/10-Q sections | `--sections`, such as `1A,7` |
| Exhibits | `--exhibits`; empty string disables them |
| Caps / note limit | numeric limit or no limit |
| Extra instructions | layer-2 relevance criterion |

Treat an empty fenced block as no constraint. Ignore example lines beginning
with `#` inside fenced blocks. No companies and no query means market-wide;
ensure at least one form is present, defaulting to `8-K` when none is selected.

Before forming layer 1, inspect the verified EDGAR vocabulary:

```bash
python3 ./.claude/skills/run/scripts/fetch.py --list-forms
```

Repeatable form flags are ORed. Form matching includes amendments by default.
When a full-text query and companies are both supplied, the fetcher resolves the
companies and restricts search results to their CIKs. Prefer decoded 8-K item
metadata over semantic guessing: executive departures are item 5.02, auditor
changes 4.01, non-reliance or restatements 4.02, and material cyber incidents
1.05.

Do not claim to filter Form 4 transaction codes before conversion; transaction
codes live in the ownership XML, not the metadata manifest. A request for
insider activity resolves layer 1 to Forms 3/4/5 as appropriate, and the final
report uses the converter's decoded ownership facts.

A request for institutional, fund-manager, super-investor, or 13F holdings
resolves layer 1 to `13F-HR` (including `13F-HR/A` unless amendments are
disabled). Do not treat `13F-NT` as a holdings report; it is a notice filing.
Do not infer buys, sells, or quarter-to-quarter changes from a single 13F.

## Step 1 — Determine the filing-date window

Use this precedence:

1. An explicit lower bound in the filter overrides stored state.
2. Otherwise use stored `next_since`.
3. Otherwise start seven days before the current local date.

When no explicit lower bound exists, run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py since edgar \
  --default "$(date -v-7d +%F)"
```

On GNU systems, use `date -d '7 days ago' +%F`. Use the current local date as
the upper bound unless the filter supplies one.

The boundary day is deliberately re-scanned because EDGAR accepts filings
throughout the day. Per-accession state suppresses completed filings. If the
watchlist or forms widen, resume normally and report that newly included scope
is covered only from this run's lower bound unless the filter requests a
backfill.

## Step 2 — Fetch filing metadata

Build the command only from resolved settings:

```bash
python3 ./.claude/skills/run/scripts/fetch.py \
  --since "$SINCE" --until "$UNTIL" --out "$SCRATCH/edgar" \
  [--ticker AAPL --ticker NVDA] [--cik 320193] \
  [--form 8-K --form 10-K --form 10-Q --form 4] \
  [--exclude-form 144] [--no-amendments] \
  [--query '"material weakness"']
```

Do not use `fetch.py --limit` as the note limit. Truncating the server results
before relevance filtering can hide eligible filings. Step 5 applies an
overflow-safe cap after relevance.

Ticker/CIK watchlists without a query use the submissions API. A query uses
EDGAR full-text search and is restricted to resolved company CIKs when supplied.
Forms without companies or a query use market-wide full-text search.

Check the printed filing counts, date range, form counts, and material 8-K block
against the resolved intent. This stage fetches metadata only; do not move
filing or exhibit downloads into it.

## Step 3 — Drop previously completed filings

Write every manifest `accession`, one per line, to
`$SCRATCH/edgar/all_ids.txt`, then run:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py filter-seen edgar \
  --ids-file "$SCRATCH/edgar/all_ids.txt" > "$SCRATCH/edgar/fresh.txt"
```

Use state-based deduplication even if Pachinko note listing appears complete;
listing is not guaranteed to return every note.

## Step 4 — Apply layer-2 relevance

Read `manifest.json` and judge every fresh filing using only `form`, `items`,
`item_labels`, `material_items`, `company`, `tickers`, `filed`, and
`report_date`. Write matching accessions, one per line, to
`$SCRATCH/edgar/keep.txt`.

If there is no layer-2 criterion, copy fresh accessions into `keep.txt`; every
later step consumes a concrete keep file. For more than about 150 fresh
filings, judge in batches. Never sample. Track fetched → fresh → kept counts and
record representative exclusions with reasons.

## Step 5 — Apply the note limit

For the default or a numeric override, run:

```bash
python3 ./.claude/skills/run/scripts/cap_keep.py \
  --manifest "$SCRATCH/edgar/manifest.json" \
  --keep "$SCRATCH/edgar/keep.txt" \
  --selected "$SCRATCH/edgar/selected.txt" \
  --deferred "$SCRATCH/edgar/deferred.txt" \
  --limit "$RESOLVED_LIMIT"
```

For an explicit unlimited instruction, replace `--limit ...` with `--no-limit`.
Capture the JSON summary. The helper selects the oldest eligible filings first
and leaves overflow in `deferred.txt`. Never record deferred accessions as
completed.

## Step 6 — Convert selected filings

```bash
python3 ./.claude/skills/run/scripts/convert.py \
  --manifest "$SCRATCH/edgar/manifest.json" \
  --out "$SCRATCH/edgar/notes" \
  --keep "$SCRATCH/edgar/selected.txt" \
  --max 0 [--sections 1A,7] [--exhibits EX-99]
```

Passing `--max 0` disables the converter's legacy cap because Step 5 already
produced explicit selected and deferred lists. Quote an empty `--exhibits ''`
argument when exhibits were explicitly disabled.

The converter fetches primary documents sequentially. It also fetches matching
documents from full-text search and the selected exhibit types; `EX-99` is
normally essential for 8-K press releases. Forms 3/4/5 use the ownership XML
parser and produce decoded transaction tables. Forms 13F-HR and 13F-HR/A locate
the filing's separate `INFORMATION TABLE` XML attachment and produce a complete,
value-sorted holdings table containing issuer, class, CUSIP, FIGI, value,
portfolio weight, shares/principal, put/call, discretion, other manager, and
voting authority. Values are normalized to dollars while retaining whether the
SEC row was reported in dollars or thousands. The parser does not compare
quarters or characterize position changes. Never truncate a parsed 13F holdings
table; every reported row is required for later comparison. Check and report
warnings, truncations of other filing documents, and filings with no retrieved
document text.

## Step 7 — Create Pachinko notes

Before listing or creating notes, follow **Creating Notes** in `./CLAUDE.md` to
resolve the destination from the `SAVE_TO_PROJECT_ID` value captured at startup.
Use that destination consistently for every `add_note` call.

Treat the converted index as a durable work queue. Process it in batches of five
and re-read checkpoint state after every batch. Do not infer completion from
tool count, elapsed time, context compaction, or a partial success message. Do
not enter Step 8 or send the final response while `remaining` is greater than
zero.

Request the next batch:

```bash
python3 ./.claude/skills/run/scripts/feedstate.py pending edgar \
  --index "$SCRATCH/edgar/notes/index.json" --batch-size 5 \
  [--exclude-file "$SCRATCH/edgar/failed.txt"]
```

The command prints `total`, `checkpointed`, `failed`, `remaining`, `terminal`,
and at most five entries under `batch`. Maintain accessions that exhaust the
retry rule in `$SCRATCH/edgar/failed.txt`, one per line. Include `--exclude-file`
on every later status call once that file exists. This preserves terminal
failure state through context compaction or an automatic continuation.

For every entry returned in `batch`, in order:

1. Call `add_note` with its `note_title` and `note_body_file_path` set to its
   `file`. Never pass the rendered filing in `note_body`.
2. If `add_note` fails transiently, make at most three total attempts. On a
   permanent failure or after the third failed attempt, append the accession to
   `failed.txt` and continue with later entries.
3. Immediately after `add_note` succeeds, checkpoint it before doing anything
   else:

   ```bash
   python3 ./.claude/skills/run/scripts/feedstate.py mark-seen edgar \
     --id "$ACCESSION"
   ```

   Checkpoint after every note, not after the loop. It updates only `seen_ids`,
   not the watermark. If it fails, retry before creating another note. If it
   still fails, stop and report the created note ID and accession.
4. Call `set_note_source` with `source_type: "webpage"`. Retry a transient
   failure up to two more times, then report it and continue; the note remains
   checkpointed and its EDGAR URLs are already in the body.
5. Collect the new note ID for the post-execution queue workflow in
   `./CLAUDE.md` and append the accession to `$SCRATCH/edgar/written.txt`.

After each batch, run `pending` again with the durable failed-accession file.
Continue until all three conditions hold:

- `terminal` is `true`
- `remaining` is `0`
- `checkpointed + failed == total`

If counts do not balance, keep processing or correct the failure list. If a
tool or execution limit interrupts the turn, continue the same loop on the next
automatic continuation; per-note checkpoints make the next `pending` call
authoritative.

After each status call, send a concise intermediate update such as
`EDGAR notes: 10/20 checkpointed, 0 failed, 10 remaining`. Never treat that
update as completion. Do not set a due date or delete suspected duplicates.

## Step 8 — Record state

Record state only after the Step 7 completion invariant passes, including on an
empty window. Never use the end-of-run record to hide a nonzero `remaining`
count.

```bash
python3 ./.claude/skills/run/scripts/feedstate.py record edgar \
  --watermark "$MAX_WRITTEN_FILED_DATE" \
  --until "$CONTINUATION_DATE" \
  [--ids-file "$SCRATCH/edgar/written.txt"] \
  --fetched "$FETCHED" --kept "$KEPT" --written "$WRITTEN"
```

Set `CONTINUATION_DATE` to the earliest `filed` date among deferred filings and
selected filings whose `add_note` call failed. If an outstanding filing has no
date, use the window start. If nothing is outstanding, use the requested window
end. On an empty window, also use the window end. Omit `--watermark` when no
filing was successfully written, and omit `--ids-file` when no accession was
written.

This re-scans from the oldest outstanding date while per-note accessions remove
successfully created notes after a partial run. Passing the same accessions to
`record` is intentionally idempotent. Never record deferred or failed
accessions.

## Step 9 — Report

Report the window, mode, companies, forms, exclusions, full-text query,
layer-2 criterion, requested sections, exhibit policy, resolved note limit, and
fetched → fresh → kept → selected → written → deferred counts. Include material
8-K items, decoded ownership activity, 13F holding counts and total reported
values, conversion warnings, note failures, representative exclusions, and the
next since-date. Never report a quarter-to-quarter 13F comparison unless a
separate workflow explicitly performed one.

Name defaults used: initial seven-day lookback, market-wide `8-K`, amendments
included, `EX-99` exhibits, complete filing bodies, and the 20-note cap.

## State and SEC access constraints

State is stored in `<project>/.feed-state/edgar.json`. Each successful note is
checkpointed immediately. Mutations use an exclusive `flock` and atomic
replacement. The ticker-to-CIK cache is stored beside it and refreshed weekly.

```bash
python3 ./.claude/skills/run/scripts/feedstate.py show edgar
python3 ./.claude/skills/run/scripts/feedstate.py mark-seen edgar \
  --id 0000320193-26-000001
python3 ./.claude/skills/run/scripts/feedstate.py reset edgar
```

Override the state directory with `RESEARCH_FEED_STATE`, or the project root
with `RESEARCH_PROJECT_DIR`.

The Python client issues requests sequentially with a 0.11-second minimum
interval, keeping one process below SEC's total 10-request/second guideline. It
honors `Retry-After` and backs off after transient failures. SEC applies the
guideline across a user's machines; concurrent EDGAR runs do not share a
limiter, so never run them in parallel. The required `SEC_CONTACT_EMAIL` is
included in every request's User-Agent.

EDGAR full-text search covers 2001 onward and cannot page beyond roughly 10,000
hits. If that ceiling is reached, report incomplete coverage and narrow the
window on the next run rather than claiming the entire result set was processed.
