import xml.etree.ElementTree as ET, sys, json

def first_not_none(*els):
    for el in els:
        if el is not None:
            return el
    return None

content = sys.stdin.read()
root = ET.fromstring(content)

ns_atom = 'http://www.w3.org/2005/Atom'
articles = []

if root.tag == f'{{{ns_atom}}}feed' or root.tag == 'feed':
    for entry in root.findall(f'{{{ns_atom}}}entry') + root.findall('entry'):
        id_el = first_not_none(entry.find(f'{{{ns_atom}}}id'), entry.find('id'))
        title_el = first_not_none(entry.find(f'{{{ns_atom}}}title'), entry.find('title'))
        link_el = first_not_none(entry.find(f'{{{ns_atom}}}link'), entry.find('link'))
        summary_el = first_not_none(entry.find(f'{{{ns_atom}}}content'), entry.find('content'), entry.find(f'{{{ns_atom}}}summary'), entry.find('content'))
        published_el = first_not_none(entry.find(f'{{{ns_atom}}}published'), entry.find('published'), entry.find(f'{{{ns_atom}}}updated'), entry.find('updated'))

        article_id = (id_el.text if id_el is not None else None) or (link_el.get('href') if link_el is not None else None)
        link = link_el.get('href') if link_el is not None else None
        title = title_el.text if title_el is not None else '(no title)'
        summary = summary_el.text if summary_el is not None else ''
        published = published_el.text if published_el is not None else ''

        if article_id:
            articles.append({'id': article_id, 'title': title, 'link': link, 'published': published, 'content': summary})
else:
    ns_content = 'http://purl.org/rss/1.0/modules/content/'
    for item in root.iter('item'):
        guid_el = item.find('guid')
        title_el = item.find('title')
        link_el = item.find('link')
        pub_el = item.find('pubDate')

        article_id = (guid_el.text if guid_el is not None else None) or (link_el.text if link_el is not None else None)
        link = link_el.text if link_el is not None else None
        title = title_el.text if title_el is not None else '(no title)'
        published = pub_el.text if pub_el is not None else ''

        # Prefer content:encoded (full HTML) over description (summary/plain text)
        content_el = item.find(f'{{{ns_content}}}encoded')
        desc_el = item.find('description')
        body_el = first_not_none(content_el, desc_el)
        summary = body_el.text if body_el is not None else ''

        if article_id:
            articles.append({'id': article_id, 'title': title, 'link': link, 'published': published, 'content': summary})

print(json.dumps(articles))
