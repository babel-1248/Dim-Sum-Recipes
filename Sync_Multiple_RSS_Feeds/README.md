# Sync Multiple RSS Feeds

A Claude Code and Codex recipe that syncs new RSS articles from multiple feeds into your Pachinko inbox as markdown notes. Feeds are processed sequentially in the order supplied.

## Customization

| Variable                       | Description                                                                                                                                                                                                                                                          |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FEED_FILE` *(required)* | A text file containing or describing how to obtain an ordered RSS/Atom feed list. The bundled Python helper reads the file, then the LLM interprets its contents. Common inputs include URL lists, OPML, other feed exports, and natural-language instructions. |
| `FILTER_FILE` *(optional)* | A plain-text file containing instructions for which articles to add to Pachinko. When set, each article's title and content are evaluated against these instructions and only matching articles are added. When not set, all new articles are added unconditionally. |

The LLM interprets `FEED_FILE` flexibly and preserves its intended source order. Duplicate URLs are processed only at their first occurrence. Instructions can point the agent to another local file, webpage, or connected source containing the feed list.
