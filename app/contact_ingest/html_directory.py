from __future__ import annotations

from html.parser import HTMLParser

from app.contact_ingest.directory_text_v16 import directory_contact_cards_table
from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import clean_text


_BLOCK_TAGS = {
    "div", "p", "br", "li", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th",
}


class _DirectoryCardHTMLParser(HTMLParser):
    """Stream explicit public-directory cards without a heavyweight HTML dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[tuple[str, str]] = []
        self._root_tag = ""
        self._same_tag_depth = 0
        self._parts: list[str] = []
        self._identity_parts: list[str] = []
        self._identity_tag = ""
        self._identity_depth = 0
        self._ignored_depth = 0

    def _active(self) -> bool:
        return bool(self._root_tag)

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        raw = next((value or "" for key, value in attrs if key.lower() == "class"), "")
        return {item.casefold() for item in raw.split() if item}

    def _start_card(self, tag: str) -> None:
        self._root_tag = tag
        self._same_tag_depth = 1
        self._parts = []
        self._identity_parts = []
        self._identity_tag = ""
        self._identity_depth = 0

    def _finish_card(self) -> None:
        identity = clean_text("".join(self._identity_parts))
        text = clean_text(" ".join(self._parts))
        if identity and text:
            self.cards.append((identity, text))
        self._root_tag = ""
        self._same_tag_depth = 0
        self._parts = []
        self._identity_parts = []
        self._identity_tag = ""
        self._identity_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return

        if not self._active():
            classes = self._classes(attrs)
            should_start = (
                tag == "section"
                or tag == "table"
                or (tag == "li" and "mandataire-item" in classes)
                or tag == "p"
            )
            if should_start:
                self._start_card(tag)
        elif tag == self._root_tag:
            self._same_tag_depth += 1

        if not self._active():
            return

        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

        if not self._identity_tag and tag in {"h3", "strong", "b"}:
            self._identity_tag = tag
            self._identity_depth = 1
        elif self._identity_tag and tag == self._identity_tag:
            self._identity_depth += 1

        if tag == "a":
            href = next((value or "" for key, value in attrs if key.lower() == "href"), "")
            folded = href.casefold()
            if folded.startswith("mailto:"):
                self._parts.append(f" Email: {href[7:]} ")
            elif folded.startswith("tel:"):
                self._parts.append(f" Telephone: {href[4:]} ")
            elif folded.startswith(("http://", "https://")):
                self._parts.append(f" Website: {href} ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth or not self._active():
            return

        if self._identity_tag and tag == self._identity_tag:
            self._identity_depth -= 1
            if self._identity_depth <= 0:
                self._identity_tag = ""
                self._identity_depth = 0

        if tag == self._root_tag:
            self._same_tag_depth -= 1
            if self._same_tag_depth <= 0:
                self._finish_card()
                return

        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not self._active():
            return
        self._parts.append(data)
        if self._identity_tag:
            self._identity_parts.append(data)


def html_directory_contact_table(html: str, *, source_member: str) -> TableData | None:
    parser = _DirectoryCardHTMLParser()
    parser.feed(html)
    parser.close()
    return directory_contact_cards_table(
        parser.cards,
        source_member=source_member,
        sheet_name="html-directory",
    )
