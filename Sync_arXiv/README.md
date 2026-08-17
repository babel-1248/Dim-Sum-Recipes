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
