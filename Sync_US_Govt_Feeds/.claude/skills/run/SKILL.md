---
name: run
description: Run the Sync US Govt Feeds recipe. Load optional plain-text instructions from FILTER_FILE, then sync US government activity into Pachinko notes one feed at a time in order — executive (Federal Register documents), House, and Senate (bills that moved plus roll call votes, with full text converted to Markdown). Use when the user says "run" in this recipe.
---

# Sync US Government Feeds

One skill covering all three branches. Feeds run **strictly in order — executive,
then house, then senate** — each completing fully (fetch → filter → convert →
notes → state) before the next begins, so a failure part way through still leaves
earlier feeds correctly recorded.

Run the entire workflow non-interactively. Never pause for confirmation or ask the
user to choose a fallback; apply the defaults and precedence rules in this skill.

Scripts live under `./.claude/skills/run/scripts/`. Invoke them with their project-relative paths so the project-local rules apply. Work in the session scratchpad (`$SCRATCH`).

| Feed | Branch | Fetch | Convert | Markdown |
|---|---|---|---|---|
| `executive` | Federal Register | `fr_fetch.py` | `fr_convert.py` | `frxml2md.py` |
| `house` | House of Representatives | `congress_fetch.py --chamber house` | `congress_convert.py` | `billxml2md.py` |
| `senate` | Senate | `congress_fetch.py --chamber senate` | `congress_convert.py` | `billxml2md.py` |

`congress_*.py` and `billxml2md.py` are genuinely shared: the two chambers' bill
handling is identical and only their vote sources differ, which is the only part
that branches. The executive feed is a different API and a different XML schema
entirely, so its scripts stay separate and are named `fr_*` rather than pretending
to be shared.

## Input

- **Filter** (optional) — loaded from `FILTER_FILE` and applied to **all three feeds**,
  e.g. *"anything on healthcare"*, *"skip the senate"*, *"only executive orders and
  Senate confirmations"*, *"nothing procedural"*.
- **Time range** (optional) — read from the filter instructions. A lower bound is
  inclusive. Without an explicit time range, each feed resumes from its own stored
  watermark; a feed with no prior state starts one calendar month before the run
  date.

## Step 0 — Resolve the filter into per-feed instructions

Load the filter instructions exactly once before fetching:

```bash
python3 ./.claude/skills/run/load_filter.py
```

Capture the output. If it is non-empty, hold it in memory as the filter instructions. If it is empty, set the filter instructions to `null`. If the script exits with an error, report the error and stop; do not silently run an unfiltered sync. Do not read `FILTER_FILE` directly by any other command.

Resolve the loaded filter instructions into:

1. **Which feeds to run.** Phrases like "skip the senate", "just the executive
   branch", "only Congress" select the feed list. Default is all three in order.
   State the list back to the user before starting.
2. **Layer 1 narrowing, per feed.** Only the executive feed has server-side
   conditions — run `python3 ./.claude/skills/run/scripts/fr_fetch.py --list-conditions` and check the
   vocabulary before assigning anything to layer 2. "Signed executive orders" looks
   like a judgement call but is really
   `presidential_document_type[]=executive_order`, a free narrowing. The congress
   feeds have no query interface at all; their only cheap narrowings are date
   window, `--source bills,votes` and `--bill-type`.
3. **Layer 2 relevance criterion.** Everything left over. The *same* criterion is
   applied to all three feeds, but judged against each feed's own fields.

If a feed ends up with an empty layer-1 and an empty layer-2, it keeps everything.

## Step 1 — Per feed, in order: executive → house → senate

Run this whole block for one feed before starting the next.

### 1a. Determine the window

Resolve the window separately for each feed, using this precedence:

1. If the filter instructions specify a time range, use that range. An explicit
   lower bound overrides stored `next_since`; `seen_ids` still prevents duplicates.
2. Otherwise, use the feed's stored `next_since` when present.
3. Otherwise, on the feed's first run, start one calendar month before the current
   local date. Do not fetch an unbounded history.

When there is no explicit time range, run:

```
SINCE=$(python3 ./.claude/skills/run/scripts/feedstate.py since <feed> --default-one-month)
```

`since` prints the feed's stored `next_since`, falling back to one calendar month
before today only when no state exists. For an explicit lower bound from the
filter, pass that date directly to the fetch command instead of calling `since`.
Apply an explicit upper bound to the fetch command as `--until`; otherwise use the
current local date as the window end.

`next_since` deliberately equals the previous run's `until`, so the boundary day is
re-scanned — items are published throughout a day and starting the day after would
silently drop anything that landed after the last run. Duplicates are prevented by
`seen_ids`, not by advancing the date.

### 1b. Fetch

```
# executive
python3 ./.claude/skills/run/scripts/fr_fetch.py --since $SINCE --out "$SCRATCH/<feed>" [--condition K=V]…
# house / senate
python3 ./.claude/skills/run/scripts/congress_fetch.py --chamber <house|senate> --since $SINCE \
    --out "$SCRATCH/<feed>" [--source bills,votes] [--bill-type …]
```
Neither downloads full text. Congress fetches take a couple of minutes for a week:
bulkdata exposes only a `lastModified` stamp, so every touched BILLSTATUS is read
and the ~1 in 8 with a real in-window action is kept.

### 1c. Drop anything already synced

```
python3 ./.claude/skills/run/scripts/feedstate.py filter-seen <feed> --ids-file <all-ids> > "$SCRATCH/<feed>/fresh.txt"
```
Write every manifest id to a file first. This is what makes re-scanning the
boundary day safe, and it is more reliable than title-dedupe against `list_notes`.

### 1d. Apply the layer-2 criterion

Judge each remaining item and write survivors' ids to `$SCRATCH/<feed>/keep.txt`.

- executive: `title`, `abstract`, `action`, `agencies`, `type`/`subtype`
- house / senate bills: `title`, `summary`, `policy_area`, `subjects`,
  `window_actions`
- house / senate votes: `title`, `question`, `result`, `document`, `is_nomination`

Judge in batches for a large manifest. **Do not sample** — every item is judged or
the feed silently lies about coverage. Report fetched → fresh → kept per feed, and
name a few dropped items with reasons.

### 1e. Convert

```
python3 ./.claude/skills/run/scripts/fr_convert.py       --manifest … --out "$SCRATCH/<feed>/notes" --keep …
python3 ./.claude/skills/run/scripts/congress_convert.py --manifest … --out "$SCRATCH/<feed>/notes" --keep …
```
**Everything expensive lives in this step, on purpose.** Full text download,
XML→markdown conversion and vote→bill resolution all happen after `--keep`, so a
filtered-out item costs zero requests. Never move any of it into a fetch script;
the one thing a fetch must download in bulk is BILLSTATUS, and only because
in-window action dates are the filter itself.

### 1f. Write the notes

Before listing or creating notes, follow the global **Creating Notes** instructions in `./CLAUDE.md` to resolve the destination from the `SAVE_TO_PROJECT_ID` value captured at startup. Use that resolved destination consistently for `list_notes` and every `add_note` call in this run. Do not choose or hardcode a destination inside this skill.

For each `index.json` entry in order, call `add_note` using its `note_title` and passing the entry's `file` as `note_body_file_path`. Never send the full rendered document through `note_body`. Then call `set_note_source` with
`source_type: "webpage"` (a fixed enum, not a URL).

For the executive feed only, `set_note_due_date` to `comments_close_on`, or to a
future `effective_on`. Congress items carry no actionable deadline.

**`list_notes` does not reliably return every note.** Rely on `filter-seen` (step
1c) for dedupe, not on titles. There is no delete-note tool, so duplicates cannot
be undone.

### 1g. Record state — always, even on an empty window

```
python3 ./.claude/skills/run/scripts/feedstate.py record <feed> --watermark <max item date> \
    --until <window end> --ids-file <ids actually written> \
    [--fetched N --kept N --written N]
```
Do this immediately after the notes are written, before moving to the next feed.
On an empty window still record `--until` so the watermark advances.

## Step 2 — Report

One table: per feed, window covered, fetched → fresh → kept → written, warnings,
and the next since-date. Name any feed skipped and why.

## State files

`<project>/.feed-state/{executive,house,senate}.json`, created on demand.

Safe for concurrent agents: every mutation takes an exclusive `flock` on a sibling
`.lock` file and writes atomically via temp-file + `os.replace`, so two agents
running this skill at once cannot lose each other's watermark. Reads take no lock
and never block a sync. Verified with 12 concurrent writers: 12/12 ids recorded,
zero lost updates.

```
python3 ./.claude/skills/run/scripts/feedstate.py show              # all three feeds at a glance
python3 ./.claude/skills/run/scripts/feedstate.py show house
python3 ./.claude/skills/run/scripts/feedstate.py reset senate      # forget a feed's watermark
```

Override the location with `US_GOV_FEED_STATE`, or the project root with
`US_GOV_PROJECT_DIR`.

## Notes

- Empty windows are normal and are not failures. The House was in recess with zero
  roll call votes from late July to mid-August 2026 while the Senate voted 13 times.
- Nominations are ~40% of Senate votes and have no bill; a filter meaning
  "legislation only" must exclude them explicitly.
- Bill note titles carry the triggering action date, because the feed is
  action-based and a bill that moves twice is two events.
- Judicial branch is not covered. CourtListener would be the source.

## A watermark is only meaningful for the filter that produced it

`watermark`/`next_since` record how far a feed was synced **under the filter used at
the time**. If the filter later widens, previously skipped categories might not
have been synced. Continue without prompting and resolve the window using Step 1a:
an explicit time range in the current filter overrides the watermark; otherwise
resume from `next_since`. `seen_ids` is always safe to trust — it lists what was
actually written.
