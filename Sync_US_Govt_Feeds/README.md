# Sync US Govt Feeds

A Claude Code and Codex recipe that syncs Federal Register, House, and Senate activity into Pachinko notes.

On the first run, the recipe limits the initial lookback to one calendar month unless the filter gives other instructions.

A filter file is highly recommended as this recipe can generate large numbers of notes.

## Customization

| Variable | Description |
| --- | --- |
| `FILTER_FILE` *(optional)* | A plain-text file containing filter instructions applied across all three feeds. The instructions can also select feeds, such as “skip the Senate” or “only executive.” When unset or empty, all three feeds are synced without relevance filtering. |
