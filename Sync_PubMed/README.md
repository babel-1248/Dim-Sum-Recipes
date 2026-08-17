# Sync PubMed

A Claude Code and Codex recipe that incrementally syncs PubMed citations into Pachinko, one note per paper. Notes include citation metadata, structured abstract, MeSH headings, keywords, DOI and links, plus full text converted from PubMed Central JATS XML when available.

On the first run, the recipe limits the initial lookback to seven days unless the filter gives another date range. Later runs resume from stored state without duplicating saved PMIDs. Every successful note is checkpointed immediately, and a deterministic pending-count check keeps Codex processing until every selected paper has either been saved or explicitly reported as failed.

The recipe creates at most 20 notes per run by default after filtering. The filter can set another positive limit or request no limit; overflow remains eligible for the next run.

Example filter:

```text
Search for base editing in inherited retinal disease.
Include reviews and clinical trials; skip single-patient case reports.
Use full text when available and limit this run to 20 notes.
```

## Customization

| Variable | Description |
| --- | --- |
| `FILTER_FILE` *(optional)* | A plain-text file containing PubMed terms, fields, MeSH headings, publication types, journals, raw Entrez query fragments, date range or basis, relevance criteria, full-text preference, and/or a note-limit override. When unset or empty, all PubMed records in the resolved window are eligible and the default 20-note limit applies. |
| `NCBI_API_KEY` *(optional)* | An NCBI API key, increasing the E-utilities request ceiling from 3 to 10 requests per second. |
| `NCBI_EMAIL` *(optional)* | A contact email sent with NCBI E-utilities requests. |

## Rate limits

NCBI E-utilities permits up to **3 requests per second without an API key** and **10 requests per second with an API key**. The recipe throttles its sequential metadata and PubMed Central requests accordingly, so `NCBI_API_KEY` is optional for ordinary runs. NCBI recommends running genuinely large jobs on weekends or between 9:00 PM and 5:00 AM Eastern on weekdays. See the [NCBI E-utilities usage guidelines](https://www.ncbi.nlm.nih.gov/books/NBK25497/).
