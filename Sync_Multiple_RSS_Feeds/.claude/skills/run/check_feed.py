"""
Usage: python3 check_feed.py <state_file> <feed_url> <skill_dir>

Fetches <feed_url>, parses it, compares against seen IDs in <state_file>,
and prints only new articles as JSON. State is read while holding the same
lock used by mark_seen.py. This script does not modify seen state.

Prints nothing (empty output) if there are no new articles.
Prints a JSON array of new articles if any are found:
  [{ "id": "...", "title": "...", "link": "...", "published": "...", "content": "..." }, ...]

On fetch failure prints: {"error": "..."}
"""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

state_path = Path(sys.argv[1])
feed_url = sys.argv[2]
skill_dir = sys.argv[3]

try:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(f"{state_path.name}.lock")
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_SH)
        try:
            with state_path.open() as state_file:
                state = json.load(state_file)
        except FileNotFoundError:
            state = {}
        if not isinstance(state, dict):
            raise ValueError(f"State file must contain a JSON object: {state_path}")
except Exception as error:
    print(json.dumps({"error": f"Unable to read state: {error}"}))
    sys.exit(0)

# Fetch feed
try:
    req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_bytes = resp.read()
except Exception as error:
    print(json.dumps({"error": str(error)}))
    sys.exit(0)

# Parse feed
proc = subprocess.run(
    ['python3', skill_dir + '/parse_feed.py'],
    input=xml_bytes.decode('utf-8', errors='replace'),
    capture_output=True,
    text=True,
)
if proc.returncode != 0:
    error = proc.stderr.strip() or "Feed parser failed"
    print(json.dumps({"error": error}))
    sys.exit(0)

try:
    articles = json.loads(proc.stdout) if proc.stdout.strip() else []
except json.JSONDecodeError as error:
    print(json.dumps({"error": f"Feed parser returned invalid JSON: {error}"}))
    sys.exit(0)

seen = set(state.get(feed_url, []))
new = [a for a in articles if a['id'] not in seen]

# Only print if there are new articles
if new:
    print(json.dumps(new))
