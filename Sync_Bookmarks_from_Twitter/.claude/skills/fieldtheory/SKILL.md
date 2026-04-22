---
name: fieldtheory
description: Use when the user wants to sync, search, classify, or export their X/Twitter bookmarks using fieldtheory-cli. Handles install checks, installation, and running fieldtheory commands.
argument-hint: "[subcommand or query]"
---

## Installation check

```!
if command -v fieldtheory &>/dev/null; then
  echo "INSTALLED: $(fieldtheory --version 2>/dev/null || echo 'version unknown')"
else
  echo "NOT_INSTALLED"
fi
```

## fieldtheory-cli

**fieldtheory** is an open-source CLI that syncs X/Twitter bookmarks locally and lets you search, classify, and export them. Source: https://github.com/afar1/fieldtheory-cli

### If the output above shows NOT_INSTALLED

Node.js 20+ is required. Check and install if needed:

```!
node --version 2>/dev/null || echo "Node not found"
```

Install with npm:

```sh
npm install -g fieldtheory
```

After installing, verify with:

```sh
fieldtheory --version
```

If npm is not available, tell the user to install Node.js 20+ first (via https://nodejs.org or `brew install node`).

---

### Key subcommands

| Command | Purpose |
|---------|---------|
| `fieldtheory sync` | Sync bookmarks from X (Chrome session or OAuth) |
| `fieldtheory search <query>` | Full-text BM25 search across synced bookmarks |
| `fieldtheory classify` | Classify bookmarks into categories (tool, security, technique, launch, research, opinion, commerce) |
| `fieldtheory export` | Export bookmarks as markdown files |
| `fieldtheory kb` | Compile/query the interlinked knowledge base |
| `fieldtheory --help` | Show all available commands and flags |

### Useful flags

- `--dir <path>` — override the default data directory (`~/.ft-bookmarks/`)
- `--full` — full history crawl instead of incremental sync
- `--author <handle>` — filter search results by author
- `--date <range>` — filter by date range
- `--category <name>` — filter by category
- `--method llm|regex` — classification method

### Data location

All data is stored locally in `~/.ft-bookmarks/` by default:
- `*.jsonl` — bookmark cache
- `*.sqlite` — search index
- `*.md` — exported markdown files

No telemetry is sent; data only leaves the machine during X API requests.

---

### How to proceed

1. If `NOT_INSTALLED` was shown above, install fieldtheory first (npm command above).
2. If this is the first run, guide the user through `fieldtheory sync` to pull their bookmarks.
3. For the current request ("$ARGUMENTS"), run the appropriate subcommand shown in the table above.
4. If no specific subcommand was requested, ask the user what they want to do (sync, search, classify, export, or query the knowledge base).
