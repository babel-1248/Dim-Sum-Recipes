"""
Usage:
  python3 get_article.py <json_file>               # List all articles: index TAB title TAB link TAB published
  python3 get_article.py <json_file> <N>           # Print HTML content of article at index N (0-based)
  python3 get_article.py <json_file> <N> convert   # Convert article N's HTML content to markdown
"""
import json, sys, os, subprocess

with open(sys.argv[1]) as f:
    articles = json.load(f)

if len(sys.argv) == 2:
    for i, a in enumerate(articles):
        print(f"{i}\t{a.get('title','')}\t{a.get('link','')}\t{a.get('published','')}")
elif len(sys.argv) == 3:
    n = int(sys.argv[2])
    print(articles[n].get('content', ''))
else:
    n = int(sys.argv[2])
    html = articles[n].get('content', '')
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        ['python3', os.path.join(skill_dir, 'html_to_markdown.py')],
        input=html, capture_output=True, text=True
    )
    print(result.stdout, end='')
