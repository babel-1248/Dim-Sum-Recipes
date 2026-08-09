import sys, re
from html.parser import HTMLParser


class Converter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._list_stack = []   # 'ul' or 'ol'
        self._ol_counters = []
        self._link_stack = []   # stack of (href, start_index_in_parts)
        self._skip = 0          # depth inside script/style

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        d = dict(attrs)

        if tag in ('script', 'style'):
            self._skip += 1
            return
        if self._skip:
            return

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append(f'\n\n{"#" * int(tag[1])} ')
        elif tag in ('p', 'div', 'section', 'article', 'header', 'footer', 'main', 'figure'):
            self.parts.append('\n\n')
        elif tag == 'br':
            self.parts.append('  \n')
        elif tag in ('strong', 'b'):
            self.parts.append('**')
        elif tag in ('em', 'i'):
            self.parts.append('*')
        elif tag == 'code':
            self.parts.append('`')
        elif tag == 'pre':
            self.parts.append('\n\n```\n')
        elif tag == 'blockquote':
            self.parts.append('\n\n> ')
        elif tag == 'ul':
            self._list_stack.append('ul')
            self.parts.append('\n')
        elif tag == 'ol':
            self._list_stack.append('ol')
            self._ol_counters.append(0)
            self.parts.append('\n')
        elif tag == 'li':
            if self._list_stack and self._list_stack[-1] == 'ol':
                self._ol_counters[-1] += 1
                self.parts.append(f'\n{self._ol_counters[-1]}. ')
            else:
                self.parts.append('\n- ')
        elif tag == 'a':
            # Record href and current parts length; collect rendered children on close
            self._link_stack.append((d.get('href', ''), len(self.parts)))
        elif tag == 'img':
            src = d.get('src', '')
            alt = d.get('alt', '')
            # Always on its own line regardless of surrounding context
            self.parts.append(f'\n\n![{alt}]({src})\n\n')
        elif tag == 'hr':
            self.parts.append('\n\n---\n\n')
        elif tag == 'figcaption':
            self.parts.append('\n*')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ('script', 'style'):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return

        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append('\n')
        elif tag in ('strong', 'b'):
            self.parts.append('**')
        elif tag in ('em', 'i'):
            self.parts.append('*')
        elif tag == 'code':
            self.parts.append('`')
        elif tag == 'pre':
            self.parts.append('\n```\n\n')
        elif tag == 'ul':
            if self._list_stack:
                self._list_stack.pop()
            self.parts.append('\n')
        elif tag == 'ol':
            if self._list_stack:
                self._list_stack.pop()
            if self._ol_counters:
                self._ol_counters.pop()
            self.parts.append('\n')
        elif tag == 'a':
            if not self._link_stack:
                return
            href, start_idx = self._link_stack.pop()
            if not href:
                return  # anchor with no href — keep rendered children, drop link syntax
            # Gather everything rendered inside the link since it opened
            content = ''.join(self.parts[start_idx:]).strip()
            del self.parts[start_idx:]
            if not content:
                return
            # If the content is solely an image, add the link as a separate line
            img_only = re.fullmatch(r'\n*!\[([^\]]*)\]\([^\)]*\)\n*', content)
            if img_only:
                self.parts.append(f'\n\n{content.strip()}\n[{img_only.group(1) or href}]({href})\n\n')
            else:
                # Strip any embedded image blocks so they don't break inline link syntax
                inline = re.sub(r'\n\n!\[[^\]]*\]\([^\)]*\)\n\n', ' ', content).strip()
                self.parts.append(f'[{inline}]({href})')
        elif tag == 'figcaption':
            self.parts.append('*\n')

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)

    def result(self):
        md = ''.join(self.parts)
        md = re.sub(r'\n{3,}', '\n\n', md)
        md = re.sub(r' +\n', '\n', md)
        return md.strip()


def convert(html):
    # html.parser treats <![CDATA[...]]> as an opaque block and swallows everything
    # inside it, so strip the wrapper before parsing if present.
    html = re.sub(r'^\s*<!\[CDATA\[', '', html)
    html = re.sub(r'\]\]>\s*$', '', html)
    c = Converter()
    c.feed(html)
    return c.result()


if __name__ == '__main__':
    print(convert(sys.stdin.read()))
