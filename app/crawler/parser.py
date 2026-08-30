"""HTML parsing for the website audit."""

from html.parser import HTMLParser

class WebsiteAuditParser(HTMLParser):
    """Small dependency-free HTML extractor for factual, on-page audit signals."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.heading_parts = []
        self._active_heading = None
        self._active_heading_parts = []
        self._in_title = False
        self._skip_text = 0
        self.text_parts = []
        self.description = ''
        self.robots = ''
        self.canonical = ''
        self.language = ''
        self.schema_blocks = 0
        self.internal_links = 0
        self.external_links = 0
        self.link_hrefs = []

    def handle_starttag(self, tag, attrs):
        attributes = {key.lower(): (value or '') for key, value in attrs}
        if tag == 'html':
            self.language = attributes.get('lang', '')
        elif tag == 'title':
            self._in_title = True
        elif tag in {'h1', 'h2', 'h3'}:
            self._active_heading = tag
            self._active_heading_parts = []
        elif tag in {'script', 'style', 'noscript'}:
            self._skip_text += 1
            if tag == 'script' and attributes.get('type', '').lower() == 'application/ld+json':
                self.schema_blocks += 1
        elif tag == 'meta':
            name = attributes.get('name', '').lower()
            property_name = attributes.get('property', '').lower()
            content = attributes.get('content', '').strip()
            if name == 'description' or property_name == 'og:description':
                self.description = self.description or content
            elif name == 'robots':
                self.robots = content.lower()
        elif tag == 'link' and 'canonical' in attributes.get('rel', '').lower():
            self.canonical = attributes.get('href', '').strip()
        elif tag == 'a':
            href = attributes.get('href', '').strip()
            if href:
                self.link_hrefs.append(href)
            if href.startswith(('http://', 'https://')):
                self.external_links += 1
            elif href:
                self.internal_links += 1

    def handle_endtag(self, tag):
        if tag == 'title':
            self._in_title = False
        elif tag == self._active_heading:
            heading = ' '.join(self._active_heading_parts).strip()
            if heading:
                self.heading_parts.append(heading)
            self._active_heading = None
            self._active_heading_parts = []
        elif tag in {'script', 'style', 'noscript'} and self._skip_text:
            self._skip_text -= 1

    def handle_data(self, data):
        text_value = ' '.join(data.split())
        if not text_value:
            return
        if self._in_title:
            self.title_parts.append(text_value)
        if self._active_heading:
            self._active_heading_parts.append(text_value)
        if not self._skip_text:
            self.text_parts.append(text_value)
