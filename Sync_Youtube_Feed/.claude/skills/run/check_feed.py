"""
Usage: python3 check_feed.py <state_file> <feed_url> <skill_dir>

Fetches <feed_url>, parses it, compares against seen IDs in <state_file>,
saves updated state, and prints only new videos as JSON.

Prints nothing (empty output) if there are no new videos.
Prints a JSON array of new videos if any are found:
  [{ "id": "...", "title": "...", "link": "...", "published": "...", "description": "..." }, ...]

On fetch failure prints: {"error": "..."}
"""
import json, sys, subprocess, urllib.request, urllib.error

state_path = sys.argv[1]
feed_url   = sys.argv[2]
skill_dir  = sys.argv[3]

try:
    with open(state_path) as f:
        state = json.load(f)
except FileNotFoundError:
    state = {}

# Fetch feed
try:
    req = urllib.request.Request(feed_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        xml_bytes = resp.read()
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)

# Parse feed
proc = subprocess.run(
    ['python3', skill_dir + '/parse_feed.py'],
    input=xml_bytes.decode('utf-8', errors='replace'),
    capture_output=True,
    text=True,
)
videos = json.loads(proc.stdout) if proc.stdout.strip() else []

seen    = set(state.get(feed_url, []))
new     = [v for v in videos if v['id'] not in seen]
all_ids = [v['id'] for v in videos]

# Save state immediately
state[feed_url] = all_ids
with open(state_path, 'w') as f:
    json.dump(state, f, indent=2)
    f.write('\n')

# Only print if there are new videos
if new:
    print(json.dumps(new))
