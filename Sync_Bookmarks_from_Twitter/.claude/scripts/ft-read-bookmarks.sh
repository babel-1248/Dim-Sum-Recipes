#!/usr/bin/env bash
# Outputs all bookmarks from the fieldtheory JSONL cache, one JSON object per line.
cat ~/.ft-bookmarks/bookmarks.jsonl 2>/dev/null || true
