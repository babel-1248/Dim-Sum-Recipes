---
name: run
description: The main recipe entrypoint. Syncs new X/Twitter bookmarks via fieldtheory and adds each new one to the Pachinko inbox as a markdown note. Run this whenever the user says "run".
argument-hint: ""
---

## Purpose

Sync new X/Twitter bookmarks and push each new one into the Pachinko inbox. This is the full pipeline:

1. **Sync** — pull new bookmarks from X via fieldtheory
2. **Diff** — identify bookmarks not yet sent to Pachinko
3. **Push** — add each new bookmark to the Pachinko inbox as a markdown note
4. **Track** — record sent IDs so they aren't duplicated on next run

---

## Step 1 — Check Node.js and fieldtheory are installed

```!
if ! command -v node &>/dev/null; then
  echo "NODE_NOT_INSTALLED"
elif ! command -v fieldtheory &>/dev/null; then
  echo "NODE: $(node --version)"
  echo "FIELDTHEORY_NOT_INSTALLED"
else
  echo "NODE: $(node --version)"
  echo "INSTALLED: $(fieldtheory --version 2>/dev/null || echo 'version unknown')"
fi
```

If `NODE_NOT_INSTALLED`: tell the user to install Node.js 20+ first (via https://nodejs.org or `brew install node`) and stop. Do not proceed.

If `FIELDTHEORY_NOT_INSTALLED`: install fieldtheory, then continue:

```sh
npm install -g fieldtheory
```

---

## Step 2 — Sync bookmarks from X

Run an incremental sync (reads Chrome session by default):

```sh
fieldtheory sync --yes
```

If sync fails due to auth, tell the user to open Chrome and log into x.com first, then retry. If they haven't set up OAuth, `fieldtheory auth` can be used for API-based sync.

---

## Step 3 — Load all bookmarks from the JSONL cache

Read the local cache file — one JSON object per line:

```sh
.claude/scripts/ft-read-bookmarks.sh
```

Parse each line as a JSON object. Relevant fields:
- `id` — tweet ID (string)
- `text` — full tweet text
- `authorHandle` — author handle (no @)
- `url` — direct URL to the tweet
- `postedAt` — timestamp string when the tweet was posted (e.g. `"Sun Apr 12 18:30:11 +0000 2026"`)
- `media` — array of image URLs (may be absent or empty)

Save this output for use in the next steps.

---

## Step 4 — Load the tracking file

The tracking file lives at `~/.ft-bookmarks/.pachinko-sent-ids` — one tweet ID per line.

```sh
.claude/scripts/ft-read-sent.sh
```

> **Important:** Always run these scripts using their relative path (`.claude/scripts/...`), never the absolute path. The allow rules in `settings.json` match on relative paths — using an absolute path will trigger a permission prompt.

Parse the output into a set of already-sent IDs.

---

## Step 5 — Identify new bookmarks

Compare the `id` field from each bookmark against the already-sent IDs set. New bookmarks are those whose `id` is NOT in the tracking file.

If there are no new bookmarks, report "No new bookmarks to sync." and stop.

---

## Step 6 — Add each new bookmark to the Pachinko inbox

For each new bookmark, call `mcp__pachinko__add_note` with:

- **`note_title`**: `@{authorHandle}: {first 80 chars of text, stripped of newlines}` — truncate with `…` if needed
- **`note_body`**: formatted markdown (see template below)
- No `to_` parameters (this places the note in the inbox)

### Note body template

```markdown
**[@{authorHandle}](https://x.com/{authorHandle})** · [View on X]({url})

{full tweet text}

{images — see below}

{quoted tweet — see below}

---
*Posted: {postedAt formatted as YYYY-MM-DD}*
```

If the bookmark has a `primaryCategory` field that is not `"unclassified"`, append it:

```markdown
*Posted: {date} · {primaryCategory}*
```

**Images:** if the `media` array is non-empty, add each URL as a markdown image after the tweet text, with a blank line between each image:

```markdown
![image](https://pbs.twimg.com/media/...)

![image](https://pbs.twimg.com/media/...)
```

If `media` is absent or empty, omit the image block entirely (no blank lines or placeholders).

**Quoted tweet:** if the bookmark has a `quotedTweet` field, add a blockquote section after the images (or after the tweet text if no images):

```markdown
> **[@{quotedTweet.authorHandle}](https://x.com/{quotedTweet.authorHandle})** · [View on X]({quotedTweet.url})
> {quotedTweet.text}
```

If `quotedTweet` is absent, omit the block entirely.

Process new bookmarks **oldest first** (ascending by `postedAt`) so the inbox order is chronological.

---

## Step 7 — Update the tracking file

After successfully adding a bookmark to Pachinko, immediately append its ID to the tracking file:

```sh
.claude/scripts/ft-append-sent.sh {id}
```

Do this per-bookmark (not batched at the end) so a failure midway doesn't lose progress.

---

## Step 8 — Report results

When done, output a summary:

```
Synced {N} new bookmark(s) to Pachinko inbox.
```

If any individual `add_note` call fails, report the failed bookmark's ID and text, then continue with the rest.
