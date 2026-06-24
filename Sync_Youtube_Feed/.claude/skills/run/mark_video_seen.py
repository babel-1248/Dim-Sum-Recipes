"""
Usage: python3 mark_video_seen.py <state_file> <feed_url> <video_id>

Marks a single video ID as seen for the resolved feed URL.
Call this only after the video has been successfully added to Pachinko.
"""
import json
import sys


if len(sys.argv) != 4:
    print("Usage: python3 mark_video_seen.py <state_file> <feed_url> <video_id>", file=sys.stderr)
    sys.exit(2)

state_path = sys.argv[1]
feed_url = sys.argv[2]
video_id = sys.argv[3]

try:
    with open(state_path) as f:
        state = json.load(f)
except FileNotFoundError:
    state = {}

seen = state.get(feed_url, [])
if video_id not in seen:
    seen.append(video_id)

state[feed_url] = seen
with open(state_path, "w") as f:
    json.dump(state, f, indent=2)
    f.write("\n")
