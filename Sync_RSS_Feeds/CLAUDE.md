# Sync RSS Feeds Recipe

A Claude Code recipe that fetches new articles from an RSS/Atom feed, filters them against optional instructions, and adds matching articles to your Pachinko inbox as markdown notes.

## Usage

Say **"run"** to execute the full pipeline:
1. Fetch new articles from the feed (unseen articles only)
2. Optionally filter articles against your filter instructions
3. Add matching articles to the Pachinko inbox as markdown notes
4. Update state so nothing is processed twice

## Setup

1. Set the `FEED_URL` environment variable to the RSS/Atom feed URL
2. Optionally set `FILTER_FILE` to a plain-text file with filtering instructions
3. Open this project in Claude Code — skills load automatically
4. Say "run" to start

## Skills included

| Skill | Description |
|-------|-------------|
| `run` | Full pipeline: fetch new articles → filter → add to Pachinko inbox |

## State

Seen article IDs are tracked in `feed_state.json` in the project root, keyed by feed URL. Only articles with IDs not present in that file are treated as new.
