from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re

from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import (
    clean_text,
    normalize_email,
    normalize_phone,
    normalize_website,
)


_CHANNEL_LINE_RE = re.compile(
    r"^\s*(?P<label>"
    r"e[\s-]*mail|email|electronic\s+mail|"
    r"telephone(?:\s*(?:no\.?|number))?|phone(?:\s*(?:no\.?|number))?|"
    r"tel\.?|mobile(?:\s+phone)?|cell(?:ular)?(?:\s+phone)?|"
    r"website|web\s*site|web|url|homepage"
    r")\s*(?::|：|=|[-–—]\s+)?\s*(?P<value>.+?)\s*$",
    flags=re.I,
)
_METADATA_RE = re.compile(
    r"^(?:"
    r"ability\s+to\s+(?:read|speak)|other\s+languages?|languages?|"
    r"educational\s+background|education|law\s+background|legal\s+background|"
    r"cases?\s+willing\s+to\s+handle|areas?\s+of\s+(?:practice|expertise)|"
    r"practice\s+areas?|speciali[sz]ation|bar\s+admissions?|admitted|"
    r"partners?\s+in\s+firm|number\s+of\s+partners?|fees?|notary|"
    r"postal\s+address|mailing\s+address|office\s+hours?|remarks?|notes?"
    r")\b",
    flags=re.I,
)
_PERSON_TITLE_RE = re.compile(
    r"^(?:dr\.?|prof\.?|mr\.?|mrs\.?|ms\.?|miss|adv\.?|atty\.?|"
    r"attorney|rechtsanwalt|rechtsanwältin)\s+",
    flags=re.I,
)
_FIRM_HINT_RE = re.compile(
    r"(?:\b(?:law|legal|attorneys?|lawyers?|advocates?|solicitors?|counsel|"
    r"partners?|associates?|chambers|notar(?:y|ies)|patent|trademark|"
    r"immigration|consult(?:ing|ants?)?|firm|office|group|llp|llc|ltd|limited|"
    r"inc|corp|company|co\.?|pllc|pc|mbb)\b|"
    r"rechtsanw|kanzlei|anwalts|avocats?|abogados?|abogad[oa]s|"
    r"知识产权|律师|事务所)",
    flags=re.I,
)
_ADDRESS_RE = re.compile(
    r"(?:\d|\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|boulevard|blvd\.?|"
    r"lane|ln\.?|drive|dr\.?|way|platz|strasse|straße|str\.?|weg|allee|"
    r"rue|via|calle|road|building|bldg\.?|suite|floor|fl\.?|box|postfach)\b)",
    flags=re.I,
)
_PAGE_OR_HEADING_RE = re.compile(
    r"^(?:page\s+\d+|list\s+of\s+|english[-\s]+speaking\s+|"
    r"united\s+states\s+(?:embassy|consulate)|u\.s\.\s+(?:embassy|consulate))",
    flags=re.I,
)


@dataclass
class _DirectoryRecord:
    person_index: int
    person: str
    first_channel_index: int
    channels: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add_channel(self, header: str, value: str) -> None:
        values = self.channels[header]
        folded = value.casefold()
        if not any(item.casefold() == folded for item in values):
            values.append(value)


def _channel_from_line(line: str) -> tuple[str, str] | None:
    match = _CHANNEL_LINE_RE.match(line)
    if not match:
        return None
    label = re.sub(r"[^a-z]", "", match.group("label").casefold())
    value = clean_text(match.group("value"))
    if not value:
        return None

    if label in {"email", "electronicmail"}:
        normalized = normalize_email(value)
        return ("Email", value) if normalized else None
    if label in {"website", "web", "url", "homepage"}:
        normalized = normalize_website(value)
        return ("Website", value) if normalized else None

    normalized = normalize_phone(value)
    return ("Phone", value) if normalized else None


def _is_metadata(line: str) -> bool:
    return bool(_METADATA_RE.match(line))


def _person_score(line: str) -> int:
    text = clean_text(line).strip("•·*-–— ")
    if not text or len(text) > 100:
        return -1
    if _channel_from_line(text) or _is_metadata(text) or _PAGE_OR_HEADING_RE.match(text):
        return -1
    if any(token in text.casefold() for token in ("http://", "https://", "www.", "@")):
        return -1
    if any(char.isdigit() for char in text) or ":" in text or "：" in text:
        return -1
    if text.isupper() and len(text) > 3:
        return -1

    words = text.split()
    if not 2 <= len(words) <= 8:
        return -1

    titled = bool(_PERSON_TITLE_RE.match(text))
    if _FIRM_HINT_RE.search(text) and not titled:
        return -1

    alpha = sum(char.isalpha() for char in text)
    visible = sum(not char.isspace() for char in text)
    if visible == 0 or alpha / visible < 0.6:
        return -1

    capitalized = 0
    lexical = 0
    for raw_word in words:
        word = raw_word.strip(".,;()[]{}'\"")
        letters = [char for char in word if char.isalpha()]
        if not letters:
            continue
        lexical += 1
        if letters[0].isupper():
            capitalized += 1
    if lexical < 2 or capitalized / lexical < 0.5:
        return -1

    score = 4
    if titled:
        score += 5
    if 2 <= len(words) <= 4:
        score += 2
    if capitalized == lexical:
        score += 1
    return score


def _find_person_anchor(lines: list[str], channel_index: int) -> tuple[int, str] | None:
    best: tuple[int, int, int, str] | None = None
    lower = max(0, channel_index - 12)
    for index in range(lower, channel_index):
        line = lines[index]
        score = _person_score(line)
        if score < 0:
            continue
        distance = channel_index - index
        candidate = (score, -distance, index, clean_text(line).strip("•·*-–— "))
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None
    return best[2], best[3]


def _looks_like_address(line: str) -> bool:
    text = clean_text(line)
    if not text or _channel_from_line(text) or _is_metadata(text):
        return False
    return bool(_ADDRESS_RE.search(text))


def _firm_and_address(lines: list[str], record: _DirectoryRecord) -> tuple[str, str]:
    between = [
        clean_text(line)
        for line in lines[record.person_index + 1 : record.first_channel_index]
        if clean_text(line)
    ]
    firm = ""
    address_parts: list[str] = []
    for line in between:
        if _channel_from_line(line) or _is_metadata(line) or _PAGE_OR_HEADING_RE.match(line):
            continue
        if _looks_like_address(line):
            address_parts.append(line)
            continue
        if address_parts and len(line.split()) <= 5 and not _FIRM_HINT_RE.search(line):
            # Preserve locality lines immediately following a street/postal line.
            address_parts.append(line)
            continue
        if not firm and len(line.split()) >= 2:
            if _FIRM_HINT_RE.search(line) or _person_score(line) < 0:
                firm = line
            elif len(line.split()) <= 8:
                firm = line
    return firm, ", ".join(address_parts)


def directory_contact_text_table(text: str, *, source_member: str) -> TableData | None:
    """Parse high-confidence narrative attorney/agent directory entries.

    Public lawyer and agent directories often render as repeated text cards rather
    than actual tables: an unlabeled person name and optional firm/address are
    followed by labeled Website/E-Mail/Telephone lines. The generic key/value
    parser cannot infer the unlabeled identity, so this adapter materializes a
    synthetic contact table only when at least two independently anchored people
    with validated channels are present. Requiring repeated people keeps ordinary
    document prose and single publisher/embassy contact blocks out of ingestion.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [clean_text(line) for line in normalized.splitlines()]
    if not any(lines):
        return None

    records: dict[int, _DirectoryRecord] = {}
    for index, line in enumerate(lines):
        if not line:
            continue
        channel = _channel_from_line(line)
        if channel is None:
            continue
        anchor = _find_person_anchor(lines, index)
        if anchor is None:
            continue
        person_index, person = anchor
        record = records.get(person_index)
        if record is None:
            record = _DirectoryRecord(
                person_index=person_index,
                person=person,
                first_channel_index=index,
            )
            records[person_index] = record
        record.first_channel_index = min(record.first_channel_index, index)
        record.add_channel(*channel)

    qualified = [record for record in records.values() if record.channels]
    if len(qualified) < 2:
        return None

    qualified.sort(key=lambda item: item.person_index)
    rows: list[list[str]] = [["Attorney", "Firm", "Address", "Email", "Phone", "Website"]]
    seen: set[tuple[str, str, str]] = set()
    for record in qualified:
        firm, address = _firm_and_address(lines, record)
        emails = "; ".join(record.channels.get("Email", []))
        phones = "; ".join(record.channels.get("Phone", []))
        websites = "; ".join(record.channels.get("Website", []))
        identity = (record.person.casefold(), firm.casefold(), emails.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        rows.append([record.person, firm, address, emails, phones, websites])

    if len(rows) < 3:
        return None
    return TableData(
        source_member=source_member,
        sheet_name="narrative-directory",
        rows=rows,
    )
