# Sync EDGAR

A Claude Code and Codex recipe that incrementally syncs SEC EDGAR filings into Pachinko, one note per filing. It decodes material 8-K item codes, converts filing HTML and exhibits to Markdown, supports selected 10-K/10-Q sections, and renders Forms 3/4/5 as ownership transaction tables.

On the first run, the recipe limits the initial lookback to seven days unless the filter gives another date range. Later runs resume from stored state without duplicating saved accessions. Every successful note is checkpointed immediately, and a deterministic pending-count check keeps processing until every selected filing has either been saved or explicitly reported as failed.

The recipe creates at most 20 notes per run by default after filtering. The filter can set another positive limit or request no limit; overflow remains eligible for the next run. With no filter, the safe default is a market-wide 8-K search for the resolved window.

Example filter:

```text
Watch AAPL, MSFT, and NVDA.
Include 8-K, 10-K, 10-Q, and Form 4; exclude amendments and Form 144.
Keep filings with material 8-K items and decode any Form 4 activity.
For annual and quarterly reports, include Items 1A and 7. Keep EX-99 exhibits.
Limit this run to 20 notes.
```

## Customization

| Variable | Description |
| --- | --- |
| `FILTER_FILE` *(optional)* | A plain-text file containing tickers or CIKs, forms, exclusions, amendment handling, an EDGAR full-text phrase, filing-date range, relevance criteria, requested report sections, exhibit handling, and/or a note-limit override. When unset or empty, a market-wide 8-K run uses the default 20-note limit. |
| `SEC_CONTACT_EMAIL` *(required)* | A contact address included in the EDGAR User-Agent so automated requests identify a responsible contact. |

## Rate limits

SEC's current fair-access guideline permits a user no more than **10 requests per second in total, regardless of how many machines submit them**. Both the metadata fetcher and filing converter use the same sequential Python client with a 0.11-second minimum interval between requests, honor `Retry-After`, and back off after transient failures. Do not run parallel EDGAR syncs to increase throughput because their combined traffic is not coordinated and can exceed the total limit.

SEC also asks automated clients to send a descriptive User-Agent containing contact information. `SEC_CONTACT_EMAIL` is therefore required by this recipe. There is no API key or account requirement. See the [SEC fair-access guidance](https://www.sec.gov/about/developer-resources).
