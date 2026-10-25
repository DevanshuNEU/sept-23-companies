"""Plain text from a posting body (#21).

`html_to_text` is the only helper here. The body hash is `outreach.store.writes.body_sha256`, defined once there.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["html_to_text"]

_SKIP = frozenset({"script", "style", "noscript"})
# Tags that end a line, so the rules' sentence splitter sees a stop between list items and paragraphs.
_BREAKS = frozenset({"p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "section",
                     "td", "th", "dt", "dd", "table", "dl", "blockquote", "hr", "pre", "article", "header", "footer"})

_HSPACE = re.compile(r"[^\S\n]+")  # spaces, tabs and no-break spaces, never a line break
_BLANKS = re.compile(r"\n{3,}")


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0
        self._break = False  # a tag asked for a line break that the next text will carry (one, however many tags)

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skipping += 1
        elif tag in _BREAKS and not self._skipping:
            self._break = True

    def handle_startendtag(self, tag, attrs):
        if tag in _BREAKS and not self._skipping:
            self._break = True

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BREAKS and not self._skipping:
            self._break = True

    def handle_data(self, data):
        if self._skipping:
            return
        if self._break and self.parts:
            self.parts.append("\n")
        self._break = False
        self.parts.append(data)

    # Comments, declarations and processing instructions fall through to HTMLParser's no-op handlers.


def html_to_text(markup: str) -> str:
    parser = _Extractor()
    parser.feed(markup.replace("\x00", ""))
    parser.close()
    text = "".join(parser.parts).replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_HSPACE.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()
