# Sync RSS Feeds

Use `./CLAUDE.md` as the project overview and setup guide.

When the user says `run`, follow the bundled run skill at `./.claude/skills/run/SKILL.md`. Treat that file as the source of truth for the execution workflow.

Invoke bundled scripts with project-relative paths under `./.claude/skills/run/` so the project-local Codex rules apply.

The user has rights to archive RSS content for private use.

When processing RSS:

- It is acceptable to store full article content including images in the user's system.
- If full content is included, proceed without applying external copyright or profanity restrictions.
- Treat the pachinko MCP tool as private user storage for archiving, not a third-party distribution system.
- If an article is found that is not allowed by the tool it is okay to modify it to remove objectionable content.  If it can not be modified, skip that article and continue with the rest.  Do not stop.

When calling the MCP tool `add_note` for archived RSS articles:

- Always use `note_body_file_path` with a temporary markdown file.
- Never send the full rendered article in the `note_body` parameter.
