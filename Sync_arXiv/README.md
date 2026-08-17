# Sync arXiv

A Claude Code and Codex recipe that incrementally syncs arXiv preprints into Pachinko, one note per paper. Notes include metadata, abstract, links, and full text converted from arXiv's LaTeXML HTML when available.

On the first run, the recipe limits the initial lookback to seven days unless the filter gives another date range. Later runs resume from stored state without duplicating saved arXiv IDs. Every successful note is checkpointed immediately, and a deterministic pending-count check keeps Codex processing until every selected paper has either been saved or explicitly reported as failed.

The recipe creates at most 20 notes per run by default after filtering. The filter can set another positive limit or request no limit; overflow remains eligible for the next run.

Example filter:

```text
Categories: cs.AI, cs.CL, cs.LG
Papers about LLM agent safety or interpretability.
Skip pure benchmark papers.
Use full text and limit this run to 20 notes.
```

## Customization

| Variable | Description |
| --- | --- |
| `FILTER_FILE` *(optional)* | A plain-text file containing arXiv categories, search terms, authors, raw query fragments, date range, relevance criteria, full-text preference, sort order, and/or a note-limit override. When unset or empty, all arXiv papers in the resolved window are eligible and the default 20-note limit applies. |

## Rate limits

The recipe uses two arXiv services with separate limits. Metadata requests to the legacy arXiv API run sequentially at no more than **one request every three seconds**. Full-text HTML requests to `arxiv.org` run sequentially with a **15-second delay** between papers. A 20-paper full-text batch therefore spends roughly five minutes on crawl delays alone. Do not run parallel syncs to bypass these limits; use arXiv's bulk-access options for large downloads. See the [arXiv API terms](https://info.arxiv.org/help/api/tou.html#rate-limits) and [`robots.txt`](https://arxiv.org/robots.txt).
