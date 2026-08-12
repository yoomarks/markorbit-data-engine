from __future__ import annotations

import re
import unicodedata

from app.contact_ingest.directory_text_v16 import extract_contact_values
from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import clean_text


_GENERIC_HEADER_ALIASES = {
    "代理地址": "Address",
    "代理人地址": "Address",
    "registrationnumber": "Agent Code",
    "jobtile": "Title",
    "escritorio": "Firm",
    "endereco": "Address",
    "cidade": "City",
    "telefone": "Phone",
    "telemovel": "Mobile",
}
_FIRM_HINT_RE = re.compile(
    r"(?i)(?:\b(?:law|legal|attorneys?|lawyers?|advocates?|solicitors?|partners?|"
    r"associates?|chambers|patent|trademark|firm|group|llp|llc|ltd|limited|inc|"
    r"corp|company|co\.?|cabinet|consult(?:ing|ants?)?)\b|"
    r"事务所|有限公司|公司|代理|知识产权)"
)


def _key(value: str) -> str:
    text = unicodedata.normalize("NFKD", clean_text(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text.casefold())


def _first_firm_and_address(block: str) -> tuple[str, str]:
    text = clean_text(block)
    before_channels = re.split(
        r"(?i)\b(?:tel|telephone|phone|mobile|cell|fax|e-?mail|email|website|web\s*site)\b",
        text,
        maxsplit=1,
    )[0]
    match = re.match(r"^(.{2,100}?)(?=\s+\#?\d)", before_channels)
    if match:
        firm = clean_text(match.group(1)).strip(" ,;.")
        address = clean_text(before_channels[match.end() :]).strip(" ,;.")
        return firm, address
    return "", clean_text(before_channels).strip(" ,;.")


def _adapt_singapore_foreign_agent(table: TableData) -> TableData | None:
    header_index = -1
    headers: list[str] = []
    for index, row in enumerate(table.rows[:20]):
        keys = {_key(value) for value in row if clean_text(value)}
        if "nameofforeignpatentsagent" in keys and "contactaddress" in keys:
            header_index = index
            headers = [clean_text(value) for value in row]
            break
    if header_index < 0:
        return None

    index_by_key = {_key(value): index for index, value in enumerate(headers)}
    agent_idx = index_by_key["nameofforeignpatentsagent"]
    contact_idx = index_by_key["contactaddress"]
    registration_idx = index_by_key.get("registrationno", -1)
    status_idx = index_by_key.get("statusofpractisingcertificate", -1)

    rows = [["Attorney", "Firm", "Address", "Email", "Phone", "Website", "Agent Code", "Status"]]
    for source_row in table.rows[header_index + 1 :]:
        attorney = clean_text(source_row[agent_idx]) if agent_idx < len(source_row) else ""
        if not attorney:
            continue
        block = clean_text(source_row[contact_idx]) if contact_idx < len(source_row) else ""
        emails, phones, websites = extract_contact_values(block)
        firm, address = _first_firm_and_address(block)
        agent_code = clean_text(source_row[registration_idx]) if 0 <= registration_idx < len(source_row) else ""
        status = clean_text(source_row[status_idx]) if 0 <= status_idx < len(source_row) else ""
        rows.append([
            attorney,
            firm,
            address,
            "; ".join(emails),
            "; ".join(phones),
            "; ".join(websites),
            agent_code,
            status,
        ])
    if len(rows) < 2:
        return None
    return TableData(
        source_member=table.source_member,
        sheet_name=f"{table.sheet_name}-foreign-agent" if table.sheet_name else "foreign-agent",
        rows=rows,
    )


_MOZAMBIQUE_HEADERS = {
    "nodo": "Agent Code",
    "nodoaopi": "Agent Code",
    "aopi": "Agent Code",
    "nome": "Attorney",
    "escritorio": "Firm",
    "endereco": "Address",
    "cidade": "City",
    "telefone": "Phone",
    "fax": "Fax",
    "telemovel": "Mobile",
    "email": "Email",
    "website": "Website",
}


def _pick_cell(record: list[str], origin: int, *, want: str = "text") -> str:
    candidates: list[tuple[int, str]] = []
    for offset in (0, 1, -1, 2, -2, 3, -3):
        idx = origin + offset
        if idx < 0 or idx >= len(record):
            continue
        value = clean_text(record[idx])
        if not value:
            continue
        if want == "email" and "@" not in value:
            continue
        if want == "web" and not ("www." in value.casefold() or "http" in value.casefold()):
            continue
        if want == "phone" and len(re.sub(r"\D", "", value)) < 5:
            continue
        if want == "text" and not any(char.isalpha() for char in value):
            continue
        candidates.append((abs(offset), value))
    return min(candidates, default=(99, ""))[1]


def _adapt_mozambique_table(table: TableData) -> TableData | None:
    if not table.rows:
        return None
    header_index = -1
    semantic: dict[str, int] = {}
    for index, row in enumerate(table.rows[:8]):
        candidate: dict[str, int] = {}
        for col, value in enumerate(row):
            mapped = _MOZAMBIQUE_HEADERS.get(_key(value))
            if mapped and mapped not in candidate:
                candidate[mapped] = col
        if {"Attorney", "Email"} <= set(candidate) and len(candidate) >= 5:
            header_index = index
            semantic = candidate
            break
    if header_index < 0:
        return None

    width = max(len(row) for row in table.rows)
    collapsed: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    current_code = ""

    def flush() -> None:
        nonlocal current, current_code
        if current is not None and current_code:
            collapsed.append((current_code, current))
        current = None
        current_code = ""

    for row in table.rows[header_index + 1 :]:
        padded = [clean_text(row[col]) if col < len(row) else "" for col in range(width)]
        code = next((value for value in padded[:4] if re.fullmatch(r"\d{1,4}", value)), "")
        if code:
            flush()
            current = padded
            current_code = code
            continue
        if current is None:
            continue
        for col, value in enumerate(padded):
            if not value:
                continue
            current[col] = clean_text(f"{current[col]} {value}") if current[col] else value
    flush()

    headers = ["Attorney", "Firm", "Address", "City", "Phone", "Mobile", "Email", "Website", "Agent Code"]
    rows = [headers]
    for agent_code, record in collapsed:
        def at(name: str, want: str = "text") -> str:
            origin = semantic.get(name)
            return _pick_cell(record, origin, want=want) if origin is not None else ""

        attorney = at("Attorney")
        firm = at("Firm")
        email = at("Email", "email")
        website = at("Website", "web")
        phone = at("Phone", "phone")
        fax = at("Fax", "phone")
        mobile = at("Mobile", "phone")
        phone_parts = []
        for item in (phone, fax):
            if item and item not in phone_parts:
                phone_parts.append(item)
        if not (attorney or firm) or not (email or phone_parts or mobile or website):
            continue
        rows.append([
            attorney,
            firm,
            at("Address"),
            at("City"),
            "; ".join(phone_parts),
            mobile,
            email,
            website,
            agent_code,
        ])
    if len(rows) < 2:
        return None
    return TableData(
        source_member=table.source_member,
        sheet_name=f"{table.sheet_name}-aopi" if table.sheet_name else "aopi",
        rows=rows,
    )


def _adapt_vertical_directory(table: TableData) -> TableData | None:
    """Parse legacy spreadsheets made of identity rows plus contact continuations."""
    rows = table.rows
    if len(rows) < 6 or max((len(row) for row in rows), default=0) < 2:
        return None
    header_keys = {_key(value) for row in rows[:3] for value in row if clean_text(value)}
    if header_keys & {
        "attorney", "firm", "email", "phone", "company", "companyname",
        "代理机构名称", "代理名称", "代理人名称", "nameofforeignpatentsagent",
    }:
        return None

    starts: list[int] = []
    for index, row in enumerate(rows):
        first = clean_text(row[0]) if row else ""
        rest = " ".join(clean_text(value) for value in row[1:] if clean_text(value))
        if first and rest and not re.match(r"(?i)^(?:tel|email|website|phone|fax)\b", first):
            starts.append(index)
    if len(starts) < 2:
        return None

    parsed: list[list[str]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else min(len(rows), start + 12)
        identity = clean_text(rows[start][0])
        block_parts = [clean_text(value) for value in rows[start][1:] if clean_text(value)]
        for row in rows[start + 1 : end]:
            block_parts.extend(clean_text(value) for value in row if clean_text(value))
        block = "\n".join(block_parts)
        emails, phones, websites = extract_contact_values(block)
        if not (emails or phones or websites):
            continue

        before_channels = re.split(
            r"(?i)\b(?:tel|telephone|phone|mobile|cell|fax|e-?mail|email|website|web\s*site)\b",
            clean_text(" ".join(block_parts)),
            maxsplit=1,
        )[0].strip(" ,;.")
        firm = identity if _FIRM_HINT_RE.search(identity) else ""
        attorney = "" if firm else identity
        parsed.append([
            attorney,
            firm,
            before_channels,
            "; ".join(emails),
            "; ".join(phones),
            "; ".join(websites),
        ])

    if len(parsed) < 2:
        return None
    return TableData(
        source_member=table.source_member,
        sheet_name=f"{table.sheet_name}-vertical-directory" if table.sheet_name else "vertical-directory",
        rows=[["Attorney", "Firm", "Address", "Email", "Phone", "Website"], *parsed],
    )


def _rewrite_known_headers(table: TableData) -> TableData:
    rows = [list(row) for row in table.rows]
    for row in rows[:60]:
        for index, value in enumerate(row):
            replacement = _GENERIC_HEADER_ALIASES.get(_key(value))
            if replacement:
                row[index] = replacement
    return TableData(source_member=table.source_member, sheet_name=table.sheet_name, rows=rows)


def adapt_contact_tables(tables: list[TableData]) -> list[TableData]:
    """Normalize real public-register layouts before generic profile detection."""
    out: list[TableData] = []
    for table in tables:
        singapore = _adapt_singapore_foreign_agent(table)
        if singapore is not None:
            out.append(singapore)
            continue
        mozambique = _adapt_mozambique_table(table)
        if mozambique is not None:
            out.append(mozambique)
            continue
        vertical = _adapt_vertical_directory(table)
        if vertical is not None:
            out.append(vertical)
            continue
        out.append(_rewrite_known_headers(table))
    return out
