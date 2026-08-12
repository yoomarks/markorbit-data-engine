from __future__ import annotations

from dataclasses import dataclass
import re

from app.contact_ingest.directory_text import (
    directory_contact_text_table as _v15_directory_contact_text_table,
)
from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import (
    clean_text,
    normalize_email,
    normalize_phone,
    normalize_website,
)


_EMAIL_RE = re.compile(
    r"(?i)[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+"
)
_WEB_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()\[\]{};,]+")
_PHONE_LABEL_RE = re.compile(
    r"(?i)\b(?:tel(?:ephone)?(?:\s*/\s*fax)?|phone|mobile|cell(?:phone)?|fax|"
    r"office\s+telephone|secretary|t[eé]l(?:[eé]phone)?|telefone|telem[oó]vel)"
    r"\b\s*(?:no\.?|number)?\s*[:：.]?\s*"
)
_PHONE_VALUE_RE = re.compile(
    r"(?<!\w)(?:\+\s*)?\(?\d{1,4}\)?(?:[\s()./\-]*\d){5,}"
    r"(?:\s*(?:x|ext\.?)\s*\d+)?(?!\w)",
    flags=re.I,
)
_DATE_OR_REGISTRATION_RE = re.compile(
    r"^(?:\d{1,3}[./-]){2}\d{2,4}$|^\d{1,4}/\d{1,4}/\d{2,4}$"
)
_FIRM_HINT_RE = re.compile(
    r"(?i)(?:\b(?:law|legal|attorneys?|lawyers?|advocates?|solicitors?|counsel|"
    r"partners?|associates?|chambers|patent|trademark|firm|group|llp|llc|ltd|"
    r"limited|inc|corp|company|co\.?|cabinet|conseils?|advogados?|consultores?|"
    r"intellectual\s+property)\b|事务所|有限公司|公司|代理|知识产权)"
)
_ADDRESS_HINT_RE = re.compile(
    r"(?i)(?:\d{1,5}\s+\w|\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"boulevard|blvd\.?|lane|drive|via|rue|str\.?|stra(?:ss|ß)e|allee|platz|"
    r"floor|fl\.?|suite|building|bldg\.?|box|postfach|avenida|av\.)\b)"
)
_HEADING_RE = re.compile(
    r"(?i)^(?:page\s+\d+|list\s+of\s+|attorneys?\s*[-–—]|legal\s+assistance|"
    r"united\s+states\s+(?:embassy|consulate)|u\.s\.\s+(?:embassy|consulate)|"
    r"embassy\s+of\s+the\s+united\s+states|main\s+areas?|areas?\s+of\s+practice|"
    r"practice|speciali[sz]ation|languages?|information|disclaimer|note|"
    r"notaries?\s+public|patents?\s*&\s*trade\s*marks|credit\s+reporting|"
    r"directory\s+updated|directory\s+of|french\s+patent\s*&\s*trademark|"
    r"professional\s+credentials|american\s+citizen\s+services|"
    r"b\s*:\s*brevet|cookie\s+settings)\b"
)
_FIELD_HEADING_RE = re.compile(
    r"(?i)^(?:date\s+of\s+registration|certificate\s+number|validity\s+period|"
    r"address(?:\s+for\s+correspondence)?|postal\s+address|place\s+of\s+work|"
    r"position|entry\s*/\s*re-entry|born|degree|education|email|e-?mail|"
    r"telephone|tel|phone|mobile|cell|fax|website|web\s*site)\b"
)
_GENERIC_LOCATION_RE = re.compile(
    r"(?i)^(?:tuscany|florence|arezzo|carrara|bansko|burgas|lovech|pleven|"
    r"plovdiv|rousse|stara\s+zagora|varna|vidin|greenland|faroe\s+islands)$"
)
_REGISTRATION_RE = re.compile(
    r"(?i)\b(?:reg(?:istration)?\.?\s*(?:no\.?|number)?|agent\s+id)"
    r"\s*[:.]?\s*([A-Z0-9][A-Z0-9/.\-]{0,30})"
)
_ADDRESS_LABEL_RE = re.compile(
    r"(?is)\b(?:address(?:\s+for\s+correspondence)?|postal\s+address|adresse|"
    r"endere[cç]o)\s*:\s*(.+?)(?=\b(?:tel|telephone|phone|mobile|cell|fax|"
    r"e-?mail|email|website|web\s*site|place\s+of\s+work|position)\b|$)"
)
_FIRM_LABEL_RE = re.compile(
    r"(?is)\b(?:place\s+of\s+work|firm|company)\s*:\s*(.+?)"
    r"(?=\b(?:position|address|tel|telephone|phone|mobile|fax|e-?mail|email|"
    r"website|web\s*site)\b|$)"
)


@dataclass
class DirectoryRow:
    attorney: str = ""
    firm: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    agent_code: str = ""

    def key(self) -> tuple[str, str, str, str]:
        return (
            self.attorney.casefold(),
            self.firm.casefold(),
            self.email.casefold(),
            self.phone.casefold(),
        )


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = clean_text(value).strip(" ,;.")
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def extract_contact_values(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract validated contact channels from prose without assigning ownership."""
    emails = _dedupe([match.group(0) for match in _EMAIL_RE.finditer(text)])
    websites = _dedupe([match.group(0).rstrip(".,") for match in _WEB_RE.finditer(text)])

    phones: list[str] = []
    label_matches = list(_PHONE_LABEL_RE.finditer(text))
    for index, match in enumerate(label_matches):
        end = label_matches[index + 1].start() if index + 1 < len(label_matches) else len(text)
        segment = text[match.end() : end]
        stops = [
            found.start()
            for pattern in (_EMAIL_RE, _WEB_RE)
            if (found := pattern.search(segment)) is not None
        ]
        if stops:
            segment = segment[: min(stops)]
        for candidate in _PHONE_VALUE_RE.finditer(segment):
            raw = clean_text(candidate.group(0)).strip(" ,;.")
            compact = re.sub(r"\s+", "", raw)
            digits = re.sub(r"\D", "", raw)
            if _DATE_OR_REGISTRATION_RE.fullmatch(compact):
                continue
            if len(digits) >= 7 and normalize_phone(raw):
                phones.append(raw)

    # Unlabelled OCR/HTML numbers are too ambiguous (dates, registration IDs,
    # postal codes). Only accept an explicit international '+' number here;
    # local numbers must be anchored by a phone/fax label above.
    for candidate in _PHONE_VALUE_RE.finditer(text):
        raw = clean_text(candidate.group(0)).strip(" ,;.")
        digits = re.sub(r"\D", "", raw)
        if raw.startswith("+") and len(digits) >= 7 and normalize_phone(raw):
            phones.append(raw)

    # Keep raw values; the planner performs authoritative channel normalization.
    emails = [value for value in emails if normalize_email(value)]
    websites = [value for value in websites if normalize_website(value)]
    return _dedupe(emails), _dedupe(phones), _dedupe(websites)


def _is_firm_name(value: str) -> bool:
    text = clean_text(value)
    if not text:
        return False
    words = text.split()
    if _FIRM_HINT_RE.search(text) and len(words) <= 14 and text.count(",") <= 2:
        return True
    letters = [char for char in text if char.isalpha()]
    if not letters or not 2 <= len(text.split()) <= 9:
        return False
    uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)
    return uppercase_ratio >= 0.78 and not _HEADING_RE.match(text)


def _looks_like_person(value: str) -> bool:
    text = clean_text(value).strip("•·*-–— ")
    if not text or len(text) > 100:
        return False
    if _HEADING_RE.match(text) or _FIELD_HEADING_RE.match(text) or _GENERIC_LOCATION_RE.match(text):
        return False
    if "(cid" in text.casefold() or any(char.isdigit() for char in text):
        return False
    if _FIRM_HINT_RE.search(text):
        return False
    if re.match(r"(?i)^(?:in\s+\w+|\w+\s+office(?:\s*\([^)]*\))?)$", text):
        return False
    words = text.split()
    if not 2 <= len(words) <= 7:
        return False
    lexical = [re.sub(r"[^A-Za-zÀ-ž'’-]", "", word) for word in words]
    lexical = [word for word in lexical if word]
    if len(lexical) < 2:
        return False
    capitalized = sum(word[0].isupper() for word in lexical)
    return capitalized / len(lexical) >= 0.8


def _card_row(identity: str, text: str) -> DirectoryRow | None:
    identity = clean_text(identity).strip(" ,;:-–—")
    if not identity or len(identity) > 130:
        return None
    emails, phones, websites = extract_contact_values(text)

    registration = _REGISTRATION_RE.search(text)
    agent_code = clean_text(registration.group(1)).upper() if registration else ""
    if not agent_code:
        # EPO professional-representative cards put the code directly after h3.
        remainder = clean_text(text)
        if remainder.casefold().startswith(identity.casefold()):
            remainder = clean_text(remainder[len(identity) :])
        code_match = re.match(r"^([0-9]{5,8})\b", remainder)
        if code_match:
            agent_code = code_match.group(1)

    if not (emails or phones or websites or agent_code):
        return None

    is_firm = _is_firm_name(identity)
    row = DirectoryRow(
        attorney="" if is_firm else identity,
        firm=identity if is_firm else "",
        email="; ".join(emails),
        phone="; ".join(phones),
        website="; ".join(websites),
        agent_code=agent_code,
    )

    address = _ADDRESS_LABEL_RE.search(text)
    if address:
        row.address = clean_text(address.group(1))

    if not is_firm:
        firm = _FIRM_LABEL_RE.search(text)
        if firm:
            row.firm = clean_text(firm.group(1)).strip(" ,;.")
        else:
            co_match = re.search(r"(?i)\bc/o\s+([^,;.\n]{2,100})", text)
            if co_match:
                row.firm = clean_text(co_match.group(1)).strip(" ,;.")

    return row


def directory_contact_cards_table(
    cards: list[tuple[str, str]],
    *,
    source_member: str,
    sheet_name: str = "html-directory",
) -> TableData | None:
    rows: list[DirectoryRow] = []
    seen: set[tuple[str, str, str, str]] = set()
    for identity, text in cards:
        row = _card_row(identity, text)
        if row is None:
            continue
        key = row.key()
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    # Repeated explicit cards are the safety boundary. A single publisher,
    # embassy, or footer contact must not become an agent directory.
    if len(rows) < 2:
        return None

    table_rows = [[
        "Attorney",
        "Firm",
        "Address",
        "Email",
        "Phone",
        "Website",
        "Agent Code",
    ]]
    table_rows.extend([
        [
            row.attorney,
            row.firm,
            row.address,
            row.email,
            row.phone,
            row.website,
            row.agent_code,
        ]
        for row in rows
    ])
    return TableData(source_member=source_member, sheet_name=sheet_name, rows=table_rows)


def _strong_anchor(value: str) -> tuple[str, str] | None:
    text = clean_text(value).strip("•· ")
    if not text or len(text) > 140:
        return None
    if (
        _HEADING_RE.match(text)
        or _FIELD_HEADING_RE.match(text)
        or _GENERIC_LOCATION_RE.match(text)
        or "(cid" in text.casefold()
    ):
        return None
    if _EMAIL_RE.search(text) or _WEB_RE.search(text) or _PHONE_LABEL_RE.search(text):
        return None
    if text.count(",") >= 3:
        return None
    if text.endswith(".") and len(text.split()) > 5:
        return None
    digits = sum(char.isdigit() for char in text)
    if digits >= 4 and not _FIRM_HINT_RE.search(text):
        return None
    if _ADDRESS_HINT_RE.search(text) and not _FIRM_HINT_RE.search(text):
        return None

    if _is_firm_name(text):
        return "firm", text
    if _looks_like_person(text):
        return "person", text
    return None


def _inline_anchor(line: str) -> tuple[str, str, str] | None:
    text = clean_text(line)
    for separator in (" – ", " — ", " - "):
        if separator not in text:
            continue
        prefix, remainder = text.split(separator, 1)
        anchor = _strong_anchor(prefix)
        if anchor:
            return anchor[0], anchor[1], clean_text(remainder)

    if ":" in text:
        prefix, remainder = text.split(":", 1)
        prefix = clean_text(prefix)
        # Location-office and field labels are not entity identities.
        if re.search(
            r"(?i)\b(?:bansko|sofia|varna|burgas|lovech|pleven|plovdiv|rousse|"
            r"office|address|contact\s+person|secretary)\s*$",
            prefix,
        ) and not re.search(r"(?i)\b(?:law|legal|ltd|llp|company|co\.?)\b", prefix):
            return None
        anchor = _strong_anchor(prefix)
        if anchor:
            return anchor[0], anchor[1], clean_text(remainder)
    return None


def _inline_directory_contact_table(text: str, *, source_member: str) -> TableData | None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [clean_text(line) for line in normalized.splitlines()]
    anchors: list[tuple[int, str, str, str]] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        inline = _inline_anchor(line)
        if inline:
            anchors.append((index, inline[0], inline[1], inline[2]))
            continue
        anchor = _strong_anchor(line)
        if anchor and anchor[0] == "firm":
            anchors.append((index, anchor[0], anchor[1], ""))

    rows: list[DirectoryRow] = []
    seen: set[tuple[str, str, str, str]] = set()
    for position, (index, kind, name, remainder) in enumerate(anchors):
        next_index = anchors[position + 1][0] if position + 1 < len(anchors) else len(lines)
        # Directory entries are compact. A hard cap avoids swallowing a page of
        # explanatory prose when the next entity starts much later.
        end = min(next_index, index + 36)
        block_lines = ([remainder] if remainder else []) + lines[index + 1 : end]
        block = "\n".join(line for line in block_lines if line)
        emails, phones, websites = extract_contact_values(block)
        if not (emails or phones or websites):
            continue

        row = DirectoryRow(
            attorney=name if kind == "person" else "",
            firm=name if kind == "firm" else "",
            email="; ".join(emails),
            phone="; ".join(phones),
            website="; ".join(websites),
        )
        address = _ADDRESS_LABEL_RE.search(block)
        if address:
            row.address = clean_text(address.group(1))
        elif remainder:
            before_channel = re.split(
                r"(?i)\b(?:tel|telephone|phone|mobile|cell|fax|e-?mail|email|"
                r"website|web\s*site)\b",
                remainder,
                maxsplit=1,
            )[0]
            if _ADDRESS_HINT_RE.search(before_channel):
                row.address = clean_text(before_channel).strip(" ,;.")

        key = row.key()
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)

    if len(rows) < 2:
        return None

    table_rows = [["Attorney", "Firm", "Address", "Email", "Phone", "Website", "Agent Code"]]
    table_rows.extend([
        [
            row.attorney,
            row.firm,
            row.address,
            row.email,
            row.phone,
            row.website,
            row.agent_code,
        ]
        for row in rows
    ])
    return TableData(
        source_member=source_member,
        sheet_name="inline-directory",
        rows=table_rows,
    )


def directory_contact_text_table(text: str, *, source_member: str) -> TableData | None:
    """V1.6 wrapper: preserve V1.5 narrative behavior, then parse inline/firm directories."""
    table = _v15_directory_contact_text_table(text, source_member=source_member)
    if table is not None:
        return table
    return _inline_directory_contact_table(text, source_member=source_member)
