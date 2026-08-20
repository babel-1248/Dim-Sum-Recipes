# Sync FRED Economic Indicators

A Claude Code and Codex recipe that creates a new Pachinko snapshot when the selected FRED economic indicators change. The snapshot combines inflation, labor-market, growth, housing, and interest-rate data in one Markdown note with period-over-period and year-over-year comparisons.

FRED API Version 2 retrieves complete releases rather than individual series. The recipe hides FRED's numeric release IDs: users select friendly indicator names, and the bundled client groups them by release so each required release is fetched only once.

## Configuration

| Variable | Description |
| --- | --- |
| `FRED_API_KEY` *(required)* | A 32-character lowercase FRED API Version 2 key. The client sends it only as an HTTPS bearer token and never writes it to generated files, notes, or state. |
| `FILTER_FILE` *(optional)* | A UTF-8 Markdown checklist. Checked (`- [x]`) indicators are included and unchecked (`- [ ]`) indicators are ignored. Use `fred-indicators-filter.md` as a starting template. |

When `FILTER_FILE` is unset or empty, the recipe includes headline CPI, core CPI, unemployment, payroll employment, real GDP, and retail sales. It shows the latest and previous non-missing observation for each indicator.

The history section supports three choices: latest and previous observation, the last 13 observations, or the last five years. If several history choices are checked, the first checked choice wins and the run reports that assumption. Unknown checked options and a checklist with no selected indicators are configuration errors.

## Refresh behavior

The first successful run creates a current snapshot. Later runs create a note only when the rendered selected data or metadata changes. Changing the selected indicators or history depth also produces a new snapshot. State is scoped to the Pachinko destination and advances only after `add_note` returns a note ID.

Each note includes FRED series links, release and publisher links, units, frequency, seasonal adjustment, update timestamps, recent observations when requested, copyright labels supplied by FRED, and calculated period and annual changes. Missing FRED observations (`.`) are omitted from calculations.

## API behavior and privacy

The recipe uses `https://api.stlouisfed.org/fred/v2/release/observations`, follows cursor pagination, issues requests sequentially, waits at least 0.55 seconds between them, honors `Retry-After`, and backs off after transient failures. FRED API Version 2 currently allows up to two requests per second before returning HTTP 429.

The API key is consumed directly from the runtime environment. It is not included in request URLs, command output, configuration JSON, manifests, Markdown notes, or checkpoint state. Requests send the selected public release IDs to FRED; Pachinko note contents and destination information are not sent to FRED.

By using the recipe, users are responsible for complying with the [FRED API Terms of Use](https://fred.stlouisfed.org/docs/api/terms_of_use.html) and any copyright restrictions reported for individual series.
