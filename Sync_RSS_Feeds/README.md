# Sync RSS Feeds

A Claude Code and Codex recipe that syncs new RSS articles into your Pachinko inbox as markdown notes from the provided RSS feed url.  It requires the following values to be provided.

## Customization

| Variable                       | Description                                                                                                                                                                                                                                                          |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FEED_URL`                   | The url of the RSS feed to sync.  Must be a valid RSS or Atom feed.                                                                                                                                                                                                |
| `FILTER_FILE` *(optional)* | A plain-text file containing instructions for which articles to add to Pachinko. When set, each article's title and content are evaluated against these instructions and only matching articles are added. When not set, all new articles are added unconditionally. |
