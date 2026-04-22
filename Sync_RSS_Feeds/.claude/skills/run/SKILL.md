# Sync RSS Feeds

Fetch new articles from every RSS/Atom feed listed in an OPML file, track which articles have already been processed, and report what is new.

## Prerequisites

The environment variable `OPML_FILE` must be set to the absolute path of the OPML file to read. If it is not set, stop immediately and tell the user to set it.

## State file

All persistent state lives in `feed_state.json` in the project root (next to `CLAUDE.md`). It is a JSON object that maps each feed URL (string) to an array of article IDs (strings) that have already been processed. If the file does not exist, treat it as `{}`.

## Steps

### 1. Check environment variable

Run:
```bash
echo "$OPML_FILE"
```
If the output is empty, stop and report: `OPML_FILE environment variable is not set.`

### 2. Load state

Read `feed_state.json` from the project root. If it does not exist, start with an empty object.

### 3. Parse the OPML file

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

### 4. For each feed URL

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
The script outputs a JSON array of objects with fields: `id`, `title`, `link`, `published`, `summary`. Parse that JSON to get the article list.

c. **Identify new articles**: Compare each article's `id` against the array stored in `feed_state.json` for this feed URL. An article is new if its `id` is NOT in that array.

d. **Collect new articles** for later processing. Do not add them to Pachinko yet — that integration will be added in a future step.

e. **Mark all articles as seen**: Merge every article `id` from this fetch (new and old) into `feed_state.json[feedUrl]`, deduplicating. This prevents re-processing on the next run even if the Pachinko step hasn't been implemented yet.

### 5. Save updated state

Write the updated `feed_state.json` back to the project root using the Write tool.

### 6. Report results

Print a summary:
- For each feed: feed URL, number of new articles found, and the title + link of each new article.
- Grand total: total new articles across all feeds.
- Confirm that `feed_state.json` has been updated.
