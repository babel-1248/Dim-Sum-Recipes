# Sync GitHub Trending

A Claude Code and Codex recipe that syncs repositories from [GitHub Trending](https://github.com/trending) into Pachinko, one note per repository. Notes preserve the Trending rank and window, description, programming language, total stars, forks, stars gained during the selected window, contributors, and source links.

The recipe supports Trending repositories only—not Trending developers. Repositories are deduplicated independently for each filter combination, and each successful note is checkpointed immediately for safe restart after a partial run.

GitHub's date-range control provides three rolling windows rather than arbitrary calendar dates: today, this week, and this month.

## Customization

| Variable | Description |
| --- | --- |
| `DATE_RANGE` *(optional)* | `daily`, `weekly`, or `monthly`. Defaults to `daily`. |
| `LANGUAGE` *(optional)* | A GitHub programming-language name or Trending path slug, such as `Python`, `C++`, `python`, or `c%2B%2B`. Defaults to any programming language. |
| `SPOKEN_LANGUAGE` *(optional)* | A spoken-language name or GitHub code, such as `English` or `en`. Defaults to any spoken language. |

The programming and spoken-language values are checked against the filter menus on the live Trending page before the filtered page is requested. Invalid values stop the run before state or notes are changed.

## Access behavior

GitHub Trending is a server-rendered web page, not a documented API. The recipe makes one request to discover and validate current filters and, when filters are selected, one request for the resulting repository page. Requests are sequential, identify the client, retry transient failures with backoff, and never visit `/trending/developers`.

Because GitHub may change its HTML, the parser validates that it received the repositories view and either repository cards or GitHub's explicit empty state. Unexpected markup stops the run without advancing state.
