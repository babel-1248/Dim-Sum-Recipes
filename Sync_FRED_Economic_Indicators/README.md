# Sync FRED Economic Indicators

A Claude Code and Codex recipe that saves selected FRED economic indicators to Pachinko in one Markdown snapshot, with period-over-period and year-over-year comparisons. It creates a new snapshot only when the selected data changes.

## Customization

| Variable | Description |
| --- | --- |
| `FRED_API_KEY` *(required)* | A FRED API key. |
| `FILTER_FILE` *(optional)* | A Markdown checklist for choosing indicators and history length. Without one, the recipe tracks headline CPI, core CPI, unemployment, payroll employment, real GDP, and retail sales. |
