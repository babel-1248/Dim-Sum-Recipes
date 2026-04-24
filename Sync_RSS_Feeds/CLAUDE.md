# Sync RSS Feeds Recipe

A Claude Code recipe that fetches new articles from every RSS/Atom feed listed in an OPML file, filters them against optional instructions, and adds matching articles to your Pachinko inbox as markdown notes.

## Usage

Say **"run"** to execute the full pipeline:
1. Parse your OPML file to get all feed URLs
2. Fetch new articles from each feed (unseen articles only)
3. Optionally filter articles against your filter instructions
4. Add matching articles to the Pachinko inbox as markdown notes
5. Update state so nothing is processed twice

## Setup

1. Set the `OPML_FILE` environment variable to the absolute path of your OPML file
2. Optionally set `FILTER_FILE` to a plain-text file with filtering instructions
3. Open this project in Claude Code — skills load automatically
4. Say "run" to start

## Skills included

| Skill | Description |
|-------|-------------|
| `run` | Full pipeline: parse OPML → fetch new articles → filter → add to Pachinko inbox |

## State

Seen article IDs are tracked in `feed_state.json` in the project root, keyed by feed URL. Only articles with IDs not present in that file are treated as new.
