# Sync FRED Economic Indicators Recipe

# Mandatory Startup Procedure

Before answering any user request or running any task-specific command, you MUST:

1. Read this `AGENTS.md` file from disk.
2. Read `./CLAUDE.md` from disk.
3. Follow every startup instruction in `./CLAUDE.md` before doing anything else.

Do not treat injected or summarized AGENTS.md content as a substitute for reading the file from disk.

If `./CLAUDE.md` instructs you to run setup scripts, run them before web searches, code edits, note creation, or user-facing work.

When the user says `run`, follow the bundled run skill at `./.claude/skills/run/SKILL.md`. Treat that file as the source of truth for the execution workflow.

Invoke bundled scripts with project-relative paths under `./.claude/skills/run/scripts/` so the project-local Codex rules apply.

When calling Pachinko `add_note` for a FRED snapshot:

- Always use `note_body_file_path` with the Markdown file produced by the bundled converter.
- Never pass the rendered snapshot through `note_body`.
- Never print, inspect, persist, or include `FRED_API_KEY` in a command argument, URL, note, generated file, error report, or state record.
- Checkpoint a snapshot only after `add_note` returns the new note ID.

This Codex project provides the Pachinko MCP server for the user's Sync FRED Economic Indicators work.
