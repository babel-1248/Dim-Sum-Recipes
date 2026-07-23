---
name: run
description: Sync Strava workout history into a Pachinko section after verifying Strava MCP authentication. Run whenever the user says "run" or asks to sync Strava workouts.
---

# Sync Strava

## Authentication gate

Complete this gate before calling any Strava data function. The synthetic Strava authentication function is the only `mcp__strava__*` function allowed before this gate succeeds.

1. Check the Strava MCP server's connection state while explicitly applying the recipe settings:

   ```sh
   claude --settings .claude/settings.json mcp get strava
   ```

   Always use this exact form. A bare `claude mcp get strava` subprocess can omit project settings and falsely report `Pending approval` even though `.claude/settings.json` already approves the server. Do not treat output from the bare form as authoritative.

2. Use `ToolSearch` to search for `strava list athlete activities workouts activity details`.
   - If Strava's real activity tools are available and the status reports `Connected`, continue to the sync workflow.
   - If authentication is required or the real tools are unavailable, use `ToolSearch` to search for `strava authenticate MCP` and load the synthetic authentication tool exposed for the unauthenticated server. Its name normally ends in `authenticate`, such as `mcp__strava__authenticate`.

3. If the synthetic authentication tool is available, call it from the current Claude Code process and handle its result as described in step 4.

   If no synthetic authentication tool is exposed, do not claim that an interactive Claude session must be started manually. Instead, run this exact project-relative command in the foreground with a 600000 ms Bash timeout:

   ```sh
   .claude/skills/run/login_strava_terminal.py
   ```

   This helper opens a real macOS Terminal window and runs the real login command from the recipe directory:

   ```sh
   claude --settings .claude/settings.json mcp login strava
   ```

   The Terminal command opens Strava's OAuth flow in the default browser. The helper keeps the original `-p` run alive and polls the explicitly configured server until the login command completes or the server reports `Connected`. Wait for the user to complete authentication; do not background the helper and do not ask the user to copy or run the command themselves.

4. Handle the authentication result:
   - If it reports that authentication completed silently, proceed to the refresh step.
   - If it returns an `auth_url`, pass that URL as one shell-quoted argument to this exact project-relative command, run in the foreground with a 600000 ms Bash timeout:

     ```sh
     .claude/skills/run/open_strava_auth.py '{auth_url}'
     ```

     The helper accepts only an HTTPS Strava authorization URL, opens it in the user's default browser, and polls until the OAuth callback has made the server connect. Do not background it. Wait while the user authorizes Strava in the browser.

5. After either helper or silent authentication succeeds, force the current `-p` session to refresh its deferred MCP tools by calling `ToolSearch` again for `strava list athlete activities workouts activity details`. If only the synthetic authentication tool is returned, call `ToolSearch` one more time for `strava activities details` before deciding that the current process did not reload the tools.

6. Verify the connection again with the explicit recipe settings:

   ```sh
   claude --settings .claude/settings.json mcp get strava
   ```

7. Continue only when both conditions are true:
   - `claude --settings .claude/settings.json mcp get strava` reports `Connected`.
   - `ToolSearch` returns Strava's real activity tools rather than only the synthetic authentication tool.

If the helper reaches its timeout, check the connection and run the refresh search once before stopping. Continue if the two conditions above are now true. Otherwise, explain that the authorization window expired and stop without calling Strava data functions.

If the interactive Terminal login succeeds and the explicit status check reports `Connected`, but both refresh searches still fail to expose Strava's real tools, explain that authorization is now saved but this particular `-p` process did not reload its MCP client. Stop without calling Strava functions; the next recipe run should use the saved authorization and proceed without another login.

If the initial status is not an authentication problem—for example, the server configuration is invalid, the service is unavailable, or the network cannot reach it—report that specific problem and stop. Do not start OAuth unless authentication is needed.

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
