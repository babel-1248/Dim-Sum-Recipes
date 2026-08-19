# Sync US Weather

A Claude Code and Codex recipe that maintains one rolling Pachinko weather note for a required five-digit US ZIP code. Each refresh contains hourly temperature, conditions, precipitation, wind, US AQI, and PM2.5 for the previous seven days and next seven days.

The replacement is interruption-safe: the new note is created and checkpointed before the previous note is deleted. A durable replacement journal lets a later continuation finish cleanup without creating another replacement.

## Configuration

| Variable | Description |
| --- | --- |
| `ZIP_CODE` *(required)* | One five-digit US ZIP code, including any leading zero. |

No API key or account is required. `SAVE_TO_PROJECT_ID`, when provided by the Pachinko wrapper, controls the destination; the recipe contains no hardcoded destination project.

## Data sources and rate limits

- Open-Meteo supplies hourly forecast and air-quality data. Its free non-commercial tier currently permits fewer than **600 calls per minute, 5,000 per hour, 10,000 per day, and 300,000 per month**. Each refresh makes two sequential Open-Meteo calls and the Python client enforces at least 0.11 seconds between outbound requests, honors `Retry-After`, and backs off after transient errors.
- Zippopotam.us resolves the ZIP to coordinates. Its public site does not publish a numeric request limit. Results are cached permanently, requests are sequential, and transient responses are retried conservatively.

Open-Meteo's free API is for non-commercial use and its weather data requires CC BY 4.0 attribution. Zippopotam.us data is published under ODbL/Database Contents licensing. The generated note includes source attribution.

## Privacy

Resolving a new ZIP sends that ZIP to Zippopotam.us. Each refresh sends its cached latitude and longitude to Open-Meteo. Open-Meteo states that free-service logs may include IP addresses and geographic coordinates and are deleted after 90 days. The recipe sends no Pachinko note contents, credentials, email addresses, or API keys to either weather provider.

## Refresh behavior

The stable note title is `US Weather — ZIP (City, ST)`. Since Pachinko note creation is additive, the recipe creates the refreshed note first, records its note ID, and then removes older notes with the exact same title. Do not manually annotate the generated note because those edits will not survive a refresh.
