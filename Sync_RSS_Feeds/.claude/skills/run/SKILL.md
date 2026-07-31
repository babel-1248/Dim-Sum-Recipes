# Sync RSS Feeds

Fetch new articles from a single RSS/Atom feed URL, track which articles have already been processed, and add new ones to the Pachinko inbox.

## Prerequisites

The environment variable `FEED_URL` must be set to the RSS/Atom feed URL to read. If it is not set, stop immediately and tell the user to set it.

The environment variable `FILTER_FILE` is optional. If set, it must point to a plain-text file containing filtering instructions that describe which articles are worth adding to Pachinko. Articles that do not match the filter are marked as seen but not added to Pachinko.

## Critical per-article transaction invariant

Treat each article as an isolated transaction. An article is complete only after exactly one of these outcomes:

1. **Filtered out:** `mark_seen.py` succeeded.
2. **Saved:** `mcp__pachinko__add_note` succeeded, `mcp__pachinko__set_note_source` was called next with source type `rss`, then `mark_seen.py` succeeded immediately after the source-setting attempt.
3. **Save failed:** the article was not marked as seen and remains eligible for retry.

After `mcp__pachinko__add_note` succeeds, the very next action must be `mcp__pachinko__set_note_source` for the returned note ID with `source_type` set to `rss`. If source setting fails, retry it once immediately. Whether source setting succeeds or its retry fails, the next action must be the `mark_seen.py` call for that same article so the already-created note is not duplicated on the next run. Do not defer, group, batch, collect, or delegate source-setting or seen-state updates. Do not inspect, filter, convert, save, or otherwise process another article between saving the note and marking its article as seen.

If using subagents, assign exactly one article to each subagent task. A subagent must not return a saved or filtered result until its `mark_seen.py` call succeeds. It must return the article ID, outcome (`saved`, `filtered`, or `save_failed`), saved note ID when applicable, whether its source was set successfully, and whether marking succeeded. Never assign a batch of articles to one subagent task.

The parent must treat a `saved` or `filtered` result without successful marking as incomplete. It must immediately run `mark_seen.py` for that article before accepting another result or assigning more work. The parent must never collect seen-state updates for a later batch.

## Steps

### 1. Check environment variable

Run:

```bash
echo "$FEED_URL"
```

If the output is empty, stop and report: `FEED_URL environment variable is not set.`

### 2. Load filter instructions

Load the filter instructions using the `load_filter.py` script bundled with this skill:

```bash
python3 <SKILL_DIR>/load_filter.py
```

Capture the output. If the output is non-empty, hold it in memory as the **filter instructions**. If the output is empty (or the script exits with an error), set filter instructions to `null` — all new articles will be added to Pachinko unconditionally.

### 3. Fetch and inspect the feed

**Fetch, parse, and compare state without modifying it** using `check_feed.py`:

```bash
python3 <SKILL_DIR>/check_feed.py <STATE_FILE_PATH> "$FEED_URL" <SKILL_DIR>
```

where `<STATE_FILE_PATH>` is the absolute path to `feed_state.json` in the project root.

The script fetches the feed URL internally, parses it, compares article IDs against the seen list in state, and prints results:

- **No output** — no new articles
- **JSON array** — new articles found: `[{ "id": "...", "title": "...", "link": "...", "published": "...", "content": "..." }, ...]`
- **`{"error": "..."}`** — fetch failed (report the error and stop)

`check_feed.py` never changes `feed_state.json`. Each article remains eligible for a later run until step 4 explicitly marks it as seen.

`mark_seen.py` serializes concurrent updates with a lock file, so it is safe for multiple subagents to acknowledge different articles at the same time.

If there are no new articles (empty output), report that and stop.

**Large output handling:** When `check_feed.py` output is too large to display inline, the runtime saves it to a file and shows a path. In that case, use `get_article.py` to work with the file:

```bash
# List all articles (index, title, link, published — tab-separated):
python3 <SKILL_DIR>/get_article.py <saved_file>

# Get HTML content of article at index N (0-based), ready to pipe:
python3 <SKILL_DIR>/get_article.py <saved_file> <N>
```

Use the list output to evaluate filter decisions. Use the content output (index N) to pipe into `html_to_markdown.py`. Obtain title, link, and published from the list output for the note metadata.

### 4. For each new article in the output

Process each article as the isolated transaction defined above. Finish its save/filter and marking sequence before starting another article. When subagents are available, each subagent task receives exactly one article and these same transaction rules.

- If filter instructions are set, evaluate the article against them using the article's title and raw HTML `content` field. Decide **yes** (add to Pachinko) or **no** (skip). If filter instructions are `null`, always decide yes.
- If no, mark the article as seen immediately:

  ```bash
  python3 <SKILL_DIR>/mark_seen.py <STATE_FILE_PATH> "$FEED_URL" "{article_id}"
  ```

  If marking it fails, report the transaction as incomplete. Do not process another article in this worker. It will be retried on a later run.
- If yes, convert the `content` field from HTML to markdown. When output was inline, pipe the raw HTML via a quoted heredoc:

  ```bash
  python3 <SKILL_DIR>/html_to_markdown.py << 'HTMLEOF'
  {raw_html_content}
  HTMLEOF
  ```

  When output was saved to a file, use the `convert` mode of `get_article.py`:

  ```bash
  python3 <SKILL_DIR>/get_article.py <saved_file> <N> convert
  ```

  Use the script's output **verbatim** as the note body — do not rewrite, summarize, or simplify it. Images must appear on their own lines (never inline within a paragraph). All other standard HTML elements (headings, bold, italic, links, lists, code, blockquote) should be converted to their markdown equivalents.

- Append metadata after a horizontal rule:

  ```
  {converted_content}

  ---
  **Link:** {link}
  **Published:** {published}
  ```

- Call `mcp__pachinko__add_note` with the rendered markdown.
  - If the call fails, log a warning and continue **without marking the article as seen**, so it can be retried on a later run.
  - If the call succeeds and returns the saved note, immediately call `mcp__pachinko__set_note_source` with:
    - `note_id`: the note ID returned by `add_note`
    - `source_type`: `rss`
  - If `set_note_source` fails, retry it once immediately. If the retry also fails, report the article ID and Pachinko note ID, then continue to `mark_seen.py` so the already-created note is not duplicated on the next run.
  - Immediately after source setting succeeds or its retry fails, mark the article as seen:

    ```bash
    python3 <SKILL_DIR>/mark_seen.py <STATE_FILE_PATH> "$FEED_URL" "{article_id}"
    ```

    If marking it fails, report the transaction as incomplete. Do not process another article in this worker. The saved note may be encountered again on the next run, so clearly report the state-update failure.

### 5. Report results

Print a summary:

- Feed URL, number of new articles found, how many passed the filter and were added to Pachinko, and the title + link of each new article (noting which were filtered out).
- Confirm how many articles were marked as seen. Do not claim `feed_state.json` was updated for any article whose `mark_seen.py` call failed.
- Report any saved article whose source could not be set to `rss` after the retry.
- For subagent work, verify that every `saved` or `filtered` result reports successful marking. Immediately repair any incomplete result before finalizing the summary; never repair them as a batch.
