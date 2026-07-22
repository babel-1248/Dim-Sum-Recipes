# Sync Strava

A Claude Code recipe for syncing Strava data through Strava's remote MCP server and saving it to Pachinko.

The recipe configures both Strava at `https://mcp.strava.com/mcp` and the local Pachinko MCP server at `http://localhost:3000/mcp`. On each run, it verifies the Strava connection before using Strava. When authentication is needed, Claude Code opens the Strava OAuth page in the default browser and waits for authorization to finish.

## Sync behavior

The recipe ensures that the configured Pachinko project contains a section named `workout history`. When no project is configured, it uses an inbox section with that name.

It compares the activity IDs in the project-local `strava_sync_state.json` file with the athlete's Strava workouts dated January 1, 2026 or later and adds only new workouts. Every new workout receives an individual detail request regardless of the number found. Each note is titled `MM-DD-YYYY — Workout Title`, contains the available workout details as readable markdown, and ends with a direct link to the activity on Strava.

The state file is updated immediately after each workout note is successfully created, so subsequent runs do not need to fetch existing Pachinko note bodies.
