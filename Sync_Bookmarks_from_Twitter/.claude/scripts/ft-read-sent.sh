#!/usr/bin/env bash
# Outputs already-sent bookmark IDs, one per line. Safe to call when file doesn't exist.
cat ~/.ft-bookmarks/.pachinko-sent-ids 2>/dev/null || true
