# Sync Strava

A Claude Code recipe that imports your Strava workouts into Pachinko.

## Before you start

This recipe uses the new Strava MCP server option available to Strava subscribers. You must have access to that feature in your Strava account.

## Using the recipe

Run the feed in Pachinko. On the first run, a browser will open so you can sign in to Strava and authorize access. After authentication finishes, rerun the feed in Pachinko to begin the workout import.

The recipe imports workouts dated January 1, 2026 or later. Older workouts are not imported.

Workouts are saved as notes in a `workout history` section in the configured project, or in the inbox when no project is selected. Each note includes the workout details and a direct link to the activity on Strava.

Later runs import only workouts that have not already been synced.
