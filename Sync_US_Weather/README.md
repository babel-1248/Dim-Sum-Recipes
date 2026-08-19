# Sync US Weather

A Claude Code and Codex recipe that creates a new Pachinko weather note on every run for a required five-digit US ZIP code. Each note contains hourly temperature, conditions, precipitation, wind, US AQI, and PM2.5 through the configured number of days ahead.

On its first successful run, the inclusive window starts seven days before today. On later runs, it starts on the last successful sync date, with the start capped at seven days before today. The end is `DAYS_AHEAD` days after today. `DAYS_AHEAD` defaults to `1`, so leaving it unset or empty preserves the assumption that the note runs through tomorrow.

Existing notes are never listed, edited, replaced, or deleted and do not influence the date window. State advances only after Pachinko successfully creates the new note. If creation fails, the prior successful date remains unchanged.

## Configuration

| Variable | Description |
| --- | --- |
| `ZIP_CODE` *(required)* | One five-digit US ZIP code, including any leading zero. |
| `DAYS_AHEAD` *(optional)* | Forecast days after today, from `0` through `6`. Defaults to `1` (through tomorrow). The maximum reflects the air-quality feed's seven-day forecast including today. |

No API key or account is required. `SAVE_TO_PROJECT_ID`, when provided by the Pachinko wrapper, controls the destination; the recipe contains no hardcoded destination project.

## Data sources and rate limits

- Open-Meteo supplies hourly forecast and air-quality data. Its free non-commercial tier currently permits fewer than **600 calls per minute, 5,000 per hour, 10,000 per day, and 300,000 per month**. Each refresh makes two sequential Open-Meteo calls and the Python client enforces at least 0.11 seconds between outbound requests, honors `Retry-After`, and backs off after transient errors.
- Zippopotam.us resolves the ZIP to coordinates. Its public site does not publish a numeric request limit. Results are cached permanently, requests are sequential, and transient responses are retried conservatively.

Open-Meteo's free API is for non-commercial use and its weather data requires CC BY 4.0 attribution. Zippopotam.us data is published under ODbL/Database Contents licensing. The generated note includes source attribution.

## Privacy

Resolving a new ZIP sends that ZIP to Zippopotam.us. Each refresh sends its cached latitude and longitude to Open-Meteo. Open-Meteo states that free-service logs may include IP addresses and geographic coordinates and are deleted after 90 days. The recipe sends no Pachinko note contents, credentials, email addresses, or API keys to either weather provider.

## Refresh behavior

Every note title includes its inclusive date range: `US Weather — ZIP (City, ST) — YYYY-MM-DD to YYYY-MM-DD`. Every run creates an additional note; prior generated notes remain untouched.
