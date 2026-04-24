# Sync RSS Feeds Recipe

## Skills

### /run

Fetches new articles from every RSS/Atom feed listed in an OPML file and tracks which articles have already been processed.

**Requires:** The `OPML_FILE` environment variable must be set to the absolute path of the OPML file.

**State:** Seen article IDs are persisted in `feed_state.json` in the project root, keyed by feed URL. Only articles with IDs not present in that file are treated as new.

**Note:** Adding new articles to Pachinko's inbox is not yet implemented — the skill currently reports new articles and updates state only.
