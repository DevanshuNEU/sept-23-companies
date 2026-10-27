"""Plain text from a posting body (#21).

`html_to_text` is the only helper here. The body hash is `outreach.store.writes.body_sha256`, defined once there.

By default it keeps only what a reader of the page sees (#112): hidden elements, `<template>` and `<svg>` are left out
and invisible characters are dropped, so a posting body or a page the judge reads holds no text the rules or a model
would see and Devanshu would not. The careers-page watcher passes `visible_only=False`: its fingerprints were taken
with the old text, and a collapsed tab can hold the jobs it watches for.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from outreach.rules.text import strip_invisible

__all__ = ["html_to_text"]

_SKIP = frozenset({"script", "style", "noscript"})
_SKIP_VISIBLE = _SKIP | {"template", "svg"}
# Tags that end a line, so the rules' sentence splitter sees a stop between list items and paragraphs.
_BREAKS = frozenset({"p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "section",
                     "td", "th", "dt", "dd", "table", "dl", "blockquote", "hr", "pre", "article", "header", "footer"})

_HSPACE = re.compile(r"[^\S\n]+")  # spaces, tabs and no-break spaces, never a line break
_BLANKS = re.compile(r"\n{3,}")

# Hidden elements (#112): text a reader of the page never sees, so neither the rules nor a model read it. A hidden
# "We sponsor H-1B visas" read yes. The style tests come from tools/posting_guard.py in ai-job-search-dev/ai-job-search
# (MIT, Mads Lorentzen), less its opacity:0, which Webflow puts on every block that fades in. The style is read as a
# browser reads it: HTMLParser has already decoded entities ("display&#58;none"), and CSS comments are removed first
# ("display:/**/none").
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?(?![\d.])"
    r"|color\s*:\s*transparent"
    r"|clip\s*:\s*rect\(\s*0"
    r"|clip-path\s*:\s*inset\(\s*100%"
    r"|(?:left|top|text-indent)\s*:\s*-\d{4,}px",
    re.I)
# A box collapsed to nothing with its overflow clipped hides its content too. Either half alone is everyday layout.
_COLLAPSED_BOX = re.compile(r"\b(?:max-)?(?:height|width)\s*:\s*0(?:\.0+)?(?:px|em|rem|%|pt)?(?![\d.])", re.I)
_CLIPPED = re.compile(r"\boverflow(?:-[xy])?\s*:\s*hidden\b", re.I)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# Tags with no closing tag: never pushed on the open-element stack, so a hidden one hides nothing after it.
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                   "track", "wbr"})
# HTML's implied ends: an open <p> is closed by any block opener, and these by a sibling of the same name.
_CLOSES_P = _BREAKS - {"br"}
_SIBLING_ENDS = frozenset({"li", "dd", "dt", "tr", "td", "th", "option"})


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    values = {name.lower(): (value or "") for name, value in attrs}
    if "hidden" in values:  # not aria-hidden: that hides from a screen reader only, and the text is on the screen
        return True
    style = _CSS_COMMENT.sub("", values.get("style", ""))
    return bool(_HIDDEN_STYLE.search(style) or (_COLLAPSED_BOX.search(style) and _CLIPPED.search(style)))


class _Extractor(HTMLParser):
    def __init__(self, visible_only: bool) -> None:
        super().__init__(convert_charrefs=True)
        self._visible_only = visible_only
        self._skip = _SKIP_VISIBLE if visible_only else _SKIP
        self.parts: list[str] = []
        self._skipping = 0
        self._break = False  # a tag asked for a line break that the next text will carry (one, however many tags)
        self._open: list[str] = []  # open elements, innermost last
        self._hidden_at: int | None = None  # index in _open of the element that started the hidden run

    def _close_to(self, index: int) -> None:
        del self._open[index:]
        if self._hidden_at is not None and self._hidden_at >= len(self._open):
            self._hidden_at = None

    def handle_starttag(self, tag, attrs):
        if tag in self._skip:
            self._skipping += 1
            return
        if tag in _BREAKS and not self._skipping:
            self._break = True
        if self._skipping or tag in _VOID:
            return
        if self._open and ((self._open[-1] == "p" and tag in _CLOSES_P)
                           or (self._open[-1] == tag and tag in _SIBLING_ENDS)):
            self._close_to(len(self._open) - 1)
        self._open.append(tag)
        if self._visible_only and self._hidden_at is None and _is_hidden(attrs):
            self._hidden_at = len(self._open) - 1

    def handle_startendtag(self, tag, attrs):
        # A "/>" on a tag that has a closing tag is ignored, as browsers do: "<div hidden/>" hides what follows it.
        if tag in _VOID or tag in self._skip:
            if tag in _BREAKS and not self._skipping:
                self._break = True
            return
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self._skip:
            self._skipping = max(0, self._skipping - 1)
            return
        if tag in _BREAKS and not self._skipping:
            self._break = True
        if self._skipping:
            return
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i] == tag:
                self._close_to(i)
                break

    def handle_data(self, data):
        if self._skipping or self._hidden_at is not None:
            return
        if self._break and self.parts:
            self.parts.append("\n")
        self._break = False
        self.parts.append(data)

    # Comments, declarations and processing instructions fall through to HTMLParser's no-op handlers.


def html_to_text(markup: str, *, visible_only: bool = True) -> str:
    parser = _Extractor(visible_only)
    parser.feed(markup.replace("\x00", ""))
    parser.close()
    text = "".join(parser.parts).replace("\x00", "")
    if visible_only:
        text = strip_invisible(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(_HSPACE.sub(" ", line).strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()
