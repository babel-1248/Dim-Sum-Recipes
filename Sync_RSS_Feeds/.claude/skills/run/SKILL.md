# Sync RSS Feeds

Fetch new articles from every RSS/Atom feed listed in an OPML file, track which articles have already been processed, and report what is new.

## Prerequisites

The environment variable `OPML_FILE` must be set to the absolute path of the OPML file to read. If it is not set, stop immediately and tell the user to set it.

The environment variable `FILTER_FILE` is optional. If set, it must point to a plain-text file containing filtering instructions that describe which articles are worth adding to Pachinko. Articles that do not match the filter are marked as seen but not added to Pachinko.

## State file

All persistent state lives in `feed_state.json` in the project root (next to `CLAUDE.md`). It is a JSON object that maps each feed URL (string) to an array of article IDs (strings) that have already been processed. If the file does not exist, treat it as `{}`.

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

### 3. Load state

Read `feed_state.json` from the project root. If it does not exist, start with an empty object.

### 4. Parse the OPML file

Use a Bash + Python one-liner to extract all feed URLs from the OPML:

```bash
python3 - <<'EOF'
import xml.etree.ElementTree as ET, os, sys
tree = ET.parse(os.environ['OPML_FILE'])
for el in tree.iter('outline'):
    url = el.get('xmlUrl') or el.get('xmlurl')
    if url:
        print(url)
EOF
```

Collect every URL that is printed. If none are found, report that the OPML contained no feeds and stop.

### 5. For each feed URL

For each feed URL collected above:

a. **Fetch the feed** using curl, saving to a temp file:

```bash
curl -s --max-time 30 -A "Mozilla/5.0" "<FEED_URL>" -o /tmp/feed.xml && echo "OK" || echo "FAILED"
```

If the fetch fails, log a warning for that feed and continue to the next one.

b. **Extract articles** from the temp file using the `parse_feed.py` script bundled with this skill (at `<SKILL_DIR>/parse_feed.py`):

```bash
python3 <SKILL_DIR>/parse_feed.py < /tmp/feed.xml
```

The script outputs a JSON array of objects with fields: `id`, `title`, `link`, `published`, `content`. Parse that JSON to get the article list.

c. **Identify new articles**: Compare each article's `id` against the array stored in `feed_state.json` for this feed URL. An article is new if its `id` is NOT in that array.

d. **Convert each new article to markdown** using this structure:

```
{content}

---
**Link:** {link}
**Published:** {published}
```

- Convert the `content` field from HTML to markdown by calling the `html_to_markdown.py` script bundled with this skill **once per article** as a separate Bash command — do not batch multiple articles into a single script:
  ```bash
  echo "<CONTENT_HTML>" | python3 <SKILL_DIR>/html_to_markdown.py
  ```

  Images must appear on their own lines (never inline within a paragraph). All other standard HTML elements (headings, bold, italic, links, lists, code, blockquote) should be converted to their markdown equivalents.
- Append the `link` and `published` metadata as bold-label lines after a horizontal rule.
- If filter instructions are set, evaluate the article against them using the article's title and converted markdown body. Decide **yes** (add to Pachinko) or **no** (skip) based solely on the filter instructions. If filter instructions are `null`, always decide yes.
- If the decision is yes, call `mcp__pachinko__add_note`, passing the rendered markdown as the note content. If the call fails, log a warning and continue — do not abort the rest of the feed.
- If the decision is no, mark the article as seen (step e still applies) but do not call `mcp__pachinko__add_note`.

e. **Update seen IDs**: Replace `feed_state.json[feedUrl]` with exactly the set of article IDs returned by this fetch — do not merge with the previous list. This keeps the state file bounded to whatever the feed currently contains, so IDs for articles that have been removed from the feed are automatically pruned.

### 6. Save updated state

Save the updated state by passing the JSON directly as an argument to the `save_state.py` script. The script writes `feed_state.json` in the current directory (the project root):

```bash
python3 <SKILL_DIR>/save_state.py '<STATE_JSON>'
```

where `<STATE_JSON>` is the full JSON object. Pass it directly as a shell argument — do **not** pipe it through `xargs` or any other command. Do **not** use the Write tool for this step.

### 7. Report results

Print a summary:

- For each feed: feed URL, number of new articles found, how many passed the filter and were added to Pachinko, and the title + link of each new article (noting which were filtered out).
- Grand total: total new articles across all feeds, and total added to Pachinko.
- Confirm that `feed_state.json` has been updated.
