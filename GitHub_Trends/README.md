# GitHub Trends

A Claude Code and Codex recipe that adds repositories from [GitHub Trending](https://github.com/trending) to Pachinko. It supports Trending repositories only, not developers.

## Customization

| Variable | Description |
| --- | --- |
| `DATE_RANGE` *(optional)* | `daily`, `weekly`, or `monthly`. Defaults to `daily`. |
| `LANGUAGE` *(optional)* | A programming language such as `Python` or `C++`. Leave blank for all languages. |
| `SPOKEN_LANGUAGE` *(optional)* | A spoken language such as `English`. Leave blank for all spoken languages. |

Repository notes are deduplicated across all configurations. Changing the date
range, programming language, or spoken language will not recreate a note for a
repository that was saved previously.
