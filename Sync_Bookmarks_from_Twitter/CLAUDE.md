# XBookmarks Recipe

A Claude Code recipe that syncs new X/Twitter bookmarks into your Pachinko inbox as markdown notes, using [fieldtheory-cli](https://github.com/afar1/fieldtheory-cli).

## Usage

Say **"run"** to execute the full pipeline:
1. Sync new bookmarks from X (via Chrome session)
2. Find bookmarks not yet sent to Pachinko
3. Add each one to the Pachinko inbox as a markdown note
4. Track sent IDs so nothing is duplicated

## Setup

1. Ensure Node.js 20+ is installed
2. Open Chrome and log into x.com (used for session-based sync)
3. Open this project in Claude Code — skills load automatically
4. Say "run" to start

## Skills included

| Skill | Description |
|-------|-------------|
| `run` | Full pipeline: sync bookmarks → push new ones to Pachinko inbox |
| `fieldtheory` | Direct access to fieldtheory-cli (sync, search, classify, export) |

## State

Processed bookmark IDs are tracked in `~/.ft-bookmarks/.pachinko-sent-ids` so each bookmark is only added to Pachinko once.
