# News Flash

Search the web for genuinely new information about a topic since the last time this recipe was run, and add a note to the Pachinko inbox only when new content is found.

## Time handling

All datetimes are stored and compared in **UTC**. When loading state or writing state, convert to/from UTC explicitly. The current UTC time must be obtained at the start of the run and used consistently throughout.

Obtain the current UTC datetime with:

```bash
python3 -c "from datetime import timezone, datetime; print(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))"
```

Use this value as `now_utc` for the remainder of the run.

## Prerequisites

The environment variable `SEARCH_QUERY` must be set to the topic to monitor. If it is not set, stop immediately and report: `SEARCH_QUERY environment variable is not set.`

## Steps

### 1. Check environment variable and get current time

**Run these as two separate commands — do NOT combine them into a single Bash call.**

First, check the environment variable:

```bash
echo "$SEARCH_QUERY"
```

If the output is empty, stop and report: `SEARCH_QUERY environment variable is not set.`

Only after confirming `SEARCH_QUERY` is set, obtain `now_utc` by running this as a separate command:

```bash
python3 -c "from datetime import timezone, datetime; print(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))"
```

Use the output as `now_utc` for the remainder of the run.

### 2. Load state

Read `news_flash_state.json` from the project root using:

```bash
python3 -c "import json,sys; d=json.load(open('news_flash_state.json')); print(json.dumps(d))" 2>/dev/null
```

- If the file does not exist or the command fails: set `since_datetime` to exactly 7 days before `now_utc`. Set `last_note` to `null`.
- If the file exists: use the stored `last_run` value (which is in UTC) as `since_datetime`, and load `last_note` (which contains `urls`, `headlines`, and `key_facts` from the previous run).

### 3. Plan and execute searches

Formulate 4–6 search queries that explicitly target content published after `since_datetime`. Because this recipe may run multiple times in a single day, include the full timestamp — not just the date — in recency signals. For example:

- `{SEARCH_QUERY} news after {Month DD YYYY HH:MM UTC}`
- `{SEARCH_QUERY} latest {Month DD YYYY}`
- `{SEARCH_QUERY} breaking news {current UTC date}`

Cover these angles:

- Breaking news and new announcements
- New analysis, reports, or commentary
- New people, products, or events that have emerged
- Follow-ups or developments on previously known storylines

Run each query using the WebSearch tool. For every result collect: title, URL, publication datetime (in UTC where available), and enough detail to write a substantive summary — specifically: what happened, who is involved, any numbers or specifics mentioned, and what the implications or next steps are.

### 4. Filter for genuinely new content

Apply the following filters in order. Discard any result that fails any check:

**Structural filters (quick):**

- The publication date is on or before `since_datetime` — discard it.
- The URL already appears in `last_note.urls` — discard it.
- The headline is substantially identical to one in `last_note.headlines` — discard it.

**Semantic filter (the critical one):**
For each result that survives the structural filters, ask: *does this article describe an event, announcement, or development that was already covered in the previous note?* Compare against `last_note.key_facts`. If the underlying fact is the same — even if the URL, headline, publisher, or wording differ — discard it.

Examples of what to discard:

- A second article about a player waiving that was already reported last run.
- A live tracker page (e.g., CBS Sports roster tracker, Ball is Life cut tracker, ESPN transactions) that has been updated but whose new content merely restates events already in `last_note.key_facts`.
- An aggregator or recap that summarises events older than `since_datetime`.

A result passes only if it describes a **new event** (something that happened after `since_datetime`) that is **not semantically equivalent** to any item in `last_note.key_facts`.

If no results remain after filtering, do not update the state file (see Step 7) and stop with the message: `No new news found for "{SEARCH_QUERY}" since {since_datetime} UTC.`

### 5. Compile the note

Synthesise the filtered results into a markdown note using the format below. Use only information from the search results — do not speculate or invent.

```
# {SEARCH_QUERY} — News Flash

**Query:** {SEARCH_QUERY}
**Covering:** {since_datetime as YYYY-MM-DD HH:MM UTC} → {now_utc as YYYY-MM-DD HH:MM UTC}

## New Developments

- **[Headline](URL)** — Publisher, Date/Time UTC

  What happened and who is involved. Include any specific numbers, names, locations, or
  timelines from the source. Close with the implication or what to watch for next.
  Aim for 3–5 sentences — enough that the reader does not need to click the link to act on the information.

(repeat for each new item)

## Sources

1. [Title](URL) — Publisher, Date/Time UTC
(repeat)
```

### 6. Add to Pachinko

Call `mcp__pachinko__add_note` with:

- `note_title`: `{SEARCH_QUERY} — News Flash`
- `note_body`: the compiled note markdown

If the call fails, print the full note to the terminal and report the error. **Do not update the state file** — leave `last_run` and `last_note` unchanged so the next run retries with the same since datetime.

After `mcp__pachinko__add_note` succeeds, capture the returned note ID and immediately call `mcp__pachinko__set_note_source` with:

- `note_id`: the note ID returned by `add_note`
- `source_type`: `webpage`

The `set_note_source` call must be the next action after `add_note`, before updating the state file. If it fails, retry it once immediately. If the retry also fails, report the Pachinko note ID, then continue to the state update so the already-created News Flash note is not duplicated on the next run.

### 7. Update state file

Write `news_flash_state.json` in the project root:

```bash
python3 -c "
import json
state = {
    'last_run': '{now_utc as YYYY-MM-DD HH:MM UTC}',
    'last_note': {
        'title': '{note_title}',
        'urls': {json list of every URL cited in the note},
        'headlines': {json list of every headline string in the note},
        'key_facts': {json list of one-sentence summaries of each distinct event or development reported in the note — these are used to semantically filter the next run}
    }
}
json.dump(state, open('news_flash_state.json', 'w'), indent=2)
"
```

Substitute the actual values before running this command.

### 8. Report results

Print a brief summary:

- Query monitored
- Coverage window (`since_datetime UTC` → `now_utc`)
- Number of new items found and added to Pachinko
- Confirmation that `news_flash_state.json` was updated
- Whether the saved note's source was set to `webpage`; report its note ID if source setting failed after the retry
