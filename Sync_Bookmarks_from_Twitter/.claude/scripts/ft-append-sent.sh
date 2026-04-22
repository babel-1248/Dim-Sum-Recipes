#!/usr/bin/env bash
# Appends a single bookmark ID to the tracking file.
# Usage: ft-append-sent.sh <tweet_id>
set -euo pipefail
if [[ -z "${1:-}" ]]; then
  echo "Usage: ft-append-sent.sh <tweet_id>" >&2
  exit 1
fi
mkdir -p ~/.ft-bookmarks
echo "$1" >> ~/.ft-bookmarks/.pachinko-sent-ids
