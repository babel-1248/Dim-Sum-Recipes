# Sync RSS Feeds

Fetch new articles from a single RSS/Atom feed URL, track which articles have already been processed, and add new ones to the Pachinko inbox.

## Prerequisites

The environment variable `FEED_URL` must be set to the RSS/Atom feed URL to read. If it is not set, stop immediately and tell the user to set it.

The environment variable `FILTER_FILE` is optional. If set, it must point to a plain-text file containing filtering instructions that describe which articles are worth adding to Pachinko. Articles that do not match the filter are marked as seen but not added to Pachinko.

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

### 3. Fetch and process the feed

**Fetch, parse, and save state** in one step using `check_feed.py`:

```bash
python3 <SKILL_DIR>/check_feed.py <STATE_FILE_PATH> "$FEED_URL" <SKILL_DIR>
```

where `<STATE_FILE_PATH>` is the absolute path to `feed_state.json` in the project root.

The script fetches the feed URL internally, parses it, compares article IDs against the seen list in state, **saves the updated state to disk immediately**, and prints results:

- **No output** — no new articles
- **JSON array** — new articles found: `[{ "id": "...", "title": "...", "link": "...", "published": "...", "content": "..." }, ...]`
- **`{"error": "..."}`** — fetch failed (report the error and stop)

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

- If filter instructions are set, evaluate the article against them using the article's title and raw HTML `content` field. Decide **yes** (add to Pachinko) or **no** (skip). If filter instructions are `null`, always decide yes.
- If no, the article is already marked as seen (state was saved in step 3) — no further action needed.
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

- Call `mcp__pachinko__add_note` with the rendered markdown. If the call fails, log a warning and continue.

### 5. Report results

Print a summary:

- Feed URL, number of new articles found, how many passed the filter and were added to Pachinko, and the title + link of each new article (noting which were filtered out).
- Confirm that `feed_state.json` has been updated.
