"""
Usage:
  python3 get_video.py <json_file>               # List all videos: index TAB title TAB link TAB published TAB thumbnail TAB description
  python3 get_video.py <json_file> <N>           # Print description of video at index N (0-based)
  python3 get_video.py <json_file> <N> markdown  # Extract video page content and transcript with Defuddle, then print markdown
"""
import json, subprocess, sys


USER_AGENT = "Mozilla/5.0"


with open(sys.argv[1]) as f:
    videos = json.load(f)

if len(sys.argv) == 2:
    for i, video in enumerate(videos):
        description = video.get("description", "").replace("\t", " ").replace("\n", " ")
        print(f"{i}\t{video.get('title','')}\t{video.get('link','')}\t{video.get('published','')}\t{video.get('thumbnail','')}\t{description}")
elif len(sys.argv) == 3:
    n = int(sys.argv[2])
    print(videos[n].get("description", ""))
elif len(sys.argv) == 4 and sys.argv[3] == "markdown":
    n = int(sys.argv[2])
    link = videos[n].get("link")
    if not link:
        print("Video link is missing.", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        ["npx", "defuddle", "parse", link, "--markdown", "--user-agent", USER_AGENT],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, end="")
        sys.exit(result.returncode)
    title = videos[n].get("title") or "Untitled video"
    print(f"# {title}\n")
    thumbnail = videos[n].get("thumbnail")
    if thumbnail:
        print(f"![{title}]({thumbnail})\n")
    print(result.stdout, end="")
else:
    print(__doc__.strip(), file=sys.stderr)
    sys.exit(2)
