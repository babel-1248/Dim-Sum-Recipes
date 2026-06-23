# Sync Youtube Feed

A Claude Code and Codex recipe that syncs Youtube transcripts for new videos in a channel into your Pachinko inbox as markdown notes.  It requires the following values to be provided.

## Customization

| Variable                       | Description                                                                                                                                                                                                                                                       |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CHANNEL_URL`                | The url of the Youtube channel to sync.  Must be a valid Youtube channel.                                                                                                                                                                                       |
| `FILTER_FILE` *(optional)* | A plain-text file containing instructions for which videos to add to Pachinko. When set, each video's title and transcript are evaluated against these instructions and only matching videos are added. When not set, all new videos are added unconditionally. |
