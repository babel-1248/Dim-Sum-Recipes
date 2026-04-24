# Sync RSS Feeds

Fetch new articles from every RSS/Atom feed listed in an OPML file, track which articles have already been processed, and report what is new.

## Prerequisites

The environment variable `OPML_FILE` must be set to the absolute path of the OPML file to read. If it is not set, stop immediately and tell the user to set it.

The environment variable `FILTER_FILE` is optional. If set, it must point to a plain-text file containing filtering instructions that describe which articles are worth adding to Pachinko. Articles that do not match the filter are marked as seen but not added to Pachinko.

## Steps

### 1. Check environment variable

Run:

```bash
echo "$OPML_FILE"
```

If the output is empty, stop and report: `OPML_FILE environment variable is not set.`

### 2. Load filter instructions

Load the filter instructions using the `load_filter.py` script bundled with this skill:

```bash
python3 <SKILL_DIR>/load_filter.py
```

Capture the output. If the output is non-empty, hold it in memory as the **filter instructions**. If the output is empty (or the script exits with an error), set filter instructions to `null` — all new articles will be added to Pachinko unconditionally.

### 3. Parse the OPML file

Use the `parse_opml.py` script bundled with this skill:

```bash
python3 <SKILL_DIR>/parse_opml.py
```

Collect every URL that is printed. If none are found, report that the OPML contained no feeds and stop.

### 4. For each feed URL

**Process feeds one at a time, in sequence. Each feed must be its own separate `check_feed.py` invocation, issued as an individual tool call. Complete ALL sub-steps (check_feed → html_to_markdown → filter decision → add_note) for one feed before issuing the next `check_feed.py` call.**

**This rule is absolute regardless of how many feeds are in the OPML file. Do NOT, under any circumstances:**
- Write a wrapper script (bash, python, or otherwise) that loops over multiple feed URLs and calls `check_feed.py` for each.
- Chain multiple `check_feed.py` invocations in a single bash command (via `for`, `while`, `xargs`, `&&`, `;`, pipelines, etc.).
- Collect articles from multiple feeds into a batch before filtering/converting/adding them.
- "Optimize" by fetching titles first across all feeds and doing content conversion later.

Scale is never a reason to batch. If there are 500 feeds, that is 500 individual `check_feed.py` tool calls, each followed by its full sub-step chain before the next one. When reporting progress to the user, label each feed individually (e.g. "Feed 34: https://example.com/feed"). Do not group multiple feeds under a single heading.

For each feed URL collected above:

a. **Fetch, parse, and save state** in one step using `check_feed.py`:

```bash
python3 <SKILL_DIR>/check_feed.py <STATE_FILE_PATH> <FEED_URL> <SKILL_DIR>
```

where `<STATE_FILE_PATH>` is the absolute path to `feed_state.json` in the project root.

The script fetches the feed URL internally, parses it, compares article IDs against the seen list in state, **saves the updated state to disk immediately**, and prints results:

- **No output** — no new articles (move on to the next feed)
- **JSON array** — new articles found: `[{ "id": "...", "title": "...", "link": "...", "published": "...", "content": "..." }, ...]`
- **`{"error": "..."}`** — fetch failed (log a warning and continue to the next feed)

If there are no new articles (empty output), move on to the next feed.

b. **For each new article in the output**, convert it to markdown and optionally add to Pachinko:

- Convert the `content` field from HTML to markdown. Write the raw HTML to `/tmp/article_content.html` using the Write tool, then run:

  ```bash
  python3 <SKILL_DIR>/html_to_markdown.py < /tmp/article_content.html
  ```

  Use the script's output **verbatim** as the note body — do not rewrite, summarize, or simplify it. Images must appear on their own lines (never inline within a paragraph). All other standard HTML elements (headings, bold, italic, links, lists, code, blockquote) should be converted to their markdown equivalents.

- Append metadata after a horizontal rule:

  ```
  {converted_content}

  ---
  **Link:** {link}
  **Published:** {published}
  ```

- If filter instructions are set, evaluate the article against them using the article's title and converted markdown body. Decide **yes** (add to Pachinko) or **no** (skip). If filter instructions are `null`, always decide yes.
- If yes, call `mcp__pachinko__add_note` with the rendered markdown. If the call fails, log a warning and continue.
- If no, the article is already marked as seen (state was saved in step a) — no further action needed.

### 5. Report results

Print a summary:

- For each feed: feed URL, number of new articles found, how many passed the filter and were added to Pachinko, and the title + link of each new article (noting which were filtered out).
- Grand total: total new articles across all feeds, and total added to Pachinko.
- Confirm that `feed_state.json` has been updated.
