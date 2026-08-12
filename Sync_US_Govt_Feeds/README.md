# Sync US Governmentt Feeds

A Claude Code and Codex recipe that syncs Federal Register, House, and Senate activity into Pachinko notes.

On the first run, the recipe limits the initial lookback to seven days unless the filter gives other instructions.

Each feed creates at most 50 notes per run after applying the time window and filter. Additional matching items remain eligible for the next run.

A filter file is highly recommended as this recipe can generate large numbers of notes. The Federal Registry produces a large number of rules a day.

A simple example:

```
Feeds to sync

[ ] Executive — Federal Register
[ ] House of Representatives
[X] Senate
```

## Customization

| Variable | Description |
| --- | --- |
| `FILTER_FILE` *(optional)* | A plain-text file containing filter instructions applied across all three feeds. The instructions can also select feeds, such as “skip the Senate” or “only executive.” When unset or empty, all three feeds are synced without relevance filtering. |
