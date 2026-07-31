# Sync Youtube Channel

Fetch new new videos from a single Youtube channel URL, track which videos have already been processed, and add new ones to the Pachinko inbox.

## Prerequisites

The environment variable `CHANNEL_URL` must be set to the Youtube channel to read. If it is not set, stop immediately and tell the user to set it.

The environment variable `FILTER_FILE` is optional. If set, it must point to a plain-text file containing filtering instructions that describe which videos are worth adding to Pachinko. Videos that do not match the filter are marked as seen but not added to Pachinko.

## Steps

### 1. Check Node.js is installed

Run:

```!
if ! command -v node &>/dev/null; then
  echo "NODE_NOT_INSTALLED"
else
  NODE_VERSION="$(node --version)"
  echo "NODE: $NODE_VERSION"
  NODE_MAJOR="${NODE_VERSION#v}"
  NODE_MAJOR="${NODE_MAJOR%%.*}"
  if [ "$NODE_MAJOR" -lt 20 ]; then
    echo "NODE_VERSION_TOO_OLD"
  else
    echo "NODE_VERSION_OK"
  fi
fi
```

If `NODE_NOT_INSTALLED`: tell the user to install Node.js 20+ first (via https://nodejs.org or `brew install node`) and stop. Do not proceed.

If `NODE_VERSION_TOO_OLD`: tell the user that Node.js 20+ is required, report the installed version, and stop. Do not proceed.

### 2. Check environment variable

Run:

```bash
echo "$CHANNEL_URL"
```

If the output is empty, stop and report: `CHANNEL_URL environment variable is not set.`

### 3. Load filter instructions

Load the filter instructions using the `load_filter.py` script bundled with this skill:

```bash
python3 <SKILL_DIR>/load_filter.py
```

Capture the output. If the output is non-empty, hold it in memory as the **filter instructions**. If the output is empty (or the script exits with an error), set filter instructions to `null` — all new videos will be added to Pachinko unconditionally.

### 4. Resolve the channel RSS feed URL

Fetch the Youtube channel page with `curl`, then extract the RSS feed URL from the page's alternate RSS link:

```bash
CHANNEL_HTML_FILE="$(mktemp)"
curl -LfsS -A "Mozilla/5.0" "$CHANNEL_URL" -o "$CHANNEL_HTML_FILE"
python3 <SKILL_DIR>/extract_feed_url.py "$CHANNEL_HTML_FILE" "$CHANNEL_URL"
```

The extractor looks for:

```html
<link rel="alternate" type="application/rss+xml" href="...">
```

Capture the script output as `FEED_URL`. If `curl` fails, or if `extract_feed_url.py` reports that no RSS alternate link was found, report the error and stop.

### 5. Fetch and process the feed

**Fetch and parse the feed** using `check_feed.py`:

```bash
python3 <SKILL_DIR>/check_feed.py <STATE_FILE_PATH> "$FEED_URL" <SKILL_DIR>
```

where `<STATE_FILE_PATH>` is the absolute path to `feed_state.json` in the project root.

The script fetches the feed URL internally, parses it, compares video IDs against the seen list in state, does **not** update state, and prints results:

- **No output** — no new videos
- **JSON array** — new videos found: `[{ "id": "...", "title": "...", "link": "...", "published": "...", "description": "...", "thumbnail": "..." }, ...]`
- **`{"error": "..."}`** — fetch failed (report the error and stop)

If there are no new videos (empty output), report that and stop.

Save the JSON array to a temporary file and use `get_video.py` to work with it. When `check_feed.py` output is too large to display inline, the runtime saves it to a file and shows a path; use that saved file as `<videos_file>`.

```bash
# List all videos (index, title, link, published, thumbnail, description — tab-separated):
python3 <SKILL_DIR>/get_video.py <videos_file>

# Get feed description of video at index N (0-based):
python3 <SKILL_DIR>/get_video.py <videos_file> <N>
```

Use the list output to evaluate filter decisions. Obtain title, link, published, thumbnail, and description from the list output for the note metadata.

### 6. Extract video content and transcript with Defuddle, then write markdown notes

- If filter instructions are set, evaluate the video against them using the video's title and feed description. Decide **yes** (add to Pachinko) or **no** (skip). If filter instructions are `null`, always decide yes.
- If no, do not mark the video as seen and do not add it to Pachinko.
- If yes, fetch the video link and use Defuddle through `get_video.py` to extract the video page content and transcript as markdown:

  ```bash
  python3 <SKILL_DIR>/get_video.py <videos_file> <N> markdown
  ```

  This runs `npx defuddle parse <link> --markdown` to extract the video page content and transcript, prepends the video title as a level 1 header, and then prepends the `media:thumbnail` image from the feed when one is present. Use the script's output **verbatim** as the note body — do not rewrite, summarize, or simplify it.
- The rendered note body must be structured as:

  ```
  # {title}

  ![{title}]({thumbnail})

  {defuddle_markdown}
  ```

- Append metadata after a horizontal rule:

  ```
  {rendered_note_body}

  ---
  **Link:** {link}
  **Published:** {published}
  **Description:** {description}
  ```
- Write the full rendered markdown to a temporary `.md` file, then call `mcp__pachinko__add_note` with `note_body_file_path` pointing to that file. Never send the full rendered video note in the `note_body` parameter. If the call fails, log a warning and continue.
- After `mcp__pachinko__add_note` succeeds, capture the returned note ID and immediately call `mcp__pachinko__set_note_source` with:
  - `note_id`: the note ID returned by `add_note`
  - `source_type`: `youtube`
- The `set_note_source` call must be the next action after `add_note`, before marking the video as seen or processing another video. If it fails, retry it once immediately. If the retry also fails, report the video ID and Pachinko note ID, then continue to the seen-state update so the already-created note is not duplicated on the next run.
- Immediately after source setting succeeds or its retry fails, mark only that video as seen:

  ```bash
  python3 <SKILL_DIR>/mark_video_seen.py <STATE_FILE_PATH> "$FEED_URL" "{video_id}"
  ```

  Do not update `feed_state.json` before the note has been successfully added.

### 7. Report results

Print a summary:

- Channel URL, resolved feed URL, number of new videos found, how many passed the filter and were added to Pachinko, and the title + link of each new video (noting which were filtered out).
- Confirm that `feed_state.json` has been updated only for videos successfully added to Pachinko.
- Report any saved video whose source could not be set to `youtube` after the retry.
