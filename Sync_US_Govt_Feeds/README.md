# Sync US Government Feeds

A Claude Code and Codex recipe that syncs Federal Register, House, and Senate activity into Pachinko notes.

On the first run, the recipe limits the initial lookback to seven days unless the filter gives other instructions.

Each feed creates at most 50 notes per run by default after applying the time window and filter. The filter may override this with another positive limit or explicitly request no limit. Additional items beyond a numeric limit remain eligible for the next run.

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
| `FILTER_FILE` *(optional)* | A plain-text file containing filter instructions applied across all three feeds. The instructions can select feeds, specify time ranges, and override the default 50-note per-feed limit, such as “limit Senate to 20” or “no note limit.” When unset or empty, all three feeds are synced without relevance filtering. |
