"""
Usage: python3 extract_feed_url.py <channel_html_file> <channel_url>

Reads a YouTube channel page and prints the RSS feed URL from:
  <link rel="alternate" type="application/rss+xml" href="...">

Exits with a non-zero status if no RSS link is found.
"""
import sys
from html.parser import HTMLParser
from urllib.parse import urljoin


class FeedLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feed_url = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link" or self.feed_url:
            return

        attrs = {name.lower(): value for name, value in attrs if name}
        rel = attrs.get("rel", "")
        link_type = attrs.get("type", "")
        href = attrs.get("href")

        rel_values = {value.strip().lower() for value in rel.split()}
        if "alternate" in rel_values and link_type.lower() == "application/rss+xml" and href:
            self.feed_url = href


if len(sys.argv) != 3:
    print("Usage: python3 extract_feed_url.py <channel_html_file> <channel_url>", file=sys.stderr)
    sys.exit(2)

html_path = sys.argv[1]
channel_url = sys.argv[2]

with open(html_path, encoding="utf-8", errors="replace") as f:
    parser = FeedLinkParser()
    parser.feed(f.read())

if not parser.feed_url:
    print("No RSS alternate link found in channel page.", file=sys.stderr)
    sys.exit(1)

print(urljoin(channel_url, parser.feed_url))
