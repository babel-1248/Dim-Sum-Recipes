# Sync RSS Feeds

A Claude Code recipe that syncs new RSS articles into your Pachinko inbox as markdown notes from an OPML file.  It requires the following values to be provided.

## Customization

| Variable                         | Description                                                                                                                                                                                                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `OPML_FILE` | The OPML file containing the list of RSS feeds to sync. Must be set to a valid OPML file path.                                                                                                                                                                       |
| `FILTER_FILE` *(optional)*| A plain-text file containing instructions for which articles to add to Pachinko. When set, each article's title and content are evaluated against these instructions and only matching articles are added. When not set, all new articles are added unconditionally. |
