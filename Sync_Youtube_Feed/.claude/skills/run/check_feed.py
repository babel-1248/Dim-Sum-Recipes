"""
Usage: python3 check_feed.py <state_file> <feed_url> <skill_dir>

Fetches <feed_url>, parses it, compares against seen IDs in <state_file>,
saves updated state, and prints only new videos as JSON.

Prints nothing (empty output) if there are no new videos.
Prints a JSON array of new videos if any are found:
  [{ "id": "...", "title": "...", "link": "...", "published": "...", "description": "..." }, ...]

On fetch failure prints: {"error": "..."}
"""
import json, sys, urllib.request, urllib.error
import xml.etree.ElementTree as ET


def first_not_none(*els):
    for el in els:
        if el is not None:
            return el
    return None


def parse_videos(feed_xml):
    root = ET.fromstring(feed_xml)

    ns_atom = 'http://www.w3.org/2005/Atom'
    ns_media = 'http://search.yahoo.com/mrss/'
    videos = []

    if root.tag == f'{{{ns_atom}}}feed' or root.tag == 'feed':
        for entry in root.findall(f'{{{ns_atom}}}entry') + root.findall('entry'):
            id_el = first_not_none(entry.find(f'{{{ns_atom}}}id'), entry.find('id'))
            link_el = first_not_none(entry.find(f'{{{ns_atom}}}link'), entry.find('link'))
            published_el = first_not_none(entry.find(f'{{{ns_atom}}}published'), entry.find('published'), entry.find(f'{{{ns_atom}}}updated'), entry.find('updated'))
            media_group_el = entry.find(f'{{{ns_media}}}group')
            media_title_el = media_group_el.find(f'{{{ns_media}}}title') if media_group_el is not None else None
            media_desc_el = media_group_el.find(f'{{{ns_media}}}description') if media_group_el is not None else None
            title_el = first_not_none(media_title_el, entry.find(f'{{{ns_atom}}}title'), entry.find('title'))

            link = link_el.get('href') if link_el is not None else None
            video_id = (id_el.text if id_el is not None else None) or link
            title = title_el.text if title_el is not None else '(no title)'
            description = media_desc_el.text if media_desc_el is not None and media_desc_el.text else ''
            published = published_el.text if published_el is not None else ''

            if video_id:
                videos.append({'id': video_id, 'title': title, 'link': link, 'published': published, 'description': description})
    else:
        ns_content = 'http://purl.org/rss/1.0/modules/content/'
        for item in root.iter('item'):
            guid_el = item.find('guid')
            title_el = item.find('title')
            link_el = item.find('link')
            pub_el = item.find('pubDate')

            video_id = (guid_el.text if guid_el is not None else None) or (link_el.text if link_el is not None else None)
            link = link_el.text if link_el is not None else None
            title = title_el.text if title_el is not None else '(no title)'
            published = pub_el.text if pub_el is not None else ''

            content_el = item.find(f'{{{ns_content}}}encoded')
            desc_el = item.find('description')
            body_el = first_not_none(content_el, desc_el)
            description = body_el.text if body_el is not None else ''

            if video_id:
                videos.append({'id': video_id, 'title': title, 'link': link, 'published': published, 'description': description})

    return videos

state_path = sys.argv[1]
feed_url   = sys.argv[2]

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

try:
    videos = parse_videos(xml_bytes.decode('utf-8', errors='replace'))
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(0)

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
