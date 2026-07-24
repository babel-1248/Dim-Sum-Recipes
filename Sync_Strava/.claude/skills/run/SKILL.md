---
name: run
description: Sync Strava workout history into a Pachinko section after verifying Strava MCP authentication. Run whenever the user says "run" or asks to sync Strava workouts.
---

# Sync Strava

## Authentication gate

Complete this gate before calling any Strava data function.

1. Check the Strava MCP server's connection state while explicitly applying the recipe settings:

   ```sh
   claude --settings .claude/settings.json mcp get strava
   ```

   Always use this exact form. A bare `claude mcp get strava` subprocess can omit project settings and falsely report `Pending approval` even though `.claude/settings.json` already approves the server. Do not treat output from the bare form as authoritative.

2. If the explicit status reports `Connected`, use `ToolSearch` to search for `strava list athlete activities workouts activity details`.
   - If Strava's activity tools are available, continue to the sync workflow.
   - If they are unavailable, report that the connected Strava server did not expose its activity tools and stop. Do not start another login merely because a tool is unavailable.

3. If the explicit status reports that Strava authentication is required, the server returned HTTP 401 or 403, or the server is disconnected because authorization is missing, run this exact project-relative command in the foreground with a 600000 ms Bash timeout:

   ```sh
   .claude/skills/run/login_strava_terminal.py
   ```

   This helper opens a real macOS Terminal window and runs the real login command from the recipe directory:

   ```sh
   claude --settings .claude/settings.json mcp login strava
   ```

   The Terminal command opens Strava's OAuth flow in the default browser. The helper keeps the original `-p` run alive and polls the explicitly configured server until the login command completes or the server reports `Connected`. Wait for the user to complete authentication; do not background the helper and do not ask the user to copy or run the command themselves.

4. When the helper succeeds, verify the server once more with the explicit recipe settings:

   ```sh
   claude --settings .claude/settings.json mcp get strava
   ```

5. If the status now reports `Connected`, do not refresh tools and do not continue to the sync workflow during this execution. Respond clearly with this exact message and stop:

   ```text
   Strava authentication completed. Rerun this feed in Pachinko to start the sync.
   ```

An execution that launches the Terminal login is authentication-only. Never call Strava data functions or start the sync workflow in that same execution, even if the current process appears able to load the tools afterward. The next Pachinko feed run starts a fresh Claude process with the saved authorization.

If the helper reaches its timeout, check the explicit connection status once before stopping. If it is connected, use the exact successful-authentication message above. Otherwise, explain that the authorization window expired and stop without calling Strava data functions.

If the initial status is not an authentication problem—for example, the server configuration is invalid, the service is unavailable, the project server is not approved, or the network cannot reach it—report that specific problem and stop. Do not start OAuth unless authentication is needed.

## Sync workflow

### 1. Resolve the destination and section

Use the `SAVE_TO_PROJECT_ID` value captured by the startup procedure in `CLAUDE.md`.

1. If `SAVE_TO_PROJECT_ID` is neither `null` nor `""`, call `mcp__pachinko__list_sections` with only `project_id` set to that value.
   - If the project exists, use it as the destination.
   - If Pachinko reports that the project does not exist, fall back to the inbox and call `mcp__pachinko__list_sections` with no destination parameters.
2. If `SAVE_TO_PROJECT_ID` is `null` or `""`, use the inbox and call `mcp__pachinko__list_sections` with no destination parameters.
3. Find a section whose trimmed title case-insensitively equals `workout history`.
4. If no matching section exists, call `mcp__pachinko__add_section` with:
   - `section_title`: `workout history`
   - `to_project_id`: the resolved project ID when using a project; omit all `to_` parameters when using the inbox.
5. Capture the matching or newly created section ID as `workout_section_id`. If `add_section` does not return an ID, list the resolved destination's sections again and obtain the new section ID. Stop if the section cannot be found or created.

Never search globally for the section. A section with the same name in another project, area, or inbox is not the destination. If more than one matching section exists in the resolved destination, prefer an exact lowercase title match; otherwise use the first returned match and mention the ambiguity in the final report. Do not create another section.

### 2. Load sync state

Load the project-local state once, using this exact project-relative command:

```sh
.claude/skills/run/load_synced_workouts.py
```

Parse the JSON output and use the keys of `synced_workouts` as `existing_activity_ids`. Each key is a numeric Strava activity ID. Do not call `mcp__pachinko__list_notes` or `mcp__pachinko__get_note` to determine sync state.

If `strava_sync_state.json` is missing, the script returns an empty version 1 state. If the command fails because the file is invalid or unreadable, stop before listing Strava workouts or adding notes. Report the state error; never treat malformed state as empty.

Use the Strava activity ID—not the Pachinko title—as the canonical duplicate key. Dates and workout titles can collide or change. State remains authoritative even if a corresponding Pachinko note is later moved, completed, archived, edited, or deleted.

### 3. List Strava workouts from 2026 onward

Use the available read-only `mcp__strava__*` tool whose description lists the authenticated athlete's activities or workouts.

- Apply an inclusive lower bound of January 1, 2026 based on each workout's local start date. Never fetch details or create notes for workouts whose local date is before `2026-01-01`.
- Prefer a server-side `from`, `start`, `after`, or equivalent date filter when the tool provides one. If the tool accepts an ISO date, use `2026-01-01`. If it accepts only an epoch-second `after` value, use `1767139200` (`2025-12-31T00:00:00Z`) so workouts on local January 1 are not lost at timezone boundaries, then discard summaries whose local date is earlier than `2026-01-01`.
- Request the largest supported page size.
- Follow the tool's page, cursor, date-window, or continuation mechanism until it returns no more activities in the 2026-and-later window. Do not stop after the first page.
- Collect the activity ID, workout title/name, local start date, and any summary fields returned for every activity dated `2026-01-01` or later.
- If the server exposes no pagination mechanism, process every activity returned and state that limitation in the final report.
- Do not call any Strava write, update, upload, or delete tool.

If listing activities fails, stop without adding notes. Preserve the existing Pachinko section and report the Strava error.

### 4. Select and fetch new workouts

Treat an in-scope Strava activity as new when its numeric activity ID is not in `existing_activity_ids`.

If there are no new activities, report `No new Strava workouts to sync.` and finish without creating a note.

Fetch complete details for every new in-scope activity, regardless of how many new activities exist. There is no maximum workout count, sampling rule, summary-only mode, or threshold that permits skipping detail retrieval.

Process workouts as a streaming loop, one activity at a time:

1. Use the available read-only `mcp__strava__*` activity-detail tool to fetch the full details for the next activity ID. Do not use a bulk detail request and do not prefetch details for multiple workouts.
2. Render that workout, add its Pachinko note, and persist its state as described below.
3. Only after that workout is finished, continue to the next activity.

Do not stop, defer remaining workouts, or ask the user to narrow the range merely because the number of new workouts is large. Continue until every new workout from 2026 onward has received an individual detail request. If a detail request fails, retry transient or rate-limit failures when the tool indicates a retry is appropriate. If that activity still cannot be retrieved, report its activity ID, skip only that activity, and continue with the remainder.

Process new activities from oldest to newest using the local workout start time. This makes results deterministic across runs.

### 5. Render each workout as markdown

Use Strava's `start_date_local` date when available; otherwise convert `start_date` to the athlete or activity timezone when the response provides one. Format the date as `MM-DD-YYYY`.

Set the Pachinko note title to:

```text
MM-DD-YYYY — {Strava workout title}
```

If the activity has no title, use its sport type; if neither exists, use `Workout`.

Render the detail response as readable markdown. Include every meaningful, human-readable field Strava returns, organized under concise headings. Include fields when present and omit empty sections. Typical content includes:

- Workout identity, sport type, local start date/time, timezone, description, and location
- Distance, moving time, elapsed time, elevation, calories, and relative effort
- Pace or speed, heart rate, cadence, power, temperature, and training metrics
- Gear, achievements, laps, splits, best efforts, and segment efforts when returned

Format durations and measurements for people rather than copying raw machine values. Preserve Strava-provided units when the server supplies formatted values. Do not invent missing metrics, expose authentication data, or dump raw JSON, map polylines, or coordinate arrays into the note.

End every note with exactly one blank line followed by this link, substituting the numeric activity ID:

```markdown
[View workout on Strava](https://www.strava.com/activities/{activity_id})
```

The direct Strava link must be the final content in the note. Do not place text, metadata, separators, or hidden comments after it.

### 6. Add new notes to the section

For each successfully rendered workout, call `mcp__pachinko__add_note` with:

- `note_title`: the formatted date and workout title
- `note_body`: the rendered workout markdown
- `to_section_id`: `workout_section_id`

Provide no other destination parameter. After `add_note` succeeds, the very next action must record that workout in local state using the returned Pachinko note ID:

```sh
.claude/skills/run/mark_workout_synced.py {activity_id} {pachinko_note_id}
```

Run this command separately for each workout. Do not defer, batch, or reorder state updates. Do not fetch, render, or save another workout between a successful `add_note` call and its `mark_workout_synced.py` call.

After the state update succeeds, add the new Pachinko note ID to the collection used by the post-execution queue procedure in `CLAUDE.md`.

If an individual `add_note` call fails, report that workout's activity ID and title, then continue. Never record a workout as synced unless `add_note` succeeds.

If `mark_workout_synced.py` fails, retry that same command once before doing anything else. If the retry also fails, stop the run immediately, report the activity ID and new Pachinko note ID whose state could not be persisted, and do not add any more notes. This prevents additional successful notes from becoming untracked.

### 7. Report results

Report:

- Whether `workout history` was found or created and whether it is in the configured project or inbox
- The number of Strava workouts from 2026 onward inspected
- The number of state-recorded workouts skipped
- The number of new workout notes added
- Any activity-detail or Pachinko-save failures
- Any state-persistence failure

Then follow the mandatory post-execution queue procedure in `CLAUDE.md` using only the note IDs created successfully during this run.
