# Sync FRED Economic Indicators

A Claude Code and Codex recipe that saves selected FRED economic indicators to Pachinko in one Markdown snapshot, with period-over-period and year-over-year comparisons. It creates a new snapshot only when the selected data changes.

## Default coverage

Without a `FILTER_FILE`, the recipe includes all 14 bundled indicators: headline and core CPI, headline and core PCE, unemployment, payroll employment, initial unemployment claims, real GDP, industrial production, retail sales, housing starts, building permits, the federal funds rate, and the 10-year Treasury yield.

The default history is the latest 13 observations for each indicator. This gives the current value, previous-period and year-over-year comparisons, and a compact trend table without dumping the full FRED history. Because the indicators have different frequencies, 13 observations means 13 weeks for initial claims, about 13 months for the monthly series, and 13 quarters for real GDP.

## Customization

| Variable | Description |
| --- | --- |
| `FRED_API_KEY` *(required)* | A [FRED API Version 2 key](https://fred.stlouisfed.org/docs/api/fred/v2/api_key.html). |
| `FILTER_FILE` *(optional)* | A Markdown checklist for choosing indicators and history length. Without one, the recipe includes all 14 bundled indicators and their latest 13 observations. |
