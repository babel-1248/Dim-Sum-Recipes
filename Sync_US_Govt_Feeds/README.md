# Sync US Govt Feeds

A Claude Code and Codex recipe that syncs Federal Register, House, and Senate activity into Pachinko notes.

Say **"run"** to execute the bundled workflow. The feeds run in order: executive, House, then Senate.

## Customization

| Variable | Description |
| --- | --- |
| `FILTER_FILE` *(optional)* | A plain-text file containing filter instructions applied across all three feeds. The instructions can also select feeds, such as “skip the Senate” or “only executive.” When unset or empty, all three feeds are synced without relevance filtering. |
