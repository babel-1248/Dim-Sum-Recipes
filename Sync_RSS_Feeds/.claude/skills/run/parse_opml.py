import xml.etree.ElementTree as ET, os, sys

tree = ET.parse(os.environ['OPML_FILE'])
for el in tree.iter('outline'):
    url = el.get('xmlUrl') or el.get('xmlurl')
    if url:
        print(url)
