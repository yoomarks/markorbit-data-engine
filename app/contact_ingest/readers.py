from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET

from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import clean_text


SUPPORTED_EXTENSIONS = {".xlsx", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".zip"}
STRUCTURED_MEMBER_EXTENSIONS = SUPPORTED_EXTENSIONS - {".zip"}
_XLSX_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CELL_REF_RE = re.compile(r"([A-Z]+)\d+")


def _column_index(ref: str) -> int:
    match = _CELL_REF_RE.fullmatch(ref.upper())
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - 64)
    return max(value - 1, 0)


def _shared_strings(zf: ZipFile) -> list[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    values: list[str] = []
    for si in root.findall(f"{{{_XLSX_MAIN}}}si"):
        parts = [node.text or "" for node in si.iter(f"{{{_XLSX_MAIN}}}t")]
        values.append("".join(parts))
    return values


def _xlsx_sheet_targets(zf: ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    relationships = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall(f"{{{_PKG_REL}}}Relationship")
    }
    out: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{_XLSX_MAIN}}}sheet"):
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(f"{{{_XLSX_REL}}}id", "")
        target = rel_targets.get(rel_id, "")
        if not target:
            continue
        if target.startswith("/"):
            member = target.lstrip("/")
        elif target.startswith("xl/"):
            member = target
        else:
            member = f"xl/{target.lstrip('./')}"
        out.append((name, member))
    return out


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return clean_text("".join(node.text or "" for node in cell.iter(f"{{{_XLSX_MAIN}}}t")))
    value_node = cell.find(f"{{{_XLSX_MAIN}}}v")
    value = "" if value_node is None else value_node.text or ""
    if kind == "s" and value:
        try:
            return clean_text(shared[int(value)])
        except (ValueError, IndexError):
            return clean_text(value)
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    return clean_text(value)


def _parse_xlsx_zip(zf: ZipFile, *, source_member: str) -> list[TableData]:
    shared = _shared_strings(zf)
    tables: list[TableData] = []
    for sheet_name, member in _xlsx_sheet_targets(zf):
        try:
            stream = zf.open(member)
        except KeyError:
            continue
        rows: list[list[str]] = []
        with stream:
            for _, row in ET.iterparse(stream, events=("end",)):
                if row.tag != f"{{{_XLSX_MAIN}}}row":
                    continue
                values: dict[int, str] = {}
                max_idx = -1
                for cell in row.findall(f"{{{_XLSX_MAIN}}}c"):
                    ref = cell.attrib.get("r", "A1")
                    idx = _column_index(ref)
                    max_idx = max(max_idx, idx)
                    values[idx] = _cell_text(cell, shared)
                rows.append([values.get(idx, "") for idx in range(max_idx + 1)] if max_idx >= 0 else [])
                row.clear()
        tables.append(TableData(source_member=source_member, sheet_name=sheet_name, rows=rows))
    return tables


def read_xlsx_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    try:
        with ZipFile(io.BytesIO(data)) as zf:
            return _parse_xlsx_zip(zf, source_member=source_member)
    except BadZipFile as exc:
        raise ValueError(f"Invalid XLSX file: {source_member}") from exc


def _decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def read_delimited_bytes(data: bytes, *, source_member: str, delimiter: str | None = None) -> list[TableData]:
    text, _encoding = _decode_text(data)
    sample = text[:65536]
    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t" if source_member.lower().endswith(".tsv") else ","
    rows = [[clean_text(value) for value in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [TableData(source_member=source_member, sheet_name="", rows=rows)]


def _dict_rows_to_table(records: list[dict[str, object]], *, source_member: str) -> list[TableData]:
    headers: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            key_text = clean_text(key)
            if key_text and key_text not in seen:
                seen.add(key_text)
                headers.append(key_text)
    rows = [headers]
    for record in records:
        clean_record = {clean_text(key): value for key, value in record.items()}
        rows.append([clean_text(clean_record.get(header, "")) for header in headers])
    return [TableData(source_member=source_member, sheet_name="", rows=rows)]


def read_json_bytes(data: bytes, *, source_member: str, ndjson: bool = False) -> list[TableData]:
    text, _encoding = _decode_text(data)
    if ndjson:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            list_values = [value for value in payload.values() if isinstance(value, list)]
            if len(list_values) != 1:
                raise ValueError("JSON object must contain exactly one list of records")
            records = list_values[0]
        else:
            raise ValueError("JSON input must be a list of objects or object containing one record list")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("JSON contact input records must be objects")
    return _dict_rows_to_table(records, source_member=source_member)


def _read_member(data: bytes, *, name: str) -> list[TableData]:
    ext = Path(name).suffix.lower()
    if ext == ".xlsx":
        return read_xlsx_bytes(data, source_member=name)
    if ext in {".csv", ".tsv"}:
        return read_delimited_bytes(data, source_member=name, delimiter="\t" if ext == ".tsv" else None)
    if ext in {".json", ".jsonl", ".ndjson"}:
        return read_json_bytes(data, source_member=name, ndjson=ext in {".jsonl", ".ndjson"})
    raise ValueError(f"Unsupported structured contact member: {name}")


def read_input(path: Path) -> list[TableData]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported contact input type {ext or '<none>'}. Supported: {supported}")
    data = path.read_bytes()
    if ext != ".zip":
        return _read_member(data, name=path.name)

    tables: list[TableData] = []
    with ZipFile(io.BytesIO(data)) as zf:
        members = [
            name for name in zf.namelist()
            if not name.endswith("/") and Path(name).suffix.lower() in STRUCTURED_MEMBER_EXTENSIONS
        ]
        if not members:
            raise ValueError("ZIP contains no supported structured contact files")
        for member in members:
            tables.extend(_read_member(zf.read(member), name=member))
    return tables
